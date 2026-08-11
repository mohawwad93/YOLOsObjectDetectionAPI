import os
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from backend.lifespan import lifespan
from backend.api.endpoints import router as detection_router

app = FastAPI(
    title="YOLOS Object Detection API",
    description="A cleanly architected FastAPI template for ML application streaming workflows.",
    version="2.0.0",
    lifespan=lifespan
)

app.include_router(detection_router, tags=["Computer Vision"])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")