import asyncio
from fastapi import APIRouter, Depends, Query
from starlette.websockets import WebSocket, WebSocketDisconnect

from ...dependencies import get_detection_service_ws
from ...services.detection_service import DetectionService, InvalidImageError
from ...services.streaming_session import LatestFrameOnlyPolicy
from ..schemas import DetectionOut

router = APIRouter()

@router.websocket("/ws/detect")
async def websocket_detect(
    websocket: WebSocket,
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service_ws),
):
    if websocket.app.state.shutting_down:
        # Reject new sessions once shutdown has begun — accepting one now
        # would just have to be torn down again seconds later.
        await websocket.close(code=1001, reason="Server shutting down")
        return

    await websocket.accept()
    websocket.app.state.active_sessions.add(websocket)
    session = LatestFrameOnlyPolicy(service, threshold)

    async def receiver_task() -> None:
        while True:
            frame_bytes = await websocket.receive_bytes()
            await session.submit_frame(frame_bytes)

    async def processor_task() -> None:
        while True:
            try:
                detections = await session.next_result()
            except InvalidImageError:
                continue  # bad frame — skip it, don't kill the stream
            payload = [DetectionOut.from_domain(d).model_dump() for d in detections]
            await websocket.send_json(payload)

    recv_worker = asyncio.create_task(receiver_task())
    proc_worker = asyncio.create_task(processor_task())
    try:
        await asyncio.gather(recv_worker, proc_worker)
    except WebSocketDisconnect:
        pass
    finally:
        recv_worker.cancel()
        proc_worker.cancel()
        websocket.app.state.active_sessions.discard(websocket)