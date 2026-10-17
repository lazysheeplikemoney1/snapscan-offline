"""Contour padding fixes nearby-line contamination without changing DB scoring.

Only the explicitly approved OLD DEV022 page is inferred; no holdout access.
"""
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from rapidocr_onnxruntime.ch_ppocr_det.utils import DBPostProcess

from app.services.ocr_engine import RapidEngine


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine():
    return RapidEngine()


def make_postprocess(cls=DBPostProcess):
    return cls(thresh=.3, box_thresh=.5, max_candidates=1000,
               unclip_ratio=1.6, score_mode="slow", use_dilation=True)


def band(curved):
    image = np.zeros((128, 320), np.uint8)
    x = np.arange(12, 308)
    y = (20 + .0015 * (x - 160) ** 2).astype(np.int32) if curved else np.full_like(x, 35)
    cv2.polylines(image, [np.column_stack((x, y)).astype(np.int32)], False, 1, 8)
    return image.astype(np.float32)[None, None] * .95


def test_curved_line_padding_does_not_reach_next_line(engine):
    prediction = band(curved=True)
    legacy_boxes, _ = make_postprocess()(prediction, (128, 320))
    boxes, scores = engine.engine.text_det.postprocess_op(prediction, (128, 320))
    assert len(boxes) == 1 and scores[0] >= .5
    # The next printed row starts at y84: padding must not pull it into this crop.
    assert np.max(legacy_boxes[:, :, 1]) >= 84
    assert np.max(boxes[:, :, 1]) < 84, "minRect padding reaches the neighboring printed row"


def test_real_dev022_full_page_recovers_without_neighbor_row_contamination(engine, record_property):
    image_path = ROOT / "data/real/dev/dev/111/real-022.jpg"
    annotation_path = ROOT / "data/real/annotations_local_111.jsonl"
    annotation = next(row for row in map(json.loads, annotation_path.read_text(encoding="utf-8").splitlines())
                      if row["image_id"] == "real-022.jpg")
    assert annotation["split"] == "dev" and "[UNK]" not in annotation["gt_text"]
    metric_spec = importlib.util.spec_from_file_location("frozen_contour_test_metrics", ROOT / "evaluation/tools/metrics.py")
    metrics = importlib.util.module_from_spec(metric_spec)
    metric_spec.loader.exec_module(metrics)
    image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    result = engine.recognize(image)
    prediction, reference = metrics.normalize_text(result["text"]), metrics.normalize_text(annotation["gt_text"])
    s, d, i = metrics.edit_ops(prediction, reference)
    value = (s+d+i)/len(reference)
    for name, metric in (("S",s),("D",d),("I",i),("N",len(reference)),("P",len(prediction)),
                         ("cer",value),("returned_boxes",len(result["boxes"]))):
        record_property(name, metric)
    assert value < .08, f"OLD DEV022 CER {value:.5f}, S/D/I/N={s}/{d}/{i}/{len(reference)}"


def test_straight_padding_remains_within_one_pixel_of_upstream(engine):
    prediction = band(curved=False)
    old_boxes, old_scores = make_postprocess()(prediction, (128, 320))
    boxes, scores = engine.engine.text_det.postprocess_op(prediction, (128, 320))
    assert len(boxes) == len(old_boxes) == 1
    # Upstream PyClipper quantizes intermediate coordinates; Java uses unit axes.
    np.testing.assert_allclose(boxes, old_boxes, atol=1)
    np.testing.assert_allclose(scores, old_scores, atol=1e-12)


def test_installation_retains_existing_db_settings(engine):
    post = engine.engine.text_det.postprocess_op
    assert type(post).__name__ == "ContourDBPostProcess"
    assert (post.thresh, post.box_thresh, post.max_candidates, post.unclip_ratio, post.min_size, post.score_mode) == (.3, .5, 1000, 1.6, 3, "slow")
    np.testing.assert_array_equal(post.dilation_kernel, np.ones((2, 2), np.uint8))


@pytest.mark.parametrize("confidence", [0, .4])
def test_blank_and_weak_ink_are_still_rejected(engine, confidence):
    assert len(engine.engine.text_det.postprocess_op(band(True)/.95*confidence, (128,320))[0]) == 0


def test_tiny_noise_is_rejected_without_crashing(engine):
    prediction = np.zeros((1,1,20,20), np.float32)
    prediction[0,0,10,10] = .95
    boxes, scores = engine.engine.text_det.postprocess_op(prediction, (20,20))
    assert len(boxes) == len(scores) == 0


@pytest.mark.parametrize("contour", [
    [[0,0],[10,10],[20,20]], [[5,5],[5,5],[5,5]],
    [[0,0],[float("nan"),2],[2,2]], [[0,0],[float("inf"),2],[2,2]],
])
def test_degenerate_contour_never_receives_rectangle_padding(contour):
    from app.services.ocr_postprocess import contour_unclip_distance
    assert contour_unclip_distance(np.asarray(contour,np.float32), 1.6) is None


def test_rectangle_distance_matches_legacy_formula_and_unit_axis_expansion():
    from app.services.ocr_postprocess import contour_unclip_distance, unclip_rectangle_by_distance
    rectangle = np.array([[10,20],[110,20],[110,40],[10,40]], np.float32)
    before = rectangle.copy()
    distance = contour_unclip_distance(rectangle, 1.6)
    assert distance == pytest.approx(100*20*1.6/(2*(100+20)))
    expanded = unclip_rectangle_by_distance(rectangle, distance)
    np.testing.assert_allclose(expanded, [[10-distance,20-distance],[110+distance,20-distance],
                                         [110+distance,40+distance],[10-distance,40+distance]])
    np.testing.assert_array_equal(rectangle, before)
    np.testing.assert_array_equal(unclip_rectangle_by_distance(rectangle, 0), before)


@pytest.mark.parametrize("distance", [-1, float("nan"), float("inf")])
def test_invalid_distance_is_not_silently_clamped(distance):
    from app.services.ocr_postprocess import unclip_rectangle_by_distance
    with pytest.raises(ValueError):
        unclip_rectangle_by_distance(np.array([[0,0],[10,0],[10,5],[0,5]],np.float32),distance)
