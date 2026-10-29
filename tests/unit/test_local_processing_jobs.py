import io

import pytest
from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.options import SharedOptions
from imagen_cli.execution.worker import Worker, _execute_pending


@pytest.fixture
def local(tmp_path):
    with Container(workspace=str(tmp_path / 'workspace')) as container:
        sessions = SessionService(container.repositories, container.artifact_store)
        session = sessions.create_session('local')
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
        source = 'art_' + 'a' * 32
        container.artifact_store.stage(source, buffer.getvalue())
        container.artifact_store.publish(source)
        container.repositories.insert('artifact_links', {
            'session_id': session.id, 'artifact_id': source, 'role': 'output',
            'created_at': '2026-10-29T00:00:00Z',
        })
        yield container, session, source


def test_blocking_transform_persists_job_and_progress(local):
    container, session, source = local
    status, data = container.execute('transform', {'artifact': source, 'op': 'resize:scale=2'})
    assert status == 'succeeded'
    assert data['artifact']['width'] == 16
    job = data.get('job', {})
    assert job.get('state') == 'succeeded'
    assert job['remote_state'] == 'not_submitted'
    assert job['progress']['stage'] == 'completed'
    assert container.repositories.get('sessions', session.id)['current_turn_id'] is None
    observed = container.execute('job get', {'job_id': job['id']})[1]
    assert [row['id'] for row in observed['artifacts']] == [data['artifact']['id']]


def test_async_needs_running_worker_without_creating_job(local):
    container, _, source = local
    container.options = SharedOptions(async_=True)
    status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    assert status == 'conflict'
    assert 'worker start' in str(data)
    assert container.repositories.list('jobs') == []


def test_worker_runs_local_queued_job_once(local):
    container, _, source = local
    worker = Worker(container.repositories)
    worker.start()
    container.options = SharedOptions(async_=True)
    status, data = container.execute('transform', {'artifact': source, 'op': 'resize:scale=2'})
    assert status == 'accepted'
    assert data['job']['state'] == 'queued'
    assert len(container.repositories.list('artifacts')) == 1
    _execute_pending(container, worker)
    job = container.repositories.get('jobs', data['job']['id'])
    assert job['state'] == 'succeeded'
    assert job['remote_state'] == 'not_submitted'
    assert len(container.execute('job get', {'job_id': job['id']})[1]['artifacts']) == 1
    _execute_pending(container, worker)
    assert len(container.repositories.list('artifacts')) == 2
    worker.stop()


def test_async_model_route_saves_output_and_tile_progress(local, monkeypatch):
    import numpy as np

    from imagen_cli.application import postprocess_worker, superres

    container, _, source = local
    worker = Worker(container.repositories)
    worker.start()
    container.options = SharedOptions(async_=True)

    def offline_model(path, pipeline, *, progress=None, **kwargs):
        params = pipeline[0]['params']
        assert params['model'] == 'realesrgan-x4plus'
        with Image.open(path) as image:
            output, _ = superres.upscale(
                image, model=params['model'], tile=params['tile'], overlap=params['overlap'],
                runner=lambda tile: np.repeat(np.repeat(tile, 4, axis=0), 4, axis=1),
                progress=progress,
            )
        buffer = io.BytesIO()
        output.save(buffer, format='PNG')
        return buffer.getvalue(), {'verification': 'offline_runner'}

    monkeypatch.setattr(postprocess_worker, 'execute', offline_model)
    status, data = container.execute('superres run', {
        'artifact': source, 'model': 'realesrgan-x4plus', 'tile': 4, 'overlap': 0, 'accept': True,
    })
    assert status == 'accepted'
    _execute_pending(container, worker)
    observed = container.execute('job get', {'job_id': data['job']['id']})[1]
    assert observed['job']['state'] == 'succeeded'
    assert observed['artifacts'][0]['width'] == 32
    events = container.repositories.list('events', job_id=data['job']['id'])
    assert any(event['event_type'] == 'progress' and event['safe_payload'].get('unit') == 'tiles'
               for event in events)
    worker.stop()


def test_queued_cancel_does_not_compute(local):
    container, _, source = local
    worker = Worker(container.repositories)
    worker.start()
    container.options = SharedOptions(async_=True)
    _, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    job_id = data['job']['id']
    status, canceled = container.execute('job cancel', {'job_id': job_id, 'scope': 'local'})
    assert status == 'succeeded'
    assert canceled['job']['state'] == 'canceled'
    _execute_pending(container, worker)
    assert len(container.repositories.list('artifacts')) == 1
    worker.stop()


def test_source_drift_fails_before_processing(local):
    container, _, source = local
    worker = Worker(container.repositories)
    worker.start()
    container.options = SharedOptions(async_=True)
    _, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    container.artifact_store.get(source).write_bytes(b'changed')
    _execute_pending(container, worker)
    assert container.repositories.get('jobs', data['job']['id'])['state'] == 'failed'
    assert len(container.repositories.list('artifacts')) == 1
    worker.stop()


def test_publication_failure_is_durable_and_reconcilable(local, monkeypatch):
    container, _, source = local
    original = container.artifact_store.publish

    def fail(identity):
        raise OSError('disk full')

    monkeypatch.setattr(container.artifact_store, 'publish', fail)
    status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    assert status == 'save_failed'
    assert data['job']['local_save_state'] == 'failed'
    staged = container.repositories.list('artifacts', producer_job_id=data['job']['id'])
    assert len(staged) == 1
    assert staged[0]['save_state'] == 'staging'
    monkeypatch.setattr(container.artifact_store, 'publish', original)
    status, recovered = container.execute('job reconcile', {'job_id': data['job']['id']})
    assert status == 'succeeded'
    assert len(recovered['artifacts']) == 1
    assert recovered['job']['state'] == 'succeeded'
    assert recovered['job']['actual_count'] == 1
    assert recovered['job']['local_save_state'] == 'saved'
    turn = container.repositories.get('turns', recovered['job']['turn_id'])
    assert turn['creation_state'] == 'succeeded'


def test_crash_before_publication_keeps_staging_record(local):
    import subprocess
    import sys

    container, _, source = local
    script = '''
import os, sys
from imagen_cli.application.container import Container
with Container(workspace=sys.argv[1]) as container:
    def crash(identity):
        os._exit(91)
    container.artifact_store.publish = crash
    container.execute('transform', {'artifact': sys.argv[2], 'op': 'invert'})
'''
    done = subprocess.run([sys.executable, '-c', script,
                           str(container.resolved_workspace.root), source], timeout=15)
    assert done.returncode == 91
    job = container.repositories.list('jobs')[-1]
    artifacts = container.repositories.list('artifacts', producer_job_id=job['id'])
    assert len(artifacts) == 1
    assert artifacts[0]['save_state'] == 'staging'
    status, data = container.execute('job reconcile', {'job_id': job['id']})
    assert status == 'succeeded'
    assert data['job']['state'] == 'succeeded'


def test_lease_expiring_during_publication_does_not_publish(local, monkeypatch):
    from datetime import UTC, datetime, timedelta

    container, _, source = local
    now = datetime.now(UTC)
    clock = [now]
    worker = Worker(container.repositories, clock=lambda: clock[0], lease_seconds=2)
    worker.start()
    container.options = SharedOptions(async_=True)
    _, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    original = container.artifact_store.publish

    def expire(identity):
        result = original(identity)
        clock[0] += timedelta(seconds=5)
        return result

    monkeypatch.setattr(container.artifact_store, 'publish', expire)
    _execute_pending(container, worker)
    output = container.repositories.list('artifacts', producer_job_id=data['job']['id'])
    assert len(output) == 1
    assert output[0]['save_state'] == 'staging'
    assert container.repositories.get('jobs', data['job']['id'])['state'] == 'running'
    worker.stop()


def test_reconcile_cannot_publish_a_running_foreground_job(local, monkeypatch):
    from imagen_cli.application import postprocess_service

    container, _, source = local
    original = postprocess_service.publish_result

    def stage(*args, **kwargs):
        artifact = original(*args, **kwargs)
        job_id = kwargs['job_id']
        status, _ = container.execute('job reconcile', {'job_id': job_id})
        assert status == 'conflict'
        assert container.repositories.get('artifacts', artifact['id'])['save_state'] == 'staging'
        return artifact

    monkeypatch.setattr(postprocess_service, 'publish_result', stage)
    status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    assert status == 'succeeded'
    assert data['job']['state'] == 'succeeded'


def test_tui_cancel_during_publication_never_publishes(local, monkeypatch):
    from threading import Event

    from imagen_cli.application import local_processing

    container, _, source = local
    canceled = Event()
    job = local_processing.submit(container.repositories, container.artifact_store,
                                  source, [{'op': 'invert'}])
    original = container.artifact_store.publish

    def cancel_publish(identity):
        result = original(identity)
        canceled.set()
        return result

    monkeypatch.setattr(container.artifact_store, 'publish', cancel_publish)
    status, data = local_processing.run(container.repositories, container.artifact_store,
                                       job['id'], cancelled=canceled)
    assert status == 'canceled'
    outputs = container.repositories.list('artifacts', producer_job_id=job['id'])
    assert outputs[0]['save_state'] == 'staging'
    status, _ = container.execute('job reconcile', {'job_id': job['id']})
    assert status == 'canceled'
    assert container.repositories.get('artifacts', outputs[0]['id'])['save_state'] == 'staging'


def test_source_replaced_at_compute_boundary_is_refused(local, monkeypatch):
    from imagen_cli.application import postprocess_worker

    container, _, source = local
    original = postprocess_worker.execute

    def replace_source(path, pipeline, **kwargs):
        Image.new('RGB', (8, 8), 'blue').save(path, format='PNG')
        return original(path, pipeline, **kwargs)

    monkeypatch.setattr(postprocess_worker, 'execute', replace_source)
    status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    assert status == 'failed'
    assert 'changed' in data['job']['error']['detail']
    assert len(container.repositories.list('artifacts')) == 1


def test_lost_lease_does_not_publish_or_override_new_owner(local, monkeypatch):
    from imagen_cli.application import postprocess_worker

    container, _, source = local
    worker = Worker(container.repositories)
    worker.start()
    container.options = SharedOptions(async_=True)
    _, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    job_id = data['job']['id']
    original = postprocess_worker.execute

    def compute(*args, **kwargs):
        result = original(*args, **kwargs)
        container.repositories.update('jobs', job_id,
                                      {'lease_token': 'new-owner', 'state': 'unknown'})
        return result

    monkeypatch.setattr(postprocess_worker, 'execute', compute)
    _execute_pending(container, worker)
    assert len(container.repositories.list('artifacts')) == 1
    assert container.repositories.get('jobs', job_id)['state'] == 'unknown'
    worker.stop()


def test_cancel_during_computation_never_publishes(local, monkeypatch):
    from imagen_cli.application import postprocess_worker

    container, _, source = local
    original = postprocess_worker.execute

    def compute(*args, **kwargs):
        data, metadata = original(*args, **kwargs)
        job = container.repositories.list('jobs')[-1]
        container.execute('job cancel', {'job_id': job['id'], 'scope': 'local'})
        return data, metadata

    monkeypatch.setattr(postprocess_worker, 'execute', compute)
    status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
    assert status == 'canceled'
    assert data['job']['state'] == 'canceled'
    assert len(container.repositories.list('artifacts')) == 1
