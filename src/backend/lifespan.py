from contextlib import asynccontextmanager
from fastapi import FastAPI
from backend.services.detector import load_object_detector

# Global application state container for runtime components
state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Safely loads heavy ML weight files once when server starts."""
    try:
        state["detector"] = load_object_detector()
    except Exception as e:
        raise RuntimeError(f"Critical error loading model weights: {e}")

    yield
    state.clear()
