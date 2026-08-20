from PIL import Image

from backend.ml.schemas import BoundingBox, Detection
from backend.services.annotation import draw_boxes


def test_returns_a_new_image_of_the_same_size():
    """The annotated image must be a distinct copy — callers should never
    find their original image mutated as a side effect."""
    original = Image.new("RGB", (300, 200), color="white")
    detections = [
        Detection(
            label="cat",
            score=0.9,
            box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100),
        )
    ]

    annotated = draw_boxes(original, detections)

    assert annotated is not original
    assert annotated.size == original.size
    assert original.getpixel((50, 50)) == (255, 255, 255)  # original untouched


def test_something_is_actually_drawn():
    """A coarse but meaningful assertion — pixels at the box border must
    differ from background — without coupling the test to exact colors
    or font rendering, which vary across environments."""
    original = Image.new("RGB", (300, 200), color="white")
    detections = [
        Detection(
            label="cat",
            score=0.9,
            box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100),
        )
    ]

    annotated = draw_boxes(original, detections)
    assert annotated.getpixel((10, 50)) != (255, 255, 255)


def test_handles_zero_detections_without_error():
    original = Image.new("RGB", (300, 200), color="white")
    annotated = draw_boxes(original, [])
    assert annotated.size == original.size
