from __future__ import annotations
import logging
import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import pipeline, Pipeline

MODEL_NAME: str = "hustvl/yolos-tiny"
DEFAULT_THRESHOLD: float = 0.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)


def _pick_device() -> int | str:
    if torch.cuda.is_available():
        return 0
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_object_detector(model_name: str = MODEL_NAME) -> Pipeline:
    device = _pick_device()
    logger.info("Loading model %s on %s...", model_name, device)
    return pipeline(task="object-detection", model=model_name, device=device)


def detect_objects(detector, image: Image.Image, threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    raw_results: list[dict] = detector(image)
    filtered = [d for d in raw_results if d["score"] >= threshold]
    filtered.sort(key=lambda d: d["score"], reverse=True)
    return filtered


def draw_boxes(image: Image.Image, detections: list[dict]) -> Image.Image:
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except OSError:
        font = ImageFont.load_default()

    # Fast color mapping by index
    for i, det in enumerate(detections):
        box = det["box"]
        x1, y1, x2, y2 = box["xmin"], box["ymin"], box["xmax"], box["ymax"]

        # Unique color based on detection index
        color = ((i * 50) % 255, (i * 80) % 255, (i * 110) % 255)

        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
        label_text = f"{det['label']} ({det['score']:.0%})"
        text_bbox = draw.textbbox((x1, y1), label_text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        label_y = max(0, y1 - text_h - 4)

        draw.rectangle([x1, label_y, x1 + text_w + 4, label_y + text_h + 4], fill=color)
        draw.text((x1 + 2, label_y + 2), label_text, fill="white", font=font)

    return annotated
