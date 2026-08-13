from pydantic import BaseModel
from ..ml.schemas import Detection

class BoundingBoxOut(BaseModel):
    xmin: int
    ymin: int
    xmax: int
    ymax: int

class DetectionOut(BaseModel):
    label: str
    confidence: float   # renamed from `score` — this rename is the whole point of this file existing
    box: BoundingBoxOut

    @classmethod
    def from_domain(cls, detection: Detection) -> "DetectionOut":
        return cls(
            label=detection.label,
            confidence=detection.score,
            box=BoundingBoxOut(**detection.box.model_dump()),
        )

class DetectionResponse(BaseModel):
    count: int
    detections: list[DetectionOut]

    @classmethod
    def from_domain(cls, detections: list[Detection]) -> "DetectionResponse":
        return cls(count=len(detections), detections=[DetectionOut.from_domain(d) for d in detections])