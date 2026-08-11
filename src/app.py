from fastapi import FastAPI
from lifespan import lifespan
from api.endpoints import router as detection_router

app = FastAPI(
    title="YOLOS Object Detection API",
    description="A cleanly architected FastAPI template for ML application streaming workflows.",
    version="2.0.0",
    lifespan=lifespan
)

app.include_router(detection_router, tags=["Computer Vision"])