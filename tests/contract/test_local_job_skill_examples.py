"""Execute the shipped local-workflow examples through the real CLI parser."""

import re
import shlex
from pathlib import Path

import pytest

from imagen_cli.application import skills_service
from imagen_cli.cli.entrypoint import _parse


def test_assets_local_job_examples_are_parseable():
    guide = (skills_service.BUNDLED / 'imagen-assets' / 'SKILL.md').read_text()
    commands = []
    for block in re.findall(r'```bash\n(.*?)```', guide, re.S):
        for line in block.splitlines():
            if line.startswith('imagen superres run ') or line.startswith('imagen job '):
                commands.append(line)
    for line in commands:
        argv = shlex.split(line)[1:]
        argv = [value.replace('ARTIFACT_ID', 'art_' + 'a' * 32)
                .replace('JOB_ID', 'job_' + 'a' * 32)
                .replace('USER_SELECTED_MODEL', 'film-degrainer') for value in argv]
        command, args, options = _parse(argv)
        if command == 'superres run':
            assert args['model'] == 'film-degrainer'
            assert args.get('accept') is not True
        if '--async' in argv:
            assert options.async_ and not options.wait
    assert len(commands) >= 5


@pytest.mark.parametrize('relative', [
    'README.md', 'README.zh-CN.md',
    'src/imagen_cli/bundled_skills/imagen-assets/SKILL.md',
])
def test_documented_compare_examples_are_parseable(relative):
    root = Path(__file__).resolve().parents[2]
    text = (root / relative).read_text()
    commands = [line for block in re.findall(r'```bash\n(.*?)```', text, re.S)
                for line in block.splitlines() if line.startswith('imagen compare ')]
    assert commands
    for line in commands:
        argv = [value.replace('art_a', 'art_' + 'a' * 32)
                .replace('art_b', 'art_' + 'b' * 32) for value in shlex.split(line)[1:]]
        command, args, _ = _parse(argv)
        assert command == 'compare'
        assert args['first'] == 'art_' + 'a' * 32
        assert args['second'] == 'art_' + 'b' * 32


def test_updated_reference_installs_with_job_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert skills_service.install_(['imagen-assets'])[0] == 'succeeded'
    installed = tmp_path / '.agents/skills/imagen-assets/SKILL.md'
    bundled = skills_service.BUNDLED / 'imagen-assets' / 'SKILL.md'
    assert installed.read_bytes() == bundled.read_bytes()
    row = next(row for row in skills_service.available() if row['name'] == 'imagen-assets')
    assert row['version'] == '1.4.0'
