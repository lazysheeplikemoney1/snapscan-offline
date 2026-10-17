"""Curved high-confidence text must survive DB scoring; weak ink must not."""
import cv2
import numpy as np
import pytest

from app.services.ocr_engine import RapidEngine


@pytest.fixture(scope="module")
def postprocess():
    return RapidEngine().engine.text_det.postprocess_op


def probability_band(confidence, curved):
    mask = np.zeros((96, 320), dtype=np.uint8)
    xs = np.arange(12, 308)
    ys = (20 + 0.0015 * (xs - 160) ** 2).astype(np.int32) if curved else np.full_like(xs, 30)
    points = np.column_stack((xs, ys)).astype(np.int32)
    cv2.polylines(mask, [points], False, 1, 8)
    return (mask.astype(np.float32) * confidence)[None, None]


def test_curved_confident_text_is_not_diluted_by_surrounding_blank_pixels(postprocess):
    boxes, scores = postprocess(probability_band(0.95, curved=True), (96, 320))
    assert len(boxes) == 1, "A confident curved text line was filtered out"
    assert scores[0] >= 0.5


def test_straight_confident_text_still_survives(postprocess):
    boxes, _ = postprocess(probability_band(0.95, curved=False), (96, 320))
    assert len(boxes) == 1


@pytest.mark.parametrize("confidence", [0.0, 0.4])
def test_empty_and_weak_regions_still_rejected(postprocess, confidence):
    boxes, _ = postprocess(probability_band(confidence, curved=True), (96, 320))
    assert len(boxes) == 0
