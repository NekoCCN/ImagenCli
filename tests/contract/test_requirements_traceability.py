"""T098: every FR/SC is traced exactly once and cites existing test evidence."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "requirements-traceability.md"
EXPECTED = {f"FR-{index:03d}" for index in range(1, 49)} | {
    f"SC-{index:03d}" for index in range(1, 14)
}
ROW = re.compile(r"^\| (FR-\d{3}|SC-\d{3}) \|(.*)\|$", re.MULTILINE)
STATUSES = {"implemented", "partial", "unimplemented"}
pytestmark = pytest.mark.skipif(
    not DOCUMENT.is_file(), reason="traceability document is local and unversioned"
)


def test_every_requirement_appears_exactly_once() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    identities = [match.group(1) for match in ROW.finditer(text)]
    assert set(identities) == EXPECTED
    assert len(identities) == len(EXPECTED)
    for identity in EXPECTED:
        assert text.count(identity) == 1, identity


def test_every_row_has_status_and_explicit_gaps() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    for match in ROW.finditer(text):
        identity, remainder = match.groups()
        cells = [cell.strip() for cell in remainder.split("|")]
        assert len(cells) == 7, identity
        requirement, milestone, tasks, evidence, status, unimplemented, live = cells
        assert requirement and tasks and evidence, identity
        assert re.match(r"M[1-4](→M[1-4])?", milestone), identity
        assert status in STATUSES, identity
        assert unimplemented, identity
        assert live, identity


def test_cited_test_files_and_functions_exist() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")
    citations = re.findall(r"`(tests/[^`:]+)::([^`]+)`", text)
    assert citations
    for path, name in citations:
        source = ROOT / path
        assert source.is_file(), path
        assert re.search(rf"^def {re.escape(name)}\b", source.read_text(encoding="utf-8"),
                         re.MULTILINE), f"{path}::{name}"
