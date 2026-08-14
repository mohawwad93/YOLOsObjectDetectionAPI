import io
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.dependencies import get_engine, get_engine_ws
from backend.ml.schemas import BoundingBox, Detection


class FakeDetectionEngine:
    """
    A test double that satisfies the DetectionEngine Protocol structurally
    — no inheritance, no @patch, no mock call-bookkeeping. It IS a
    DetectionEngine, as far as type checkers and runtime code are
    concerned, simply by having the right shape.

    Returns hardcoded, deterministic detections instantly: no torch, no
    transformers, no weight download, no GPU/CPU contention. Every test
    using this engine is millisecond-scale and fully offline.
    """

    def __init__(self, canned_detections: list[Detection] | None = None):
        self._ready = True
        self._canned = canned_detections if canned_detections is not None else [
            Detection(label="cat", score=0.91, box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100)),
            Detection(label="dog", score=0.42, box=BoundingBox(xmin=150, ymin=20, xmax=260, ymax=180)),
        ]

    @property
    def is_ready(self) -> bool:
        return self._ready

    def predict(self, image: Image.Image, threshold: float) -> list[Detection]:
        # Mirrors the real engine's contract, including threshold
        # filtering — so tests can exercise threshold logic without ever
        # touching a real model.
        return [d for d in self._canned if d.score >= threshold]


def _make_test_lifespan(engine: FakeDetectionEngine):
    """
    Builds a lifespan that sets app.state.engine directly, bypassing the
    real lifespan's engine.load() call entirely.

    This matters because dependency_overrides ONLY intercepts Depends()
    resolution at request time — it does nothing to stop the real
    lifespan from loading real weights on startup if we reused it. Phase
    1's app.py already takes `lifespan` as a factory parameter (for this
    exact reason), so swapping it here costs us nothing.
    """
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.engine = engine
        yield
    return _lifespan


@pytest.fixture
def fake_engine() -> FakeDetectionEngine:
    return FakeDetectionEngine()


@pytest.fixture
def sample_image_bytes() -> bytes:
    """A tiny real JPEG, generated in memory rather than loaded from
    disk — keeps tests hermetic, with no fixture files to go stale."""
    image = Image.new("RGB", (200, 200), color=(120, 120, 120))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def app(fake_engine) -> FastAPI:
    """
    A fresh app per test. Two independent safeguards against ever
    touching the real model, corresponding to the two things that could
    otherwise load it:
      1. A test lifespan, so `lifespan` startup never calls .load().
      2. dependency_overrides, so route handlers never see the real
         engine even if something else set app.state.engine.
    Belt and suspenders — each guards a different mechanism.
    """
    app = create_app(app_lifespan=_make_test_lifespan(fake_engine))
    app.dependency_overrides[get_engine] = lambda: fake_engine
    app.dependency_overrides[get_engine_ws] = lambda: fake_engine
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)