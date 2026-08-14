# YOLOS Object Detection API

A FastAPI service wrapping a Hugging Face YOLOS object-detection model, supporting
both single-image detection (REST) and real-time detection over a video stream
(WebSocket), served alongside a lightweight HTML/canvas frontend.

This repository is mid-transformation from a working demo into an industry-grade,
layered service. **This README covers the current, post–Phase 2 Step 1 state.**
For the reasoning behind the structure — and for a from-scratch walkthrough if
you're onboarding or revisiting this later — see:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the layered architecture pattern,
  dependency injection, and configuration management: the *why* and *what*.
- [`docs/PHASE_1_REFACTORING_GUIDE.md`](docs/PHASE_1_REFACTORING_GUIDE.md) — the
  full transformation walkthrough, decision rules, and a complete file-by-file
  reference of every module.
- [`docs/PHASE_2_STEP_1_TESTING_GUIDE.md`](docs/PHASE_2_STEP_1_TESTING_GUIDE.md) —
  the unit and integration test suite: Walking Skeleton testing, Fakes vs.
  Mocks, `dependency_overrides` internals, and a complete file-by-file
  reference of every test.

## Tech stack

| Concern | Choice |
|---|---|
| Package/environment management | `uv` |
| Web framework | FastAPI (REST + WebSocket) |
| Model | Hugging Face `transformers` pipeline, `hustvl/yolos-tiny` |
| Frontend | Static HTML + Canvas, no build step |
| Config | `pydantic-settings` |

## Project structure

```
src/
├── backend/
│   ├── __init__.py
│   ├── app.py                    # FastAPI app factory / entrypoint
│   ├── config.py                 # Pydantic-settings: single source of config truth
│   ├── dependencies.py           # DI provider functions used with Depends()
│   ├── lifespan.py               # Wires the ML engine into app.state on startup
│   ├── api/
│   │   ├── __init__.py
│   │   ├── schemas.py            # Pydantic request/response (wire) models
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── detection.py      # REST endpoints — HTTP concerns only
│   │       └── streaming.py      # WebSocket endpoint — HTTP concerns only
│   ├── services/
│   │   ├── __init__.py
│   │   ├── detection_service.py  # Core domain workflow, shared by both routes
│   │   ├── annotation.py         # Image-drawing / presentation concern
│   │   └── streaming_session.py  # Real-time frame-prioritization policy
│   └── ml/
│       ├── __init__.py
│       ├── base.py               # DetectionEngine contract (Protocol)
│       ├── schemas.py            # Detection / BoundingBox domain value objects
│       └── yolos_engine.py       # Concrete Hugging Face YOLOS implementation
└── frontend/
    └── index.html

tests/
├── conftest.py              # shared fixtures — FakeDetectionEngine, app, client
├── services/
│   ├── test_detection_service.py
│   ├── test_annotation.py
│   └── test_streaming_session.py
└── api/
    ├── test_detection.py
    └── test_streaming.py
```

Every module has exactly one reason to change. See `docs/ARCHITECTURE.md` for why
that property matters and `docs/PHASE_1_REFACTORING_GUIDE.md` for what belongs
where. The `tests/` tree mirrors `src/backend/` on purpose — see
`docs/PHASE_2_STEP_1_TESTING_GUIDE.md`.

## Getting started

```bash
# Install dependencies into a uv-managed virtual environment
uv sync

# Run the API in development mode
uv run fastapi dev src/backend/app.py

# Open the frontend
# -> http://localhost:8000

# Run the test suite (fast — no real model weights are ever loaded)
uv run pytest
```

## Configuration

All environment-dependent values are read once at startup via `pydantic-settings`
(`backend/config.py`). Override any of them with environment variables prefixed
`APP_`, or via a `.env` file in the working directory.

| Variable | Default | Description |
|---|---|---|
| `APP_MODEL_NAME` | `hustvl/yolos-tiny` | Hugging Face model identifier to load |
| `APP_DEFAULT_THRESHOLD` | `0.5` | Default confidence threshold if not passed per request |
| `APP_DEVICE_PREFERENCE` | `auto` | `auto` \| `cpu` \| `cuda` \| `mps` |
| `APP_FRAME_QUEUE_MAXSIZE` | `1` | Backpressure queue size for the WebSocket stream |

Example `.env`:

```
APP_MODEL_NAME=hustvl/yolos-tiny
APP_DEVICE_PREFERENCE=cuda
APP_DEFAULT_THRESHOLD=0.6
```

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/detect` | `POST` | Upload an image, get back JSON detections (`DetectionResponse`) |
| `/detect/image` | `POST` | Upload an image, get back a JPEG with bounding boxes drawn on it |
| `/ws/detect` | `WebSocket` | Stream JPEG frames, receive JSON detections per frame in near real time |

### `POST /detect`

**Query params:** `threshold` (float, `0.0`–`1.0`, default `0.5`)
**Body:** multipart file upload, `image/*`
**Response:**

```json
{
  "count": 2,
  "detections": [
    {
      "label": "person",
      "confidence": 0.94,
      "box": { "xmin": 120, "ymin": 40, "xmax": 310, "ymax": 480 }
    }
  ]
}
```

### `POST /detect/image`

Same input as `/detect`. Response is `image/jpeg` with bounding boxes and labels
rendered directly on the image.

### `WS /ws/detect?threshold=0.8`

Client sends binary JPEG frames; server responds with a JSON array of detections
per frame, using the same shape as `DetectionResponse.detections`. The server
keeps only the most recent frame in flight — see
[`docs/PHASE_1_REFACTORING_GUIDE.md`](docs/PHASE_1_REFACTORING_GUIDE.md#streaming-session--the-frame-prioritization-policy)
for why, and how that policy is implemented and tested independently of the
WebSocket transport.

## Testing

Full unit and integration coverage of the business logic, presentation rules,
and streaming policy — with zero dependency on real model weights or PyTorch.
`FakeDetectionEngine` satisfies the `DetectionEngine` Protocol structurally, so
the entire suite runs in milliseconds and is safe to run on every commit.

```bash
uv run pytest                       # full suite
uv run pytest tests/services        # business logic + presentation only
uv run pytest tests/api             # API layer, real routing + real service
```

See [`docs/PHASE_2_STEP_1_TESTING_GUIDE.md`](docs/PHASE_2_STEP_1_TESTING_GUIDE.md)
for the full reasoning and file-by-file reference.

## Status

- **Phase 1 — Code Architecture & Decoupling:** complete. See the docs above.
- **Phase 2, Step 1 — Unit & Integration Testing:** complete. See the docs above.
- **Phase 2, remaining steps — packaging and operability (`/healthz`, warmup
  metrics, `uv` packaging):** not yet started.
