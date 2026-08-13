import logging
import torch
from PIL import Image
from transformers import pipeline, Pipeline
from .schemas import BoundingBox, Detection

logger = logging.getLogger(__name__)

class YolosDetectionEngine:
    """
    Owns EVERYTHING infra-specific: device selection, loading, warmup,
    and translating raw HF output into our own Detection schema.
    Nothing outside this file needs to know it's HuggingFace/torch at all.
    """
    def __init__(self, model_name: str, device_preference: str = "auto") -> None:
        self._model_name = model_name
        self._device = self._resolve_device(device_preference)
        self._pipeline: Pipeline | None = None  # set explicitly by load()

    @staticmethod
    def _resolve_device(preference: str) -> int | str:
        if preference != "auto":
            return preference
        if torch.cuda.is_available():
            return 0
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def load(self) -> None:
        # Explicit load step, called once from lifespan — not at import
        # time, not on first request. Makes startup cost visible and
        # gives you a single place to add retry/timeout/warmup logic.
        logger.info("Loading %s on device=%s", self._model_name, self._device)
        self._pipeline = pipeline(
            task="object-detection", model=self._model_name, device=self._device
        )

    @property
    def is_ready(self) -> bool:
        return self._pipeline is not None

    def predict(self, image: Image.Image, threshold: float) -> list[Detection]:
        if not self.is_ready:
            raise RuntimeError("predict() called before load()")
        with torch.inference_mode():
            raw = self._pipeline(image)
        return [
            Detection(
                label=r["label"],
                score=float(r["score"]),
                box=BoundingBox(**{k: int(v) for k, v in r["box"].items()}),
            )
            for r in raw
            if r["score"] >= threshold
        ]