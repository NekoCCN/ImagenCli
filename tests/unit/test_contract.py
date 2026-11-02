"""One contract for remote jobs: statuses, canonical JSON, digest and derived paths."""

import hashlib
from pathlib import Path

import pytest

from imagen_cli.contract import (
    DIGEST_FIELDS,
    JobStatus,
    canonical,
    job_digest,
    job_paths,
    reject_client_paths,
)

JOB_ID = "job_0123456789ab4def8abc0123456789ab"


def record() -> dict:
    return {
        "schema_version": 1,
        "provider": "modal",
        "endpoint_host": "example.modal.run",
        "gpu": "L4",
        "operation": "watermark.invisible",
        "pipeline": "qwen-zimage",
        "normalized_params": {"force": True, "tile": False},
        "input_sha256": "a" * 64,
        "upload_bytes": 1024,
        "max_seconds": 600,
        "worker_image_digest": "sha256:" + "b" * 64,
        "license": "qwen-image-2512",
    }


def test_canceled_is_a_contract_status():
    assert JobStatus.CANCELED == "canceled"
    assert JobStatus("canceled") is JobStatus.CANCELED
    assert set(JobStatus) >= {
        JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED,
        JobStatus.FAILED, JobStatus.INTERRUPTED, JobStatus.UNKNOWN, JobStatus.CANCELED,
    }


def test_canonical_is_compact_sorted_and_rejects_floats():
    assert canonical({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'
    with pytest.raises(TypeError, match="float"):
        canonical({"ratio": 0.5})
    with pytest.raises(TypeError, match="bytes"):
        canonical({"blob": b"x"})


def test_the_digest_changes_with_every_field():
    base = job_digest(record())
    for field in DIGEST_FIELDS:
        changed = record()
        changed[field] = "different"
        assert job_digest(changed) != base, field


def test_the_digest_requires_exactly_the_contract_fields():
    value = record()
    value.pop("license")
    with pytest.raises(ValueError, match="missing"):
        job_digest(value)
    with pytest.raises(ValueError, match="unknown"):
        job_digest({**record(), "extra": 1})


def test_client_supplied_paths_are_rejected():
    reject_client_paths({"operation": "watermark.visible"})
    for field in ("input", "output", "input_path", "output_path"):
        with pytest.raises(ValueError, match="paths"):
            reject_client_paths({"operation": "watermark.visible", field: "/etc/passwd"})


def test_paths_are_derived_from_the_job_id_only():
    root = Path("/jobs")
    paths = job_paths(root, JOB_ID)
    assert paths["input"] == root / JOB_ID / "input.bin"
    assert paths["output"] == root / JOB_ID / "output.bin"
    for bad in ("../escape", "job_nothex", "job_0123456789ab4def8abc0123456789ag"):
        with pytest.raises(ValueError, match="job id"):
            job_paths(root, bad)


def test_the_digest_is_a_sha256_hex_value():
    digest = job_digest(record())
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == hashlib.sha256(canonical(record())).hexdigest()
