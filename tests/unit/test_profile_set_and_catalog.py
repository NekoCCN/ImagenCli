"""profile set and model catalog: create/update, parsing, trust boundary."""

from pathlib import Path

import httpx
import pytest

from imagen_cli.application import model_catalog_service
from imagen_cli.application.container import Container
from imagen_cli.storage.database import connect
from imagen_cli.storage.repositories import Repositories


def set_profile(
    workspace: Path, model: str = "m1", revision: int | None = None,
    base_url: str = "https://gateway.example/v1",
):
    with Container(workspace=str(workspace)) as container:
        return container.execute("profile set", {
            "profile": "demo", "provider": "openai", "family": "images",
            "base_url": base_url, "model_default": model,
            "credential_ref": "env:DEMO_KEY", "revision": revision or 1,
        })


def test_profile_set_creates_then_bumps_revision(tmp_path):
    created = set_profile(tmp_path)
    assert created[0] == "succeeded"
    assert created[1]["profile"]["revision"] == 1
    updated = set_profile(tmp_path, "m2", revision=1)
    assert updated[0] == "succeeded"
    assert updated[1]["profile"]["revision"] == 2
    stale = set_profile(tmp_path, "m3", revision=1)
    assert stale[0] == "conflict"


def test_models_use_declares_and_selects_the_default(tmp_path):
    set_profile(tmp_path, model="m1")
    with Container(workspace=str(tmp_path)) as container:
        status, data = container.execute(
            "models use", {"profile": "demo", "id": "gpt-image-2.5"}
        )
        assert status == "succeeded"
        assert data["profile"]["model_default"] == "gpt-image-2.5"
        assert data["profile"]["revision"] == 2
        declared = container.repositories.get("provider_models", "demo:gpt-image-2.5")
        assert declared["evidence"] == "manual"
        again = container.execute(
            "models use", {"profile": "demo", "id": "gpt-image-2.5"}
        )
        assert again[1]["profile"]["revision"] == 2


@pytest.fixture
def repositories(tmp_path):
    connection = connect(tmp_path / "state.sqlite3")
    repos = Repositories(connection)
    repos.insert("profiles", {
        "id": "p1", "name": "p1", "provider": "openai", "family": "images",
        "base_url": "https://gateway.example/v1", "model_default": "m1",
        "credential_ref": "env:KEY", "trust_policy": {}, "revision": 1,
    })
    yield repos
    connection.close()


def test_refresh_parses_openai_and_never_writes_capabilities(repositories):
    def handler(request):
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": [{"id": "gpt-image-2.5"}]})

    profile = repositories.get("profiles", "p1", include_private=True)
    before = len(repositories.list("capabilities", limit=1000))
    data = model_catalog_service.refresh(
        repositories, profile,
        resolve_credential=lambda _: "secret", send_credential=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert [row["model_id"] for row in data["models"]] == ["gpt-image-2.5"]
    assert len(repositories.list("capabilities", limit=1000)) == before
    assert repositories.list("provider_models", limit=10)[0]["evidence"] == "discovered"


def test_refresh_parses_gemini_names(repositories):
    profile = repositories.get("profiles", "p1", include_private=True)
    profile = {**profile, "family": "generate_content"}

    def handler(request):
        assert request.headers["x-goog-api-key"] == "secret"
        return httpx.Response(200, json={"models": [{"name": "models/gemini-3-pro-image-preview"}]})

    data = model_catalog_service.refresh(
        repositories, profile,
        resolve_credential=lambda _: "secret", send_credential=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert data["models"][0]["model_id"] == "gemini-3-pro-image-preview"


def test_non_loopback_http_is_refused(repositories):
    profile = {**repositories.get("profiles", "p1", include_private=True),
               "base_url": "http://gateway.example/v1"}
    with pytest.raises(ValueError, match="https or a loopback"):
        model_catalog_service.refresh(repositories, profile)


def test_auth_failure_is_reported(repositories):
    profile = repositories.get("profiles", "p1", include_private=True)
    with pytest.raises(ValueError, match="authentication"):
        model_catalog_service.refresh(
            repositories, profile,
            client=httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(401)
            )),
        )


def test_endpoint_change_invalidates_declared_models(tmp_path):
    set_profile(tmp_path)
    connection = connect(tmp_path / "state.sqlite3")
    repositories = Repositories(connection)
    repositories.insert("provider_models", {
        "id": "demo:gpt-image-2.5", "profile_id": "demo", "model_id": "gpt-image-2.5",
        "display_name": "gpt-image-2.5", "fetched_at": "2026-10-24T00:00:00Z",
        "source_url": "https://gateway.example/v1/models", "evidence": "discovered",
    })
    connection.close()
    same_host = set_profile(tmp_path, "m2", revision=1)
    assert same_host[0] == "succeeded"
    connection = connect(tmp_path / "state.sqlite3")
    assert Repositories(connection).list("provider_models", limit=10)
    connection.close()
    moved = set_profile(tmp_path, "m3", revision=2, base_url="https://other.example/v1")
    assert moved[0] == "succeeded"
    connection = connect(tmp_path / "state.sqlite3")
    assert Repositories(connection).list("provider_models", limit=10) == []
    connection.close()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "timed out"),
        (lambda request: httpx.Response(429), "rate limited"),
        (lambda request: httpx.Response(503), "endpoint failed"),
        (lambda request: httpx.Response(404), "not found"),
    ],
)
def test_catalog_failures_are_mapped(repositories, failure, message):
    profile = repositories.get("profiles", "p1", include_private=True)
    with pytest.raises(ValueError, match=message):
        model_catalog_service.refresh(
            repositories, profile,
            client=httpx.Client(transport=httpx.MockTransport(failure)),
        )
