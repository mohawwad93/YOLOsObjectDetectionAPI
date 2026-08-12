import asyncio
from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

router = APIRouter()

import io
import torch
from fastapi import APIRouter, UploadFile, File, Query, HTTPException, status
from PIL import Image

from backend.lifespan import state
from backend.services.detector import detect_objects

router = APIRouter()

@router.post(
    "/detect",
    summary="Detect objects in an uploaded image (High Performance JSON)",
    response_description="Returns a lightweight list of detected objects and their normalized coordinates"
)
def detect(
    file: UploadFile = File(..., description="The image file to analyze (JPEG/PNG)"),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0, description="Confidence threshold")
):
    """
    High-performance object detection endpoint.
    Returns raw JSON bounding box vectors to completely bypass server-side image encoding bottle-necks.
    """
    # 1. Light verification checks
    if not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {file.content_type}. Please upload an image file."
        )

    # 2. Extract the global pipeline instance from context state
    detector_pipeline = state.get("detector")
    if not detector_pipeline:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model weights have not been successfully loaded into memory yet."
        )

    try:
        # 3. Read image binary stream (Synchronous read maps better inside standard thread pool)
        contents = file.file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse image data. Ensure the payload is a valid binary image."
        )

    # 4. High-performance inference window execution
    # torch.inference_mode() is faster than torch.no_grad() by completely skipping tracker graph links
    with torch.inference_mode():
        detections = detect_objects(detector_pipeline, image, threshold=threshold)

    # 5. Instantly build and serialize response dictionary matrices
    return [
        {
            "label": det["label"],
            "score": float(det["score"]),
            "box": {
                "xmin": int(det["box"]["xmin"]),
                "ymin": int(det["box"]["ymin"]),
                "xmax": int(det["box"]["xmax"]),
                "ymax": int(det["box"]["ymax"])
            }
        }
        for det in detections
    ]


@router.websocket("/ws/detect")
async def websocket_detect(websocket: WebSocket, threshold: float = 0.5):
    detector_pipeline = state.get("detector")
    if not detector_pipeline:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="Model not loaded")
        return

    await websocket.accept()

    # Bounded queue of maxsize 1 creates our back-pressure choke point
    frame_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)

    async def receiver_task():
        """Task 1: Ingests bytes from network as fast as possible. Drops old frames."""
        try:
            while True:
                frame_bytes = await websocket.receive_bytes()

                # If the queue is full, pull out and discard the stale frame
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                        frame_queue.task_done()
                    except asyncio.QueueEmpty:
                        pass

                # Push the absolute newest frame into the queue
                await frame_queue.put(frame_bytes)
        except WebSocketDisconnect:
            pass
        except Exception:
            raise

    async def processor_task():
        """Task 2: Pulls the latest frame from the queue, runs ML inference, and transmits JSON."""
        try:
            while True:
                # Wait until a frame is available in the queue
                frame_bytes = await frame_queue.get()

                try:
                    # Offload the synchronous PIL image decode and model execution
                    # running it in the default threadpool prevents blocking the main event loop
                    loop = asyncio.get_running_loop()

                    def run_inference():
                        image = Image.open(io.BytesIO(frame_bytes)).convert("RGB")
                        return detect_objects(detector_pipeline, image, threshold=threshold)

                    detections = await loop.run_in_executor(None, run_inference)

                    response_payload = [
                        {"label": det["label"], "score": float(det["score"]), "box": det["box"]}
                        for det in detections
                    ]

                    # Instantly send JSON tracking vectors back to user browser
                    await websocket.send_json(response_payload)
                except Exception:
                    # If an individual frame fails to decode, don't crash the loop
                    pass
                finally:
                    frame_queue.task_done()
        except WebSocketDisconnect:
            pass
        except Exception:
            raise

    # Concurrently launch both tasks inside the persistent WebSocket connection scope
    recv_worker = asyncio.create_task(receiver_task())
    proc_worker = asyncio.create_task(processor_task())

    try:
        # Keep the connection alive until one of the workers throws or exits
        # (e.g. when WebSocketDisconnect occurs)
        await asyncio.gather(recv_worker, proc_worker)
    except Exception:
        # Safe cleanup of outstanding background executions upon connection breakdown
        recv_worker.cancel()
        proc_worker.cancel()
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

