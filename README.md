# YOLOS Object Detection API

A FastAPI service wrapping a Hugging Face YOLOS object-detection model, supporting
both single-image detection (REST) and real-time detection over a video stream
(WebSocket), served alongside a lightweight HTML/canvas frontend.

This repository is mid-transformation from a working demo into an industry-grade,
layered service. **This README covers the current, post–Phase 2 (complete) state.**
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
- [`docs/PHASE_2_STEP_2_HEALTH_GUIDE.md`](docs/PHASE_2_STEP_2_HEALTH_GUIDE.md) —
  liveness vs. readiness, the thundering herd problem, graceful WebSocket
  shutdown on `SIGTERM`, and a complete file-by-file reference of the
  `/healthz` and `/readyz` implementation.
- [`docs/PHASE_2_STEP_3_DEPENDENCY_MANAGEMENT_GUIDE.md`](docs/PHASE_2_STEP_3_DEPENDENCY_MANAGEMENT_GUIDE.md) —
  deterministic builds with `uv.lock`, dependency groups, cross-platform
  CUDA/CPU wheel routing in a single lockfile, and the full `pyproject.toml`
  reference.
- [`docs/PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md`](docs/PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md) —
  the multi-stage `Dockerfile`, build-cache mechanics, least-privilege
  non-root setup, the worker-to-model memory math, and a full nine-issue
  debugging narrative from actually shipping it (platform mismatches, the
  CPU/GPU extras split, `HF_HOME` permissions, and more).

## Tech stack

| Concern | Choice |
|---|---|
| Package/environment management | `uv` — locked, deterministic builds; see `docs/PHASE_2_STEP_3_DEPENDENCY_MANAGEMENT_GUIDE.md` |
| Web framework | FastAPI (REST + WebSocket) |
| Model | Hugging Face `transformers` pipeline, `hustvl/yolos-tiny` |
| Frontend | Static HTML + Canvas, no build step |
| Config | `pydantic-settings` |
| Container | Multi-stage Docker, non-root, CPU/GPU variants; see `docs/PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md` |

## Project structure

```
src/
├── backend/
│   ├── __init__.py
│   ├── app.py                    # FastAPI app factory / entrypoint
│   ├── config.py                 # Pydantic-settings: single source of config truth
│   ├── dependencies.py           # DI provider functions used with Depends()
│   ├── lifespan.py               # Background model load, health state, graceful shutdown
│   ├── api/
│   │   ├── __init__.py
│   │   ├── schemas.py            # Pydantic request/response (wire) models
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── detection.py      # REST endpoints — HTTP concerns only
│   │       ├── streaming.py      # WebSocket endpoint — HTTP concerns only
│   │       └── health.py         # /healthz, /readyz — liveness + readiness probes
│   ├── services/
│   │   ├── __init__.py
│   │   ├── detection_service.py  # Core domain workflow, shared by both routes
│   │   ├── annotation.py         # Image-drawing / presentation concern
│   │   └── streaming_session.py  # Real-time frame-prioritization policy
│   └── ml/
│       ├── __init__.py
│       ├── base.py               # DetectionEngine contract (Protocol) + EngineStatus
│       ├── schemas.py            # Detection / BoundingBox domain value objects
│       └── yolos_engine.py       # Concrete Hugging Face YOLOS implementation
└── frontend/
    └── index.html

Dockerfile                      # multi-stage: uv-managed build → non-root production image
.dockerignore
pyproject.toml
uv.lock

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
# (production deps + the `dev` group, which includes `test`)
uv sync

# Run the API locally, with autoreload
# NOTE: binds 127.0.0.1 by design — local development only, never a
# container. See docs/PHASE_2_STEP_3_DEPENDENCY_MANAGEMENT_GUIDE.md.
uv run fastapi dev src/backend/app.py

# Open the frontend
# -> http://localhost:8000

# Run the test suite (fast — no real model weights are ever loaded)
uv run pytest
```

**Container / production** is built as a multi-stage Docker image — not run
via `uv run` at all. `--platform=linux/amd64` is mandatory on anything that
might be pushed or deployed; `TORCH_BACKEND` selects the CPU or CUDA build of
`torch` (see `docs/PHASE_2_STEP_3_DEPENDENCY_MANAGEMENT_GUIDE.md` and
`docs/PHASE_2_STEP_4_CONTAINERIZATION_GUIDE.md`):

```bash
# Production (GPU) — the only combination ever pushed to a registry
docker build --platform=linux/amd64 --build-arg TORCH_BACKEND=gpu -t yolos-detection-api:gpu .

# CPU variant — CI runners, CPU-only tiers
docker build --platform=linux/amd64 --build-arg TORCH_BACKEND=cpu -t yolos-detection-api:cpu .

# Run
docker run --platform=linux/amd64 -p 8000:8000 yolos-detection-api:gpu
```

Inside the container, the process is started with `uvicorn backend.app:app`
directly (not `fastapi run`) at a single worker per container — see the Step
4 guide's debugging narrative for why each of those choices is load-bearing,
not incidental. Apple Silicon developers building locally can add
`--platform=linux/arm64` for a native, non-emulated build — but that image is
for local iteration only and must never be pushed or deployed.

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
| `APP_FRONTEND_DIR` | `src/frontend` | Static frontend directory. Local dev resolves this relative to cwd; the container sets it to an absolute path (`/app/frontend`), since `--no-editable` means there's no source tree to compute a relative path from |

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
| `/healthz` | `GET` | Liveness probe — is the process alive? Ignores model load state. |
| `/readyz` | `GET` | Readiness probe — can this instance serve traffic right now? |

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

### `GET /healthz` / `GET /readyz`

`/healthz` returns `200 {"status": "alive"}` within milliseconds of container
start and stays that way through the entire model-loading window — it never
restarts a pod just because the model is still loading. `/readyz` returns
`503 {"status": "not_ready", "engine_status": "loading"}` until the model is
fully loaded and warmed up, then `200 {"status": "ready", "engine_status": "ready"}`.
See
[`docs/PHASE_2_STEP_2_HEALTH_GUIDE.md`](docs/PHASE_2_STEP_2_HEALTH_GUIDE.md)
for the full liveness-vs-readiness reasoning, the graceful WebSocket shutdown
behavior on `SIGTERM`, and sample Kubernetes probe configuration.

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
- **Phase 2, Step 2 — Operational Health, Liveness & Readiness:** complete. See the docs above.
- **Phase 2, Step 3 — Reproducible Environments & Dependency Management:** complete. See the docs above.
- **Phase 2, Step 4 — Production Containerization:** complete. See the docs above.
- **Phase 2: complete.** Next up: Kubernetes manifests wiring the Step 2 probes and Step 4 image into an actual Deployment/Service.
