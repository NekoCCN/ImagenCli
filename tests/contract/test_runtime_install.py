"""One public command installs the runtime and any selected weights, preview or accept."""

import pytest
from jsonschema import ValidationError

from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import COMMANDS, discover
from imagen_cli.cli.tui.app import command_fields


def test_runtime_install_is_folded_into_superres_install():
    assert "runtime install" not in COMMANDS
    assert "superres install" in COMMANDS


def test_superres_install_previews_runtime_only_without_a_model():
    command, args, _ = _parse(["superres", "install", "--backend", "cpu"])
    assert command == "superres install"
    assert args == {"backend": "cpu"}


def test_superres_install_accepts_model_backend_and_accept():
    command, args, _ = _parse(
        ["superres", "install", "realesr-animevideov3", "--backend", "cuda", "--accept"]
    )
    assert command == "superres install"
    assert args == {"model": "realesr-animevideov3", "backend": "cuda", "accept": True}


def test_superres_install_tui_exposes_model_backend_and_accept():
    schema = discover("superres install")["input_schema"]
    assert schema["properties"]["backend"]["enum"] == ["cpu", "cuda", "openvino"]
    assert "model" not in schema.get("required", ())
    fields, _ = command_fields("superres install")
    assert {field[0] for field in fields} == {"model", "backend", "accept"}


def test_superres_install_rejects_an_arbitrary_backend():
    with pytest.raises(ValidationError):
        _parse(["superres", "install", "--backend", "untrusted-package"])
