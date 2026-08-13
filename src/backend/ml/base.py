from typing import Protocol, runtime_checkable
from PIL import Image
from .schemas import Detection

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
    def is_ready(self) -> bool: ...