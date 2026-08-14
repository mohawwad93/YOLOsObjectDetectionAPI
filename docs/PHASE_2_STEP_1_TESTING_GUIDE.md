# Phase 2, Step 1: unit & integration testing

This is the working reference for the test suite built on top of Phase 1's
layered architecture: the reasoning behind each testing decision, the full
directory layout (including the `api/` / `services/` split), and the complete
code for every test file — corrected against a real bug we hit while building
it, which is documented in Part E because it's a better lesson live than
sanitized.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`PHASE_1_REFACTORING_GUIDE.md`](PHASE_1_REFACTORING_GUIDE.md) first if you
haven't — this document assumes the layered structure and the
`DetectionEngine` contract already exist.

---

## Part A — Architectural rationale

### The Walking Skeleton, and why real model weights don't belong in CI

A **Walking Skeleton** (Alistair Cockburn's term) is the thinnest possible
slice of a system that exercises every architectural seam end-to-end — all
the layers wired together and talking to each other — with minimal real
functionality inside each part. The point isn't to prove any one component is
correct in isolation; it's to prove the *wiring* is sound: that a request
really flows from the API layer, through the service layer, to something
satisfying the engine contract, and back out as a valid response.

The integration test suite in this project **is** a walking skeleton: it
exercises the real API layer, the real `DetectionService`, the real
`LatestFrameOnlyPolicy` — every seam Phase 1 created — with a
`FakeDetectionEngine` standing in for the "flesh" (actual model weights).
That's a deliberate design choice, not a shortcut. Loading `hustvl/yolos-tiny`
in CI on every commit is an anti-pattern for concrete reasons:

- **Cost and speed.** Hundreds of megabytes downloaded (or a cache to
  maintain), seconds of load time, multiplied across every parallel test
  worker — on every commit. A slow suite is a suite people stop running
  before every push, which is exactly when it's most valuable.
- **Flakiness from a dependency you don't control.** A Hugging Face Hub rate
  limit or outage now fails CI for reasons that have nothing to do with
  whether the code is correct.
- **It tests the wrong thing.** A CI run should answer "did *our* code
  regress?" Whether YOLOS correctly identifies a cat is Hugging Face's testing
  responsibility. If model correctness genuinely needs checking, that belongs
  in a separate, deliberately small "smoke test" tier — marked
  `@pytest.mark.slow`, run nightly or pre-release, never on every commit (see
  Part F).

### Why the `DetectionEngine` Protocol gives us Fakes "for free" — Fakes vs. Mocks

A **Mock** (from `unittest.mock` or similar) is a dynamic stand-in that
records calls and lets you assert on interactions: "was `.predict()` called
once, with these arguments?" It doesn't know or enforce the real interface —
a bare `Mock()` accepts `.predict(wrong, args, extra="whatever")` without
complaint, because nothing checks it against `DetectionEngine`. If the real
interface changes, a mock-based test can keep passing right up until
production breaks.

A **Fake** is a real, working implementation of the same interface —
simplified internals, correct behavior. `FakeDetectionEngine` isn't
*pretending* to be a `DetectionEngine`; because `DetectionEngine` is a
structurally-typed `Protocol`, `FakeDetectionEngine` genuinely *is* one, the
same way `YolosDetectionEngine` is. Consequences:

- A type checker (or the `@runtime_checkable` `isinstance` check) validates it
  against the same contract as production code. If `predict()`'s signature
  changes, the fake breaks the same way the real engine would — not silently.
- It has actual behavior worth testing against: threshold filtering,
  ordering — logic, not just a call log.

This is the direct payoff of Phase 1's decision to define `DetectionEngine` as
an explicit contract before writing the concrete engine: the same seam that
let us swap ML backends in production lets us swap in a fast, safe test
double now, with zero extra machinery.

### `app.dependency_overrides` under the hood

FastAPI resolves a route's dependency tree at request time: every
`Depends(some_callable)` becomes a node in a graph that FastAPI walks, calling
each callable and collecting return values. `app.dependency_overrides` is
literally a plain `dict`, keyed by the original callable, living on the
`FastAPI` app instance. Before calling a dependency while solving a request,
FastAPI checks: is this callable a key in `app.dependency_overrides`? If so,
it calls the **override** instead — and never calls the original function's
body at all.

Two consequences that directly shape the test code in Part D:

1. **Scope is exactly `Depends()`.** Nothing else is touched — not module
   globals, not values set during `lifespan`, not `app.state` set outside a
   dependency. Our engine is instantiated during `lifespan`, so overriding a
   dependency alone does nothing to stop the real `lifespan` from loading real
   weights on startup. That's why `create_app()` was given an `app_lifespan`
   parameter (see the Phase 1 doc's updated `app.py`) — two independent
   mechanisms guard two independent things.
2. **It replaces the whole callable, not just its internals.** If `get_engine`'s
   real body contains a readiness check (`if engine is None or not
   engine.is_ready: raise HTTPException(503, ...)`), overriding `get_engine`
   skips that check entirely — the override never runs the original code path.
   Testing that branch means *not* overriding the dependency, and instead
   controlling what the real dependency finds in `app.state` (see
   `client_with_unready_engine` in Part D).

---

## Part B — Testing blueprint: isolating each component

### `services/annotation.py` — pure function, pure test

`draw_boxes(image, detections)` takes a `PIL.Image` and a `list[Detection]`
and returns a new image. No FastAPI, no engine, no network — a pure
transformation, tested by constructing an image and `Detection`/`BoundingBox`
objects directly (no engine needed, fake or real) and asserting on the output
image. Assertions stay coarse and meaningful (same size, original untouched,
*something* changed at the box border) rather than exact pixel or color
values, so the test doesn't become brittle to font-rendering differences
across environments.

### `services/streaming_session.py` — policy under simulated load

`LatestFrameOnlyPolicy` is async and queue-based, but its correctness claim is
about *ordering and dropping behavior*, not networking. It's tested by calling
`submit_frame()` / `next_result()` directly with `pytest-asyncio`, using a
`FakeDetectionEngine` through a real `DetectionService`. Backpressure is
simulated by submitting several frames before ever draining the queue and
asserting only the newest survives — the exact claim the class exists to
make. No `WebSocket`, no `TestClient`, no event-loop juggling beyond what
`pytest-asyncio` already provides.

### `services/detection_service.py` — the core workflow, in isolation

`DetectionService.detect_from_bytes` is tested against exactly what it's
responsible for: sorting by score, forwarding threshold to the engine
correctly, and translating a decode failure into our own `InvalidImageError`
rather than leaking a raw PIL exception — all testable with a
`FakeDetectionEngine` and no HTTP layer at all.

### `api/routes/*` — the walking skeleton itself

Everything above is exercised in isolation with direct calls. The API layer
tests are the one place the *whole* stack runs together — real routing, real
request validation, real service, real streaming policy — through FastAPI's
`TestClient`, with only the engine swapped for the fake. This is what proves
Phase 1's seams actually compose, not just that each one works alone.

---

## Part C — Test directory layout

Tests are organized to mirror the source tree's `api/` / `services/` split,
which keeps a test file's location an immediate answer to "what does this
cover":

```
tests/
├── conftest.py              # shared fixtures — FakeDetectionEngine, app, client
├── services/
│   ├── test_detection_service.py
│   ├── test_annotation.py
│   └── test_streaming_session.py
└── api/
    ├── test_detection.py    # POST /detect, POST /detect/image
    └── test_streaming.py    # WS /ws/detect
```

**No `__init__.py` files, and no imports needed between test files and
`conftest.py`.** This is worth understanding, not just copying: pytest
auto-discovers fixtures defined in a `conftest.py` for every test file in that
directory *and every directory beneath it*, no matter how deeply nested. That
single property is what makes the `api/` / `services/` split free — every
fixture defined once at `tests/conftest.py` is available identically inside
`tests/api/` and `tests/services/`, with zero relative-import gymnastics. It's
also why the "unready engine" setup lives in `conftest.py` as its own fixture
(`client_with_unready_engine`, in Part D) rather than being written inline
inside `tests/api/test_detection.py` — inlining it would have needed the
`FakeDetectionEngine` class and the lifespan-builder helper imported across a
subfolder boundary, which is exactly the kind of fragile wiring a shared
fixture avoids entirely.

---

## Part D — Full file reference

### `tests/conftest.py`

```python
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
            # high confidence — survives any threshold <= 0.91
            Detection(label="cat", score=0.91, box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100)),
            # low confidence — survives only threshold <= 0.42. Documented
            # here deliberately: a test author choosing a threshold has to
            # know this boundary, and we got bitten once by not checking it
            # (see Part E) — so now it's written down at the source.
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
    1's app.py takes `lifespan` as a factory parameter for exactly this
    reason, so swapping it here costs nothing.
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
    Belt and suspenders — each guards a different mechanism (see Part A).
    """
    app = create_app(app_lifespan=_make_test_lifespan(fake_engine))
    app.dependency_overrides[get_engine] = lambda: fake_engine
    app.dependency_overrides[get_engine_ws] = lambda: fake_engine
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def client_with_unready_engine() -> TestClient:
    """
    Deliberately provides NO override for get_engine. The `client` fixture
    above replaces get_engine's entire callable, which would silently
    skip its own 'not ready' branch (dependency_overrides replaces the
    whole function, not just patches its internals — see Part A). This
    fixture lets that real branch run, by controlling only what
    app.state.engine holds.
    """
    not_ready_engine = FakeDetectionEngine()
    not_ready_engine._ready = False
    app = create_app(app_lifespan=_make_test_lifespan(not_ready_engine))
    with TestClient(app) as c:
        yield c
```

### `tests/services/test_streaming_session.py`

```python
import asyncio
import pytest

from backend.services.detection_service import DetectionService
from backend.services.streaming_session import LatestFrameOnlyPolicy


pytestmark = pytest.mark.asyncio


async def test_keeps_only_latest_frame_under_backpressure(fake_engine, sample_image_bytes):
    """
    The core policy claim: with maxsize=1, submitting three frames before
    ever draining the queue must leave only the LAST one behind. Proved
    behaviorally — drain once, then confirm the queue is empty rather
    than still holding a backlog — so the test survives internal
    refactors of the class instead of asserting on private state.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.5, maxsize=1)

    await session.submit_frame(sample_image_bytes)  # frame 1 — evicted
    await session.submit_frame(sample_image_bytes)  # frame 2 — evicted
    await session.submit_frame(sample_image_bytes)  # frame 3 — survives

    await session.next_result()  # drains the one surviving frame

    with pytest.raises(asyncio.QueueEmpty):
        session._queue.get_nowait()


async def test_submit_never_blocks_even_under_sustained_load(fake_engine, sample_image_bytes):
    """
    Regression guard for the exact failure mode backpressure exists to
    prevent: a slow/absent consumer must never make the producer (the
    WebSocket receive loop, in production) block on queue.put(). Fires 50
    submissions with nothing draining the queue; the whole burst must
    complete near-instantly.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.5, maxsize=1)

    async def submit_many():
        for _ in range(50):
            await session.submit_frame(sample_image_bytes)

    await asyncio.wait_for(submit_many(), timeout=1.0)


async def test_next_result_delegates_to_detection_service(fake_engine, sample_image_bytes):
    """
    Confirms the policy actually calls through to DetectionService rather
    than reimplementing inference glue itself.

    threshold=0.0 is deliberate: this test's job is to prove delegation
    and ordering, not filtering — using 0.5 here originally filtered the
    dog (score 0.42) out and produced a confusing failure. Filtering has
    its own dedicated test below; keeping threshold at 0.0 here keeps
    this test's intent legible from its own arguments.
    """
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.0)

    await session.submit_frame(sample_image_bytes)
    detections = await session.next_result()

    assert [d.label for d in detections] == ["cat", "dog"]  # sorted by score desc


async def test_threshold_flows_through_to_filtering(fake_engine, sample_image_bytes):
    """A high threshold should filter out the low-confidence 'dog' —
    proving threshold correctly reaches: session -> service -> engine."""
    service = DetectionService(fake_engine)
    session = LatestFrameOnlyPolicy(service, threshold=0.8)

    await session.submit_frame(sample_image_bytes)
    detections = await session.next_result()

    assert [d.label for d in detections] == ["cat"]
```

### `tests/services/test_detection_service.py`

```python
import pytest
from backend.services.detection_service import DetectionService, InvalidImageError


def test_detections_are_sorted_by_confidence_descending(fake_engine, sample_image_bytes):
    """Verifies DetectionService's own contribution — sorting — independent
    of whatever order the engine happens to return results in."""
    service = DetectionService(fake_engine)
    detections = service.detect_from_bytes(sample_image_bytes, threshold=0.0)
    scores = [d.score for d in detections]
    assert scores == sorted(scores, reverse=True)


def test_threshold_is_forwarded_to_the_engine(fake_engine, sample_image_bytes):
    service = DetectionService(fake_engine)
    detections = service.detect_from_bytes(sample_image_bytes, threshold=0.95)
    assert detections == []  # both canned detections score below 0.95


def test_invalid_bytes_raise_our_own_domain_error(fake_engine):
    """
    The entire point of InvalidImageError: callers (routes) should never
    need to know PIL raises UnidentifiedImageError internally — they
    catch one documented exception, in our vocabulary, not a third-party
    library's.
    """
    service = DetectionService(fake_engine)
    with pytest.raises(InvalidImageError):
        service.detect_from_bytes(b"not an image", threshold=0.5)
```

### `tests/services/test_annotation.py`

```python
from PIL import Image
from backend.ml.schemas import BoundingBox, Detection
from backend.services.annotation import draw_boxes


def test_returns_a_new_image_of_the_same_size():
    """The annotated image must be a distinct copy — callers should never
    find their original image mutated as a side effect."""
    original = Image.new("RGB", (300, 200), color="white")
    detections = [Detection(label="cat", score=0.9, box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100))]

    annotated = draw_boxes(original, detections)

    assert annotated is not original
    assert annotated.size == original.size
    assert original.getpixel((50, 50)) == (255, 255, 255)  # original untouched


def test_something_is_actually_drawn():
    """A coarse but meaningful assertion — pixels at the box border must
    differ from background — without coupling the test to exact colors
    or font rendering, which vary across environments."""
    original = Image.new("RGB", (300, 200), color="white")
    detections = [Detection(label="cat", score=0.9, box=BoundingBox(xmin=10, ymin=10, xmax=100, ymax=100))]

    annotated = draw_boxes(original, detections)
    assert annotated.getpixel((10, 50)) != (255, 255, 255)


def test_handles_zero_detections_without_error():
    original = Image.new("RGB", (300, 200), color="white")
    annotated = draw_boxes(original, [])
    assert annotated.size == original.size
```

### `tests/api/test_detection.py`

```python
def test_post_detect_returns_fake_engine_results(client, sample_image_bytes):
    """
    Full request/response cycle through the real API layer, real
    DetectionService, real Pydantic validation — everything except the
    engine itself, which the `client` fixture wires to the fake.

    threshold=0.0 for the same reason as the streaming_session test above:
    this test is about the response shape, not filtering, and 0.5 here
    quietly drops the dog and produces a wrong-looking failure.
    """
    response = client.post(
        "/detect",
        params={"threshold": 0.0},
        files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["detections"][0]["label"] == "cat"
    assert "confidence" in body["detections"][0]  # confirms score->confidence reached the wire


def test_post_detect_rejects_non_image_content_type(client):
    response = client.post("/detect", files={"file": ("test.txt", b"hello", "text/plain")})
    assert response.status_code == 400


def test_post_detect_returns_422_for_corrupt_image_bytes(client):
    response = client.post("/detect", files={"file": ("test.jpg", b"not a jpeg", "image/jpeg")})
    assert response.status_code == 422


def test_post_detect_image_returns_a_jpeg(client, sample_image_bytes):
    response = client.post(
        "/detect/image",
        params={"threshold": 0.0},
        files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_detect_returns_503_when_the_real_engine_is_not_ready(client_with_unready_engine):
    """
    Uses the dedicated fixture rather than `client` — see conftest.py and
    Part A for why `client`'s full dependency override would silently
    bypass this exact branch.
    """
    response = client_with_unready_engine.post("/detect", files={"file": ("t.jpg", b"x", "image/jpeg")})
    assert response.status_code == 503
```

### `tests/api/test_streaming.py`

```python
def test_websocket_handshake_and_first_detection(client, sample_image_bytes):
    """
    TestClient's websocket_connect drives a real ASGI WebSocket handshake
    against the real route and real LatestFrameOnlyPolicy — only the
    engine underneath is fake. This is the one test that proves the
    whole streaming stack wires together end to end, not just each piece
    in isolation.
    """
    with client.websocket_connect("/ws/detect?threshold=0.0") as websocket:
        websocket.send_bytes(sample_image_bytes)
        payload = websocket.receive_json()

        assert isinstance(payload, list)
        assert payload[0]["label"] == "cat"
```

---

## Part E — Lessons and gotchas worth remembering

| Issue | Where | Takeaway |
|---|---|---|
| `dependency_overrides` doesn't stop `lifespan` from loading real weights | `app.py` / `conftest.py` | Overrides only intercept `Depends()` resolution. A singleton created in `lifespan` needs its own swap mechanism — hence `app_lifespan` becoming a factory parameter. |
| Overriding a dependency skips its *entire* body, including checks you might want to test | `dependencies.py`, `test_detection.py` | To test `get_engine`'s own 503 branch, don't override it — control what it finds in `app.state` instead (`client_with_unready_engine`). |
| A test asserted `["cat", "dog"]` at `threshold=0.5`, but the fake's `dog` scores `0.42` | `test_streaming_session.py` (live bug, caught during review) | The fixture's canned scores and a test's threshold are coupled, and nothing enforces that coupling. Fix: document the scores at the point of definition, and default test thresholds to `0.0` unless filtering is the specific thing under test — so a non-zero threshold reads as "filtering is the point" at a glance. |
| Test files split into `api/` and `services/` subfolders needed no new imports | `tests/` layout | `conftest.py` fixtures are auto-discovered for every directory beneath them, arbitrarily nested. Anything a cross-folder test needs — including one-off setups like the unready-engine client — belongs in `conftest.py` as a fixture, not inlined with a relative import. |

---

## Part F — Running the suite

```bash
# Full suite
uv run pytest

# Just the newly reorganized layers
uv run pytest tests/services tests/api

# Verbose, stop on first failure — useful while iterating
uv run pytest -vx
```

Add `pytest-asyncio` to dev dependencies for the `streaming_session` tests.
Either mark async tests explicitly (`pytestmark = pytest.mark.asyncio` at the
top of a file, as used above) or set `asyncio_mode = "auto"` under
`[tool.pytest.ini_options]` in `pyproject.toml` to avoid marking every async
test by hand.

**On CI and real-model smoke tests:** everything in this suite runs in
milliseconds and belongs on every commit. If a separate tier that loads real
weights against real sample images is ever added — to catch a genuine
Hugging Face API or model-behavior change — give it its own marker:

```python
@pytest.mark.slow
def test_real_yolos_engine_detects_a_known_image():
    ...
```

and exclude it from the default run (`pytest -m "not slow"`), reserving it for
a nightly job or pre-release gate rather than every push. That keeps the
walking skeleton fast enough that nobody is ever tempted to skip running it.
