import io
from fastapi import APIRouter, UploadFile, File, Query, HTTPException, status
from fastapi.responses import StreamingResponse
from PIL import Image

from lifespan import state
from services.detector import detect_objects, draw_boxes

router = APIRouter()

@router.post(
    "/detect",
    summary="Detect objects in an uploaded image",
    response_description="Returns the annotated JPEG image with bounding boxes"
)
async def detect(
    file: UploadFile = File(..., description="The image file to analyze (JPEG/PNG)"),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0, description="Confidence threshold")
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {file.content_type}. Please upload an image file."
        )

    try:
        contents = await file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse image data."
        )

    # Core execution via state context
    detector_pipeline = state.get("detector")
    if not detector_pipeline:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model is currently unavailable."
        )

    detections = detect_objects(detector_pipeline, image, threshold=threshold)
    annotated_image = draw_boxes(image, detections)

    # Encode back to byte array stream
    output_buffer = io.BytesIO()
    annotated_image.save(output_buffer, format="JPEG", quality=90)
    output_buffer.seek(0)

    return StreamingResponse(output_buffer, media_type="image/jpeg")
