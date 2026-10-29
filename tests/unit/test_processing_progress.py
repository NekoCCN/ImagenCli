import numpy as np
from PIL import Image

from imagen_cli.application import postprocess_service, postprocess_worker, superres


def test_render_reports_each_step_and_encoding(tmp_path):
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8), 'red').save(path)
    events = []
    data, _ = postprocess_service.render(path, [{'op': 'invert'}, {'op': 'grayscale'}],
                                         progress=events.append)
    assert data
    steps = [event for event in events if event.get('unit') == 'steps']
    assert [(event['completed'], event['total']) for event in steps] == [(0, 2), (1, 2), (2, 2)]
    assert events[-1]['stage'] == 'encoding'


def test_real_subprocess_forwards_progress(tmp_path):
    path = tmp_path / 'source.png'
    Image.new('RGB', (8, 8), 'red').save(path)
    events = []
    data, _ = postprocess_worker.execute(path, [{'op': 'invert'}], progress=events.append)
    assert data
    assert any(event.get('unit') == 'steps' and event['completed'] == 1 for event in events)
    assert any(event['stage'] == 'encoding' for event in events)
    assert all('path' not in event for event in events)


def test_tiles_report_only_successfully_written_cores():
    events = []
    array = np.zeros((7, 9, 3), dtype=np.uint8)
    output = superres.tiled_upscale(array, lambda tile: np.repeat(np.repeat(tile, 2, 0), 2, 1),
                                    scale=2, tile=4, overlap=1, progress=events.append)
    assert output.shape == (14, 18, 3)
    assert [(event['completed'], event['total']) for event in events] == [(i, 6) for i in range(7)]
    assert all(event['unit'] == 'tiles' for event in events)


def test_bad_tile_never_reports_completion():
    import pytest

    events = []
    with pytest.raises(ValueError, match='shape'):
        superres.tiled_upscale(np.zeros((4, 4, 3)), lambda tile: tile, scale=2,
                               tile=4, overlap=0, progress=events.append)
    assert [event['completed'] for event in events] == [0]
