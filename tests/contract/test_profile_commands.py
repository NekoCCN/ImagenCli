"""Profile command contract tests for explicit TOML and credential references."""

from pathlib import Path

from jsonschema import validate

from imagen_cli.application.container import Container
from imagen_cli.cli.registry import discover
from tests.integration.test_local_cli import invoke


def config(path: Path, *, revision: int = 1) -> None:
    path.write_text(
        f'''id = "local"
name = "Local"
provider = "openai"
family = "images"
base_url = "https://example.test/v1"
model_default = "image-model"
credential_ref = "keyring:local"
revision = {revision}
[trust_policy]
allow_redirects = false
''', encoding="utf-8")


def test_profile_add_list_show_and_update_are_persistent(tmp_path):
    workspace = tmp_path / "work"
    first = tmp_path / "profile.toml"
    config(first)
    code, result = invoke(workspace, "profile", "add", "local", "--config-file", str(first))
    assert code == 0
    assert result["data"]["profile"]["revision"] == 1
    assert "credential_ref" not in str(result)
    assert invoke(workspace, "profile", "list")[0] == 0
    assert invoke(workspace, "profile", "show", "local")[1]["data"]["profile"]["id"] == "local"
    validate(
        invoke(workspace, "profile", "show", "local")[1],
        discover("profile show")["output_schema"],
    )
    second = tmp_path / "profile2.toml"
    config(second, revision=1)
    code, result = invoke(workspace, "profile", "update", "local", "--config-file", str(second))
    assert code == 0
    assert result["data"]["profile"]["revision"] == 2
    assert invoke(workspace, "profile", "update", "local", "--config-file", str(second))[0] == 5
    with Container(workspace=str(workspace)) as container:
        stored = container.repositories.get("profiles", "local", include_private=True)
        assert stored["revision"] == 2
        assert stored["credential_ref"] == "keyring:local"


def test_profile_rejects_plaintext_credential_and_bad_config(tmp_path):
    workspace = tmp_path / "work"
    path = tmp_path / "bad.toml"
    config(path)
    path.write_text(path.read_text() + '\napi_key = "secret"\n', encoding="utf-8")
    assert invoke(workspace, "profile", "add", "local", "--config-file", str(path))[0] == 2
    assert invoke(workspace, "profile", "show", "missing")[0] == 4
