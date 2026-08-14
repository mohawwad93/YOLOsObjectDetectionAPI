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
    catch one documented exception, in our vocabulary, not a
    third-party library's.
    """
    service = DetectionService(fake_engine)
    with pytest.raises(InvalidImageError):
        service.detect_from_bytes(b"not an image", threshold=0.5)