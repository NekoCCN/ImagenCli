"""T050: coverage matrix, evidence-aware discovery and unsupported-status gating."""

from pathlib import Path

import httpx
import pytest

from imagen_cli.capabilities import discovery
from imagen_cli.capabilities.catalog_data import CANONICAL_RECORDS
from imagen_cli.capabilities.coverage import (
    DIMENSION_KEYS,
    DIMENSIONS,
    matrix,
    transition,
)
from imagen_cli.capabilities.report import render
from imagen_cli.domain.models import CapabilityRecord


def test_matrix_has_thirteen_named_routes_by_eighteen_dimensions():
    routes = matrix()
    assert len(routes) == 13
    assert len(DIMENSIONS) == 18
    assert len(DIMENSION_KEYS) == 18
    for route in routes:
        assert set(route.cells) == set(DIMENSION_KEYS)
        assert route.source.startswith("https://")
        assert route.source_date == "2026-10-24"
    names = " ".join(route.name for route in routes)
    assert "其他" not in names
    assert "other public" not in names.casefold()
    assert "AUTOMATIC1111" in names


def test_no_cell_is_tested_without_live_evidence():
    routes = matrix()
    statuses = {route.cells[key].status for route in routes for key in DIMENSION_KEYS}
    assert "T" not in statuses
    assert all(
        route.cells[key].evidence.strip()
        for route in routes for key in DIMENSION_KEYS
    )


def test_status_transitions_require_the_destination_evidence():
    assert transition("U", "D", live_evidence=False, offline_evidence=False, source=True) == "D"
    assert transition("D", "I", live_evidence=False, offline_evidence=True, source=True) == "I"
    assert transition("I", "T", live_evidence=True, offline_evidence=True, source=True) == "T"
    with pytest.raises(ValueError, match="live evidence"):
        transition("I", "T", live_evidence=False, offline_evidence=True, source=True)
    with pytest.raises(ValueError, match="offline evidence"):
        transition("D", "I", live_evidence=False, offline_evidence=False, source=True)
    with pytest.raises(ValueError, match="located source"):
        transition("U", "D", live_evidence=False, offline_evidence=False, source=False)
    with pytest.raises(ValueError, match="demoted"):
        transition("T", "I", live_evidence=True, offline_evidence=True, source=True)
    with pytest.raises(ValueError, match="unknown coverage status"):
        transition("I", "Z", live_evidence=True, offline_evidence=True, source=True)


def test_offline_records_are_not_promoted_to_tested():
    assert CANONICAL_RECORDS
    for record in CANONICAL_RECORDS:
        assert record.support_status.value == "I"
        assert record.offline_evidence
        assert record.live_evidence == []
    report = discovery.discover(CANONICAL_RECORDS)
    assert report.items
    assert all(item["support_status"] == "I" for item in report.items)
    assert all(item["evidence"]["kind"] == "offline_only" for item in report.items)
    assert all(item["evidence"]["promoted"] is False for item in report.items)


def test_forged_tested_record_is_reported_as_implemented_or_declared():
    record = CANONICAL_RECORDS[0]
    forged = record.model_copy(update={
        "support_status": "T", "live_evidence": [], "offline_evidence": [],
    })
    assert isinstance(forged, CapabilityRecord)
    assert discovery.effective_status(forged) == "D"
    forged_offline = record.model_copy(update={"support_status": "T", "live_evidence": []})
    assert discovery.effective_status(forged_offline) == "I"


def test_discovery_filters_exact_records_without_promoting_status():
    report = discovery.discover(
        CANONICAL_RECORDS, provider="bfl", operation="generate",
    )
    assert {item["provider"] for item in report.items} == {"bfl"}
    assert all(item["operation"] == "generate" for item in report.items)
    assert all(item["support_status"] == "I" for item in report.items)


def test_refresh_dry_run_never_requests_or_authenticates():
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry-run must not send a request")

    report = discovery.refresh(
        CANONICAL_RECORDS, dry_run=True, transport=httpx.MockTransport(forbidden),
    )
    assert report["mode"] == "dry_run"
    assert report["authenticated"] is False
    assert report["requests"] == 0
    assert report["status_changes"] == []
    assert report["sources"]


def test_refresh_uses_public_get_without_authorization_and_never_promotes():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<html>ok</html>")

    report = discovery.refresh(
        CANONICAL_RECORDS, dry_run=False, transport=httpx.MockTransport(handler),
    )
    assert report["authenticated"] is False
    assert report["status_changes"] == []
    assert report["requests"] == len(report["sources"])
    assert seen
    assert all(request.method == "GET" for request in seen)
    assert all("authorization" not in request.headers for request in seen)
    assert all(item["support_status"] == "I" for item in discovery.discover(
        CANONICAL_RECORDS,
    ).items)


def test_refresh_requires_an_explicit_transport_instead_of_implicit_live_calls():
    with pytest.raises(ValueError, match="explicit transport"):
        discovery.refresh(CANONICAL_RECORDS, dry_run=False)


def test_generated_report_file_matches_renderer_and_claims_no_tested_rows():
    path = Path(__file__).resolve().parents[2] / "docs" / "coverage-report.md"
    text = path.read_text(encoding="utf-8")
    assert text == render()
    assert "- 单元格状态：T=0" in text
    assert "AUTOMATIC1111 Stable Diffusion WebUI API" in text
    assert "其他明确公开自托管 API" not in text
    # Offline evidence must be described as I, never as a live-tested claim.
    rows = [line for line in text.splitlines() if "| I |" in line]
    assert rows


def test_report_counts_never_turn_unknown_cells_into_tested():
    text = render()
    assert "\n| T |" not in text
    assert "T=0" in text
