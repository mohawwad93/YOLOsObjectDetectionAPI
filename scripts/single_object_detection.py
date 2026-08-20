"""
Single Object Detection Script
===============================

Detects objects in a single image using a pre-trained YOLOS (You Only Look at
One Sequence) model from Hugging Face Transformers. This script loads a tiny
model suitable for educational purposes and CPU-friendly execution.

Key Concepts Demonstrated:
    - Loading pre-trained models from Hugging Face Hub via the pipeline API
    - Image pre-processing with Pillow
    - Object detection inference (forward pass)
    - Bounding-box visualization

Usage:
    # Basic detection on an image
    python scripts/single_object_detection.py path/to/image.jpg

    # Raise the confidence threshold (fewer, more-certain results)
    python scripts/single_object_detection.py path/to/image.jpg --threshold 0.7

    # Save the annotated image to a custom path
    python scripts/single_object_detection.py path/to/image.jpg --output annotated.jpg

Dependencies:
    transformers[torch]  — Hugging Face model hub + PyTorch backend
    torch                — Deep-learning framework
    Pillow               — Image loading / drawing
    accelerate           — Optimised inference (optional but recommended)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import pipeline

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The model identifier on Hugging Face Hub.
# YOLOS-tiny is a DETR-style transformer fine-tuned on COCO (80 classes).
# It's < 25 MB — quick to download and runs comfortably on CPU.
MODEL_NAME: str = "hustvl/yolos-tiny"

# Default confidence threshold. The model returns every detection it can think
# of; a threshold of 0.5 filters out low-confidence guesses.
DEFAULT_THRESHOLD: float = 0.5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helper: device selection
# ---------------------------------------------------------------------------


def _pick_device() -> int | str:
    """Return the best available torch device.

    Priority: CUDA GPU  →  Apple MPS  →  CPU (fallback).
    """
    if torch.cuda.is_available():
        logger.info("Using CUDA GPU: %s", torch.cuda.get_device_name(0))
        return 0  # first CUDA device
    if torch.backends.mps.is_available():
        logger.info("Using Apple Metal (MPS)")
        return "mps"
    logger.info("Using CPU")
    return "cpu"


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def load_object_detector(
    model_name: str = MODEL_NAME,
    device: int | str | None = None,
):
    """Create a Hugging Face object-detection pipeline.

    The ``pipeline`` API is the simplest way to use a pre-trained model: it
    bundles the model, tokenizer / image-processor, and post-processing into
    a single callable object.

    Args:
        model_name: Hugging Face Hub model ID.
        device:    Torch device override (``None`` = auto-select).

    Returns:
        A callable pipeline that accepts PIL images and returns a list of
        detection dicts.
    """
    if device is None:
        device = _pick_device()

    logger.info("Loading model  %s  ...", model_name)

    # The pipeline factory inspects the model tag ("object-detection") and
    # picks the correct architecture class, image processor, etc.
    detector = pipeline(
        task="object-detection",
        model=model_name,
        device=device,
    )

    logger.info("Model loaded successfully.")
    return detector


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def load_image(image_path: Path) -> Image.Image:
    """Open an image from disk and convert to RGB.

    The RGB conversion is important: JPEGs are usually RGB already, but PNGs
    may have an alpha channel that the model does not expect.
    """
    logger.info("Loading image: %s", image_path)
    image = Image.open(image_path).convert("RGB")
    logger.info("Image size: %d × %d px", *image.size)
    return image


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_objects(
    detector,
    image: Image.Image,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict]:
    """Run the detector on *image* and keep only high-confidence results.

    Args:
        detector:  A Hugging Face object-detection pipeline.
        image:     PIL image in RGB mode.
        threshold: Minimum confidence score (0.0 – 1.0).

    Returns:
        List of dicts, each with keys ``score``, ``label``, ``box``.
        ``box`` is a dict with keys ``xmin``, ``ymin``, ``xmax``, ``ymax``
        (pixel coordinates, absolute).
    """
    logger.info("Running detection (threshold ≥ %.2f) ...", threshold)

    # The pipeline returns **all** detections; we filter client-side.
    raw_results: list[dict] = detector(image)

    filtered = [d for d in raw_results if d["score"] >= threshold]

    # Sort by confidence so the most certain detections are drawn on top.
    filtered.sort(key=lambda d: d["score"], reverse=True)

    logger.info("Found %d objects above threshold.", len(filtered))
    for det in filtered:
        logger.info(
            "  %-20s  confidence: %.2f%%",
            det["label"],
            det["score"] * 100,
        )

    return filtered


# ---------------------------------------------------------------------------
# Visualisation
# ---------------------------------------------------------------------------


def draw_boxes(
    image: Image.Image,
    detections: list[dict],
) -> Image.Image:
    """Return a **copy** of *image* with bounding boxes overlaid.

    Each box is labelled with the class name and confidence percentage.
    """
    # Work on a copy so the original stays unmodified.
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    # Use a built-in Pillow font.  If you want something prettier you can
    # pass a .ttf path to ImageFont.truetype().
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except OSError:
        font = ImageFont.load_default()

    # Colour per label — assigns a consistent hue to each class name.
    colour_map: dict[str, tuple[int, int, int]] = {}

    for det in detections:
        label: str = det["label"]
        score: float = det["score"]
        box: dict = det["box"]  # {xmin, ymin, xmax, ymax}

        x1, y1 = box["xmin"], box["ymin"]
        x2, y2 = box["xmax"], box["ymax"]

        # Assign a colour or generate a new one.
        if label not in colour_map:
            colour_map[label] = _label_colour(len(colour_map))

        colour = colour_map[label]

        # --- Draw rectangle ---
        draw.rectangle([x1, y1, x2, y2], outline=colour, width=3)

        # --- Draw label background + text ---
        label_text = f"{label} ({score:.0%})"

        # Measure text so we can draw a filled rectangle behind it.
        text_bbox = draw.textbbox((x1, y1), label_text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

        # Position the label just above the box; clamp to image top edge.
        label_y = max(0, y1 - text_h - 4)

        draw.rectangle(
            [x1, label_y, x1 + text_w + 4, label_y + text_h + 4],
            fill=colour,
        )
        draw.text((x1 + 2, label_y + 2), label_text, fill="white", font=font)

    return annotated


def _label_colour(index: int) -> tuple[int, int, int]:
    """Return a distinct RGB colour for the *index*-th class label.

    Uses a simple golden-ratio hue wheel so adjacent indices are visually
    distinct.
    """
    import colorsys

    golden_ratio_conjugate = 0.618033988749895
    hue = (index * golden_ratio_conjugate) % 1.0
    # Convert HSV → RGB (0-255)
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
    return int(r * 255), int(g * 255), int(b * 255)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect objects in a single image using a Hugging Face model.",
    )

    parser.add_argument(
        "image",
        type=Path,
        help="Path to the input image (JPEG, PNG, etc.).",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Confidence threshold (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Where to save the annotated image.  Defaults to <input>_detected.<ext>.",
    )
    parser.add_argument(
        "--model",
        "-m",
        type=str,
        default=MODEL_NAME,
        help=f"Hugging Face model ID (default: {MODEL_NAME}).",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        choices=["cpu", "cuda", "mps", "auto"],
        default="auto",
        help="Device override (default: auto-detect).",
    )

    args = parser.parse_args()

    # --- Validate inputs ---------------------------------------------------
    if not args.image.exists():
        parser.error(f"Image not found: {args.image}")

    if not 0.0 <= args.threshold <= 1.0:
        parser.error("Threshold must be between 0.0 and 1.0.")

    # --- Determine output path ---------------------------------------------
    output_path: Path = args.output or (
        args.image.parent / f"{args.image.stem}_detected{args.image.suffix}"
    )

    # --- Run pipeline ------------------------------------------------------
    device = None if args.device == "auto" else args.device

    detector = load_object_detector(model_name=args.model, device=device)
    image = load_image(args.image)

    detections = detect_objects(
        detector=detector,
        image=image,
        threshold=args.threshold,
    )

    # --- Save result -------------------------------------------------------
    annotated = draw_boxes(image, detections)

    annotated.save(output_path)
    logger.info("Annotated image saved to: %s", output_path)

    # Print a summary table to stdout as well.
    if detections:
        print(f"\n📦  {len(detections)} object(s) detected:\n")
        print(f"{'Label':<20} {'Confidence':>10}")
        print("-" * 32)
        for d in detections:
            print(f"{d['label']:<20} {d['score']:>9.1%}")
    else:
        print("\n⚠️   No objects detected above the confidence threshold.")


if __name__ == "__main__":
    main()
