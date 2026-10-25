import io
import json

from imagen_cli.cli.entrypoint import main


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_interactive_skills_and_model_cancel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", Terminal("\n"))
    out, err = Terminal(), io.StringIO()
    assert main(["skills", "install"], stdout=out, stderr=err) != 0
    assert not (tmp_path / ".claude").exists()
    monkeypatch.setattr("sys.stdin", Terminal("\n"))
    out, err = Terminal(), io.StringIO()
    assert main(["superres", "fetch"], stdout=out, stderr=err) != 0
    assert "输入序号" in err.getvalue()


def test_fetch_positional_compatibility_and_noninteractive_missing():
    from imagen_cli.cli.entrypoint import _parse

    command, args, _ = _parse(["superres", "fetch", "realesrgan-x4plus", "--accept"])
    assert command == "superres fetch"
    assert args["model"] == "realesrgan-x4plus"
    out = io.StringIO()
    assert main(["superres", "fetch", "--agent"], stdout=out, stderr=io.StringIO()) == 2
    assert json.loads(out.getvalue())["status"] == "validation_error"
