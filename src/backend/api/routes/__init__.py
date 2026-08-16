from fastapi import APIRouter
from .detection import router as detection_router
from .streaming import router as streaming_router
from .health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)      # no prefix — /healthz, /readyz at root
api_router.include_router(detection_router)
api_router.include_router(streaming_router)