import io
import json

from imagen_cli.cli.entrypoint import main


def test_targets_and_uninstall_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for command in ("install", "uninstall"):
        out = io.StringIO()
        code = main(
            [
                "skills",
                command,
                "--target",
                "codex",
                "--target",
                "cline",
                "--skill",
                "imagen",
                "--agent",
                "--no-interactive",
            ],
            stdout=out,
            stderr=io.StringIO(),
        )
        payload = json.loads(out.getvalue())
        assert code == 0, payload
        assert payload["status"] == "succeeded"
    assert not (tmp_path / ".agents/skills/imagen").exists()
