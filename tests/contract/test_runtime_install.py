"""The public CLI exposes installation as a preview or explicit accepted mutation."""

import pytest
from jsonschema import ValidationError

from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import discover
from imagen_cli.cli.tui.app import command_fields


def test_runtime_install_cli_defaults_to_preview():
    command, args, _ = _parse(["runtime", "install", "--backend", "cpu"])
    assert command == "runtime install"
    assert args == {"backend": "cpu"}


def test_runtime_install_accept_is_explicit():
    command, args, _ = _parse(["runtime", "install", "--backend", "cuda", "--accept"])
    assert command == "runtime install"
    assert args == {"backend": "cuda", "accept": True}


def test_runtime_install_tui_has_backend_choice():
    schema = discover("runtime install")["input_schema"]
    assert schema["properties"]["backend"]["enum"] == ["cpu", "cuda", "openvino"]
    fields, _ = command_fields("runtime install")
    assert {field[0] for field in fields} == {"backend", "accept"}


def test_runtime_install_rejects_arbitrary_package():
    with pytest.raises(ValidationError):
        _parse(["runtime", "install", "--backend", "untrusted-package"])
