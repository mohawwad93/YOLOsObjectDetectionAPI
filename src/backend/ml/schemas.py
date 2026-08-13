from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    """Domain value object. Pixel-space coordinates, always integers."""
    xmin: int
    ymin: int
    xmax: int
    ymax: int

class Detection(BaseModel):
    """
    Returned by any DetectionEngine. Deliberately minimal and framework-
    agnostic — it has no idea whether it'll end up as JSON, a WebSocket
    frame, or pixels drawn on an image. That indifference is the point.
    """
    label: str
    score: float = Field(ge=0.0, le=1.0)
    box: BoundingBox