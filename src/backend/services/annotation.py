from __future__ import annotations
from PIL import Image, ImageDraw, ImageFont
from ..ml.schemas import Detection

DEFAULT_FONT_SIZE = 16

def draw_boxes(image: Image.Image, detections: list[Detection]) -> Image.Image:
    """
    Renders domain Detection objects onto a copy of the image. Kept out
    of DetectionService because most callers (JSON /detect, the WS
    stream) never want a rendered image and shouldn't pay for PIL drawing
    calls or even import this module.
    """
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    font = _load_font()

    for i, det in enumerate(detections):
        color = ((i * 50) % 255, (i * 80) % 255, (i * 110) % 255)
        box = det.box
        draw.rectangle([box.xmin, box.ymin, box.xmax, box.ymax], outline=color, width=3)

        label_text = f"{det.label} ({det.score:.0%})"
        text_bbox = draw.textbbox((box.xmin, box.ymin), label_text, font=font)
        text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        label_y = max(0, box.ymin - text_h - 4)

        draw.rectangle([box.xmin, label_y, box.xmin + text_w + 4, label_y + text_h + 4], fill=color)
        draw.text((box.xmin + 2, label_y + 2), label_text, fill="white", font=font)

    return annotated

def _load_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", DEFAULT_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()