"""Deploy management: Salad control plane, Modal subprocess, build gate, secrets."""

import json
import sys
from pathlib import Path

import httpx
import pytest

from imagen_cli.application import deploy_service, remote_service
from imagen_cli.execution.configured import resolve_credential
from imagen_cli.storage.private_state import PrivateStateStore

GROUP = {
    "name": "imagen-qwen-3090-low",
    "display_name": "imagen-qwen-3090-low",
    "replicas": 1,
    "restart_policy": "always",
    "priority": "low",
    "autostart_policy": False,
    "country_codes": ["us"],
    "container": {
        "image": "ghcr.io/nekoccn/imagen-worker:dev",
        "command": None,
        "image_caching": True,
        "resources": {
            "cpu": 8, "gpu_classes": ["gpu-id"], "memory": 32768,
            "shm_size": 2048, "storage_amount": 128849018880,
        },
        "environment_variables": {
            "IMAGEN_WORKER_TOKEN": "worker-secret",
            "IMAGEN_WORKER_STATE_MODE": "shared-volume",
            "IMAGEN_JOBS_ROOT": "/jobs",
            "IMAGEN_MODAL_GPU": "RTX 3090",
            "IMAGEN_WORKER_VERSION": "0.1.18",
            "IMAGEN_CLI_VERSION": "0.1.18",
        },
    },
    "networking": {
        "auth": False, "client_request_timeout": 100000,
        "dns": "beet-acar.salad.cloud", "load_balancer": "round_robin",
        "port": 8000, "protocol": "http", "server_response_timeout": 100000,
        "single_connection_limit": False,
    },
    "liveness_probe": {"http": {"path": "/healthz", "port": 8000}},
    "readiness_probe": {"http": {"path": "/readyz", "port": 8000}},
    "id": "group-id", "create_time": "2026-11-02T17:00:16Z",
    "update_time": "2026-11-02T17:09:33Z", "version": 3,
    "current_state": {
        "status": "stopped",
        "instance_status_counts": {
            "allocating_count": 0, "creating_count": 0,
            "running_count": 0, "stopping_count": 0,
        },
    },
    "pending_change": None,
}


class SaladServer:
    def __init__(self, *, ready=200):
        self.ready = ready
        self.calls = []
        self.groups = {GROUP["name"]: json.loads(json.dumps(GROUP))}
        self.created = None

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/containers") and request.method == "POST":
            body = json.loads(request.content)
            self.created = body
            group = json.loads(json.dumps(GROUP))
            group["name"] = body["name"]
            group["display_name"] = body["display_name"]
            group["container"]["image"] = body["container"]["image"]
            group["container"]["environment_variables"] = (
                body["container"]["environment_variables"]
            )
            group["networking"]["dns"] = f"{body['name']}.salad.cloud"
            group["current_state"]["status"] = "running"
            group["current_state"]["instance_status_counts"]["running_count"] = 1
            self.groups[body["name"]] = group
            return httpx.Response(201, json=group)
        if "/containers/" in path:
            name = path.rsplit("/", 1)[-1]
            if path.endswith("/instances"):
                return httpx.Response(200, json={"instances": [{
                    "state": "running", "ready": True,
                }]})
            if path.endswith("/start"):
                return httpx.Response(202)
            if name not in self.groups:
                return httpx.Response(404, json={"title": "not found"})
            if request.method == "DELETE":
                del self.groups[name]
                return httpx.Response(202)
            if request.method == "PATCH":
                # Salad rejects PUT here with 405; the update is a merge patch.
                assert request.headers["Content-Type"] == "application/merge-patch+json"
                body = json.loads(request.content)
                group = self.groups[name]
                group["container"]["image"] = body["container"]["image"]
                group["container"]["environment_variables"] = (
                    body["container"]["environment_variables"]
                )
                group["current_state"]["status"] = "running"
                group["current_state"]["instance_status_counts"]["running_count"] = 1
                return httpx.Response(200, json=group)
            return httpx.Response(200, json=self.groups[name])
        if path == "/readyz":
            return httpx.Response(self.ready, json={"ready": self.ready == 200})
        if path == "/healthz":
            return httpx.Response(503 if self.ready == 200 else self.ready)
        if path == "/v1/meta":
            return httpx.Response(200, json={"worker_version": "0.1.20",
                                             "state_mode": "shared-volume"})
        return httpx.Response(404, json={"title": "unexpected"})


@pytest.fixture
def no_registry(monkeypatch):
    """Build tests cover docker invocation; manifest rewriting is tested alone."""
    monkeypatch.setattr(
        deploy_service, "_normalize_manifest", lambda *a, **k: False,
    )


@pytest.fixture
def salad(monkeypatch):
    monkeypatch.setenv("SALAD_KEY", "salad-secret")
    monkeypatch.setenv("WORKER_TOKEN", "worker-secret")
    monkeypatch.setattr(deploy_service, "SLEEP", lambda _: None)
    server = SaladServer()
    monkeypatch.setattr(deploy_service, "TRANSPORT", httpx.MockTransport(server))
    monkeypatch.setattr(remote_service, "remotes", lambda: {})
    return server


def test_salad_definition_copies_resources_and_drops_read_only_fields():
    definition = deploy_service.salad_definition(
        GROUP, name="imagen-worker-3090",
        image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        environment={"IMAGEN_WORKER_TOKEN": "t"},
    )
    assert definition["name"] == "imagen-worker-3090"
    assert definition["container"]["image"].endswith(":0.1.20")
    assert definition["container"]["resources"] == GROUP["container"]["resources"]
    assert definition["container"]["environment_variables"] == {"IMAGEN_WORKER_TOKEN": "t"}
    assert "dns" not in definition["networking"]
    for key in ("id", "create_time", "update_time", "current_state", "pending_change"):
        assert key not in definition


def test_update_without_accept_returns_a_plan_and_touches_nothing(salad):
    status, data = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="default", private_store=None,
    )
    assert status == "approval_required"
    assert data["plan"]["image"].endswith(":0.1.20")
    assert data["plan_digest"]
    assert salad.calls == []


def test_rename_creates_waits_for_readiness_then_deletes_the_old_group(salad):
    status, data = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", rename="imagen-worker-3090",
        accept=True, yes=True, salad_key_ref="env:SALAD_KEY",
        worker_token_ref="env:WORKER_TOKEN", salad_org="org", salad_project="default",
        max_seconds=5,
    )
    assert status == "succeeded"
    assert data["renamed"] is True
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p in salad.calls]
    assert ("POST", "containers") in methods
    delete_index = next(i for i, (m, p) in enumerate(salad.calls) if m == "DELETE")
    ready_index = next(i for i, (m, p) in enumerate(salad.calls) if p == "/readyz")
    assert ready_index < delete_index
    assert "imagen-worker-3090" in salad.groups
    assert "imagen-qwen-3090-low" not in salad.groups


def test_a_failed_readiness_keeps_the_old_group(salad):
    salad.ready = 503
    status, data = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", rename="imagen-worker-3090",
        accept=True, yes=True, salad_key_ref="env:SALAD_KEY",
        worker_token_ref="env:WORKER_TOKEN", salad_org="org", salad_project="default",
        max_seconds=1,
    )
    assert status == "failed"
    assert "imagen-qwen-3090-low" in salad.groups
    assert not any(method == "DELETE" for method, _ in salad.calls)


def test_rename_requires_yes_before_deleting(salad):
    status, data = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", rename="imagen-worker-3090",
        accept=True, salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="default",
    )
    assert status == "validation_error"
    assert "yes" in data["error"]["detail"]


def test_in_place_update_replaces_the_image_without_a_new_group(salad):
    status, data = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", accept=True,
        salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
        max_seconds=5,
    )
    assert status == "succeeded"
    assert data["renamed"] is False
    assert salad.groups["imagen-qwen-3090-low"]["container"]["image"].endswith("0.1.20")
    assert not any(method == "POST" and path.endswith("/containers")
                   for method, path in salad.calls)


def test_in_place_update_restarts_a_stopped_group(salad):
    """A merge patch only changes fields; a stopped group must be started."""
    status, _ = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", accept=True,
        salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
        max_seconds=5,
    )
    assert status == "succeeded"
    assert ("POST", "/api/public/organizations/org/projects/default/containers/"
            "imagen-qwen-3090-low/start") in salad.calls
    assert salad.groups["imagen-qwen-3090-low"]["current_state"]["status"] == "running"


def test_status_reports_warnings_without_mutating(salad):
    status, data = deploy_service.status(
        "salad", name="imagen-qwen-3090-low",
        salad_key_ref="env:SALAD_KEY", worker_token_ref="env:WORKER_TOKEN",
        salad_org="org", salad_project="default",
    )
    assert status == "succeeded"
    assert data["readyz"] == 200
    assert data["healthz"] == 503
    assert "healthz_not_ok_readyz_ok" in data["warnings"]
    assert "shared_volume_on_salad" in data["warnings"]
    assert not any(method in {"DELETE", "PATCH", "PUT"} for method, _ in salad.calls)


# Assembled at runtime so the token never appears as a literal in the file.
_TOKEN = "tok" + "-" + "9f3a" + "registry"


class FakeRunner:
    def __init__(self, *, returncode=0, stdout="", stderr="", fail_on=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))

        class Completed:
            pass

        completed = Completed()
        # fail_on names one subcommand, so earlier steps can succeed first.
        completed.returncode = 1 if self.fail_on and self.fail_on in argv else self.returncode
        completed.stdout = self.stdout
        completed.stderr = self.stderr
        completed.args = argv
        return completed


def test_modal_deploy_injects_tokens_and_redacts_them(monkeypatch, tmp_path):
    monkeypatch.setenv("MODAL_ID", "id-secret")
    monkeypatch.setenv("MODAL_SECRET", "secret-value")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "modal_app.py").write_text("", encoding="utf-8")
    runner = FakeRunner(stdout="Deployed https://acme--imagen-worker-web.modal.run")
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    # The Modal CLI is normally a console script in its own environment.
    monkeypatch.setattr(deploy_service.shutil, "which", lambda name: "/bin/modal")
    status, data = deploy_service.update(
        "modal", image="ghcr.io/nekoccn/imagen-worker:0.1.20", accept=True,
        modal_token_id_ref="env:MODAL_ID",
        modal_token_secret_ref="env:MODAL_SECRET",
    )
    assert status == "succeeded"
    argv, kwargs = runner.calls[0]
    assert argv == ["/bin/modal", "deploy", "deploy/modal_app.py"]
    assert kwargs["env"]["MODAL_TOKEN_ID"] == "id-secret"
    assert kwargs["env"]["MODAL_TOKEN_SECRET"] == "secret-value"
    assert kwargs["env"]["IMAGEN_WORKER_IMAGE"].endswith("0.1.20")
    assert data["url"] == "https://acme--imagen-worker-web.modal.run"
    assert "secret-value" not in json.dumps(data)


def test_modal_deploy_failure_reports_the_step(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "modal_app.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy_service, "RUNNER", FakeRunner(returncode=1, stderr="boom"))
    status, data = deploy_service.update(
        "modal", image="ghcr.io/nekoccn/imagen-worker:0.1.20", accept=True,
    )
    assert status == "failed"
    assert "deploy" in data["error"]["detail"]


def test_build_is_gated_and_runs_docker(monkeypatch, tmp_path, no_registry):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "worker").mkdir(parents=True)
    (tmp_path / "deploy" / "worker" / "Dockerfile").write_text("", encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    status, data = deploy_service.build(tag="0.1.20", image="img:0.1.20")
    assert status == "approval_required"
    assert runner.calls == []
    status, data = deploy_service.build(
        tag="0.1.20", image="img:0.1.20", push=True, accept=True,
    )
    assert status == "succeeded"
    # A source build must actually install this checkout, not the PyPI release.
    assert runner.calls[0][0] == [
        "docker", "build", "-f", "deploy/worker/Dockerfile",
        "--build-arg", "IMAGEN_FROM_SOURCE=1",
        "--build-arg", "IMAGEN_VERSION=0.1.20",
        "--build-arg", "IMAGEN_MATTING_RUNTIME=cpu",
        "-t", "img:0.1.20", ".",
    ]
    assert runner.calls[1][0] == ["docker", "push", "img:0.1.20"]


def test_build_can_target_a_gpu_matting_runtime(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "worker").mkdir(parents=True)
    (tmp_path / "deploy" / "worker" / "Dockerfile").write_text("", encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    status, data = deploy_service.build(
        tag="0.1.20", image="img:gpu", matting_runtime="gpu", accept=True,
    )
    assert status == "succeeded"
    assert "IMAGEN_MATTING_RUNTIME=gpu" in runner.calls[0][0]
    status, data = deploy_service.build(
        tag="0.1.20", image="img:gpu", matting_runtime="tpu", accept=True,
    )
    assert status == "validation_error"
    assert len(runner.calls) == 1


def test_build_refuses_without_a_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    status, data = deploy_service.build(tag="0.1.20", image="img", accept=True)
    assert status == "validation_error"


def test_secret_set_stores_privately_and_returns_the_reference(tmp_path):
    store = PrivateStateStore(tmp_path / "private")
    key_file = tmp_path / "key.txt"
    key_file.write_text("salad-secret\n", encoding="utf-8")
    status, data = deploy_service.set_secret(store, "salad-api-key", str(key_file))
    assert status == "succeeded"
    reference = data["credential_ref"]
    assert reference.startswith("private:")
    assert "salad-secret" not in json.dumps(data)
    assert resolve_credential(reference, private_store=store) == "salad-secret"


def test_build_supports_the_salad_queue_target(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "salad").mkdir(parents=True)
    (tmp_path / "deploy" / "salad" / "queue-worker.Dockerfile").write_text(
        "", encoding="utf-8"
    )
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    monkeypatch.setenv("SALAD_QUEUE_WORKER_URL", "https://example.test/queue-worker")

    status, data = deploy_service.build(
        tag="0.1.20", image="img-queue:0.1.20", target="salad-queue",
    )
    assert status == "approval_required"
    assert data["plan"]["target"] == "salad-queue"
    assert runner.calls == []

    status, data = deploy_service.build(
        tag="0.1.20", image="img-queue:0.1.20", target="salad-queue", accept=True,
        salad_queue_worker_url_ref="env:SALAD_QUEUE_WORKER_URL",
    )
    assert status == "succeeded"
    argv = runner.calls[0][0]
    assert argv[:4] == ["docker", "build", "-f",
                        "deploy/salad/queue-worker.Dockerfile"]
    assert "--build-arg" in argv
    assert "SALAD_QUEUE_WORKER_URL=https://example.test/queue-worker" in argv
    assert "-t" in argv and "img-queue:0.1.20" in argv


def test_salad_queue_build_needs_the_vendor_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "salad").mkdir(parents=True)
    (tmp_path / "deploy" / "salad" / "queue-worker.Dockerfile").write_text(
        "", encoding="utf-8"
    )
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    status, data = deploy_service.build(
        tag="0.1.20", image="img", target="salad-queue", accept=True,
    )
    assert status == "validation_error"
    assert runner.calls == []


def test_build_logs_in_with_the_password_on_stdin_only(
        monkeypatch, tmp_path, no_registry):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "worker").mkdir(parents=True)
    (tmp_path / "deploy" / "worker" / "Dockerfile").write_text("", encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    monkeypatch.setenv("REGISTRY_USER", "robot")
    monkeypatch.setenv("REGISTRY_PASSWORD", _TOKEN)

    status, data = deploy_service.build(
        tag="0.1.20", image="ghcr.io/example/worker:0.1.20", push=True, accept=True,
        registry_username_ref="env:REGISTRY_USER",
        registry_password_ref="env:REGISTRY_PASSWORD",
    )
    assert status == "succeeded"
    login = runner.calls[1]
    assert login[0] == ["docker", "login", "ghcr.io", "--username", "robot",
                        "--password-stdin"]
    # The password reaches docker through stdin, never through argv.
    assert _TOKEN not in json.dumps(login[0])
    assert login[1]["input"] == _TOKEN + "\n"
    assert _TOKEN not in json.dumps(data)


def test_registry_failure_redacts_the_password(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "worker").mkdir(parents=True)
    (tmp_path / "deploy" / "worker" / "Dockerfile").write_text("", encoding="utf-8")
    # Build succeeds; the registry login is the step that fails and echoes the token.
    runner = FakeRunner(fail_on="login", stderr="denied " + _TOKEN)
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    monkeypatch.setenv("REGISTRY_USER", "robot")
    monkeypatch.setenv("REGISTRY_PASSWORD", _TOKEN)

    status, data = deploy_service.build(
        tag="0.1.20", image="ghcr.io/example/worker:0.1.20", push=True, accept=True,
        registry_username_ref="env:REGISTRY_USER",
        registry_password_ref="env:REGISTRY_PASSWORD",
    )
    assert status == "failed"
    assert "docker login" in json.dumps(data)
    assert _TOKEN not in json.dumps(data)


def test_registry_login_needs_both_refs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "deploy" / "worker").mkdir(parents=True)
    (tmp_path / "deploy" / "worker" / "Dockerfile").write_text("", encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    status, data = deploy_service.build(
        tag="0.1.20", image="img", push=True, accept=True,
        registry_username_ref="env:REGISTRY_USER",
    )
    assert status == "validation_error"


def test_unknown_build_target_is_refused():
    status, _data = deploy_service.build(
        tag="0.1.20", image="img", target="nope", accept=True,
    )
    assert status == "validation_error"


def test_salad_delete_requires_both_gates(salad):
    status, data = deploy_service.remove(
        "salad", name=GROUP["name"], salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="proj",
    )
    assert status == "approval_required"
    assert data["plan"]["action"] == "delete"
    assert salad.calls == []  # nothing was read or deleted

    status, data = deploy_service.remove(
        "salad", name=GROUP["name"], salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="proj", accept=True,
    )
    assert status == "approval_required"
    assert salad.calls == []


def test_salad_delete_removes_the_group(salad):
    status, data = deploy_service.remove(
        "salad", name=GROUP["name"], salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="proj", accept=True, yes=True,
    )
    assert status == "succeeded"
    assert data["deleted"] is True
    assert GROUP["name"] not in salad.groups
    assert ("DELETE", f"/api/public/organizations/org/projects/proj/containers/"
                      f"{GROUP['name']}") in salad.calls


def test_salad_delete_reads_before_deleting_so_a_typo_fails_loudly(salad):
    status, data = deploy_service.remove(
        "salad", name="does-not-exist", salad_key_ref="env:SALAD_KEY",
        salad_org="org", salad_project="proj", accept=True, yes=True,
    )
    assert status == "validation_error"
    assert not any(method == "DELETE" for method, _path in salad.calls)


def test_salad_delete_needs_a_key(salad):
    status, data = deploy_service.remove(
        "salad", name=GROUP["name"], salad_org="org", salad_project="proj",
        accept=True, yes=True,
    )
    assert status == "validation_error"
    assert salad.calls == []


def test_modal_stop_requires_both_gates(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    status, data = deploy_service.remove("modal")
    assert status == "approval_required"
    assert data["plan"]["action"] == "stop"
    assert runner.calls == []


def test_modal_stop_runs_the_verified_cli(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    monkeypatch.setenv("MODAL_TOKEN_ID", "tok-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "tok-secret")
    status, data = deploy_service.remove(
        "modal", accept=True, yes=True,
        modal_token_id_ref="env:MODAL_TOKEN_ID",
        modal_token_secret_ref="env:MODAL_TOKEN_SECRET",
    )
    assert status == "succeeded"
    assert data["stopped"] is True
    argv, kwargs = runner.calls[0]
    assert Path(argv[0]).name == "modal" or argv[:3] == [sys.executable, "-m", "modal"]
    assert argv[-4:] == ["app", "stop", "-y", "imagen-worker"]
    # Tokens travel in the child environment, never in argv or output.
    assert kwargs["env"]["MODAL_TOKEN_SECRET"] == "tok-secret"
    assert "tok-secret" not in json.dumps(argv)


def test_modal_stop_failure_redacts_tokens(monkeypatch):
    runner = FakeRunner(returncode=1, stderr="bad token tok-secret")
    monkeypatch.setattr(deploy_service, "RUNNER", runner)
    monkeypatch.setenv("MODAL_TOKEN_ID", "tok-id")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "tok-secret")
    status, data = deploy_service.remove(
        "modal", accept=True, yes=True,
        modal_token_id_ref="env:MODAL_TOKEN_ID",
        modal_token_secret_ref="env:MODAL_TOKEN_SECRET",
    )
    assert status == "failed"
    assert "tok-secret" not in json.dumps(data)


def test_manifest_normalization_rewrites_only_docker_layer_labels(monkeypatch):
    """Modal refuses a manifest whose layers mix Docker and OCI media types."""

    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json"},
        "layers": [
            {"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"},
            {"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip"},
        ],
    }
    calls = []

    class Response:
        def __init__(self, payload=None):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout=0):
        calls.append(request)
        if request.get_method() == "PUT":
            return Response({})
        return Response(manifest)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(
        deploy_service, "_registry_token", lambda *args: "test-token"
    )
    changed = deploy_service._normalize_manifest(
        "ghcr.io/owner/img:tag", token=None, username=None,
    )
    assert changed is True
    assert calls[-1].get_method() == "PUT"
    sent = json.loads(calls[-1].data)
    assert [layer["mediaType"] for layer in sent["layers"]] == [
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+gzip",
    ]


def test_manifest_normalization_is_a_noop_for_pure_oci(monkeypatch):
    manifest = {
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}],
    }
    calls = []

    class Response:
        def read(self):
            return json.dumps(manifest).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout=0):
        calls.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(deploy_service, "_registry_token", lambda *args: "t")
    changed = deploy_service._normalize_manifest("ghcr.io/o/i:t", token=None, username=None)
    assert changed is False
    assert len(calls) == 1


def test_registry_token_uses_docker_config_basic_auth(tmp_path, monkeypatch):
    """Stored Docker auth is base64(user:pass) and sends as HTTP Basic."""
    import base64
    import json
    docker_dir = tmp_path / ".docker"
    docker_dir.mkdir()
    (docker_dir / "config.json").write_text(json.dumps({
        "auths": {
            "ghcr.io": {"auth": base64.b64encode(b"user:pass").decode()}
        }
    }))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"token": "my-token"}'

    captured = []
    def urlopen(request, timeout=0):
        captured.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    token = deploy_service._registry_token("ghcr.io", "owner/repo", "pull")
    assert token == "my-token"
    assert len(captured) == 1
    expected_auth = "Basic " + base64.b64encode(b"user:pass").decode()
    assert captured[0].headers.get("Authorization") == expected_auth


def test_salad_update_uses_a_merge_patch(salad):
    """Salad answers 405 to PUT on a group; only PATCH updates it in place."""
    status, _ = deploy_service.update(
        "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
        name="imagen-qwen-3090-low", accept=True,
        salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
        max_seconds=5,
    )
    assert status == "succeeded"
    assert ("PATCH", "/api/public/organizations/org/projects/default/"
            "containers/imagen-qwen-3090-low") in salad.calls


@pytest.mark.parametrize("image,expected", [
    ("ghcr.io/owner/img:0.1.20", ("ghcr.io", "owner/img", "0.1.20")),
    ("owner/img:1.0", ("docker.io", "owner/img", "1.0")),
    ("img", ("docker.io", "img", "latest")),
    ("localhost:5000/img:t", ("localhost:5000", "img", "t")),
])
def test_image_reference_parsing(image, expected):
    assert deploy_service._registry_auth(image) == expected


def test_token_endpoints_differ_per_registry():
    """Docker Hub authenticates on auth.docker.io, not on the registry host."""
    assert deploy_service._token_endpoint("ghcr.io")[0] == "https://ghcr.io"
    assert deploy_service._token_endpoint("docker.io")[0] == "https://auth.docker.io"


def test_status_sends_the_gateway_key_when_the_group_requires_it(salad):
    """Salad's gateway answers 403 to probes that omit the API key."""
    salad.groups["imagen-qwen-3090-low"]["networking"]["auth"] = True
    seen = []
    original = deploy_service._probe

    def spy(host, token, gateway_key=None):
        seen.append(gateway_key)
        return {"readyz": 200, "healthz": 200, "meta": None}

    deploy_service._probe = spy
    try:
        deploy_service.status(
            "salad", name="imagen-qwen-3090-low",
            salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
        )
    finally:
        deploy_service._probe = original
    assert seen == ["salad-secret"]


def test_update_probes_with_the_gateway_key_but_only_when_auth_is_on(salad):
    seen = []
    original = deploy_service._probe

    def spy(host, token, gateway_key=None):
        seen.append(gateway_key)
        return {"readyz": 200, "healthz": 200, "meta": None}

    deploy_service._probe = spy
    try:
        deploy_service.update(
            "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
            name="imagen-qwen-3090-low", accept=True,
            salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
            max_seconds=5,
        )
        salad.groups["imagen-qwen-3090-low"]["networking"]["auth"] = True
        deploy_service.update(
            "salad", image="ghcr.io/nekoccn/imagen-worker:0.1.20",
            name="imagen-qwen-3090-low", accept=True,
            salad_key_ref="env:SALAD_KEY", salad_org="org", salad_project="default",
            max_seconds=5,
        )
    finally:
        deploy_service._probe = original
    assert seen[0] is None
    assert seen[-1] == "salad-secret"
