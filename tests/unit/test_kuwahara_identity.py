"""Historical implementations remain explicitly selectable across installations."""

import subprocess
import sys

from PIL import Image

from imagen_cli.application import media_ops, postprocess_service


def test_legacy_kuwahara_ids_bind_exact_implementations():
    from imagen_cli.application import filters_numpy

    assert media_ops.OPS["kuwahara_pillow_legacy"] is media_ops.kuwahara
    assert media_ops.OPS["kuwahara_numpy_legacy"] is filters_numpy.kuwahara
    assert media_ops.OPS["kuwahara"] is filters_numpy.kuwahara


def test_legacy_selection_is_recorded_in_provenance():
    step = postprocess_service.resolve_steps([{"op": "kuwahara_pillow_legacy"}])[0]
    assert step["implementation"].endswith("media_ops.kuwahara")
    assert media_ops.OPS[step["op"]](Image.new("RGB", (3, 3))).size == (3, 3)


def test_bare_kuwahara_never_falls_back_without_numpy():
    result = subprocess.run([sys.executable, "-c", '''
import sys
sys.modules["numpy"] = None
from PIL import Image
from imagen_cli.application import media_ops
try:
    media_ops.OPS["kuwahara"](Image.new("RGB", (3, 3)))
except ValueError as error:
    assert "NumPy" in str(error)
else:
    raise AssertionError("silently fell back to Pillow")
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
