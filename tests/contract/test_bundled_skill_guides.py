
from imagen_cli.application import skills_service
from imagen_cli.cli.entrypoint import _parse


def test_installed_guide_has_workflow_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert skills_service.install_(["imagen"])[0] == "succeeded"
    root = tmp_path / ".claude/skills/imagen"
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
