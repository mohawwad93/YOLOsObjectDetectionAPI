from __future__ import annotations
import asyncio
from ..ml.schemas import Detection
from .detection_service import DetectionService

class LatestFrameOnlyPolicy:
    """
    'Always process the newest frame, drop anything older.' A business
    decision about live-detection behavior, not a transport detail —
    that's why it's testable with plain bytes and no network at all.
    """
    def __init__(self, detection_service: DetectionService, threshold: float, maxsize: int = 1):
        self._service = detection_service
        self._threshold = threshold
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=maxsize)

    async def submit_frame(self, frame_bytes: bytes) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(frame_bytes)

    async def next_result(self) -> list[Detection]:
        frame_bytes = await self._queue.get()
        try:
            # Inference is synchronous/CPU-bound — offload it to the
            # default threadpool so it doesn't block the event loop.
            # This class owns the queueing POLICY; it still needs to be
            # a good async citizen about how it runs the blocking call.
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._service.detect_from_bytes, frame_bytes, self._threshold
            )
        finally:
            self._queue.task_done()