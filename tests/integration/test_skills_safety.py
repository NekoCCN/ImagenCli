import json
from pathlib import Path

from imagen_cli.application import skills_service


def test_nested_manifest_is_user_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_service.install_(['imagen'])
    target = tmp_path / '.claude/skills/imagen'
    (target / 'references/.imagen-install.json').write_text('user content')
    assert skills_service.uninstall_(['imagen'])[0] == 'conflict'
    assert (target / 'references/.imagen-install.json').exists()


def test_invalid_manifest_cannot_claim_user_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / '.claude/skills/imagen'
    target.mkdir(parents=True)
    (target / '.imagen-install.json').write_text(json.dumps({
        'schema_version': 1, 'skill': 'imagen', 'files': {'bad': 17},
    }))
    (target / 'user.txt').write_text('preserve')
    assert skills_service.install_(['imagen'], force=True)[0] == 'conflict'
    assert (target / 'user.txt').read_text() == 'preserve'


def test_empty_or_invalid_digest_manifest_is_unmanaged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / '.claude/skills/imagen'
    target.mkdir(parents=True)
    for files in ({}, {'SKILL.md': 'z' * 64}):
        (target / '.imagen-install.json').write_text(json.dumps({
            'schema_version': 1, 'skill': 'imagen', 'files': files,
        }))
        assert skills_service.install_(['imagen'], force=True)[0] == 'conflict'


def test_shared_agent_states_are_not_dropped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_service.install_(['imagen'], agents=['codex', 'cline'])
    _, data = skills_service.list_(agents=['codex', 'cline'], scope='project')
    row = next(row for row in data['skills'] if row['name'] == 'imagen')
    assert row['installed']['project'] == {'codex': True, 'cline': True}
    _, data = skills_service.uninstall_(['imagen'], agents=['codex', 'cline'])
    assert data['removed'][0]['agents'] == ['codex', 'cline']


def test_partial_uninstall_returns_completed_targets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_service.install_(['imagen'], agents=['claude-code', 'codex'])
    original = skills_service.shutil.rmtree
    def fail_second(path, *args, **kwargs):
        if Path(path) == tmp_path / '.agents/skills/imagen':
            raise PermissionError('locked')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(skills_service.shutil, 'rmtree', fail_second)
    status, data = skills_service.uninstall_(['imagen'], agents=['claude-code', 'codex'])
    assert status == 'internal_error'
    assert data['removed'][0]['agent'] == 'claude-code'
    assert data['failed'][0]['agent'] == 'codex'


def test_failed_rollback_preserves_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_service.install_(['imagen'])
    target = tmp_path / '.claude/skills/imagen'
    (target / 'user.txt').write_text('preserve')
    original = Path.rename
    def fail_publish_and_restore(path, destination):
        if Path(destination) == target and path != target:
            raise PermissionError('locked')
        return original(path, destination)
    monkeypatch.setattr(Path, 'rename', fail_publish_and_restore)
    status, data = skills_service.install_(['imagen'], force=True)
    assert status == 'internal_error'
    backups = list((tmp_path / '.claude/skills').rglob('user.txt'))
    assert len(backups) == 1 and backups[0].read_text() == 'preserve'
    assert Path(data['failed'][0]['backup']).is_dir()
