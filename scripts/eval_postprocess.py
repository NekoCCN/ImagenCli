"""Offline synthetic smoothing evaluation; creates a new output directory only."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PIL import __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from imagen_cli.application import media_ops  # noqa: E402
from imagen_cli.application.filters_numpy import ssim  # noqa: E402
from imagen_cli.application.postprocess_service import (  # noqa: E402
    check_budget,
    normalize_color,
)

VARIANTS = {
    "guided_fixed": ("guided_smooth", {"radius": 8, "epsilon": 0.01, "strength": 1}),
    "weighted_guided": (
        "weighted_guided_smooth", {"radius": 8, "epsilon": 0.01, "strength": 1},
    ),
    **{
        f"local_laplacian_{samples}": (
            "local_laplacian_sampled",
            {"sigma": 0.2, "detail": 2, "samples": samples, "levels": 5, "strength": 1},
        )
        for samples in (8, 32)
    },
}


def mean_neighbor_difference(image: Image.Image) -> float:
    """Forward differences retain Nyquist texture that central differences cancel."""
    values = np.asarray(image.convert("L"), dtype=np.float64)
    differences = [np.abs(np.diff(values, axis=axis)).ravel() for axis in (0, 1)]
    count = sum(part.size for part in differences)
    return float(sum(part.sum() for part in differences) / count) if count else 0.0


def synthetic_cases() -> dict[str, Image.Image]:
    line = np.full((256, 256, 3), 250, dtype=np.uint8)
    line[64, 20:236] = 10
    line[20:236, 128] = 10
    for index in range(180):
        line[190 - index // 2, 30 + index] = 10
    cel = np.zeros_like(line)
    cel[:, :128] = (210, 120, 90)
    cel[:, 128:] = (40, 70, 150)
    cel[60:90, 40:100] = (250, 230, 210)
    checker = (np.indices((256, 256)).sum(0) % 2 * 60 + 100).astype(np.uint8)
    return {
        "line_1px": Image.fromarray(line),
        "cel_edge": Image.fromarray(cel),
        "checker_1px": Image.fromarray(np.repeat(checker[..., None], 3, 2)),
    }


def transparency_pair(fractional: bool) -> tuple[Image.Image, Image.Image]:
    pixels = np.zeros((256, 256, 4), dtype=np.uint8)
    pixels[..., :3] = (90, 140, 200)
    pixels[..., 3] = 255
    pixels[:, :128, 3] = 0
    if fractional:
        pixels[:, 128:, 3] = np.arange(1, 129, dtype=np.uint8)
    first, second = pixels.copy(), pixels.copy()
    first[:, :128, :3] = (255, 0, 255)
    second[:, :128, :3] = (0, 255, 0)
    return Image.fromarray(first), Image.fromarray(second)


def paired_cases() -> dict[str, Image.Image]:
    cases = synthetic_cases()
    return {name: cases[name] for name in ("line_1px", "cel_edge")}


def degrade_gaussian(source: Image.Image) -> Image.Image:
    rng = np.random.default_rng(20261025)
    values = np.asarray(source, dtype=np.float64)
    noise = rng.normal(0, 8, values.shape)
    degraded = np.clip(np.rint(values + noise), 0, 255).astype(np.uint8)
    return Image.fromarray(degraded, mode="RGB")


def evaluate_paired(output: Path, variants: dict) -> list[dict]:
    records = []
    degradation = {
        "kind": "additive_rgb_gaussian", "seed": 20261025, "sigma": 8,
        "encoding": "srgb_u8_clip_round",
    }
    for name, clean in paired_cases().items():
        degraded = degrade_gaussian(clean)
        clean_file = f"paired-{name}-clean.png"
        input_file = f"paired-{name}-input.png"
        clean.save(output / clean_file)
        degraded.save(output / input_file)
        clean_values = np.asarray(clean, dtype=np.float64)
        input_values = np.asarray(degraded, dtype=np.float64)
        variant_records = []
        for label, (operation, params) in variants.items():
            result = media_ops.OPS[operation](degraded, **params)
            filename = f"paired-{name}-{label}.png"
            result.save(output / filename)
            result_values = np.asarray(result, dtype=np.float64)
            variant_records.append({
                "label": label,
                "file": filename,
                "mae_vs_clean": float(np.abs(result_values - clean_values).mean()),
                "ssim_vs_clean": ssim(clean, result),
            })
        records.append({
            "case": f"{name}_gaussian", "degradation": degradation,
            "clean_file": clean_file, "input_file": input_file,
            "clean_sha256": hashlib.sha256((output / clean_file).read_bytes()).hexdigest(),
            "input_sha256": hashlib.sha256((output / input_file).read_bytes()).hexdigest(),
            "input_mae_vs_clean": float(np.abs(input_values - clean_values).mean()),
            "input_ssim_vs_clean": ssim(clean, degraded),
            "variants": variant_records,
        })
    return records


def evaluation_variants(ablation: bool) -> dict:
    variants = dict(VARIANTS)
    if ablation:
        for baseline, changes in (
            ("weighted_guided", {"radius": (4, 16), "epsilon": (0.001, 0.1)}),
            ("local_laplacian_8", {"detail": (0.5, 1), "levels": (3, 7), "samples": (16,)}),
        ):
            operation, params = VARIANTS[baseline]
            for parameter, values in changes.items():
                for value in values:
                    variants[f"{baseline}_{parameter}_{value}"] = (
                        operation, {**params, parameter: value},
                    )
    return variants


def evaluate_local(path: Path, output: Path, index: int, variants: dict) -> dict:
    # Only explicitly supplied PNGs are read; never discover files in a workspace.
    if path.suffix.lower() != ".png":
        raise ValueError("local evaluation accepts explicit PNG inputs only")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with Image.open(path) as opened:
        for operation, params in variants.values():
            check_budget(opened.size, [{"op": operation, "params": params}])
        source, color = normalize_color(opened)
    width, height = source.size
    crop_width, crop_height = min(256, width), min(256, height)
    left, top = (width - crop_width) // 2, (height - crop_height) // 2
    box = (left, top, left + crop_width, top + crop_height)
    source.save(output / f"local-{index}-source.png", icc_profile=source.info["icc_profile"])
    source.crop(box).save(output / f"local-{index}-crop-source.png")
    records = []
    original = np.asarray(source, dtype=np.float64)
    for label, (operation, params) in variants.items():
        started = time.perf_counter()
        result = media_ops.OPS[operation](source, **params)
        seconds = time.perf_counter() - started
        filename = f"local-{index}-{label}.png"
        result.save(output / filename, icc_profile=source.info["icc_profile"])
        crop_name = f"local-{index}-{label}-crop.png"
        result.crop(box).save(output / crop_name)
        records.append({
            "label": label, "file": filename, "seconds": seconds,
            "mae": float(np.abs(np.asarray(result, dtype=np.float64) - original).mean()),
            "ssim_vs_source": ssim(source, result),
            "crops": [{"box": list(box), "file": crop_name,
                       "ssim_vs_source": ssim(source.crop(box), result.crop(box))}],
        })
    return {"source": str(path), "sha256": digest, "source_size": list(source.size),
            "color": color, "variants": records}


def evaluate(output: Path, *, inputs: list[Path] | None = None, ablation: bool = False) -> dict:
    variants = evaluation_variants(ablation)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for name, source in synthetic_cases().items():
        source.save(output / f"{name}-source.png")
        grey = np.asarray(source.convert("L"), dtype=np.float64)
        baseline = mean_neighbor_difference(source)
        records = []
        for label, (operation, params) in variants.items():
            started = time.perf_counter()
            result = media_ops.OPS[operation](source, **params)
            seconds = time.perf_counter() - started
            result.save(output / f"{name}-{label}.png")
            values = np.asarray(result.convert("L"), dtype=np.float64)
            record = {
                "label": label, "seconds": seconds,
                "neighbor_ratio": mean_neighbor_difference(result) / baseline,
            }
            if name == "line_1px":
                record["line_lift"] = float((values - grey)[grey < 64].mean())
            if name == "checker_1px":
                record["std_ratio"] = float(values.std() / grey.std())
            records.append(record)
        cases.append({"case": name, "source_size": list(source.size), "variants": records})
    transparency = {}
    for name, fractional in (("binary", False), ("fractional", True)):
        first, second = transparency_pair(fractional)
        original = np.asarray(first)
        hidden = original[..., 3] == 0
        first.save(output / f"rgba-{name}-source-a.png")
        second.save(output / f"rgba-{name}-source-b.png")
        checks = {}
        for label, (operation, params) in variants.items():
            one = media_ops.OPS[operation](first, **params)
            two = media_ops.OPS[operation](second, **params)
            a, b = np.asarray(one), np.asarray(two)
            one.save(output / f"rgba-{name}-{label}.png")
            checks[label] = {
                "alpha_exact": bool(np.array_equal(a[..., 3], original[..., 3])),
                "hidden_preserved": bool(np.array_equal(a[hidden], original[hidden])),
                "visible_ignores_hidden": bool(np.array_equal(a[~hidden], b[~hidden])),
            }
        transparency[name] = checks
    report = {
        "scope": "synthetic negative cases, not real-image quality acceptance",
        "metric": "pillow_l_mean_abs_neighbor_difference_v1",
        "environment": {
            "python": platform.python_version(), "numpy": np.__version__,
            "pillow": pillow_version,
        },
        "variants": {key: {"op": op, "params": params} for key, (op, params) in variants.items()},
        "cases": cases, "transparency": transparency,
        "paired_scope": "synthetic additive RGB Gaussian noise only",
        "paired_cases": evaluate_paired(output, variants),
        "ssim_metric": "local_gaussian_pillow_l_v1",
        "local_images": [
            evaluate_local(path, output, index, variants) for index, path in enumerate(inputs or [])
        ],
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; never overwritten",
    )
    parser.add_argument(
        "--input", action="append", type=Path, default=[],
        help="Explicit PNG input; may be repeated and is never discovered automatically",
    )
    parser.add_argument("--ablation", action="store_true", help="Run bounded parameter variants")
    args = parser.parse_args()
    evaluate(args.output, inputs=args.input, ablation=args.ablation)
    print(args.output / "report.json")

if __name__ == "__main__":
    main()
