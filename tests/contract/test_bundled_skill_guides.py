
from imagen_cli.application import skills_service
from imagen_cli.cli.entrypoint import _parse


def test_provider_guide_examples_match_cli_and_keep_openai_primary():
    guide = (skills_service.BUNDLED / "imagen" / "references" / "workflows.md").read_text()
    assert guide.index("--provider openai") < guide.index("--provider gemini")
    assert "--models-path" in guide
    assert "--send-credential" in guide
    for provider, family in (("openai", "images"), ("gemini", "generate_content")):
        command, args, _ = _parse([
            "profile", "set", "demo", "--provider", provider, "--family", family,
            "--base-url", "https://gateway.example/v1beta",
            "--models-path", "/v1/models", "--model-default", "image-model",
            "--credential-ref", "env:IMAGEN_API_KEY",
        ])
        assert command == "profile set"
        assert args["models_path"] == "/v1/models"


def test_bundled_guides_are_english_with_user_language_guidance():
    for path in skills_service.BUNDLED.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not any("一" <= char <= "鿿" for char in text), str(path)
        if path.name == "SKILL.md":
            assert "user's language" in text, str(path)


def test_installed_guide_has_workflow_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert skills_service.install_(["imagen"])[0] == "succeeded"
    root = tmp_path / ".agents/skills/imagen"
    text = (root / "SKILL.md").read_text()
    assert "references/workflows.md" in text
    guide = (root / "references/workflows.md").read_text()
    assert "experimental" in guide
    assert "--accept" in guide
    assert "null" in guide
    for args in (
        ["superres", "models", "--agent"],
        ["superres", "fetch", "--model", "realesrgan-x4plus", "--accept", "--agent"],
        ["skills", "install", "--target", "codex", "--agent"],
    ):
        _parse(args)
