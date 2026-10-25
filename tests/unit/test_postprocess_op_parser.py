"""Operation strings preserve comma-containing values without losing typed options."""

import pytest
from PIL import Image

from imagen_cli.application import media_ops
from imagen_cli.cli.commands.postprocess import parse_op


@pytest.mark.parametrize(("text", "expected"), [
    ("periodic_notch:frequencies=0.125,0;0,0.25,width=0.02,strength=0.5",
     {"frequencies": "0.125,0;0,0.25", "width": 0.02, "strength": 0.5}),
    ("pad:width=20,height=30,color=12,34,56",
     {"width": 20, "height": 30, "color": "12,34,56"}),
    ('chroma_transfer:reference="folder/a,b=c.png",strength=0.5',
     {"reference": "folder/a,b=c.png", "strength": 0.5}),
    ('periodic_notch:frequencies="0.125,0",strength=0.5',
     {"frequencies": "0.125,0", "strength": 0.5}),
])
def test_comma_values_are_preserved(text, expected):
    assert parse_op(text)["params"] == expected


def test_notch_operation_string_executes():
    step = parse_op("periodic_notch:frequencies=0.125,0,width=0.02")
    image = Image.new("RGB", (16, 16), (100, 120, 140))
    result = media_ops.OPS[step["op"]](image, **step["params"])
    assert result.tobytes() == image.tobytes()


@pytest.mark.parametrize("text", [
    "blur:radius=2,radius=3", 'chroma_transfer:reference="unfinished',
    "blur:=2", "blur:radius=2,", "blur:radius=2,broken",
])
def test_malformed_or_duplicate_parameters_are_rejected(text):
    with pytest.raises(ValueError):
        parse_op(text)
