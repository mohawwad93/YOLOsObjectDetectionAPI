# app.py
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from .api.routes import api_router
from .config import get_settings
from .lifespan import lifespan as production_lifespan


def create_app(app_lifespan=production_lifespan) -> FastAPI:
    # Factory instead of a bare module-level `app = FastAPI()` — lets
    # tests spin up a fresh app (fresh lifespan, fresh state) instead of
    # sharing one global instance and risking state leaking between tests.
    app = FastAPI(
        title="YOLOS Object Detection API",
        version="2.0.0",
        lifespan=app_lifespan,
    )
    app.include_router(api_router, tags=["Computer Vision"])
    settings = get_settings()
    app.mount(
        "/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend"
    )
    return app


app = create_app()
