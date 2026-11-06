"""Deploy and update the remote worker on Modal and SaladCloud.

Control-plane only: image builds and pushes run as explicit subprocesses, and
credentials are resolved from ``env:`` or ``private:`` references without ever
being written into output or config.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from imagen_cli import i18n
from imagen_cli.application import remote_service
from imagen_cli.execution.configured import resolve_credential

TRANSPORT: httpx.BaseTransport | None = None
RUNNER: Any = subprocess.run
SLEEP: Any = time.sleep

SALAD_API = "https://api.salad.com"
POLL_SECONDS = 5.0
NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
REFERENCE = re.compile(
    r"^(env:[A-Za-z_][A-Za-z0-9_]*|private:[A-Za-z][A-Za-z0-9_-]{0,127})$"
)
VERSION = re.compile(r"^v?(\d+\.\d+\.\d+)$")
MODAL_URL = re.compile(r"https://[A-Za-z0-9.-]+\.modal\.run")
VENDORS = ("salad", "modal")
MERGE_PATCH = "application/merge-patch+json"
# One checkout, two images: the base worker, and the Salad queue image built on it.
BUILD_TARGETS = ("worker", "salad-queue")
MATTING_RUNTIMES = ("cpu", "gpu")
MODAL_APP = "imagen-worker"


class DeployError(Exception):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _outcome(
    function: Callable[..., Any],
) -> Callable[..., tuple[str, dict[str, Any]]]:
    """Public commands return a status tuple; internal helpers raise."""

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        try:
            return cast(tuple[str, dict[str, Any]], function(*args, **kwargs))
        except DeployError as error:
            return error.status, {
                "error": {"type": error.status, "detail": error.detail},
            }

    return wrapper


def _config() -> dict[str, Any]:
    try:
        payload = json.loads(i18n.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _ref(reference: str | None, private_store: Any, label: str) -> str | None:
    if not reference:
        return None
    if not REFERENCE.fullmatch(reference):
        raise DeployError("validation_error", f"{label} must be env:NAME or private:NAME")
    try:
        return resolve_credential(reference, private_store=private_store)
    except (OSError, ValueError) as error:
        raise DeployError("validation_error", f"{label} is unavailable: {reference}") from error


def _digest(plan: dict[str, Any]) -> str:
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version_of(image: str) -> str | None:
    if "@" in image:
        return None
    match = VERSION.fullmatch(image.rsplit(":", 1)[-1]) if ":" in image else None
    return match.group(1) if match else None


def _target(org: str | None, project: str | None) -> tuple[str, str]:
    section = ((_config().get("deploy") or {}).get("salad") or {})
    organization = org or str(section.get("organization") or "")
    project_name = project or str(section.get("project") or "")
    if not organization or not project_name:
        raise DeployError(
            "validation_error",
            "Salad organization and project are required; pass --salad-org and "
            "--salad-project or set deploy.salad.organization/project in config.json",
        )
    for value in (organization, project_name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise DeployError("validation_error", "Salad organization or project is invalid")
    return organization, project_name


def _salad_request(
    method: str, key: str, org: str, project: str, path: str, *,
    body: dict[str, Any] | None = None, timeout: float = 60.0,
    content_type: str | None = None,
) -> httpx.Response:
    url = f"/api/public/organizations/{org}/projects/{project}{path}"
    with httpx.Client(
        base_url=SALAD_API,
        headers={"Salad-Api-Key": key},
        timeout=timeout,
        transport=TRANSPORT,
        follow_redirects=False,
    ) as client:
        if content_type is not None:
            return client.request(
                method, url, content=json.dumps(body).encode(),
                headers={"Content-Type": content_type},
            )
        return client.request(method, url, json=body)


def _salad_error(response: httpx.Response, step: str) -> DeployError:
    if response.status_code in {401, 403}:
        return DeployError(
            "authentication_error",
            f"Salad {step} was rejected (HTTP {response.status_code})",
        )
    if response.status_code in {400, 404, 409, 422}:
        return DeployError(
            "validation_error", f"Salad {step} failed (HTTP {response.status_code})",
        )
    return DeployError("failed", f"Salad {step} failed (HTTP {response.status_code})")


def salad_get(key: str, org: str, project: str, name: str) -> dict[str, Any]:
    response = _salad_request("GET", key, org, project, f"/containers/{name}")
    if response.status_code != 200:
        raise _salad_error(response, f"read {name}")
    return cast(dict[str, Any], response.json())


def salad_create(key: str, org: str, project: str, definition: dict[str, Any]) -> dict[str, Any]:
    response = _salad_request("POST", key, org, project, "/containers", body=definition)
    if response.status_code not in {200, 201}:
        raise _salad_error(response, f"create {definition.get('name')}")
    return cast(dict[str, Any], response.json())


def salad_update(key: str, org: str, project: str, name: str,
                 definition: dict[str, Any]) -> dict[str, Any]:
    """Update a group in place with a JSON merge patch.

    Salad answers ``PUT`` on this path with 405: the documented in-place update
    is ``PATCH`` with ``application/merge-patch+json``, and only the fields in
    the body change.
    """
    response = _salad_request(
        "PATCH", key, org, project, f"/containers/{name}", body=definition,
        content_type=MERGE_PATCH,
    )
    if response.status_code not in {200, 202}:
        raise _salad_error(response, f"update {name}")
    return cast(dict[str, Any], response.json())


def salad_start(key: str, org: str, project: str, name: str) -> None:
    response = _salad_request("POST", key, org, project, f"/containers/{name}/start")
    if response.status_code not in {200, 202}:
        raise _salad_error(response, f"start {name}")


def salad_delete(key: str, org: str, project: str, name: str) -> None:
    response = _salad_request("DELETE", key, org, project, f"/containers/{name}")
    if response.status_code not in {200, 202, 204}:
        raise _salad_error(response, f"delete {name}")


def salad_definition(
    current: dict[str, Any], *, name: str, image: str, environment: dict[str, str],
) -> dict[str, Any]:
    container = current.get("container") or {}
    definition: dict[str, Any] = {
        "name": name,
        "display_name": name,
        "replicas": current.get("replicas", 1),
        "restart_policy": current.get("restart_policy", "always"),
        "priority": current.get("priority", "low"),
        "autostart_policy": current.get("autostart_policy", False),
        "country_codes": current.get("country_codes"),
        "container": {
            key: value for key, value in {
                "image": image,
                "resources": container.get("resources"),
                "environment_variables": environment,
                "command": container.get("command"),
                "image_caching": container.get("image_caching", True),
            }.items() if value is not None
        },
        "networking": {
            key: value for key, value in (current.get("networking") or {}).items()
            if key != "dns"
        },
        "liveness_probe": current.get("liveness_probe"),
        "readiness_probe": current.get("readiness_probe"),
    }
    return {key: value for key, value in definition.items() if value is not None}


def _environment(current: dict[str, Any], *, image: str, token: str | None,
                 overrides: dict[str, str]) -> dict[str, str]:
    environment = {
        str(key): str(value)
        for key, value in (
            (current.get("container") or {}).get("environment_variables") or {}
        ).items()
    }
    if token:
        environment["IMAGEN_WORKER_TOKEN"] = token
    version = _version_of(image)
    if version:
        environment["IMAGEN_WORKER_VERSION"] = version
        environment["IMAGEN_CLI_VERSION"] = version
    environment.update(overrides)
    return environment


def _probe(host: str, token: str | None,
           gateway_key: str | None = None) -> dict[str, Any]:
    """Probe a worker through its public host.

    Salad's Container Gateway answers 403 to every request that lacks the
    ``Salad-Api-Key`` header when gateway auth is enabled, ``/healthz``
    included, so a readiness wait without it can never succeed.
    """
    result: dict[str, Any] = {"readyz": None, "healthz": None, "meta": None}
    headers = {"Salad-Api-Key": gateway_key} if gateway_key else {}
    with httpx.Client(
        base_url=f"https://{host}", timeout=30.0, transport=TRANSPORT,
        follow_redirects=False, headers=headers,
    ) as client:
        for path in ("/readyz", "/healthz"):
            try:
                result[path[1:]] = client.get(path).status_code
            except httpx.HTTPError:
                result[path[1:]] = None
        if token:
            try:
                response = client.get(
                    "/v1/meta", headers={"Authorization": "Bearer " + token},
                )
                if response.status_code == 200:
                    result["meta"] = response.json()
            except (httpx.HTTPError, ValueError):
                pass
    return result


def _wait_until_ready(key: str, org: str, project: str, name: str, *,
                      token: str | None, max_seconds: int,
                      gateway_key: str | None = None) -> dict[str, Any]:
    attempts = max(1, int(max_seconds // POLL_SECONDS))
    last: dict[str, Any] = {}
    for _ in range(attempts):
        group = salad_get(key, org, project, name)
        host = (group.get("networking") or {}).get("dns")
        counts = (group.get("current_state") or {}).get("instance_status_counts") or {}
        if host and int(counts.get("running_count") or 0) >= 1:
            last = _probe(str(host), token, gateway_key)
            if last["readyz"] == 200:
                return {**group, "probe": last}
        SLEEP(POLL_SECONDS)
    raise DeployError(
        "failed",
        f"Salad group {name} did not become ready before --max-seconds "
        f"(readyz={last.get('readyz')})",
    )


def _salad_update(
    *, image: str, name: str, rename: str | None, overrides: dict[str, str],
    key: str, token: str | None, org: str, project: str, max_seconds: int,
    yes: bool,
) -> tuple[str, dict[str, Any]]:
    current = salad_get(key, org, project, name)
    environment = _environment(current, image=image, token=token, overrides=overrides)
    # Gateway auth needs the same key the requests themselves carry, and the
    # group definition read above already says whether it is enabled.
    gateway_key = key if (current.get("networking") or {}).get("auth") else None

    if rename is None:
        definition = salad_definition(current, name=name, image=image,
                                      environment=environment)
        group = salad_update(key, org, project, name, definition)
        # An update does not resume a stopped group, so waiting for readiness
        # would only burn the whole timeout on a group nobody started.
        if (current.get("current_state") or {}).get("status") != "running":
            salad_start(key, org, project, name)
        ready = _wait_until_ready(key, org, project, name, token=token,
                                  max_seconds=max_seconds,
                                  gateway_key=gateway_key)
        return "succeeded", {
            "vendor": "salad", "name": name, "image": image, "renamed": False,
            "state": (group.get("current_state") or {}).get("status"),
            "readyz": (ready.get("probe") or {}).get("readyz"),
        }

    if not yes:
        raise DeployError(
            "validation_error",
            "renaming creates a new group and deletes the old one; add --yes "
            "to authorize the delete after the new group is ready",
        )
    try:
        salad_get(key, org, project, rename)
    except DeployError as error:
        if error.status != "validation_error":
            raise
    else:
        raise DeployError("validation_error", f"target group {rename} already exists")

    definition = salad_definition(current, name=rename, image=image,
                                  environment=environment)
    salad_create(key, org, project, definition)
    if (current.get("current_state") or {}).get("status") != "running":
        salad_start(key, org, project, rename)
    ready = _wait_until_ready(key, org, project, rename, token=token,
                              max_seconds=max_seconds, gateway_key=gateway_key)
    salad_delete(key, org, project, name)
    return "succeeded", {
        "vendor": "salad", "name": rename, "previous_name": name,
        "image": image, "renamed": True,
        "readyz": (ready.get("probe") or {}).get("readyz"),
    }


def _modal_update(
    *, image: str, gpu: str | None, memory_mb: int | None, cpu: float | None,
    token_id: str | None, token_secret: str | None,
) -> tuple[str, dict[str, Any]]:
    app = Path("deploy/modal_app.py")
    if not app.is_file():
        raise DeployError(
            "validation_error",
            "deploy/modal_app.py is missing; run from a repository checkout",
        )
    environment = os.environ.copy()
    environment["IMAGEN_WORKER_IMAGE"] = image
    if gpu:
        environment["IMAGEN_MODAL_GPU"] = gpu
    if memory_mb:
        environment["IMAGEN_MODAL_MEMORY_MB"] = str(memory_mb)
    if cpu:
        environment["IMAGEN_MODAL_CPU"] = str(cpu)
    if token_id:
        environment["MODAL_TOKEN_ID"] = token_id
    if token_secret:
        environment["MODAL_TOKEN_SECRET"] = token_secret
    completed = RUNNER(
        [*modal_command(), "deploy", str(app)],
        capture_output=True, text=True, env=environment,
    )
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    for secret in (token_id, token_secret):
        if secret:
            output = output.replace(secret, "<redacted>")
    if completed.returncode != 0:
        tail = output.strip().splitlines()[-1] if output.strip() else "no output"
        raise DeployError(
            "failed", f"modal deploy failed (exit {completed.returncode}): {tail}"
        )
    match = MODAL_URL.search(output)
    if match is None:
        raise DeployError("failed", "modal deploy succeeded without printing a URL")
    return "succeeded", {
        "vendor": "modal", "app": "imagen-worker", "image": image,
        "url": match.group(0),
    }


def _modal_status(remote_name: str, private_store: Any) -> dict[str, Any]:
    if remote_name not in remote_service.remotes():
        raise DeployError("validation_error", f"remote {remote_name} is not registered")
    try:
        result = remote_service.status(remote_name, private_store=private_store)
    except remote_service.RemoteError as error:
        raise DeployError(error.status, str(error)) from error
    meta = result.get("meta") or {}
    warnings = []
    if meta.get("state_mode") == "shared-volume":
        warnings.append("shared_volume")
    return {
        "vendor": "modal", "name": remote_name,
        "endpoint": result.get("endpoint"), "meta": meta,
        "warnings": warnings,
    }


@_outcome
def update(
    vendor: str, *, image: str, name: str | None = None, rename: str | None = None,
    set_env: list[str] | None = None, accept: bool = False, yes: bool = False,
    salad_key_ref: str | None = None, worker_token_ref: str | None = None,
    salad_org: str | None = None, salad_project: str | None = None,
    modal_token_id_ref: str | None = None,
    modal_token_secret_ref: str | None = None,
    modal_gpu: str | None = None, modal_memory_mb: int | None = None,
    modal_cpu: float | None = None, max_seconds: int = 900,
    private_store: Any = None,
) -> tuple[str, dict[str, Any]]:
    if vendor not in VENDORS:
        raise DeployError("validation_error", "vendor must be salad or modal")
    if not image.strip():
        raise DeployError("validation_error", "image is required")
    overrides: dict[str, str] = {}
    for item in set_env or []:
        env_key, separator, value = item.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_key):
            raise DeployError("validation_error", f"invalid --set-env value: {item}")
        overrides[env_key] = value
    plan = {
        "vendor": vendor,
        "name": name,
        "image": image,
        "rename": rename,
        "set_env": sorted(overrides),
    }
    if not accept:
        return "approval_required", {
            "plan": {**plan, "action": "rename" if rename else "update"},
            "plan_digest": _digest(plan),
        }
    if vendor == "modal":
        if name or rename:
            raise DeployError(
                "validation_error",
                "Modal app names are fixed; --name and --rename apply to Salad only",
            )
        if set_env:
            raise DeployError("validation_error", "Modal environment overrides are not supported")
        return _modal_update(
            image=image, gpu=modal_gpu, memory_mb=modal_memory_mb, cpu=modal_cpu,
            token_id=_ref(modal_token_id_ref, private_store, "Modal token id"),
            token_secret=_ref(modal_token_secret_ref, private_store, "Modal token secret"),
        )
    if not name:
        raise DeployError("validation_error", "--name is required for Salad")
    if not NAME.fullmatch(name) or (rename and not NAME.fullmatch(rename)):
        raise DeployError(
            "validation_error",
            "Salad group names use lowercase letters, digits and hyphens",
        )
    key = _ref(salad_key_ref, private_store, "Salad API key")
    if not key:
        raise DeployError("validation_error", "--salad-key-ref is required for Salad")
    token = _ref(worker_token_ref, private_store, "worker token")
    organization, project_name = _target(salad_org, salad_project)
    return _salad_update(
        image=image, name=name, rename=rename, overrides=overrides, key=key,
        token=token, org=organization, project=project_name,
        max_seconds=max_seconds, yes=yes,
    )


def _salad_status(
    *, name: str, key: str, token: str | None, org: str, project: str,
) -> dict[str, Any]:
    group = salad_get(key, org, project, name)
    host = (group.get("networking") or {}).get("dns")
    gateway_key = key if (group.get("networking") or {}).get("auth") else None
    probe = _probe(str(host), token, gateway_key) if host \
        else {"readyz": None, "healthz": None, "meta": None}
    container = group.get("container") or {}
    environment = container.get("environment_variables") or {}
    image = str(container.get("image") or "")
    warnings: list[str] = []
    if probe["healthz"] not in {200, None} and probe["readyz"] == 200:
        warnings.append("healthz_not_ok_readyz_ok")
    if (group.get("current_state") or {}).get("status") != "running":
        warnings.append("group_not_running")
    if environment.get("IMAGEN_WORKER_STATE_MODE") == "shared-volume":
        warnings.append("shared_volume_on_salad")
    registered = [
        (record_name, record)
        for record_name, record in remote_service.remotes().items()
        if urlparse(str(record.get("endpoint") or "")).hostname == host
    ]
    if (group.get("networking") or {}).get("auth") and registered:
        if not any(record.get("gateway_credential_ref") for _, record in registered):
            warnings.append("gateway_auth_unregistered")
    if not registered:
        warnings.append("remote_not_registered")
    version = _version_of(image)
    if version and environment.get("IMAGEN_WORKER_VERSION") not in {None, version}:
        warnings.append("version_drift")
    return {
        "vendor": "salad", "name": name, "image": image,
        "state": (group.get("current_state") or {}).get("status"),
        "instances": (group.get("current_state") or {}).get("instance_status_counts"),
        "readyz": probe["readyz"], "healthz": probe["healthz"],
        "meta": probe["meta"],
        "remote": {
            "registered": bool(registered),
            "names": [record_name for record_name, _ in registered],
        },
        "warnings": warnings,
    }


@_outcome
def status(
    vendor: str, *, name: str | None = None,
    salad_key_ref: str | None = None, worker_token_ref: str | None = None,
    salad_org: str | None = None, salad_project: str | None = None,
    private_store: Any = None,
) -> tuple[str, dict[str, Any]]:
    if vendor == "salad":
        if not name or not NAME.fullmatch(name):
            raise DeployError("validation_error", "--name is required for Salad status")
        key = _ref(salad_key_ref, private_store, "Salad API key")
        if not key:
            raise DeployError("validation_error", "--salad-key-ref is required for Salad status")
        organization, project_name = _target(salad_org, salad_project)
        return "succeeded", _salad_status(
            name=name, key=key,
            token=_ref(worker_token_ref, private_store, "worker token"),
            org=organization, project=project_name,
        )
    if vendor == "modal":
        return "succeeded", _modal_status(name or "modal", private_store)
    raise DeployError("validation_error", "vendor must be salad or modal")


def modal_command() -> list[str]:
    """Locate the Modal CLI.

    ``modal`` is commonly installed with ``uv tool install`` or ``pipx``, which
    puts it on PATH in a separate environment; importing it from this
    interpreter's ``sys.executable`` then fails with "No module named modal".
    """
    executable = shutil.which("modal")
    if executable:
        return [executable]
    try:
        import modal  # noqa: F401
    except ImportError:
        raise DeployError(
            "validation_error",
            "the Modal CLI is not installed; run: uv tool install modal",
        ) from None
    return [sys.executable, "-m", "modal"]


# Docker's legacy builder labels the layers it produced with the Docker media
# type while OCI base-image layers keep the OCI type, so one image can carry
# both. The bytes are identical gzipped tars; Modal's unpacker only accepts the
# OCI label, so a mixed manifest deploys as "unsupported mediatype".
DOCKER_LAYER_TYPES = {
    "application/vnd.docker.image.rootfs.diff.tar.gzip":
        "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip":
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
}
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def _registry_auth(image: str) -> tuple[str, str, str]:
    """Split an image reference into (registry, repository, tag-or-digest)."""
    name = image.split("@", 1)[0]
    if "@" in image:
        raise DeployError("validation_error", "normalize by tag, not by digest")
    if "/" not in name:
        registry, remainder = "docker.io", name
    else:
        first, _, rest = name.partition("/")
        # A registry host has a dot or a port; otherwise the first segment is
        # a Docker Hub namespace ("owner/image").
        if "." in first or ":" in first or first == "localhost":
            registry, remainder = first, rest
        else:
            registry, remainder = "docker.io", name
    repository, _, tag = remainder.rpartition(":")
    if not repository:
        repository, tag = remainder, "latest"
    return registry, repository, tag


def _token_endpoint(registry: str) -> tuple[str, str]:
    """Token URL and service name per registry; Docker Hub does not self-serve."""
    if registry == "ghcr.io":
        return "https://ghcr.io", "ghcr.io"
    if registry == "docker.io":
        return "https://auth.docker.io", "registry.docker.io"
    return f"https://{registry}", registry


def _registry_token(registry: str, repository: str, scope: str) -> str:
    """Fetch a registry bearer token, using docker's stored credentials."""
    import json as jsonlib
    import urllib.request

    credentials: dict[str, str] = {}
    try:
        config = jsonlib.loads(
            (Path.home() / ".docker" / "config.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        config = {}
    encoded = (config.get("auths") or {}).get(registry, {}).get("auth")
    if encoded:
        credentials = {"Authorization": "Basic " + encoded.strip()}
    host, service = _token_endpoint(registry)
    request = urllib.request.Request(
        f"{host}/token?scope=repository:{repository}:{scope}&service={service}",
        headers=credentials,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return str(jsonlib.loads(response.read())["token"])


def _normalize_manifest(image: str, *, token: str | None, username: str | None) -> bool:
    """Rewrite Docker-typed layer labels to OCI so Modal can unpack the image.

    Returns True when the manifest changed. Registries that need a fresh login
    get it from ``--registry-user-ref``/``--registry-password-ref``; otherwise
    the credentials docker already stored are reused.
    """
    import json as jsonlib
    import urllib.request

    registry, repository, tag = _registry_auth(image)
    host, _service = _token_endpoint(registry)
    token = token or _registry_token(registry, repository, "pull,push")
    headers = {"Authorization": "Bearer " + token, "Accept": OCI_MANIFEST}
    request = urllib.request.Request(
        f"{host}/v2/{repository}/manifests/{tag}", headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            manifest = jsonlib.loads(response.read())
    except OSError as error:
        raise DeployError(
            "failed", f"registry manifest read failed: {type(error).__name__}"
        ) from error
    rewritten = 0
    for layer in manifest.get("layers") or []:
        replacement = DOCKER_LAYER_TYPES.get(str(layer.get("mediaType")))
        if replacement:
            layer["mediaType"] = replacement
            rewritten += 1
    if not rewritten:
        return False
    body = jsonlib.dumps(manifest).encode()
    put = urllib.request.Request(
        f"{host}/v2/{repository}/manifests/{tag}", data=body, method="PUT",
        headers={**headers, "Content-Type": OCI_MANIFEST},
    )
    try:
        with urllib.request.urlopen(put, timeout=120):
            pass
    except OSError as error:
        raise DeployError(
            "failed", f"registry manifest update failed: {type(error).__name__}"
        ) from error
    return True


def _run(argv: list[str], step: str) -> None:
    _run_with(argv, step, None)


def _run_with(argv: list[str], step: str, password: str | None,
              secrets: list[str] | None = None) -> None:
    """Run one external command; a registry password reaches docker on stdin only."""
    if password is None:
        completed = RUNNER(argv, capture_output=True, text=True)
    else:
        completed = RUNNER(argv, capture_output=True, text=True,
                           input=password + "\n")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        message = f"{detail[-1] if detail else 'no output'}"
        raise DeployError(
            "failed",
            f"{step} failed (exit {completed.returncode}): "
            + _scrub(message, [*(secrets or []), *([password] if password else [])]),
        )


def _scrub(text: str, secrets: list[str]) -> str:
    """Remove any resolved credential from text that may be echoed back."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


@_outcome
def build(
    *, tag: str, image: str, push: bool = False, accept: bool = False,
    target: str = "worker",
    salad_queue_worker_url_ref: str | None = None,
    base_image: str | None = None,
    matting_runtime: str = "cpu",
    registry_username_ref: str | None = None,
    registry_password_ref: str | None = None,
    private_store: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Build one worker image, optionally from source, optionally pushing it.

    Two images share one checkout: ``worker`` is the base HTTP worker, ``salad-queue``
    adds the vendor Queue Worker binary on top of that base image.
    """
    if target not in BUILD_TARGETS:
        raise DeployError("validation_error", "target must be worker or salad-queue")
    if matting_runtime not in MATTING_RUNTIMES:
        raise DeployError("validation_error", "matting runtime must be cpu or gpu")
    plan: dict[str, Any] = {"tag": tag, "image": image, "target": target,
                            "push": bool(push)}
    if target == "worker":
        plan["matting_runtime"] = matting_runtime
    if target == "salad-queue":
        plan["base_image"] = base_image or f"imagen-worker:{tag}"
    else:
        plan["from_source"] = True
    if registry_username_ref:
        plan["registry_user_ref"] = registry_username_ref
    if not accept:
        return "approval_required", {"plan": plan, "plan_digest": _digest(plan)}

    if target == "worker":
        if not Path("deploy/worker/Dockerfile").is_file():
            raise DeployError(
                "validation_error",
                "deploy/worker/Dockerfile is missing; run from a repository checkout",
            )
        # The plan already promises a source build; without this the Dockerfile
        # installs the released version from PyPI, which may not carry the
        # operations this checkout is being deployed for.
        _run(
            ["docker", "build", "-f", "deploy/worker/Dockerfile",
             "--build-arg", "IMAGEN_FROM_SOURCE=1",
             "--build-arg", f"IMAGEN_VERSION={tag}",
             "--build-arg", f"IMAGEN_MATTING_RUNTIME={matting_runtime}",
             "-t", image, "."],
            "docker build",
        )
    else:
        dockerfile = Path("deploy/salad/queue-worker.Dockerfile")
        if not dockerfile.is_file():
            raise DeployError(
                "validation_error",
                "deploy/salad/queue-worker.Dockerfile is missing; run from a "
                "repository checkout",
            )
        queue_url = _ref(salad_queue_worker_url_ref, private_store,
                         "Salad Queue Worker download URL")
        if not queue_url:
            raise DeployError(
                "validation_error",
                "--salad-queue-worker-url-ref is required for the salad-queue target; "
                "the vendor binary is not redistributable",
            )
        _run(
            ["docker", "build", "-f", str(dockerfile),
             "--build-arg", f"BASE={base_image or f'imagen-worker:{tag}'}",
             "--build-arg", f"SALAD_QUEUE_WORKER_URL={queue_url}",
             "-t", image, "."],
            "docker build",
        )

    normalized = False
    if push:
        # Login first: a private registry refuses the push otherwise, and the
        # password must never appear in argv or in the plan.
        _docker_login(image, registry_username_ref, registry_password_ref, private_store)
        _run(["docker", "push", image], "docker push")
        # Modal cannot unpack a manifest that mixes Docker and OCI layer labels;
        # the legacy builder emits exactly that on top of an OCI base image.
        normalized = _normalize_manifest(image, token=None, username=None)
    return "succeeded", {"image": image, "tag": tag, "target": target,
                         "pushed": bool(push), "manifest_normalized": normalized}


def _docker_login(image: str, username_ref: str | None, password_ref: str | None,
                  private_store: Any) -> None:
    if not username_ref and not password_ref:
        return
    if not username_ref or not password_ref:
        raise DeployError(
            "validation_error",
            "registry login needs both --registry-user-ref and --registry-password-ref",
        )
    username = _ref(username_ref, private_store, "registry username")
    password = _ref(password_ref, private_store, "registry password")
    if not username or not password:
        raise DeployError("validation_error", "registry credentials are unavailable")
    server = image.split("/", 1)[0] if "/" in image else "docker.io"
    _run_with(
        ["docker", "login", server, "--username", username, "--password-stdin"],
        "docker login",
        password,
        [username, password],
    )


@_outcome
def remove(
    vendor: str, *, name: str | None = None, accept: bool = False,
    yes: bool = False,
    salad_key_ref: str | None = None,
    salad_org: str | None = None, salad_project: str | None = None,
    modal_token_id_ref: str | None = None,
    modal_token_secret_ref: str | None = None,
    private_store: Any = None,
) -> tuple[str, dict[str, Any]]:
    """Delete a container group (Salad) or stop a Modal app; two explicit gates.

    Deletion is irreversible and may leave a registered remote pointing at nothing,
    so the plan is shown first, then ``accept`` and ``yes`` are both required.
    """
    if vendor not in VENDORS:
        raise DeployError("validation_error", "vendor must be salad or modal")
    if vendor == "salad":
        if not name or not NAME.fullmatch(name):
            raise DeployError("validation_error", "--name is required to delete a Salad group")
        organization, project_name = _target(salad_org, salad_project)
        plan: dict[str, Any] = {
            "vendor": "salad", "action": "delete", "name": name,
            "organization": organization, "project": project_name,
        }
        if not accept or not yes:
            return "approval_required", {
                "plan": plan, "plan_digest": _digest(plan),
                "error": {"type": "approval_required", "detail": i18n.t(
                    "Deleting a Salad group is irreversible and stops billing for it. "
                    "Review the plan, then rerun with --accept --yes.",
                    "删除 Salad 容器组不可撤回，并会停止该组计费。请确认计划后重跑，"
                    "并加上 --accept --yes。",
                )},
            }
        key = _ref(salad_key_ref, private_store, "Salad API key")
        if not key:
            raise DeployError("validation_error", "--salad-key-ref is required to delete")
        # Read first: a mistyped name must fail loudly, not silently report success.
        salad_get(key, organization, project_name, name)
        salad_delete(key, organization, project_name, name)
        return "succeeded", {**plan, "deleted": True}

    token_id = _ref(modal_token_id_ref, private_store, "Modal token id")
    token_secret = _ref(modal_token_secret_ref, private_store, "Modal token secret")
    plan = {"vendor": "modal", "action": "stop", "app": MODAL_APP}
    if not accept or not yes:
        return "approval_required", {
            "plan": plan, "plan_digest": _digest(plan),
            "error": {"type": "approval_required", "detail": i18n.t(
                "Stopping the Modal app terminates its running containers "
                "(next deploy recreates it). Rerun with --accept --yes.",
                "停止 Modal 应用会终止其运行中的容器（下次 deploy 会重建）。"
                "请重跑并加上 --accept --yes。",
            )},
        }
    environment = os.environ.copy()
    if token_id:
        environment["MODAL_TOKEN_ID"] = token_id
    if token_secret:
        environment["MODAL_TOKEN_SECRET"] = token_secret
    completed = RUNNER(
        [*modal_command(), "app", "stop", "-y", MODAL_APP],
        capture_output=True, text=True, env=environment,
    )
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    for secret in (token_id, token_secret):
        if secret:
            output = output.replace(secret, "<redacted>")
    if completed.returncode != 0:
        tail = output.strip().splitlines()[-1] if output.strip() else "no output"
        raise DeployError(
            "failed", f"modal app stop failed (exit {completed.returncode}): {tail}"
        )
    return "succeeded", {**plan, "stopped": True}


@_outcome
def set_secret(private_store: Any, name: str, key_file: str) -> tuple[str, dict[str, Any]]:
    from imagen_cli.cli.commands.profile import read_key

    if private_store is None:
        raise DeployError("validation_error", "private credential storage is unavailable")
    if not NAME.fullmatch(name):
        raise DeployError(
            "validation_error",
            "secret names use lowercase letters, digits and hyphens",
        )
    try:
        value = read_key(key_file)
    except (OSError, ValueError) as error:
        raise DeployError("validation_error", str(error)) from error
    reference = private_store.write_credential("deploy-" + name, value)
    return "succeeded", {"name": name, "credential_ref": reference}
