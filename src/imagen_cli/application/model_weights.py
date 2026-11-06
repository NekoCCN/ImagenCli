"""One weight store for every local model family: paths, digests, downloads.

Model families keep their own pinned manifests; this module owns the parts that
must behave identically for all of them, so a new family cannot drift into a
weaker download path.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_WEIGHT_BYTES = 2 * 1024 * 1024 * 1024


def weights_directory() -> Path:
    configured = os.environ.get("IMAGEN_MODEL_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "imagen" / "models"


def weight_path(name: str, suffix: str = ".onnx") -> Path:
    if suffix not in {".onnx", ".pt"}:
        raise ValueError("weights suffix must be .onnx or .pt")
    path = weights_directory() / f"{name}{suffix}"
    for candidate in (path, path.with_suffix(".sha256"), path.with_suffix(".part")):
        if any(part.is_symlink() for part in (candidate, *candidate.parents)):
            raise ValueError("model paths must not contain symlinks")
    return path


def digest_path(name: str, suffix: str = ".onnx") -> Path:
    return weight_path(name, suffix).with_suffix(".sha256")


def recorded_digest(name: str, suffix: str = ".onnx") -> str | None:
    path = digest_path(name, suffix)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        return None
    return value


def record_digest(name: str, suffix: str, digest: str) -> None:
    path = digest_path(name, suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(digest, encoding="ascii")
    temporary.replace(path)


def weight_state(path: Path, expected: str | None, verify: bool,
                 *, max_bytes: int | None = None) -> str:
    limit = MAX_WEIGHT_BYTES if max_bytes is None else max_bytes
    if not path.exists():
        return "missing"
    if not path.is_file() or path.stat().st_size > limit:
        return "corrupt"
    if not verify:
        return "ready" if expected is not None else "unverified"
    if expected is None:
        return "unverified"
    return "ready" if hashlib.sha256(path.read_bytes()).hexdigest() == expected else "corrupt"


def download(url: str, target: Path) -> None:
    import httpx

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    if target.exists():
        return
    with httpx.stream(
        "GET", url, timeout=120.0, follow_redirects=True, trust_env=False
    ) as response:
        response.raise_for_status()
        if response.headers.get("content-length") and int(
            response.headers["content-length"]
        ) > MAX_WEIGHT_BYTES:
            raise ValueError("weights exceed the size limit")
        with temporary.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
                if output.tell() > MAX_WEIGHT_BYTES:
                    raise ValueError("weights exceed the size limit")
    temporary.replace(target)


def ensure_weight(
    name: str,
    pinned: str | None,
    url: str | None,
    *,
    suffix: str = ".onnx",
    accept: bool = False,
    download_fn: Callable[[str, Path], None] | None = None,
    label: str = "weights",
    max_bytes: int | None = None,
) -> Path:
    """Return a verified weight file, or raise; nothing is fetched without ``accept``."""
    limit = MAX_WEIGHT_BYTES if max_bytes is None else max_bytes
    path = weight_path(name, suffix)
    if path.exists():
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("weights exceed the size limit or are not a regular file")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = pinned or recorded_digest(name, suffix)
        if expected is not None and expected != actual:
            raise ValueError("weights digest mismatch")
        if expected is None:
            record_digest(name, suffix, actual)
        return path
    if url is None:
        raise ValueError(
            f"weights are missing; place {path.name} in {path.parent} "
            f"(no maintained {label} release is claimed)"
        )
    if not accept:
        raise ValueError(
            "weights are missing; rerun with accept=true after reviewing " + str(url)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".imagen-download-") as scratch:
        staged = Path(scratch) / path.name
        (download_fn or download)(str(url), staged)
        if not staged.is_file() or staged.stat().st_size > limit:
            raise ValueError("weights exceed the size limit or are not a regular file")
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        if pinned is not None and digest != pinned:
            raise ValueError("downloaded weights do not match the pinned digest")
        # Hard-link publication refuses an existing file instead of overwriting it.
        os.link(staged, path)
    record_digest(name, suffix, digest)
    return path


def entry_row(name: str, entry: dict[str, Any], *, verify: bool) -> dict[str, Any]:
    """One catalog row shared by every family's ``models`` listing and TUI panel."""
    path = weight_path(name)
    expected = entry.get("sha256") or recorded_digest(name)
    state = weight_state(path, expected, verify)
    if verify and state == "unverified" and path.is_file():
        record_digest(name, ".onnx", hashlib.sha256(path.read_bytes()).hexdigest())
    return {
        "model": name,
        "task": entry.get("task"),
        "category": entry.get("category", "general"),
        "usage": entry.get("usage"),
        "license": entry["license"],
        "source": entry["source"],
        "note": entry.get("note"),
        "downloadable": entry.get("url") is not None,
        "downloaded": path.exists(),
        "state": state,
        "bytes": path.stat().st_size if path.exists() else None,
        "sha256": entry.get("sha256"),
        "multiple": entry.get("multiple", 1),
    }
