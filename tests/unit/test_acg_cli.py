"""The optional ACG model install is reachable through the CLI registry."""

from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import discover


def test_superres_install_is_discoverable_and_accepts_a_model():
    command = discover("superres install")
    schema = command["input_schema"]
    assert schema["properties"]["model"]["type"] == "string"
    assert schema["properties"]["backend"]["default"] == "cpu"
    assert schema["properties"]["accept"]["default"] is False
    assert schema["required"] == ["model"]
    assert "superres install" in {row["name"] for row in command["commands"]}


def test_superres_install_parses_a_positional_model():
    command, args, _ = _parse(["superres", "install", "realesr-animevideov3", "--accept"])
    assert command == "superres install"
    assert args["model"] == "realesr-animevideov3"
    assert args["accept"] is True


def test_superres_install_requires_explicit_accept():
    command, args, _ = _parse(["superres", "install", "realesr-animevideov3"])
    assert command == "superres install"
    assert args.get("accept") is not True
