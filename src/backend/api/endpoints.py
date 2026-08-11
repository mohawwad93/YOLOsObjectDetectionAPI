import io
from fastapi import APIRouter, UploadFile, File, Query, HTTPException, status
from fastapi.responses import StreamingResponse
from PIL import Image
from starlette.websockets import WebSocket, WebSocketDisconnect

from backend.lifespan import state
from backend.services.detector import detect_objects, draw_boxes

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


@router.websocket("/ws/detect")
async def websocket_detect(websocket: WebSocket, threshold: float = 0.5):
    """
    Accepts a continuous stream of binary image frames over a WebSocket connection,
    runs object detection, and streams back the annotated JPEG frames.
    """
    detector_pipeline = state.get("detector")
    if not detector_pipeline:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="Model not loaded")
        return

    await websocket.accept()

    try:
        while True:
            # Receive raw binary image bytes from the client
            data = await websocket.receive_bytes()

            try:
                # Parse image from the frame bytes
                image = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                # If a frame is corrupted, skip it instead of crashing the socket
                continue

            # Run detection and draw annotations
            detections = detect_objects(detector_pipeline, image, threshold=threshold)
            annotated_image = draw_boxes(image, detections)

            # Convert annotated frame to bytes
            output_buffer = io.BytesIO()
            annotated_image.save(output_buffer, format="JPEG", quality=75)  # 75% quality for lower latency
            output_buffer.seek(0)

            # Send the annotated binary frame directly back to the client
            await websocket.send_bytes(output_buffer.read())

    except WebSocketDisconnect:
        # Client closed connection naturally
        pass
    except Exception:
        # Catch unexpected socket failures safely
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
