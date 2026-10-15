"""Local T039 session handlers, intentionally independent of CLI wiring."""

from pathlib import Path

import pytest

from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.commands import session
from imagen_cli.storage.artifacts import ArtifactStore
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


@pytest.fixture
def service(tmp_path: Path):
    connection = connect(tmp_path / "state.sqlite3")
    repositories = Repositories(connection)
    yield SessionService(repositories, ArtifactStore(tmp_path / "artifacts", repositories))
    connection.close()


def test_create_show_resume_by_name_without_active_session(service):
    status, data = session.create(service, name="海报", intent="测试", model="explicit")
    assert status == "succeeded"
    identity = data["session"]["id"]
    assert session.show(service, "海报")[1]["session"]["id"] == identity
    resumed = session.resume(service, identity)
    assert resumed[0] == "succeeded"
    assert resumed[1]["turns"] == []
    assert resumed[1]["session"]["current_turn_id"] is None
    assert service.repositories.list("jobs") == []


def test_list_pagination_and_deleted_session_exclusion(service):
    first = session.create(service, name="first")[1]["session"]
    session.create(service, name="second")
    status, page = session.list_(service, limit=1)
    assert status == "succeeded"
    assert [row["name"] for row in page["sessions"]] == ["first"]
    assert page["next_cursor"] is not None
    second = session.list_(service, limit=1, cursor=page["next_cursor"])[1]
    assert [row["name"] for row in second["sessions"]] == ["second"]
    assert second["next_cursor"] is None
    service.repositories.update("sessions", first["id"], {"deleted_at": "2026-10-23T00:00:00Z"})
    assert session.show(service, first["id"])[0] == "not_found"
    assert [row["name"] for row in session.list_(service)[1]["sessions"]] == ["second"]


def test_duplicate_name_and_missing_session(service):
    session.create(service, name="same")
    assert session.create(service, name="same")[0] == "conflict"
    assert session.resume(service, "missing")[0] == "not_found"


@pytest.mark.parametrize("values", [{"limit": True}, {"limit": 1001}, {"cursor": "bad!"}])
def test_invalid_pagination_is_rejected(service, values):
    with pytest.raises(ValueError):
        session.list_(service, **values)
