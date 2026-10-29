"""Super-resolution plumbing: tiling, providers and weight policy without downloads."""

import pytest

pytest.importorskip("numpy")

import io
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image

from imagen_cli.application import superres
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def nearest_runner(tile):
    return np.repeat(np.repeat(tile, 2, axis=0), 2, axis=1)


def test_tiled_upscale_matches_a_single_tile():
    array = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
    result = superres.tiled_upscale(array, nearest_runner, scale=2)
    expected = np.repeat(np.repeat(array, 2, axis=0), 2, axis=1)
    assert np.array_equal(result, expected)


def test_tiled_upscale_avoids_full_image_float_py_buffers():
    source = np.full((9, 7, 3), 123, dtype=np.uint8)
    result = superres.tiled_upscale(source, nearest_runner, scale=2, tile=4, overlap=1)
    # uint8 is the final output range and a quarter of the float32 buffer.
    assert result.dtype == np.uint8
    assert result.nbytes == 18 * 14 * 3
    assert np.array_equal(result, np.full((18, 14, 3), 123, dtype=np.uint8))


def test_overlapping_tiles_leave_no_seam():
    array = np.full((16, 16, 3), 10, dtype=np.uint8)

    def noisy(tile):
        return np.repeat(np.repeat(tile, 2, axis=0), 2, axis=1) + 5

    result = superres.tiled_upscale(array, noisy, scale=2, tile=8, overlap=2)
    assert result.shape == (32, 32, 3)
    assert np.array_equal(result, np.full((32, 32, 3), 15, dtype=np.uint8))


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_model_output_is_rejected(value):
    def invalid(tile):
        return np.full((tile.shape[0] * 4, tile.shape[1] * 4, 3), value)

    with pytest.raises(ValueError, match="finite"):
        superres.upscale(Image.new("RGB", (4, 4)), runner=invalid)


def test_x2_model_pads_odd_dimensions():
    pytest.importorskip("onnxruntime")
    # Exercise the real exported model when available, without downloading.
    path = superres.weights_path("realesrgan-x2plus")
    if not path.exists():
        pytest.skip("local x2 model is not installed")
    output, _ = superres.upscale(
        Image.new("RGB", (9, 7)), model="realesrgan-x2plus", backend="cpu",
    )
    assert output.size == (18, 14)


@pytest.mark.parametrize("model", ["film-degrainer", "dejpg-srformer-light"])
def test_restoration_models_preserve_dimensions_and_alpha(model):
    source = Image.new("RGBA", (9, 7), (80, 120, 160, 128))
    output, _ = superres.upscale(source, model=model, runner=lambda tile: tile)
    assert output.size == (9, 7)
    assert output.getpixel((0, 0)) == (80, 120, 160, 128)
    entry = next(item for item in superres.catalog()["models"] if item["model"] == model)
    assert entry["task"] in {"denoise", "dejpeg"}


def test_unknown_model_and_backend_are_refused():
    image = Image.new("RGB", (4, 4))
    with pytest.raises(ValueError, match="unknown super-resolution model"):
        superres.upscale(image, model="nope", runner=nearest_runner)
    with pytest.raises(ValueError, match="ai-onnx|unavailable"):
        superres.upscale(image, model="realesrgan-x4plus", backend="cuda")
    with pytest.raises(ValueError, match="ai-torch"):
        superres.upscale(image, model="realesrgan-x4plus", backend="torch")


def test_torchscript_is_rejected_before_deserialization(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    (tmp_path / "realesrgan-x4plus.pt").write_bytes(b"untrusted checkpoint")

    def forbidden(*args, **kwargs):
        pytest.fail("untrusted TorchScript reached deserialization")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        jit=SimpleNamespace(load=forbidden),
    ))
    with pytest.raises(ValueError, match="unsafe.*TorchScript"):
        superres.upscale(Image.new("RGB", (4, 4)), backend="torch")


@pytest.mark.parametrize("suffix", [".onnx", ".pt"])
def test_weights_symlink_is_rejected_without_touching_target(
    tmp_path, monkeypatch, suffix, symlink_or_skip
):
    root = tmp_path / "models"
    root.mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"private bytes")
    symlink_or_skip(root / ("realesrgan-x4plus" + suffix), external)
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(root))
    with pytest.raises(ValueError, match="symlink"):
        superres.ensure_weights("realesrgan-x4plus", suffix=suffix)
    assert external.read_bytes() == b"private bytes"


def test_weights_size_checked_before_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(superres, "MAX_WEIGHT_BYTES", 4)
    (tmp_path / "realesrgan-x4plus.onnx").write_bytes(b"too large")
    with pytest.raises(ValueError, match="size limit"):
        superres.ensure_weights("realesrgan-x4plus")


def test_digest_sidecar_symlink_is_rejected(tmp_path, monkeypatch, symlink_or_skip):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2, "license": "test", "source": "local",
    })
    (tmp_path / "manual.onnx").write_bytes(b"weights")
    external = tmp_path / "external"
    external.write_bytes(b"private bytes")
    symlink_or_skip(tmp_path / "manual.sha256", external)
    with pytest.raises(ValueError, match="symlink"):
        superres.ensure_weights("manual")
    assert external.read_bytes() == b"private bytes"


def test_models_without_an_upstream_export_require_a_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2, "license": "test", "source": "local",
    })
    with pytest.raises(ValueError, match="place manual.onnx"):
        superres.ensure_weights("manual")
    path = tmp_path / "manual.onnx"
    path.write_bytes(b"weights")
    assert superres.ensure_weights("manual") == path


def test_pinned_models_require_accept_before_download(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="accept=true"):
        superres.ensure_weights("realesrgan-x4plus")
    with pytest.raises(ValueError, match="pinned digest"):
        superres.ensure_weights(
            "realesrgan-x4plus", accept=True,
            download=lambda url, target: target.write_bytes(b"wrong"),
        )


def test_download_is_verified_before_publication(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, 'test', {
        'url': 'https://example.org/weights', 'sha256': hashlib.sha256(b'weights').hexdigest(),
    })
    def download(url, target):
        assert not (tmp_path / 'test.onnx').exists()
        assert target != tmp_path / 'test.onnx'
        target.write_bytes(b'weights')
    assert (
        superres.ensure_weights('test', accept=True, download=download).read_bytes()
        == b'weights'
    )


def test_failed_download_leaves_no_weights_or_partial(tmp_path, monkeypatch):
    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path))
    def download(url, target):
        target.write_bytes(b'partial')
        raise OSError('interrupted')
    with pytest.raises(OSError):
        superres.ensure_weights('realesrgan-x4plus', accept=True, download=download)
    assert list(tmp_path.iterdir()) == []


def test_manual_weights_record_and_enforce_a_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2, "license": "test", "source": "local",
    })
    path = tmp_path / "manual.onnx"
    path.write_bytes(b"weights")
    superres.ensure_weights("manual")
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        superres.ensure_weights("manual")


def test_alpha_is_carried_through_upscaling():
    image = Image.new("RGBA", (4, 4), (30, 60, 90, 128))
    quadruple = lambda tile: np.repeat(np.repeat(tile, 4, axis=0), 4, axis=1)  # noqa: E731
    result, engine = superres.upscale(image, runner=quadruple)
    assert result.size == (16, 16)
    assert result.getchannel("A").getpixel((0, 0)) == 128
    assert engine == {"tile": 256, "overlap": 16}


def test_catalog_reports_each_model_and_availability(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    value = superres.catalog()
    names = {entry["model"] for entry in value["models"]}
    assert {"realesrgan-x4plus", "realesrgan-x2plus",
            "realesrgan-x4plus-anime", "swinir-s-x2", "swinir-s-x4"} <= names
    anime = next(item for item in value["models"]
                 if item["model"] == "realesrgan-x4plus-anime")
    assert anime["downloadable"] is True and anime["downloaded"] is False


def test_round_up_pads_to_the_model_multiple():
    assert superres._round_up(65, 8) == 72
    assert superres._round_up(64, 8) == 64
    assert superres._round_up(65, 1) == 65


def test_benchmark_compares_models_without_writing(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    repositories = Repositories(connect(root / "state.sqlite3"))
    store = ArtifactStore(root / "artifacts", repositories)
    image = Image.new("RGB", (32, 32))
    for y in range(32):
        for x in range(32):
            image.putpixel((x, y), ((x * 8) % 256, (y * 8) % 256, ((x ^ y) * 4) % 256))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    identity = "art_" + "d" * 32
    store.stage(identity, buffer.getvalue())
    store.publish(identity)

    def fake_upscale(source, *, model, **_kwargs):
        scale = int(superres.MANIFEST[model]["scale"])
        return source.resize(
            (source.width * scale, source.height * scale), Image.Resampling.NEAREST
        ), {"provider": "fake"}

    monkeypatch.setattr(superres, "upscale", fake_upscale)
    before = repositories.list("artifacts", limit=10)
    result = superres.benchmark(
        repositories, store, identity, ["realesrgan-x4plus", "realesrgan-x2plus"]
    )
    assert [item["model"] for item in result["results"]] == [
        "realesrgan-x4plus", "realesrgan-x2plus",
    ]
    for item in result["results"]:
        assert item["psnr"] > 0 and 0 < item["ssim"] <= 1
        assert item["ssim_metric"] == "local_gaussian_pillow_l_v1"
        assert item["duration_ms"] >= 0
    assert len(repositories.list("artifacts", limit=10)) == len(before)
    repositories.connection.close()


class Repeated:
    """Mimic protobuf's upb repeated container: sequence, but no __iter__."""

    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


class Proto:
    """Small protobuf-shaped double; ONNX is an optional, absent dependency in CI."""

    def __init__(self, kind, **fields):
        self.DESCRIPTOR = SimpleNamespace(full_name="onnx." + kind)
        self.__dict__.update(fields)

    def ListFields(self):
        return [
            (None, Repeated(value) if isinstance(value, list) else value)
            for key, value in self.__dict__.items()
            if key != "DESCRIPTOR"
        ]


def standard_model(**fields):
    return Proto(
        "ModelProto", graph=Proto("GraphProto", node=[
            Proto("NodeProto", domain="", op_type="Identity")
        ]), opset_import=[Proto("OperatorSetIdProto", domain="", version=13)], **fields
    )


@pytest.fixture
def onnx_boundary(tmp_path, monkeypatch):
    path = tmp_path / "manual.onnx"
    path.write_bytes(b"local model stub")
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "manual", {
        "url": None, "sha256": None, "scale": 2,
    })
    state = SimpleNamespace(model=standard_model(), events=[], session_bytes=None)

    def load_model(source, *, load_external_data):
        assert load_external_data is False
        state.events.append("load")
        return state.model

    def check_model(model):
        state.events.append("check")
        if getattr(model, "invalid", False):
            raise RuntimeError("graph is not topologically sorted")

    def session(source, *, providers):
        state.events.append("session")
        state.session_bytes = source
        return SimpleNamespace(
            get_inputs=lambda: [SimpleNamespace(name="X", shape=[1, 3, None, None])],
            get_providers=lambda: providers,
        )

    # SessionOptions/custom-op registration is deliberately not provided.
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider"], InferenceSession=session,
    ))
    monkeypatch.setitem(sys.modules, "onnx", SimpleNamespace(
        load_model=load_model, checker=SimpleNamespace(check_model=check_model),
        defs=SimpleNamespace(
            onnx_opset_version=lambda: 23,
            has=lambda name, domain="": name == "Identity" and domain == "",
        ),
    ))
    return state


def make_local_runner():
    return superres._make_runner("manual", backend="cpu", device=None, accept=False)


@pytest.mark.parametrize("container", ["initializer", "nested_graph", "function", "sparse"])
@pytest.mark.parametrize("marker", ["location", "metadata"])
def test_external_data_is_rejected_before_ort(onnx_boundary, container, marker):
    tensor = Proto("TensorProto", data_location=1 if marker == "location" else 0,
                   external_data=[] if marker == "location" else [Proto("StringStringEntryProto")])
    holder = {
        "initializer": tensor,
        "nested_graph": Proto("AttributeProto", graphs=[Proto("GraphProto", tensor=tensor)]),
        "function": Proto("FunctionProto", domain="", attribute_proto=[
            Proto("AttributeProto", t=tensor)
        ]),
        "sparse": Proto("SparseTensorProto", values=tensor, indices=tensor),
    }[container]
    onnx_boundary.model.payload = [holder]
    with pytest.raises(ValueError, match="external data"):
        make_local_runner()
    assert "session" not in onnx_boundary.events
    assert "check" not in onnx_boundary.events


@pytest.mark.parametrize("kind,fields", [
    ("NodeProto", {"domain": "", "op_type": "RunArbitraryCode"}),
    ("NodeProto", {"domain": "com.microsoft", "op_type": "FusedConv"}),
    ("NodeProto", {"domain": "ai.onnx.ml", "op_type": "TreeEnsemble"}),
    ("OperatorSetIdProto", {"domain": "custom", "version": 1}),
    ("OperatorSetIdProto", {"domain": "ai.onnx.ml", "version": 3}),
    ("OperatorSetIdProto", {"domain": "", "version": 0}),
    ("OperatorSetIdProto", {"domain": "", "version": 9999}),
    ("FunctionProto", {"domain": "", "name": "Identity"}),
    ("FunctionProto", {"domain": "custom", "name": "Run"}),
])
def test_nonstandard_domains_and_opsets_are_rejected(onnx_boundary, kind, fields):
    onnx_boundary.model.payload = [Proto("GraphProto", node=[Proto(kind, **fields)])]
    with pytest.raises(ValueError, match="domain|opset"):
        make_local_runner()
    assert "session" not in onnx_boundary.events


def test_checker_failure_is_rejected_before_ort(onnx_boundary):
    onnx_boundary.model.invalid = True
    with pytest.raises(ValueError, match="ONNX.*preflight"):
        make_local_runner()
    assert "session" not in onnx_boundary.events


def test_standard_model_is_checked_before_mock_ort(onnx_boundary):
    _runner, engine = make_local_runner()
    assert onnx_boundary.events == ["load", "check", "session"]
    assert onnx_boundary.session_bytes == b"local model stub"
    assert engine["provider"] == "CPUExecutionProvider"


def test_missing_onnx_explains_extra_without_starting_ort(onnx_boundary, monkeypatch):
    monkeypatch.setitem(sys.modules, "onnx", None)
    with pytest.raises(ValueError, match="ai-onnx"):
        make_local_runner()
    assert "session" not in onnx_boundary.events


def test_missing_runtime_names_the_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(ValueError, match="imagen superres install --backend cpu --accept"):
        superres._make_runner("manual", backend=None, device=None, accept=False)


def test_pinned_digest_is_enforced_before_preflight(onnx_boundary, monkeypatch):
    monkeypatch.setitem(superres.MANIFEST["manual"], "sha256", "0" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        make_local_runner()
    assert not onnx_boundary.events


def test_malformed_model_is_rejected_before_ort(onnx_boundary, monkeypatch):
    def bad_load(*args, **kwargs):
        raise RuntimeError("invalid protobuf")

    monkeypatch.setattr(sys.modules["onnx"], "load_model", bad_load)
    with pytest.raises(ValueError, match="ONNX.*preflight"):
        make_local_runner()
    assert "session" not in onnx_boundary.events
