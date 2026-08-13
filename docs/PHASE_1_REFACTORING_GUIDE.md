# Phase 1 refactoring guide: code architecture & decoupling

This is the working reference for Phase 1 of the transformation: how the
monolithic demo was pulled apart into the layered structure described in
[`ARCHITECTURE.md`](ARCHITECTURE.md), why each decision was made, and the
complete code for every file in the new layout.

Read `ARCHITECTURE.md` first if you haven't — this document assumes you know
what the API / service / ML engine layers are for and jumps straight into the
mechanics.

---

## Part A — The transformation, step by step

This is the thinking process used to move a piece of tangled logic (originally,
YOLO model loading and inference living directly inside route handlers) out
into its own layer. The same steps apply to any future refactor in this
codebase.

**Step 1 — Ask "why does this change?"**
The model-loading logic only changes when the model, weights, framework, or
device strategy changes. That's a different axis of change than "how do I
parse a query parameter" or "what HTTP status do I return." Different reasons
to change → different classes, in different modules.

**Step 2 — Define the contract before the implementation.**
Before writing a concrete engine, write down what *any* detection backend must
be able to do: given an image and a threshold, return a list of detections;
report whether it's ready. That becomes `DetectionEngine`, a `Protocol` in
`ml/base.py`. The service and API layers are written against this contract and
never import `transformers` directly.

**Step 3 — Move loading into `__init__` / `load()`, not a bare module function.**
Encapsulate device selection and the loaded pipeline as *instance state* of a
class (`YolosDetectionEngine`), instead of a free function that hands a raw
`Pipeline` object to whoever calls it.

**Step 4 — Attach the loaded instance to `app.state`, not a bare global dict.**
FastAPI's `app.state` is built for exactly this: one typed, discoverable slot
for app-lifetime singletons. It's accessed through exactly one dependency
provider function (`get_engine` / `get_engine_ws`) — never by reaching into
`app.state` from route bodies directly.

**Step 5 — Pull the shared workflow into a service, used by both routes.**
The original REST and WebSocket handlers each independently glued together
decode → threshold → sort. `DetectionService.detect_from_bytes` is now the one
place that logic lives; both routes call it. This is also what makes the
original duplicate-import bug structurally impossible going forward — there's
exactly one place detection logic lives, so there's nothing left to
copy-paste.

**Step 6 — The route shrinks to almost nothing.**
Parse input, call the injected service, shape the response, translate domain
errors to HTTP errors. That's the entire job of a route in this architecture.

### Why this matters at team / enterprise scale

- **Parallel ownership.** ML engineers can rewrite engine internals — swap
  models, add batching, move to ONNX — without touching or understanding the
  API layer, and vice versa.
- **A shared contract.** `DetectionEngine` becomes something teams agree on
  explicitly — the natural seam for model versioning or A/B testing between two
  engines behind the same interface later.
- **Three narrow, fast test surfaces instead of one slow, fragile one.**
  Unit-test the engine's device logic, unit-test the service's filtering logic
  with a fake engine, integration-test the route with a fake service.
- **Operational hooks become trivial.** Because loading is isolated behind
  `is_ready`, a `/healthz` endpoint, startup warmup metrics, or clean 503s are
  one addition instead of duplicated `if not detector_pipeline` checks per
  route.
- **Open/Closed in practice.** A second model, a model registry, or GPU worker
  pools become *new implementations of the same interface* — extension by
  addition, not modification of working code.

---

## Part B — Deciding what belongs in the service layer

This is the rule that gets misapplied most often, so it's worth stating
precisely, separate from the general layer description in `ARCHITECTURE.md`.

**The test:** if the answer changes depending on *how the request arrived*
(REST vs. WebSocket), it doesn't belong in the service layer. If it changes
depending on *what model or framework is running*, it doesn't belong in the
service layer either. What's left — the actual business workflow of "turn an
image into meaningful, ranked detections" — is the service layer's job.

### Belongs in the service layer

1. **The core detection workflow** — decode bytes → call the engine → apply
   domain rules → return domain objects (`DetectionService.detect_from_bytes`).
2. **Business rules that aren't model mechanics** — threshold filtering, sort
   order, minimum box size, label allow/deny lists. These change because
   product requirements change, not because the model changed, so the engine's
   `predict()` returns everything above a sane floor and the service decides
   what to do with it.
3. **Domain-specific errors** — `InvalidImageError` and similar are defined
   here. The API layer catches them and maps to HTTP status codes; the service
   itself never knows what a `422` is.
4. **The real-time frame-prioritization policy** — "keep only the newest
   frame, drop stale ones" is a business decision about how live detection
   should behave, not a WebSocket implementation detail. It's implemented as
   `LatestFrameOnlyPolicy`, which knows nothing about `WebSocket.receive_bytes()`
   — only "given frames arriving faster than I can process, keep the latest."
   That's what makes it unit-testable with plain `bytes` objects and no network
   at all, and swappable later (e.g., temporal smoothing across N frames)
   without touching the route.

### Does *not* belong in the service layer

- **Image annotation (`draw_boxes`).** Neither an HTTP concern nor strictly an
  ML concern — it's a *presentation* concern, split into its own module
  (`services/annotation.py`) so callers that only want JSON never pay for PIL
  drawing calls or even import it.
- **HTTP input validation** (`file.content_type.startswith("image/")`) — stays
  in the route.
- **Device selection, `torch.inference_mode()`, pipeline invocation** — stays
  in the engine. The service should never import `torch` or `transformers`.
- **`asyncio.Queue`, `WebSocket.receive_bytes()`, `websocket.send_json()`
  mechanics** — transport primitives stay in the route. Only the *policy* they
  implement (`LatestFrameOnlyPolicy`) moves to the service layer.

---

## Part C — Complete restructured layout

```
src/
├── backend/
│   ├── __init__.py
│   ├── app.py
│   ├── config.py
│   ├── dependencies.py
│   ├── lifespan.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── schemas.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── detection.py
│   │       └── streaming.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── detection_service.py
│   │   ├── annotation.py
│   │   └── streaming_session.py
│   └── ml/
│       ├── __init__.py
│       ├── base.py
│       ├── schemas.py
│       └── yolos_engine.py
└── frontend/
    └── index.html
```

`backend/__init__.py`, `backend/api/__init__.py`, `backend/services/__init__.py`,
and `backend/ml/__init__.py` are empty — they only mark the directories as
packages.

---

## Part D — Full file reference

### `config.py`

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Every environment-dependent value lives here — never as a magic
    literal scattered through business or infra code.
    """
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    model_name: str = "hustvl/yolos-tiny"
    default_threshold: float = 0.5
    device_preference: str = "auto"   # auto | cpu | cuda | mps
    frame_queue_maxsize: int = 1

@lru_cache
def get_settings() -> Settings:
    # lru_cache turns this into a cheap singleton: env vars are parsed
    # and validated exactly once, then reused — and it's still trivially
    # override-able in tests via dependency_overrides.
    return Settings()
```

### `ml/schemas.py`

```python
from pydantic import BaseModel, Field

class BoundingBox(BaseModel):
    """Domain value object. Pixel-space coordinates, always integers."""
    xmin: int
    ymin: int
    xmax: int
    ymax: int

class Detection(BaseModel):
    """
    Returned by any DetectionEngine. Deliberately minimal and framework-
    agnostic — it has no idea whether it'll end up as JSON, a WebSocket
    frame, or pixels drawn on an image. That indifference is the point.
    """
    label: str
    score: float = Field(ge=0.0, le=1.0)
    box: BoundingBox
```

### `ml/base.py`

```python
from typing import Protocol, runtime_checkable
from PIL import Image
from .schemas import Detection

@runtime_checkable
class DetectionEngine(Protocol):
    """
    Every inference backend must satisfy this. The service and API layers
    program against THIS, never against transformers.Pipeline directly.
    Swapping frameworks later means writing a new class that satisfies
    this Protocol — nothing above this layer has to change.
    """
    def predict(self, image: Image.Image, threshold: float) -> list[Detection]: ...

    @property
    def is_ready(self) -> bool: ...
```

### `ml/yolos_engine.py`

```python
import logging
import torch
from PIL import Image
from transformers import pipeline, Pipeline
from .schemas import BoundingBox, Detection

logger = logging.getLogger(__name__)

class YolosDetectionEngine:
    """
    Owns EVERYTHING infra-specific: device selection, loading, warmup,
    and translating raw HF output into our own Detection schema.
    Nothing outside this file needs to know it's HuggingFace/torch at all.
    """
    def __init__(self, model_name: str, device_preference: str = "auto") -> None:
        self._model_name = model_name
        self._device = self._resolve_device(device_preference)
        self._pipeline: Pipeline | None = None  # set explicitly by load()

    @staticmethod
    def _resolve_device(preference: str) -> int | str:
        if preference != "auto":
            return preference
        if torch.cuda.is_available():
            return 0
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def load(self) -> None:
        # Explicit load step, called once from lifespan — not at import
        # time, not on first request. Makes startup cost visible and
        # gives you a single place to add retry/timeout/warmup logic.
        logger.info("Loading %s on device=%s", self._model_name, self._device)
        self._pipeline = pipeline(
            task="object-detection", model=self._model_name, device=self._device
        )

    @property
    def is_ready(self) -> bool:
        return self._pipeline is not None

    def predict(self, image: Image.Image, threshold: float) -> list[Detection]:
        if not self.is_ready:
            raise RuntimeError("predict() called before load()")
        with torch.inference_mode():
            raw = self._pipeline(image)
        return [
            Detection(
                label=r["label"],
                score=float(r["score"]),
                box=BoundingBox(**{k: int(v) for k, v in r["box"].items()}),
            )
            for r in raw
            if r["score"] >= threshold
        ]
```

### `services/detection_service.py`

```python
import io
from PIL import Image
from ..ml.base import DetectionEngine
from ..ml.schemas import Detection

class InvalidImageError(Exception):
    """Domain error: input bytes aren't a decodable image.
    The API layer maps this to a 422 — the service doesn't know HTTP exists."""

class DetectionService:
    """
    The single place that knows the business workflow: bytes -> ranked
    detections. Both the REST and WebSocket routes call this same method
    — this is what eliminates the duplicated decode/filter/sort glue that
    the original monolith had separately in each handler.
    Depends on the DetectionEngine ABSTRACTION (injected), never a
    concrete model — testable with a fake engine in microseconds.
    """
    def __init__(self, engine: DetectionEngine):
        self._engine = engine

    def detect_from_bytes(self, image_bytes: bytes, threshold: float) -> list[Detection]:
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception as e:
            raise InvalidImageError("Could not decode image payload") from e

        detections = self._engine.predict(image, threshold)
        return sorted(detections, key=lambda d: d.score, reverse=True)
```

### `services/annotation.py`

```python
from __future__ import annotations
from PIL import Image, ImageDraw, ImageFont
from ..ml.schemas import Detection

DEFAULT_FONT_SIZE = 16

def draw_boxes(image: Image.Image, detections: list[Detection]) -> Image.Image:
    """
    Renders domain Detection objects onto a copy of the image. Kept out
    of DetectionService because most callers (JSON /detect, the WS
    stream) never want a rendered image and shouldn't pay for PIL drawing
    calls or even import this module.
    """
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    font = _load_font()

    for i, det in enumerate(detections):
        color = ((i * 50) % 255, (i * 80) % 255, (i * 110) % 255)
        box = det.box
        draw.rectangle([box.xmin, box.ymin, box.xmax, box.ymax], outline=color, width=3)

        label_text = f"{det.label} ({det.score:.0%})"
        text_bbox = draw.textbbox((box.xmin, box.ymin), label_text, font=font)
        text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        label_y = max(0, box.ymin - text_h - 4)

        draw.rectangle([box.xmin, label_y, box.xmin + text_w + 4, label_y + text_h + 4], fill=color)
        draw.text((box.xmin + 2, label_y + 2), label_text, fill="white", font=font)

    return annotated

def _load_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", DEFAULT_FONT_SIZE)
    except OSError:
        return ImageFont.load_default()
```

### `services/streaming_session.py`

```python
from __future__ import annotations
import asyncio
from ..ml.schemas import Detection
from .detection_service import DetectionService

class LatestFrameOnlyPolicy:
    """
    'Always process the newest frame, drop anything older.' A business
    decision about live-detection behavior, not a transport detail —
    that's why it's testable with plain bytes and no network at all.
    """
    def __init__(self, detection_service: DetectionService, threshold: float, maxsize: int = 1):
        self._service = detection_service
        self._threshold = threshold
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=maxsize)

    async def submit_frame(self, frame_bytes: bytes) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(frame_bytes)

    async def next_result(self) -> list[Detection]:
        frame_bytes = await self._queue.get()
        try:
            # Inference is synchronous/CPU-bound — offload it to the
            # default threadpool so it doesn't block the event loop.
            # This class owns the queueing POLICY; it still needs to be
            # a good async citizen about how it runs the blocking call.
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._service.detect_from_bytes, frame_bytes, self._threshold
            )
        finally:
            self._queue.task_done()
```

### `dependencies.py`

> **Gotcha this file fixes:** `HTTPException` does not translate into a graceful
> close over a WebSocket connection — FastAPI's HTTP exception handling doesn't
> apply to the WS scope, so it can leak as an unhandled error instead of
> closing the socket cleanly. The fix is a WS-specific dependency variant that
> raises `WebSocketException` instead, sharing the actual lookup logic with the
> REST variant so there's still only one place that reads `app.state`.

```python
from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from .ml.base import DetectionEngine
from .services.detection_service import DetectionService

def _engine_from_app_state(app) -> DetectionEngine | None:
    # Shared lookup — the ONLY place either dependency touches app.state.
    engine: DetectionEngine | None = getattr(app.state, "engine", None)
    if engine is None or not engine.is_ready:
        return None
    return engine

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
```

### `lifespan.py`

```python
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
```

### `api/schemas.py`

> **Why this file exists separately from `ml/schemas.py`:** today `Detection`'s
> fields and the JSON a client wants are nearly identical, which makes it
> tempting to just `.model_dump()` the domain object directly. But "identical
> today" and "the same thing" are different claims. The moment a field needs to
> be renamed for frontend compatibility, or the response needs a stable public
> ID, or the API needs to version independently of internal changes, an
> unguarded domain model forces a breaking choice. A translation boundary costs
> one small mapping function now in exchange for the API and the domain being
> free to evolve independently later. `score` is renamed to `confidence` here
> specifically to make that seam real rather than hypothetical.

```python
from pydantic import BaseModel
from ..ml.schemas import Detection

class BoundingBoxOut(BaseModel):
    xmin: int
    ymin: int
    xmax: int
    ymax: int

class DetectionOut(BaseModel):
    label: str
    confidence: float   # renamed from `score` — the whole point of this file existing
    box: BoundingBoxOut

    @classmethod
    def from_domain(cls, detection: Detection) -> "DetectionOut":
        return cls(
            label=detection.label,
            confidence=detection.score,
            box=BoundingBoxOut(**detection.box.model_dump()),
        )

class DetectionResponse(BaseModel):
    count: int
    detections: list[DetectionOut]

    @classmethod
    def from_domain(cls, detections: list[Detection]) -> "DetectionResponse":
        return cls(count=len(detections), detections=[DetectionOut.from_domain(d) for d in detections])
```

### `api/routes/detection.py`

```python
import io
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image

from ...dependencies import get_detection_service
from ...services.detection_service import DetectionService, InvalidImageError
from ...services.annotation import draw_boxes
from ..schemas import DetectionResponse

router = APIRouter()

@router.post("/detect", response_model=DetectionResponse, summary="Detect objects, returns JSON")
async def detect(
    file: UploadFile = File(...),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported format")
    contents = await file.read()
    try:
        detections = service.detect_from_bytes(contents, threshold)
    except InvalidImageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return DetectionResponse.from_domain(detections)


@router.post("/detect/image", summary="Detect objects, returns an annotated JPEG")
async def detect_image(
    file: UploadFile = File(...),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service),
):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported format")
    contents = await file.read()
    try:
        detections = service.detect_from_bytes(contents, threshold)
    except InvalidImageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    # Honest trade-off: this decodes the image a second time, since
    # DetectionService encapsulates its own decode step and doesn't hand
    # the PIL object back out. Fine at this scale; if this endpoint gets
    # hot, the fix is a small one — give DetectionService a `.decode()`
    # method both callers share, rather than reaching into its internals.
    image = Image.open(io.BytesIO(contents)).convert("RGB")
    annotated = draw_boxes(image, detections)

    buffer = io.BytesIO()
    annotated.save(buffer, format="JPEG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/jpeg")
```

### `api/routes/streaming.py`

```python
import asyncio
from fastapi import APIRouter, Depends, Query
from starlette.websockets import WebSocket, WebSocketDisconnect

from ...dependencies import get_detection_service_ws
from ...services.detection_service import DetectionService, InvalidImageError
from ...services.streaming_session import LatestFrameOnlyPolicy
from ..schemas import DetectionOut

router = APIRouter()

@router.websocket("/ws/detect")
async def websocket_detect(
    websocket: WebSocket,
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    service: DetectionService = Depends(get_detection_service_ws),
):
    await websocket.accept()
    session = LatestFrameOnlyPolicy(service, threshold)

    async def receiver_task() -> None:
        while True:
            frame_bytes = await websocket.receive_bytes()
            await session.submit_frame(frame_bytes)

    async def processor_task() -> None:
        while True:
            try:
                detections = await session.next_result()
            except InvalidImageError:
                continue  # bad frame — skip it, don't kill the stream
            payload = [DetectionOut.from_domain(d).model_dump() for d in detections]
            await websocket.send_json(payload)

    recv_worker = asyncio.create_task(receiver_task())
    proc_worker = asyncio.create_task(processor_task())
    try:
        await asyncio.gather(recv_worker, proc_worker)
    except WebSocketDisconnect:
        pass
    finally:
        recv_worker.cancel()
        proc_worker.cancel()
```

Both routes send the same `DetectionOut` shape (`confidence`, not `score`) —
one wire vocabulary, two transports.

### `api/routes/__init__.py`

```python
from fastapi import APIRouter
from .detection import router as detection_router
from .streaming import router as streaming_router

api_router = APIRouter()
api_router.include_router(detection_router)
api_router.include_router(streaming_router)
```

### `app.py`

```python
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
```

---

## Part E — Lessons and gotchas worth remembering

| Issue | Where | Takeaway |
|---|---|---|
| Duplicated router and mismatched `backend.*` / `app.*` imports in the original `endpoints.py` | Original monolith | A symptom, not a cause — happens whenever there's no single place a piece of logic is allowed to live. Fixed structurally, not by "being more careful," once `DetectionService` became the one shared workflow. |
| `HTTPException` doesn't cleanly close a WebSocket | `dependencies.py` | Transport-specific dependency variants (`get_engine` vs. `get_engine_ws`) sharing one lookup function. Don't assume an HTTP-oriented error type works across transports. |
| `/detect/image` decodes the uploaded image twice | `api/routes/detection.py` | A deliberate, documented trade-off, not an oversight. `DetectionService` encapsulates its own decode step; giving it a shared `.decode()` method is the fix if this endpoint becomes hot. |
| `score` → `confidence` rename | `api/schemas.py` | The wire format and the domain model are allowed to diverge on purpose — that's the reason `api/schemas.py` exists as a separate file from `ml/schemas.py`. |

## Part F — What's next: Phase 2 preview

Phase 1 delivered the structural seams. Phase 2 is about proving they work and
making the service operable:

- **Unit tests per layer** — a fake `DetectionEngine` for testing
  `DetectionService` and `LatestFrameOnlyPolicy` without any real model weights;
  a narrow test of `YolosDetectionEngine._resolve_device` in isolation; route
  tests using `app.dependency_overrides`.
- **`/healthz`** — a cheap endpoint reporting `engine.is_ready`, now trivial
  because readiness is already a first-class property on the engine contract.
- **Packaging with `uv`** — locking dependencies, a reproducible `pyproject.toml`,
  and a container build that only reinstalls when the lockfile changes.
- **Warmup metrics and graceful shutdown** — both have an obvious home now:
  `lifespan.py`, right where the engine is loaded and torn down.
