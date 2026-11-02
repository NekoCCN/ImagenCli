"""Catalog state is digest-aware; a torn sidecar never crashes or reads as ready."""

import hashlib

from imagen_cli.application import superres
from imagen_cli.cli.entrypoint import _parse

PINNED = "realesr-animevideov3"


def _row(report, name):
    return next(row for row in report["models"] if row["model"] == name)


def test_a_seven_byte_weight_is_corrupt_when_verified(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    superres.weights_path(PINNED).write_bytes(b"7 bytes")
    row = _row(superres.catalog(verify=True), PINNED)
    assert row["state"] == "corrupt"
    assert row["downloaded"] is True


def test_a_matching_weight_is_ready_when_verified(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    payload = b"weights"
    superres.weights_path(PINNED).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(superres.MANIFEST[PINNED], "sha256", digest)
    assert _row(superres.catalog(verify=True), PINNED)["state"] == "ready"


def test_a_torn_sidecar_reports_unverified(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "unpinned", {
        "url": "https://example.invalid/unpinned.onnx",
        "scale": 1, "license": "test", "source": "https://example.invalid",
    })
    superres.weights_path("unpinned").write_bytes(b"weights")
    superres.digest_path("unpinned").write_text("torn", encoding="ascii")
    assert _row(superres.catalog(), "unpinned")["state"] == "unverified"
    assert _row(superres.catalog(verify=True), "unpinned")["state"] == "unverified"


def test_the_default_catalog_does_not_hash_weights(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    payload = b"weights"
    superres.weights_path(PINNED).write_bytes(payload)
    monkeypatch.setattr(
        superres.hashlib, "sha256",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("hashed")),
    )
    assert _row(superres.catalog(), PINNED)["state"] == "ready"


def test_verification_records_a_valid_sidecar_atomically(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEN_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(superres.MANIFEST, "unpinned", {
        "url": None, "scale": 1, "license": "test", "source": "https://example.invalid",
    })
    payload = b"weights"
    superres.weights_path("unpinned").write_bytes(payload)
    superres.catalog(verify=True)
    sidecar = superres.digest_path("unpinned")
    assert sidecar.read_text(encoding="ascii") == hashlib.sha256(payload).hexdigest()
    assert not list(tmp_path.glob("*.part"))


def test_superres_models_parses_the_verify_flag():
    command, args, _ = _parse(["superres", "models", "--verify"])
    assert command == "superres models"
    assert args == {"verify": True}
