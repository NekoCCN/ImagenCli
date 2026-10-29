import io
import json

from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.entrypoint import main


def seed(tmp_path):
    root = tmp_path / 'workspace'
    with Container(workspace=str(root)) as container:
        service = SessionService(container.repositories, container.artifact_store)
        session = service.create_session('a')
        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
        source = 'art_aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa'
        container.artifact_store.stage(source, buffer.getvalue())
        container.artifact_store.publish(source)
        container.repositories.insert('artifact_links', {
            'session_id': session.id, 'artifact_id': source, 'role': 'output',
            'created_at': '2026-10-29T00:00:00Z',
        })
    return root, source


def test_jsonl_processing_has_progress_and_one_terminal(tmp_path):
    root, source = seed(tmp_path)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(['--workspace', str(root), 'transform', source, '--op', 'invert',
                 '--wait', '--output', 'jsonl'], stdout=stdout, stderr=stderr)
    assert code == 0
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events[0]['event'] == 'started'
    assert events[-1]['event'] == 'terminal'
    assert sum(event['event'] == 'terminal' for event in events) == 1
    assert [event['seq'] for event in events] == list(range(1, len(events) + 1))
    assert any(event['event'] == 'progress' and event['data']['stage'] == 'processing'
               for event in events)
    assert stderr.getvalue() == ''


def test_local_async_result_matches_discovery_schema(tmp_path):
    from jsonschema import validate

    from imagen_cli.cli.options import SharedOptions
    from imagen_cli.cli.registry import discover
    from imagen_cli.cli.renderers import result
    from imagen_cli.execution.worker import Worker

    root, source = seed(tmp_path)
    with Container(workspace=str(root), options=SharedOptions(async_=True)) as container:
        worker = Worker(container.repositories)
        worker.start()
        status, data = container.execute('transform', {'artifact': source, 'op': 'invert'})
        assert status == 'accepted'
        validate(result(status, data), discover('transform')['output_schema'])
        worker.stop()


def test_json_processing_keeps_one_result(tmp_path):
    root, source = seed(tmp_path)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert main(['--workspace', str(root), 'transform', source, '--op', 'invert',
                 '--output', 'json'], stdout=stdout, stderr=stderr) == 0
    assert json.loads(stdout.getvalue())['status'] == 'succeeded'
    assert len(stdout.getvalue().splitlines()) == 1
    assert stderr.getvalue() == ''


def test_human_progress_uses_stderr_only():
    from imagen_cli.cli.progress import TerminalProgress
    from imagen_cli.cli.renderers import Renderer

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    stdout, stderr = Terminal(), Terminal()
    with TerminalProgress(Renderer('human', stdout, stderr)) as progress:
        progress({'stage': 'processing', 'completed': 3, 'total': 8, 'unit': 'tiles'})
    assert stdout.getvalue() == ''
    assert '3/8' in stderr.getvalue()


def test_tui_observes_new_external_artifacts(tmp_path):
    import asyncio

    from imagen_cli.application.postprocess_service import publish_result
    from imagen_cli.cli.tui.app import Deps, WorkbenchApp

    root, source = seed(tmp_path)

    async def exercise():
        with Container(workspace=str(root)) as container:
            session = container.repositories.list('sessions')[0]['id']
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
            async with app.run_test(size=(100, 35)) as pilot:
                assert len(app.film_items) == 1
                data = container.artifact_store.get(source).read_bytes()
                result = publish_result(container.repositories, container.artifact_store, source,
                                        [{'op': 'invert'}], data, {}, session_id=session)
                app._poll_jobs()
                await pilot.pause()
                assert result['id'] in {item['id'] for item in app.film_items}

    asyncio.run(exercise())


def test_idle_tui_poll_never_decodes_images_again(tmp_path, monkeypatch):
    import asyncio

    from imagen_cli.cli.tui.app import Deps, WorkbenchApp
    from imagen_cli.storage import artifacts

    root, source = seed(tmp_path)

    async def exercise():
        with Container(workspace=str(root)) as container:
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store))
            async with app.run_test(size=(100, 35)):
                app._poll_jobs()
                def forbidden(*args, **kwargs):
                    raise AssertionError('idle tick must not decode')
                monkeypatch.setattr(artifacts, 'inspect_media', forbidden)
                app._poll_jobs()
                app._poll_jobs()
                assert app.film_items[0]['id'] == source

    asyncio.run(exercise())


def test_tui_processing_creates_a_queryable_job(tmp_path):
    import asyncio

    from imagen_cli.cli.tui.app import Deps, TransformScreen, WorkbenchApp

    root, source = seed(tmp_path)

    async def exercise():
        with Container(workspace=str(root)) as container:
            session = container.repositories.list('sessions')[0]['id']
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=container.execute, workspace_root=root))
            async with app.run_test(size=(100, 35)) as pilot:
                screen = TransformScreen(app.deps, source, session,
                                         container.artifact_store.get(source))
                await app.push_screen(screen)
                screen._run_pipeline([{'op': 'invert'}])
                await app.workers.wait_for_complete()
                await pilot.pause()
                jobs = container.repositories.list('jobs')
                assert len(jobs) == 1
                assert jobs[0]['state'] == 'succeeded'
                observed = container.execute('job get', {'job_id': jobs[0]['id']})[1]
                assert len(observed['artifacts']) == 1

    asyncio.run(exercise())


def test_tui_transform_accepts_real_step_progress(tmp_path):
    import asyncio

    from textual.app import App
    from textual.widgets import ProgressBar

    from imagen_cli.cli.tui.app import Deps, TransformScreen, WorkbenchApp

    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8), 'red').save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(100, 35)):
            screen = TransformScreen(Deps(None, None), 'art', 'ses', path)
            await app.push_screen(screen)
            screen._processing_progress({'stage': 'processing', 'completed': 3,
                                         'total': 8, 'unit': 'tiles', 'operation': 'superres'})
            bar = screen.query_one('#processing-progress', ProgressBar)
            assert bar.total == 8
            assert bar.progress == 3
            assert '3/8' in str(screen.status.content)

    asyncio.run(exercise())
