# services/detection_service.py
import io

from PIL import Image

from ..ml.base import DetectionEngine
from ..ml.schemas import Detection


class InvalidImageError(Exception):
    """Domain error: input bytes aren't a decodable image.
    The API layer maps this to a 422 — the service doesn't know HTTP exists."""


class DetectionService:
    """Core workflow, shared by both REST and WebSocket callers."""

    def __init__(self, engine: DetectionEngine):
        self._engine = engine

    def detect_from_bytes(
        self, image_bytes: bytes, threshold: float
    ) -> list[Detection]:
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as e:
            raise InvalidImageError("Could not decode image payload") from e

        detections = self._engine.predict(image, threshold)
        return sorted(detections, key=lambda d: d.score, reverse=True)
