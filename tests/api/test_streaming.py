def test_websocket_handshake_and_first_detection(client, sample_image_bytes):
    """
    TestClient's websocket_connect drives a real ASGI WebSocket handshake
    against the real route and real LatestFrameOnlyPolicy — only the
    engine underneath is fake. This is the one test that proves the
    whole streaming stack wires together end to end, not just each piece
    in isolation.
    """
    with client.websocket_connect("/ws/detect?threshold=0.5") as websocket:
        websocket.send_bytes(sample_image_bytes)
        payload = websocket.receive_json()

        assert isinstance(payload, list)
        assert payload[0]["label"] == "cat"
