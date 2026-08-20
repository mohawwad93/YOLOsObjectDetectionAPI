def test_post_detect_returns_fake_engine_results(client, sample_image_bytes):
    """
    Full request/response cycle through the real API layer, real
    DetectionService, real Pydantic validation — everything except the
    engine itself, which the `client` fixture wires to the fake.
    """
    response = client.post(
        "/detect",
        params={
            "threshold": 0.0
        },  # this test is about the response shape, not filtering
        files={"file": ("test.jpg", sample_image_bytes, "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert body["detections"][0]["label"] == "cat"
    assert (
        "confidence" in body["detections"][0]
    )  # confirms score->confidence actually reached the wire


def test_post_detect_rejects_non_image_content_type(client):
    response = client.post(
        "/detect", files={"file": ("test.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400


def test_post_detect_returns_422_for_corrupt_image_bytes(client):
    response = client.post(
        "/detect", files={"file": ("test.jpg", b"not a jpeg", "image/jpeg")}
    )
    assert response.status_code == 422


def test_detect_returns_503_when_the_real_engine_is_not_ready(
    client_with_unready_engine,
):
    """
    Uses the dedicated fixture rather than `client` — the `client` fixture's
    full dependency override would silently skip get_engine's own 'not
    ready' branch, which is exactly what this test needs to exercise.
    """
    response = client_with_unready_engine.post(
        "/detect", files={"file": ("t.jpg", b"x", "image/jpeg")}
    )
    assert response.status_code == 503
