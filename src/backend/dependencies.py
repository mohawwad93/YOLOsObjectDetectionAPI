from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from .ml.base import DetectionEngine
from .services.detection_service import DetectionService

def _engine_from_app_state(app) -> DetectionEngine | None:
    # Shared lookup — the ONLY place either dependency touches app.state.
    engine: DetectionEngine | None = getattr(app.state, "engine", None)
    if engine is None or not engine.is_ready:
        return None
    return engine

def get_engine_or_none(request: Request) -> DetectionEngine | None:
    """
    Used ONLY by /healthz and /readyz. The feature-route dependencies
    (get_engine, get_engine_ws) deliberately raise on an unready engine —
    exactly what you want for a request that can't proceed. Health
    routes need the opposite: they must inspect status and return a
    deliberately shaped JSON body with the right status code either way,
    for both healthy and unhealthy states. A raised HTTPException would
    short-circuit that into FastAPI's generic {"detail": ...} envelope,
    which isn't what an orchestrator's probe or a dashboard wants to parse.
    """
    return getattr(request.app.state, "engine", None)

def get_engine(request: Request) -> DetectionEngine:
    """HTTP variant: translates 'not loaded' into a 503 response."""
    engine = _engine_from_app_state(request.app)
    if engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model not loaded yet.")
    return engine

async def get_engine_ws(websocket: WebSocket) -> DetectionEngine:
    """WS variant: same lookup, transport-appropriate error instead."""
    engine = _engine_from_app_state(websocket.app)
    if engine is None:
        raise WebSocketException(code=status.WS_1013_TRY_AGAIN_LATER, reason="Model not loaded yet.")
    return engine

def get_detection_service(engine: DetectionEngine = Depends(get_engine)) -> DetectionService:
    return DetectionService(engine)

def get_detection_service_ws(engine: DetectionEngine = Depends(get_engine_ws)) -> DetectionService:
    return DetectionService(engine)