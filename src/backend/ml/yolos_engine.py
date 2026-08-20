import logging

import torch
from PIL import Image
from transformers import Pipeline, pipeline

from .base import EngineStatus
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
        self._status: EngineStatus = EngineStatus.NOT_LOADED

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
        """
        Device resolution + weight loading only. Deliberately does NOT
        flip status to READY — that's warm_up()'s job — so a caller can
        observe 'weights loaded, warming up' as a distinct state from
        'fully ready', which is exactly what lifespan.py does below.
        """
        self._status = EngineStatus.LOADING
        logger.info("Loading %s on device=%s", self._model_name, self._device)
        try:
            self._pipeline = pipeline(
                task="object-detection", model=self._model_name, device=self._device
            )
        except Exception:
            self._status = EngineStatus.FAILED
            # Fail fast: propagate rather than swallow. A retry-with-backoff
            # for transient network errors belongs INSIDE this try block
            # (a few attempts before giving up) — but once attempts are
            # exhausted, re-raising is deliberate. Swallowing it here would
            # leave status stuck at FAILED with nothing ever finding out.
            logger.exception("Engine failed to load")
            raise

    def warm_up(self) -> None:
        """
        One throwaway inference before signaling READY — absorbs
        first-call costs (lazy CUDA context, JIT tracing) that would
        otherwise land on the first real user request after a deploy.
        """
        if self._pipeline is None:
            raise RuntimeError("warm_up() called before load()")
        try:
            self._pipeline(Image.new("RGB", (32, 32)))
        except Exception:
            self._status = EngineStatus.FAILED
            logger.exception("Engine warm-up failed")
            raise
        self._status = EngineStatus.READY

    @property
    def status(self) -> EngineStatus:
        return self._status

    @property
    def is_ready(self) -> bool:
        return self._status == EngineStatus.READY

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
