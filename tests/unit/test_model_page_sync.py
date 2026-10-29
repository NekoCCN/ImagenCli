import asyncio
import io

import pytest
from PIL import Image

from imagen_cli.application.container import Container
from imagen_cli.application.session_service import SessionService
from imagen_cli.cli.tui.app import (
    Deps,
    ModelProcessScreen,
    WorkbenchApp,
    _control_fields,
    _convert_arguments,
)


def test_local_timeout_stays_in_arguments_and_is_numeric(tmp_path):
    assert 'timeout' not in {row[0] for row in _control_fields('transform')}
    assert 'timeout' not in {row[0] for row in _control_fields('superres run')}
    args = _convert_arguments('transform', {'artifact': 'art_' + 'a' * 32,
                                          'op': 'invert', 'timeout': '17.5'})
    assert args['timeout'] == 17.5
    with Container(workspace=str(tmp_path)) as container:
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
        args['artifact'] = source
        status, data = container.execute('transform', args)
        assert status == 'succeeded'
        turn = container.repositories.get('turns', data['job']['turn_id'])
        assert turn['parameters']['timeout'] == 17.5


@pytest.mark.parametrize('size', [(140, 42), (80, 24)])
def test_model_page_lists_real_models_without_transform_chrome(tmp_path, monkeypatch, size):
    from imagen_cli.application import superres

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(superres, 'available_providers', lambda: ['CPUExecutionProvider'])
    path = tmp_path / 'source.png'
    Image.new('RGBA', (120, 80), (220, 80, 60, 200)).save(path)

    async def exercise():
        app = WorkbenchApp(Deps(None, None))
        # Harness avoids initializing workspace views, but preserves shipped CSS/theme.
        from textual.app import App
        class Harness(App):
            CSS = WorkbenchApp.CSS
        app = Harness()
        async with app.run_test(size=size) as pilot:
            screen = ModelProcessScreen(Deps(None, None), 'art', 'ses', path)
            await app.push_screen(screen)
            await pilot.pause()
            assert not screen.query('#transform-categories')
            assert not screen.query('#transform-presets')
            assert screen.query_one('#processing-models').row_count == 8
            assert screen.query_one('#model-backend')
            assert screen.query_one('#model-timeout').value == '900'
            assert screen.query_one('#model-mode')
            assert '120' in str(screen.query_one('#model-source-size').content)
            run = screen.query_one('#model-run')
            assert run.region.bottom <= size[1]
            assert run.region.height == 1
            assert screen.query_one('#model-close').region.right <= size[0]
            assert screen.query_one('#processing-models').region.height >= 3
            assert screen.query_one('#model-preview').region.height >= 2
            title = screen.query_one('.panel-title')
            assert title.region.height == 1
            if size[0] == 80:
                assert screen.view.region.y >= screen.table.region.bottom

    asyncio.run(exercise())


def test_model_parameters_survive_foreground_execution(tmp_path, monkeypatch):
    import numpy as np

    from imagen_cli.application import postprocess_worker, superres

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    catalog = superres.catalog()
    for row in catalog['models']:
        row['downloaded'] = True
    monkeypatch.setattr(superres, 'catalog', lambda: catalog)

    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
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

            def compute(path, pipeline, *, progress=None, **kwargs):
                assert kwargs['timeout'] == 17.5
                params = pipeline[0]['params']
                assert params['model'] == 'realesr-animevideov3'
                assert params['tile'] == 4 and params['overlap'] == 0
                assert params['backend'] == 'cpu'
                with Image.open(path) as image:
                    result, _ = superres.upscale(
                        image, model=params['model'], tile=4, overlap=0,
                        runner=lambda tile: np.repeat(np.repeat(tile, 4, 0), 4, 1),
                        progress=progress,
                    )
                output = io.BytesIO()
                result.save(output, format='PNG')
                return output.getvalue(), {}

            monkeypatch.setattr(postprocess_worker, 'execute', compute)
            app = WorkbenchApp(Deps(container.repositories, container.artifact_store,
                                    execute=container.execute,
                                    workspace_root=container.resolved_workspace.root))
            async with app.run_test(size=(100, 35)) as pilot:
                screen = ModelProcessScreen(app.deps, source, session.id,
                                             container.artifact_store.get(source))
                await app.push_screen(screen)
                index = next(i for i, row in enumerate(screen.rows)
                             if row['model'] == 'realesr-animevideov3')
                screen.table.move_cursor(row=index)
                await pilot.pause()
                screen.tile.value, screen.overlap.value = '4', '0'
                screen.timeout.value, screen.backend.value = '17.5', 'cpu'
                screen.action_apply()
                await pilot.pause()
                await app.screen.dismiss(True)
                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
                jobs = container.repositories.list('jobs')
                assert len(jobs) == 1 and jobs[0]['state'] == 'succeeded'
                assert container.artifact_store.get(source).read_bytes() == buffer.getvalue()

    asyncio.run(exercise())


@pytest.mark.parametrize('screen_type', ['model', 'transform'])
def test_processing_progress_ignores_popped_screen(tmp_path, monkeypatch, screen_type):
    from textual.app import App

    from imagen_cli.cli.tui.app import TransformScreen

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8)).save(path)

    async def exercise():
        app = App()
        async with app.run_test() as pilot:
            cls = ModelProcessScreen if screen_type == 'model' else TransformScreen
            screen = cls(Deps(None, None), 'art', 'ses', path)
            await app.push_screen(screen)
            await screen.dismiss(False)
            await pilot.pause()
            screen._processing_progress({'stage': 'processing', 'completed': 1, 'total': 2})

    asyncio.run(exercise())


def test_model_worker_cancel_does_not_touch_removed_controls(tmp_path, monkeypatch):
    from threading import Event

    from imagen_cli.application import postprocess_worker

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    entered, release, finished = Event(), Event(), Event()

    def compute(path, pipeline, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            raise ValueError('postprocess cancelled')
        finally:
            finished.set()

    monkeypatch.setattr(postprocess_worker, 'execute', compute)

    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
            service = SessionService(container.repositories, container.artifact_store)
            session = service.create_session('a')
            buffer = io.BytesIO()
            Image.new('RGB', (8, 8)).save(buffer, format='PNG')
            source = 'art_aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa'
            container.artifact_store.stage(source, buffer.getvalue())
            container.artifact_store.publish(source)
            deps = Deps(container.repositories, container.artifact_store,
                        execute=container.execute, workspace_root=container.resolved_workspace.root)
            from textual.app import App
            app = App()
            async with app.run_test() as pilot:
                screen = ModelProcessScreen(deps, source, session.id,
                                            container.artifact_store.get(source))
                await app.push_screen(screen)
                screen._start({'artifact': source, 'session': session.id,
                               'model': 'film-degrainer', 'tile': 256, 'overlap': 16,
                               'accept': True, 'timeout': 900})
                try:
                    assert await asyncio.to_thread(entered.wait, 2)
                    await screen.remove_children()
                    for worker in list(app.workers):
                        worker.cancel()
                    await pilot.pause()
                finally:
                    release.set()
                    assert await asyncio.to_thread(finished.wait, 2)
                    await app.workers.wait_for_complete()
                assert not screen.running

    asyncio.run(exercise())


def test_model_download_can_outlive_closed_page(tmp_path, monkeypatch):
    from threading import Event

    from textual.app import App

    from imagen_cli.application import superres

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    started, release = Event(), Event()
    model = 'film-degrainer'
    weights = b'offline test weights'
    import hashlib
    monkeypatch.setitem(superres.MANIFEST[model], 'sha256', hashlib.sha256(weights).hexdigest())

    def download(url, target):
        started.set()
        assert release.wait(5), 'test did not release the download'
        target.write_bytes(weights)

    monkeypatch.setattr(superres, '_download', download)
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8)).save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        with Container(workspace=str(tmp_path / 'workspace')) as container:
            deps = Deps(container.repositories, container.artifact_store,
                        execute=container.execute,
                        workspace_root=container.resolved_workspace.root)
            app = Harness()
            async with app.run_test(size=(100, 35)) as pilot:
                screen = ModelProcessScreen(deps, 'art', 'ses', path)
                await app.push_screen(screen)
                screen.table.move_cursor(row=next(i for i, row in enumerate(screen.rows)
                                                 if row['model'] == model))
                await pilot.pause()
                screen.action_fetch()
                await pilot.pause()
                app.screen.dismiss(True)
                try:
                    assert await asyncio.to_thread(started.wait, 2)
                    await pilot.pause()
                    assert not screen.query_one('#model-close').disabled
                    assert str(screen.query_one('#model-close').label) in {'Close', '关闭'}
                    assert any(word in str(screen.status.content)
                               for word in ('continue', '下载继续'))
                    screen.action_cancel()
                    await pilot.pause()
                    assert app.screen is not screen
                finally:
                    release.set()
                    await app.workers.wait_for_complete()
                await pilot.pause()
                assert superres.weights_path(model).read_bytes() == weights
                assert not list(superres.weights_path(model).parent.glob('.imagen-download-*'))

    asyncio.run(exercise())


def test_fixed_model_switch_restores_editable_tile_defaults(tmp_path, monkeypatch):
    from textual.app import App

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8)).save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(100, 35)) as pilot:
            screen = ModelProcessScreen(Deps(None, None), 'art', 'ses', path)
            await app.push_screen(screen)
            fixed = next(i for i, row in enumerate(screen.rows)
                         if row['model'] == 'dejpg-srformer-light')
            screen.table.move_cursor(row=fixed)
            await pilot.pause()
            assert screen.tile.value == '64' and screen.tile.disabled
            normal = next(i for i, row in enumerate(screen.rows)
                          if row['model'] == 'realesr-animevideov3')
            screen.table.move_cursor(row=normal)
            await pilot.pause()
            assert not screen.tile.disabled
            assert screen.tile.value == '256' and screen.overlap.value == '16'

    asyncio.run(exercise())


def test_model_run_requires_explicit_license_confirmation(tmp_path, monkeypatch):
    from textual.app import App

    from imagen_cli.application import superres
    from imagen_cli.cli.tui.app import ConfirmScreen

    monkeypatch.setenv('IMAGEN_MODEL_DIR', str(tmp_path / 'models'))
    catalog = superres.catalog()
    for row in catalog['models']:
        row['downloaded'] = True
    monkeypatch.setattr(superres, 'catalog', lambda: catalog)
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8), 'red').save(path)

    class Harness(App):
        CSS = WorkbenchApp.CSS

    async def exercise():
        app = Harness()
        async with app.run_test(size=(100, 35)) as pilot:
            screen = ModelProcessScreen(Deps(None, None), 'art', 'ses', path)
            await app.push_screen(screen)
            screen.action_apply()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            assert screen.running is False
            app.screen.dismiss(False)
            await pilot.pause()
            assert app.screen is screen
            assert screen.running is False

    asyncio.run(exercise())
