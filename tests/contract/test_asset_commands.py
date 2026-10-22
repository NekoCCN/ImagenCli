"""Opening an artifact hands the managed path to the platform opener."""


from imagen_cli.application.container import Container
from imagen_cli.cli.entrypoint import _parse


def test_asset_open_parses_a_positional_id_and_folder_flag():
    identity = "art_" + "1" * 32
    command, args, _ = _parse(["asset", "open", identity, "--folder"])
    assert command == "asset open"
    assert args == {"artifact_id": identity, "folder": True}


def test_asset_open_uses_the_platform_opener(tmp_path, png_bytes, monkeypatch):
    calls = []

    def fake_open(path, *, folder):
        calls.append((path.name, folder))

    monkeypatch.setattr("imagen_cli.application.opener.open_path", fake_open)
    identity = "art_" + "1" * 32
    with Container(workspace=str(tmp_path / "ws")) as container:
        container.artifact_store.stage(identity, png_bytes)
        container.artifact_store.publish(identity)
        status, data = container.execute("asset open", {"artifact_id": identity})
        assert status == "succeeded"
        assert data["opened"] == "image"
        status, data = container.execute(
            "asset open", {"artifact_id": identity, "folder": True}
        )
        assert status == "succeeded"
        assert data["opened"] == "folder"
        missing = container.execute("asset open", {"artifact_id": "art_" + "2" * 32})
        assert missing[0] == "not_found"
    assert [folder for _, folder in calls] == [False, True]
