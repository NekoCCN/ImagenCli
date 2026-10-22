"""CLI planning validates persisted declarations without provider access."""

import pytest
from jsonschema import validate

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.registry import discover
from tests.contract.test_profile_commands import config
from tests.integration.test_local_cli import invoke
from tests.support import capability


@pytest.mark.parametrize("command,operation,inputs", [
    ("generate", "edit", ["file:missing.png"]),
    ("edit", "generate", []),
])
def test_container_rejects_operation_mismatch_before_storage(tmp_path, command, operation, inputs):
    workspace = tmp_path / "unused"
    with Container(workspace=str(workspace)) as container:
        with pytest.raises(ValueError):
            container.execute(command, {
                "schema_version": 1, "operation": operation, "session": "poster",
                "prompt": "image", "inputs": inputs,
            })
    assert not workspace.exists()


def seed(workspace, tmp_path):
    path = tmp_path / "profile.toml"
    config(path)
    assert invoke(workspace, "profile", "add", "local", "--config-file", str(path))[0] == 0
    with Container(workspace=str(workspace)) as container:
        SessionService(container.repositories, container.artifact_store).create_session(
            "poster", default_profile_id="local", default_model="image-model",
        )
        row = capability(parameter_schema={
            "type": "object", "properties": {"count": {"type": "integer", "maximum": 2}},
            "additionalProperties": False,
        })
        container.repositories.insert("capabilities", row)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("蓝天", encoding="utf-8")
    return prompt


def test_dry_run_persists_plan_but_no_job_or_turn(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt), "--count", "2", "--dry-run")
    assert code == 0
    assert result["status"] == "planned"
    validate(result, discover("generate")["output_schema"])
    assert result["data"]["plan"]["normalized_parameters"] == {"count": 2}
    with Container(workspace=str(workspace)) as container:
        assert len(container.repositories.list("plans")) == 1
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("turns") == []


def test_unapproved_creation_returns_reviewable_plan_without_dispatch(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt))
    assert code == 6
    assert result["status"] == "approval_required"
    validate(result, discover("generate")["output_schema"])
    assert result["data"]["plan"]["prompt_snapshot"] == "[redacted]"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("jobs") == []
        assert container.repositories.list("approvals")[0]["state"] == "pending"


def test_unsupported_parameters_do_not_create_plan(tmp_path):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    code, result = invoke(workspace, "generate", "--session", "poster", "--prompt-file",
                          str(prompt), "--count", "0", "--dry-run")
    assert code == 2
    assert result["status"] == "validation_error"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("plans") == []
        assert container.repositories.list("jobs") == []


def test_edit_freezes_file_bytes_into_managed_plan(tmp_path):
    import io

    from PIL import Image

    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
    source = tmp_path / "input.png"
    source.write_bytes(image.getvalue())
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("capabilities", capability(
            id="edit", operation="edit", continuation_strategy="explicit_images",
            parameter_schema={"type": "object", "properties": {"count": {"type": "integer"}},
                              "additionalProperties": False},
        ))
    code, result = invoke(workspace, "edit", "--session", "poster", "--prompt-file", str(prompt),
                          "--input", f"file:{source}", "--dry-run")
    assert code == 0
    source.unlink()
    with Container(workspace=str(workspace)) as container:
        plan = container.repositories.get("plans", result["data"]["plan"]["id"],
                                          include_private=True)
        identity = plan["portable_context"]["input_artifact_ids"][0]
        assert container.artifact_store.get(identity).read_bytes() == image.getvalue()
        assert len(plan["input_hashes"]) == 1
        assert container.repositories.list("jobs") == []


def test_edit_accepts_existing_managed_artifact(tmp_path):
    import io

    from PIL import Image

    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="PNG")
    identity = "art_" + "9" * 32
    with Container(workspace=str(workspace)) as container:
        container.artifact_store.stage(identity, image.getvalue())
        container.artifact_store.publish(identity)
        container.repositories.insert("capabilities", capability(
            id="edit", operation="edit", continuation_strategy="explicit_images",
            parameter_schema={"type": "object", "properties": {"count": {"type": "integer"}},
                              "additionalProperties": False},
        ))
    code, result = invoke(workspace, "edit", "--session", "poster", "--prompt-file", str(prompt),
                          "--input", f"artifact:{identity}", "--dry-run")
    assert code == 0
    assert result["data"]["plan"]["portable_context"] == {
        "kind": "explicit_images", "input_artifact_ids": [identity], "mask_artifact_id": None,
    }


def test_invalid_later_input_does_not_publish_earlier_input(tmp_path, png_bytes):
    workspace = tmp_path / "work"
    prompt = seed(workspace, tmp_path)
    source = tmp_path / "input.png"
    source.write_bytes(png_bytes)
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("capabilities", capability(
            id="edit", operation="edit", continuation_strategy="explicit_images",
            parameter_schema={"type": "object", "properties": {"count": {"const": 1}},
                              "additionalProperties": False},
        ))
    code, result = invoke(
        workspace, "edit", "--session", "poster", "--prompt-file", str(prompt),
        "--input", f"file:{source}", "--input", f"file:{tmp_path / 'missing.png'}", "--dry-run",
    )
    assert code == 2
    assert result["status"] == "validation_error"
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("artifacts") == []
        assert container.repositories.list("plans") == []

