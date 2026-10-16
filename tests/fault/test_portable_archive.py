"""Portable archives fail closed and never carry secrets or execution rights."""

import hashlib
import io
import json
import stat
import zipfile

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.export_service import export_session
from imagen_cli.application.import_service import import_archive
from imagen_cli.application.session_service import SessionService
from imagen_cli.domain.primitive import new_id

SECRET = "fixture-only-secret-never-real"


def _seed(container, png_bytes):
    service = SessionService(container.repositories, container.artifact_store)
    session = service.create_session("portable")
    turn = service.create_turn(
        session.id,
        instruction="draw",
        operation="generate",
        resolved_profile_snapshot={
            "provider": "openai",
            "credential_ref": f"env:{SECRET}",
            "api_key": SECRET,
            "temporary_url": f"https://images.example.test/a.png?token={SECRET}",
            "signed_url": f"https://images.example.test/a.png?signature={SECRET}",
            "raw_response": {"data": SECRET},
        },
        model="image-model",
        parameters={"count": 1},
    )
    artifact = service.save_artifact(
        turn.id,
        png_bytes,
        origin_manifest={
            "kind": "provider_output",
            "output_index": 0,
            "signed_url": f"https://images.example.test/a.png?signature={SECRET}",
        },
    )
    return session.id, turn.id, artifact.id


def _export(container, session_id, destination):
    return export_session(container.repositories, container.artifact_store, session_id, destination)


def _entries(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rewrite(source, destination, mutate):
    entries = _entries(source)
    manifest = json.loads(entries["manifest.json"])
    mutate(manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(destination, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _raw_archive(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _import(container, path, **kwargs):
    return import_archive(container.repositories, container.artifact_store, path, **kwargs)


@pytest.mark.parametrize(
    "entry",
    ["../evil.png", "/absolute.png", "C:/windows.png", "assets/..", "assets/a\\b.png"],
)
def test_unsafe_zip_paths_are_rejected(tmp_path, entry):
    archive = tmp_path / "bad.zip"
    _raw_archive(archive, {entry: b"x", "manifest.json": b"{}"})
    with Container(workspace=str(tmp_path / "work")) as container:
        with pytest.raises(ValueError):
            _import(container, archive)


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600])
def test_symlink_hardlink_and_unsupported_modes_are_rejected(tmp_path, mode):
    archive = tmp_path / "linked.zip"
    info = zipfile.ZipInfo("assets/entry.image")
    info.external_attr = mode << 16
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(info, b"target")
        handle.writestr("manifest.json", b"{}")
    with Container(workspace=str(tmp_path / "work")) as container:
        with pytest.raises(ValueError):
            _import(container, archive)


def test_encrypted_entries_are_rejected(tmp_path):
    archive = tmp_path / "encrypted.zip"
    info = zipfile.ZipInfo("assets/entry.image")
    info.flag_bits = 0x1
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(info, b"ciphertext")
        handle.writestr("manifest.json", b"{}")
    with Container(workspace=str(tmp_path / "work")) as container:
        with pytest.raises(ValueError):
            _import(container, archive)


def test_duplicate_normalized_paths_and_file_directory_collisions_are_rejected(tmp_path):
    for entries in (
        {"manifest.json": b"{}", "assets/A.image": b"x", "assets/a.image": b"y"},
        {"manifest.json": b"{}", "assets/a.image": b"x", "assets/a.image/nested": b"y"},
    ):
        archive = tmp_path / "duplicate.zip"
        _raw_archive(archive, entries)
        with Container(workspace=str(tmp_path / "work")) as container:
            with pytest.raises(ValueError):
                _import(container, archive)


def test_oversized_entries_are_rejected(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, _, _ = _seed(container, png_bytes)
        archive = tmp_path / "history.zip"
        _export(container, session_id, archive)
        with pytest.raises(ValueError):
            _import(container, archive, max_file_size=8)


def test_duplicate_ids_in_the_manifest_are_rejected(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, _, artifact_id = _seed(container, png_bytes)
        valid = tmp_path / "valid.zip"
        _export(container, session_id, valid)
        duplicate = tmp_path / "duplicate-id.zip"

        def mutate(manifest):
            manifest["artifacts"][0]["id"] = artifact_id
            manifest["artifacts"].append({**manifest["artifacts"][0], "path": "assets/other.image"})
            manifest["references"][0]["artifact_id"] = artifact_id

        _rewrite(valid, duplicate, mutate)
        with pytest.raises(ValueError):
            _import(container, duplicate)


def test_private_fields_and_secrets_are_rejected_from_manifests(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, _, _ = _seed(container, png_bytes)
        valid = tmp_path / "valid.zip"
        _export(container, session_id, valid)
        poisoned = tmp_path / "poisoned.zip"

        def mutate(manifest):
            manifest["session"]["api_key"] = SECRET
            manifest["turns"][0]["temporary_url"] = SECRET

        _rewrite(valid, poisoned, mutate)
        with pytest.raises(ValueError):
            _import(container, poisoned)


def test_export_excludes_secrets_private_state_and_approval_rights(tmp_path, png_bytes):
    secret_file = tmp_path / "private-marker.bin"
    secret_file.write_bytes(b"private-provider-response")
    with Container(workspace=str(tmp_path / "work")) as container:
        container.private_state_store.write("job_" + "1" * 32, b"private-provider-response")
        session_id, _, _ = _seed(container, png_bytes)
        archive = tmp_path / "safe.zip"
        _export(container, session_id, archive)
        entries = _entries(archive)
        text = "\n".join(
            data.decode("utf-8", "ignore") for name, data in entries.items()
            if name.endswith(".json")
        )
        assert SECRET not in text
        assert "credential_ref" not in text
        assert "api_key" not in text
        assert "signed_url" not in text
        assert "temporary_url" not in text
        assert "raw_response" not in text
        assert "private-provider-response" not in text
        assert "execution_job_id" not in text
        assert not any(name.startswith("private/") for name in entries)
        assert not any("private-marker" in name for name in entries)


def test_export_marks_missing_files_and_native_contexts(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, turn_id, artifact_id = _seed(container, png_bytes)
        container.repositories.insert("native_contexts", {
            "id": new_id("ctx"),
            "provider": "openai",
            "family": "images",
            "profile_identity": "offline",
            "model": "image-model",
            "format_version": 1,
            "private_relpath": "context.bin",
            "sha256": "a" * 64,
            "expires_at": None,
            "availability": "available",
            "source_turn_id": turn_id,
        })
        archive = tmp_path / "missing.zip"
        data = _export(container, session_id, archive)
        assert any(item["kind"] == "native_context_missing" for item in data["omissions"])
        entries = _entries(archive)
        manifest = json.loads(entries["manifest.json"])
        assert manifest["native_contexts"][0]["availability"] == "missing"
        assert manifest["native_contexts"][0]["source_turn_id"] == turn_id
        container.artifact_store.get(artifact_id).unlink()
        data = _export(container, session_id, tmp_path / "missing-file.zip")
        assert any(
            item["kind"] == "missing_file" and item["artifact_id"] == artifact_id
            for item in data["omissions"]
        )


def test_import_remaps_ids_preserves_relationships_and_reports_missing_native(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "source")) as source:
        session_id, turn_id, artifact_id = _seed(source, png_bytes)
        source.repositories.insert("native_contexts", {
            "id": new_id("ctx"),
            "provider": "openai",
            "family": "images",
            "profile_identity": "offline",
            "model": "image-model",
            "format_version": 1,
            "private_relpath": "context.bin",
            "sha256": "a" * 64,
            "expires_at": None,
            "availability": "available",
            "source_turn_id": turn_id,
        })
        archive = tmp_path / "history.zip"
        _export(source, session_id, archive)
        expected_sha = hashlib.sha256(png_bytes).hexdigest()

    with Container(workspace=str(tmp_path / "target")) as target:
        data = _import(target, archive)
        imported = data["session"]
        assert imported["id"] != session_id
        assert data["origin_map"]["session"][session_id] == imported["id"]
        assert session_id not in data["origin_map"]["turns"]
        new_turn = data["origin_map"]["turns"][turn_id]
        new_artifact = data["origin_map"]["artifacts"][artifact_id]
        turn = target.repositories.get("turns", new_turn)
        artifact = target.repositories.get("artifacts", new_artifact)
        assert turn["session_id"] == imported["id"]
        assert artifact["producer_turn_id"] == new_turn
        assert artifact["sha256"] == expected_sha
        assert target.artifact_store.get(new_artifact).read_bytes() == png_bytes
        refs = target.repositories.list("artifact_references", turn_id=new_turn)
        assert [ref["artifact_id"] for ref in refs] == [new_artifact]
        assert data["native_contexts_missing"][0]["availability"] == "missing"
        assert data["native_contexts_missing"][0]["source_turn_id"] == new_turn
        assert target.repositories.list("approvals") == []
        assert target.repositories.list("plans") == []


def test_export_never_overwrites_an_existing_file(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, _, _ = _seed(container, png_bytes)
        destination = tmp_path / "existing.zip"
        destination.write_bytes(b"user file")
        with pytest.raises(FileExistsError):
            _export(container, session_id, destination)
        assert destination.read_bytes() == b"user file"


def test_import_rejects_a_valid_zip_with_an_unexpected_extra_entry(tmp_path, png_bytes):
    with Container(workspace=str(tmp_path / "work")) as container:
        session_id, _, _ = _seed(container, png_bytes)
        valid = tmp_path / "valid.zip"
        _export(container, session_id, valid)
        extra = tmp_path / "extra.zip"
        entries = _entries(valid)
        entries["assets/unlisted.image"] = io.BytesIO(png_bytes).getvalue()
        with zipfile.ZipFile(extra, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        with pytest.raises(ValueError):
            _import(container, extra)
