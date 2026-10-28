"""A plan this build accepts must be a request the selected route can send."""

import pytest

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from tests.integration.test_local_cli import invoke

OPENAI = ("openai", "images", "gpt-image-2.5-sunburst", "https://api.openai.com/v1")
GEMINI = (
    "gemini", "generate_content", "gemini-3.1-flash-image",
    "https://generativelanguage.googleapis.com/v1beta",
)


def seed(tmp_path, route):
    workspace = tmp_path / "work"
    provider, family, model, base_url = route
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("profiles", {
            "id": "local", "name": "local", "provider": provider, "family": family,
            "base_url": base_url, "model_default": model,
            "credential_ref": "env:IMAGEN_KEY", "trust_policy": {}, "revision": 1,
        })
        SessionService(container.repositories, container.artifact_store).create_session(
            "poster", default_profile_id="local", default_model=model,
        )
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("蓝天", encoding="utf-8")
    return workspace, prompt


def plan(tmp_path, route, *flags):
    workspace, prompt = seed(tmp_path, route)
    code, result = invoke(
        workspace, "generate", "--session", "poster", "--prompt-file", str(prompt), *flags
    )
    return workspace, code, result


@pytest.mark.parametrize("route,flags,expected", [
    (OPENAI, ["--size", "1536x864"], {"size": "1536x864", "count": 1}),
    (OPENAI, ["--size", "auto"], {"size": "auto", "count": 1}),
    (GEMINI, ["--aspect-ratio", "16:9", "--image-size", "2K"],
     {"aspect_ratio": "16:9", "image_size": "2K", "count": 1}),
    (GEMINI, ["--image-size", "4K"], {"image_size": "4K", "count": 1}),
])
def test_route_parameters_reach_the_plan(tmp_path, route, flags, expected):
    _, code, result = plan(tmp_path, route, *flags)
    assert code == 6, result
    assert result["data"]["plan"]["normalized_parameters"] == expected


@pytest.mark.parametrize("route,flags,hint", [
    (OPENAI, ["--size", "1920x1081"], "16"),
    (OPENAI, ["--size", "4096x1024"], "1:3"),
    (OPENAI, ["--image-size", "2K"], "size"),
    (OPENAI, ["--aspect-ratio", "16:9"], "size"),
    (GEMINI, ["--size", "1024x1024"], "aspect_ratio"),
    (GEMINI, ["--image-size", "2k"], "1K"),
    (GEMINI, ["--aspect-ratio", "5:3"], "16:9"),
    (GEMINI, ["--count", "2"], "count"),
])
def test_values_the_route_cannot_send_are_refused_before_a_plan_exists(
    tmp_path, route, flags, hint,
):
    workspace, code, result = plan(tmp_path, route, *flags)
    assert code == 2, result
    assert result["status"] == "validation_error"
    assert hint in result["error"]["message"]
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("plans") == []
        assert container.repositories.list("jobs") == []


def test_a_shut_down_model_is_refused_at_plan_time(tmp_path):
    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("profiles", {
            "id": "local", "name": "local", "provider": "openai", "family": "images",
            "base_url": "https://api.openai.com/v1", "model_default": "gpt-image-1",
            "credential_ref": "env:IMAGEN_KEY", "trust_policy": {}, "revision": 1,
        })
        SessionService(container.repositories, container.artifact_store).create_session(
            "poster", default_profile_id="local", default_model="gpt-image-1",
        )
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("蓝天", encoding="utf-8")
    code, result = invoke(
        workspace, "generate", "--session", "poster", "--prompt-file", str(prompt)
    )
    assert code == 2, result
    assert "gpt-image-1" in result["error"]["message"]


def test_repository_has_no_plan_after_refusal(tmp_path):
    workspace, code, _ = plan(tmp_path, GEMINI, "--size", "1024x1024")
    assert code == 2
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("plans") == []


def test_a_shut_down_gemini_model_is_refused_at_plan_time(tmp_path):
    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("profiles", {
            "id": "local", "name": "local", "provider": "gemini",
            "family": "generate_content",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model_default": "gemini-3-pro-image-preview",
            "credential_ref": "env:IMAGEN_KEY", "trust_policy": {}, "revision": 1,
        })
        SessionService(container.repositories, container.artifact_store).create_session(
            "poster", default_profile_id="local", default_model="gemini-3-pro-image-preview",
        )
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("蓝天", encoding="utf-8")
    code, result = invoke(
        workspace, "generate", "--session", "poster", "--prompt-file", str(prompt)
    )
    assert code == 2, result
    assert "gemini-3-pro-image-preview" in result["error"]["message"]


def test_a_route_without_an_executor_is_refused_before_approval(tmp_path):
    workspace = tmp_path / "work"
    with Container(workspace=str(workspace)) as container:
        container.repositories.insert("profiles", {
            "id": "local", "name": "local", "provider": "stability", "family": "images",
            "base_url": "https://api.stability.ai", "model_default": "core",
            "credential_ref": "env:IMAGEN_KEY", "trust_policy": {}, "revision": 1,
        })
        SessionService(container.repositories, container.artifact_store).create_session(
            "poster", default_profile_id="local", default_model="core",
        )
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("蓝天", encoding="utf-8")
    code, result = invoke(
        workspace, "generate", "--session", "poster", "--prompt-file", str(prompt)
    )
    assert code == 2, result
    assert result["status"] == "unsupported_capability"
    assert "stability" in result["error"]["message"]
    with Container(workspace=str(workspace)) as container:
        assert container.repositories.list("plans") == []
