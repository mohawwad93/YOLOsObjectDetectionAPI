from enum import StrEnum
from typing import Protocol, runtime_checkable

from PIL import Image

from .schemas import Detection


class EngineStatus(StrEnum):
    """
    Finer-grained than a boolean on purpose. is_ready alone can't tell a
    caller 'still loading, give it a few more seconds' apart from
    'failed permanently, this pod will never become healthy' — and an
    orchestrator needs to treat those two cases very differently.
    """

    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


@runtime_checkable
class DetectionEngine(Protocol):
    """
    Every inference backend must satisfy this. The service and API layers
    program against THIS, never against transformers.Pipeline directly.
    Swapping frameworks later means writing a new class that satisfies
    this Protocol — nothing above this layer has to change.
    """

    def predict(self, image: Image.Image, threshold: float) -> list[Detection]: ...

    @property
    def status(self) -> EngineStatus: ...

    @property
    def is_ready(self) -> bool:
        """Convenience for callers that only care about the binary
        question — equivalent to status == EngineStatus.READY. The
        feature routes (get_engine / get_engine_ws) keep using this
        unchanged; only the health routes need the richer status."""
        ...
