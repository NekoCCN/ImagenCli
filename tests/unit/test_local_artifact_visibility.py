import io

import pytest
from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.application.postprocess_service import apply
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.commands.session import show
from imagen_cli.cli.human import render
from imagen_cli.cli.tui.app import Deps, WorkbenchApp


@pytest.fixture
def mixed(tmp_path):
    with Container(workspace=str(tmp_path / 'workspace')) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session('mixed')
        other = service.create_session('other')
        turn = service.create_turn(session.id, instruction='offline fixture', operation='generate',
                                   resolved_profile_snapshot={}, model='fixture', parameters={})
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), (180, 90, 40)).save(buffer, format='PNG')
        source = service.save_artifact(turn.id, buffer.getvalue(),
                                       origin_manifest={'kind': 'offline_fixture'})
        output = apply(container.repositories, container.artifact_store, source.id,
                       [{'op': 'resize', 'params': {'scale': 2}}])
        yield container, service, session, other, source, output


def test_tui_keeps_local_results_when_session_has_generated_output(mixed):
    container, _, session, _, source, output = mixed
    app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
    app.current = session.id
    items = app._load_artifacts()
    assert [item['id'] for item in items] == [source.id, output['id']]
    assert items[-1]['operation'] == 'transform'


def test_cli_session_show_and_human_render_include_local_results(mixed):
    _, service, session, _, source, output = mixed
    status, data = show(service, session.id)
    assert status == 'succeeded'
    assert [row['id'] for row in data.get('artifacts', [])] == [source.id, output['id']]
    assert output['id'] in render({'ok': True, 'status': status, 'data': data}, columns=160)


def test_missing_file_is_unavailable_in_cli_and_skipped_by_tui(mixed):
    container, service, session, _, source, output = mixed
    container.artifact_store.get(output['id']).unlink()
    status, data = show(service, session.id)
    assert status == 'succeeded'
    missing = next(row for row in data['artifacts'] if row['id'] == output['id'])
    assert missing['available'] is False
    app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
    assert [item['id'] for item in app._load_artifacts(session.id)] == [source.id]


def test_outputs_are_deduplicated_and_isolated_by_session(mixed):
    container, _, session, other, source, output = mixed
    container.repositories.insert('artifact_links', {
        'session_id': session.id, 'artifact_id': source.id, 'role': 'output',
        'created_at': '2026-10-29T00:00:00Z',
    })
    app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
    assert [row['id'] for row in app._load_artifacts(session.id)] == [source.id, output['id']]
    assert app._load_artifacts(other.id) == []
