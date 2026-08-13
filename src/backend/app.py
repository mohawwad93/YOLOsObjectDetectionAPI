# app.py
from pathlib import Path
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from .lifespan import lifespan
from .api.routes import api_router

def create_app() -> FastAPI:
    # Factory instead of a bare module-level `app = FastAPI()` — lets
    # tests spin up a fresh app (fresh lifespan, fresh state) instead of
    # sharing one global instance and risking state leaking between tests.
    app = FastAPI(
        title="YOLOS Object Detection API",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.include_router(api_router, tags=["Computer Vision"])

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    return app

app = create_app()