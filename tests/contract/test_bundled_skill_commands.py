"""Bundled skill examples must stay runnable against the current CLI parser."""

import re
import shlex
from pathlib import Path

import pytest

from imagen_cli.cli import options as cli_options
from imagen_cli.cli.entrypoint import _parse
from imagen_cli.cli.registry import COMMANDS, discover

BUNDLED = Path(__file__).resolve().parents[2] / "src" / "imagen_cli" / "bundled_skills"
PLACEHOLDERS = {
    "ARTIFACT_ID": "art_" + "a" * 32,
    "SOURCE_ID": "art_" + "b" * 32,
    "JOB_ID": "job_" + "a" * 32,
    "SESSION_ID": "ses_" + "a" * 32,
    "BATCH_ID": "bat_" + "a" * 32,
    "PLAN_ID": "pln_" + "a" * 32,
    "PROFILE_ID": "prf_" + "a" * 32,
    "USER_SELECTED_MODEL": "film-degrader",
    "PLAN_DIGEST": "b" * 64,
    "DIGEST": "a" * 64,
    "art_a": "art_" + "a" * 32,
    "art_b": "art_" + "b" * 32,
    "NAME": "demo",
    "PATH": "/tmp/x.png",
}
PATTERN = re.compile(
    "|".join(re.escape(key) for key in sorted(PLACEHOLDERS, key=len, reverse=True))
)


def _blocks(text: str):
    kind, buffer = None, []
    for line in text.splitlines():
        if line.startswith("```"):
            if kind is None:
                kind = line[3:].strip()
            else:
                yield kind, buffer
                kind, buffer = None, []
            continue
        if kind is not None:
            buffer.append(line)


def _commands():
    for path in sorted(BUNDLED.rglob("*.md")):
        for kind, lines in _blocks(path.read_text(encoding="utf-8")):
            if kind not in {"", "bash", "sh"}:
                continue
            joined, buffer = [], ""
            for line in lines:
                buffer += line.rstrip()
                if buffer.endswith("\\"):
                    buffer = buffer[:-1] + " "
                    continue
                joined.append(buffer.strip())
                buffer = ""
            for line in joined:
                if line.startswith("imagen "):
                    yield path, line


def _substitute(line: str) -> str:
    line = line.replace("art_...", "@P@").replace("bat_...", "@B@").replace("ses_...", "@S@")
    line = PATTERN.sub(lambda match: PLACEHOLDERS[match.group(0)], line)
    return (line.replace("@P@", "art_" + "a" * 32)
                .replace("@B@", "bat_" + "a" * 32)
                .replace("@S@", "ses_" + "a" * 32))


def test_skill_examples_match_the_cli_parser():
    failures = []
    for path, line in _commands():
        try:
            _parse(shlex.split(_substitute(line))[1:])
        except ValueError as error:
            if "input file unavailable" in str(error):
                continue
            failures.append(f"{path.name}: {line}: {error}")
        except Exception as error:  # noqa: BLE001 - report the parser's own message
            failures.append(f"{path.name}: {line}: {type(error).__name__}: {error}")
    assert not failures, "\n".join(failures)


def _known_flags() -> set[str]:
    # Parser-level input options are not declared in any command schema.
    known = set(cli_options.OPTION_METADATA) | {"input-json"}
    for name in COMMANDS:
        known |= set(discover(name)["input_schema"].get("properties", {}))
    for controls in cli_options.COMMAND_CONTROLS.values():
        known |= controls
    return {"--" + name.replace("_", "-") for name in known}


@pytest.mark.parametrize("path", sorted(BUNDLED.rglob("*.md")), ids=lambda path: path.name)
def test_documented_flags_exist(path):
    known = _known_flags()
    mentioned = set(re.findall(r"--[a-z][a-z0-9-]+", path.read_text(encoding="utf-8")))
    assert not mentioned - known, f"unknown flags in {path.name}: {sorted(mentioned - known)}"
