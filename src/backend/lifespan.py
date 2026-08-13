from contextlib import asynccontextmanager
from fastapi import FastAPI
from .config import get_settings
from .ml.yolos_engine import YolosDetectionEngine

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = YolosDetectionEngine(settings.model_name, settings.device_preference)
    engine.load()               # heavy, exactly once per process
    app.state.engine = engine   # typed, discoverable — not a bare dict
    yield
    # room here later for graceful shutdown: free GPU memory, close pools