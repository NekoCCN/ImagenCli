"""Secrets never reach JSON output, errors, logs or exported payloads."""

import json

from imagen_cli.cli.entrypoint import main


def test_environment_secret_never_appears_in_output(tmp_path, capsys, monkeypatch):
    secret = "sk-test-should-never-appear"
    monkeypatch.setenv("IMAGEN_TEST_SECRET", secret)
    code = main([
        "--workspace", str(tmp_path), "--output", "json", "profile", "show", "missing",
    ])
    out = capsys.readouterr()
    assert code != 0
    assert secret not in out.out and secret not in out.err


def test_credential_reference_is_not_expanded_in_results(tmp_path, capsys):
    from tests.integration.test_local_cli import invoke

    path = tmp_path / "profile.toml"
    path.write_text(
        'id = "local"\nname = "Local"\nprovider = "openai"\nfamily = "images"\n'
        'base_url = "https://example.test/v1"\nmodel_default = "image-model"\n'
        'credential_ref = "env:IMAGEN_TEST_SECRET"\nrevision = 1\n[trust_policy]\n',
    )
    workspace = tmp_path / "work"
    code, result = invoke(workspace, "profile", "add", "local", "--config-file", str(path))
    assert code == 0
    assert "env:IMAGEN_TEST_SECRET" not in json.dumps(result)


def test_export_archive_contains_no_credentials_or_private_state(tmp_path, png_bytes):
    import zipfile

    from imagen_cli.application.container import Container
    from imagen_cli.application.export_service import export_session
    from tests.integration.test_approved_execution import execute, service, setup

    secret = "sk-export-must-not-leak"
    workspace = tmp_path / "ws"
    with Container(workspace=str(workspace)) as container:
        plan, _, validator, executor, _, _ = setup(container, png_bytes)
        execute(service(container, validator, executor), plan)
        destination = tmp_path / "session.zip"
        export_session(container.repositories, container.artifact_store,
                       plan.session_id, destination)
    with zipfile.ZipFile(destination) as archive:
        for name in archive.namelist():
            payload = archive.read(name)
            assert secret.encode() not in payload
            assert b"credential_ref" not in payload
            assert b"private_locator_ref" not in payload
            assert b"fixture-not-resolved" not in payload
