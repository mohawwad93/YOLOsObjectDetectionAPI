from backend.app import create_app
from tests.conftest import FakeDetectionEngine, _make_test_lifespan
from fastapi.testclient import TestClient


def test_post_detect_returns_fake_engine_results(client, sample_image_bytes):
    """
    Full request/response cycle through the real API layer, real
    DetectionService, real Pydantic validation — everything except the
    engine itself, which the `client` fixture wires to the fake.
    """
    response = client.post(
        "/detect",
        params={"threshold": 0.0},  # this test is about the response shape, not filtering
        files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["detections"][0]["label"] == "cat"
    assert "confidence" in body["detections"][0]  # confirms score->confidence actually reached the wire


def test_post_detect_rejects_non_image_content_type(client):
    response = client.post("/detect", files={"file": ("test.txt", b"hello", "text/plain")})
    assert response.status_code == 400


def test_post_detect_returns_422_for_corrupt_image_bytes(client):
    response = client.post("/detect", files={"file": ("test.jpg", b"not a jpeg", "image/jpeg")})
    assert response.status_code == 422


def test_detect_returns_503_when_the_real_engine_is_not_ready():
    """
    Deliberately does NOT use the `client` fixture, because that fixture
    overrides get_engine entirely — which would skip the readiness check
    we're trying to test (see §1: overriding replaces the whole callable,
    not just its internals). Instead we let the real get_engine run
    unmodified and control what it finds in app.state.
    """
    not_ready_engine = FakeDetectionEngine()
    not_ready_engine._ready = False

    app = create_app(app_lifespan=_make_test_lifespan(not_ready_engine))
    # No dependency_overrides here — get_engine's real body executes.

    with TestClient(app) as unready_client:
        response = unready_client.post("/detect", files={"file": ("t.jpg", b"x", "image/jpeg")})

    assert response.status_code == 503