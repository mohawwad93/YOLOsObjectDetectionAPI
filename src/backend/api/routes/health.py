from fastapi import APIRouter, Depends, Request, Response, status

from ..schemas import LivenessResponse, ReadinessResponse
from ...dependencies import get_engine_or_none
from ...ml.base import DetectionEngine, EngineStatus

router = APIRouter()


@router.get("/healthz", summary="Liveness probe", response_model=LivenessResponse)
async def liveness(
    engine: DetectionEngine | None = Depends(get_engine_or_none),
) -> LivenessResponse:
    """
    Is this process alive and able to handle an HTTP request at all?
    Deliberately does NOT check whether the engine is loaded. The one
    exception: a PERMANENTLY failed load — restarting the container is
    the correct remedy for that, unlike a normal in-progress load.
    """
    if engine is not None and engine.status == EngineStatus.FAILED:
        return LivenessResponse(status="unhealthy", reason="engine_failed")
    return LivenessResponse(status="alive")


@router.get("/readyz", summary="Readiness probe", response_model=ReadinessResponse)
async def readiness(
    response: Response,
    request: Request,
    engine: DetectionEngine | None = Depends(get_engine_or_none),
) -> ReadinessResponse:
    """Can this instance correctly serve a request RIGHT NOW?"""
    if request.app.state.shutting_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready", reason="shutting_down")

    if engine is None or engine.status != EngineStatus.READY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            engine_status=engine.status.value if engine else "not_loaded",
        )

    return ReadinessResponse(status="ready", engine_status=engine.status.value)