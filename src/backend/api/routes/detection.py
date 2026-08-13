import io
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image

from ...dependencies import get_detection_service
from ...services.detection_service import DetectionService, InvalidImageError
from ...services.annotation import draw_boxes
from ..schemas import DetectionResponse

router = APIRouter()

@router.post("/detect", response_model=DetectionResponse, summary="Detect objects, returns JSON")
async def detect(
    file: UploadFile = File(...),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported format")
    contents = await file.read()
    try:
        detections = service.detect_from_bytes(contents, threshold)
    except InvalidImageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return DetectionResponse.from_domain(detections)


@router.post("/detect/image", summary="Detect objects, returns an annotated JPEG")
async def detect_image(
    file: UploadFile = File(...),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported format")
    contents = await file.read()
    try:
        detections = service.detect_from_bytes(contents, threshold)
    except InvalidImageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    # Honest trade-off: this decodes the image a second time, since
    # DetectionService encapsulates its own decode step and doesn't hand
    # the PIL object back out. Fine at this scale; if this endpoint gets
    # hot, the fix is a small one — give DetectionService a `.decode()`
    # method both callers share, rather than reaching into its internals.
    image = Image.open(io.BytesIO(contents)).convert("RGB")
    annotated = draw_boxes(image, detections)

    buffer = io.BytesIO()
    annotated.save(buffer, format="JPEG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/jpeg")