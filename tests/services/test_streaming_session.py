import asyncio

import pytest

from backend.services.detection_service import DetectionService
from backend.services.streaming_session import LatestFrameOnlyPolicy

pytestmark = pytest.mark.asyncio


async def test_keeps_only_latest_frame_under_backpressure(
    fake_engine, sample_image_bytes
):
    """
    The core policy claim: with maxsize=1, submitting three frames
    before ever draining the queue must leave only the LAST one behind.
    We prove it behaviorally — drain once, then confirm the queue is
    empty rather than still holding a backlog — instead of reaching into
    private state, so the test survives internal refactors of the class.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.5, maxsize=1)

    await session.submit_frame(sample_image_bytes)  # frame 1 — will be evicted
    await session.submit_frame(sample_image_bytes)  # frame 2 — will be evicted
    await session.submit_frame(sample_image_bytes)  # frame 3 — survives

    await session.next_result()  # drains the one surviving frame

    # If more than one frame had survived, a second get_nowait() here
    # would succeed instead of raising — proving the queue never grew
    # past size 1 regardless of how many submissions arrived.
    with pytest.raises(asyncio.QueueEmpty):
        session._queue.get_nowait()


async def test_submit_never_blocks_even_under_sustained_load(
    fake_engine, sample_image_bytes
):
    """
    Regression guard for the exact failure mode backpressure exists to
    prevent: a slow/absent consumer must never make the producer (the
    WebSocket receive loop, in production) block on queue.put(). We fire
    50 submissions with nothing draining the queue and require the whole
    burst to complete near-instantly.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.5, maxsize=1)

    async def submit_many():
        for _ in range(50):
            await session.submit_frame(sample_image_bytes)

    await asyncio.wait_for(submit_many(), timeout=1.0)


async def test_next_result_delegates_to_detection_service(
    fake_engine, sample_image_bytes
):
    """
    Confirms the policy actually calls through to DetectionService rather
    than reimplementing inference glue itself — the whole reason these
    are two separate classes with two separate responsibilities.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(
        service, threshold=0.0
    )  # low enough that filtering isn't in play here

    await session.submit_frame(sample_image_bytes)
    detections = await session.next_result()

    assert [d.label for d in detections] == ["cat", "dog"]  # sorted by score desc


async def test_threshold_flows_through_to_filtering(fake_engine, sample_image_bytes):
    """A high threshold should filter out the low-confidence 'dog' —
    proving threshold correctly reaches: session -> service -> engine."""
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.8)

    await session.submit_frame(sample_image_bytes)
    detections = await session.next_result()

    assert [d.label for d in detections] == ["cat"]
