"""Document-boundary tests use known synthetic paper corners, not OCR output."""
import itertools
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import doc_detect


def scene(background=35, paper_level=245, quad=None, shadow=False):
    height, width = 720, 960
    paper = np.full((600, 400, 3), paper_level, np.uint8)
    if shadow:
        gradient = np.linspace(0.65, 1.0, 400)[None, :, None]
        paper = (paper * gradient).astype(np.uint8)
    cv2.putText(paper, "RECEIPT", (35, 65), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (35, 35, 35), 2)
    for index in range(7):
        cv2.putText(paper, f"Item {index+1}    12.50", (25, 130 + index*45), cv2.FONT_HERSHEY_SIMPLEX, .55, (40, 40, 40), 1)
    cv2.putText(paper, "TOTAL    87.50", (25, 520), cv2.FONT_HERSHEY_SIMPLEX, .7, (35, 35, 35), 2)
    # Dark marks near all corners make over-tight text-block crops observable.
    for x, y in ((14, 14), (382, 14), (382, 582), (14, 582)):
        cv2.circle(paper, (x, y), 3, (25, 25, 25), -1)
    truth = np.array(quad if quad is not None else [[210, 100], [740, 135], [690, 640], [140, 590]], np.float32)
    source = np.array([[0, 0], [399, 0], [399, 599], [0, 599]], np.float32)
    transform = cv2.getPerspectiveTransform(source, truth)
    foreground = cv2.warpPerspective(paper, transform, (width, height))
    mask = cv2.warpPerspective(np.full((600, 400), 255, np.uint8), transform, (width, height))
    image = np.full((height, width, 3), background, np.uint8)
    image[mask > 0] = foreground[mask > 0]
    return image, truth


def iou(first, second):
    intersection, _ = cv2.intersectConvexConvex(np.asarray(first, np.float32), np.asarray(second, np.float32))
    return intersection / (cv2.contourArea(first) + cv2.contourArea(second) - intersection)


@pytest.mark.parametrize("settings", [
    {},
    {"background": 225},
    {"background": 246, "paper_level": 230},
    {"shadow": True},
    {"quad": [[400, 70], [780, 360], [460, 660], [100, 380]]},
    {"quad": [[280, 145], [740, 230], [650, 590], [160, 630]]},
])
def test_detects_complete_paper_on_dark_light_rotated_and_small_scenes(settings):
    image, truth = scene(**settings)
    quad = doc_detect.find_doc_quad(image)
    assert quad is not None, settings
    overlap = iou(quad, doc_detect.order_points(truth))
    error = np.linalg.norm(quad - doc_detect.order_points(truth), axis=1)
    assert overlap > .93, (settings, overlap, quad)
    assert float(np.max(error)) < 12, (settings, error, quad)


def test_single_text_block_and_inner_border_on_blank_background_is_not_paper():
    image = np.full((720, 960, 3), 245, np.uint8)
    cv2.rectangle(image, (200, 210), (760, 470), (35, 35, 35), 2)
    for index in range(5):
        cv2.putText(image, "ONLY TEXT BLOCK", (235, 250 + 40*index), cv2.FONT_HERSHEY_SIMPLEX, 1, (20, 20, 20), 2)
    assert doc_detect.find_doc_quad(image) is None


def test_small_paper_keeps_full_page_until_user_explicitly_selects_manual_corners():
    image, truth = scene(quad=[[415,235],[525,250],[505,565],[400,550]])
    result = doc_detect.detect_document(image)
    assert result["quad"] is None and result["rejection_reason"] == "candidate_too_small"
    # A genuine distant receipt can be small too; manual confirmation remains valid.
    selected = doc_detect.validate_quad(truth, image.shape)
    cropped = doc_detect.four_point_transform(image, selected)
    assert cropped.size > 0 and cropped.shape[0] < image.shape[0]


@pytest.mark.parametrize("kind", ["white", "dark", "noise", "checkerboard", "tiny"])
def test_no_paper_is_rejected_instead_of_manufacturing_a_quad(kind):
    if kind == "noise":
        image = np.random.default_rng(20261015).integers(0, 256, (480, 640, 3), np.uint8)
    elif kind == "checkerboard":
        axes = np.indices((480, 640))
        grid = (((axes[0]//12 + axes[1]//12) % 2) * 180 + 35).astype(np.uint8)
        image = np.repeat(grid[:, :, None], 3, axis=2)
    else:
        image = np.full((480, 640, 3), 245 if kind == "white" else 25, np.uint8)
        if kind == "tiny":
            cv2.rectangle(image, (300, 220), (320, 245), (245, 245, 245), -1)
    assert doc_detect.find_doc_quad(image) is None


def test_order_points_is_unique_for_symmetric_diamond_and_every_permutation():
    diamond = np.array([[200, 50], [350, 200], [200, 350], [50, 200]], np.float32)
    expected = diamond.copy()
    for permutation in itertools.permutations(diamond.tolist()):
        ordered = doc_detect.order_points(np.array(permutation))
        assert len(np.unique(ordered, axis=0)) == 4
        assert np.allclose(ordered, expected)
        assert cv2.isContourConvex(ordered)


@pytest.mark.parametrize("quad", [
    [[30, 30], [300, 30], [30, 400], [300, 400]],  # crossed manual taps
    [[30, 30], [30, 30], [300, 400], [30, 400]],
    [[30, 30], [100, 100], [200, 200], [300, 300]],
    [[-10, 30], [300, 30], [300, 400], [30, 400]],
    [[30, 30], [900, 30], [300, 400], [30, 400]],
    [[30, 30], [31, 30], [31, 31], [30, 31]],
    [[30, 30], [300, 30], [float("nan"), 400], [30, 400]],
])
def test_validate_quad_rejects_bad_manual_points_without_reordering(quad):
    validator = getattr(doc_detect, "validate_quad", None)
    assert callable(validator), "Missing manual-quad validation"
    with pytest.raises(ValueError):
        validator(quad, (480, 640, 3))


def test_valid_manual_quad_returns_same_continuous_order():
    validator = getattr(doc_detect, "validate_quad", None)
    assert callable(validator), "Missing manual-quad validation"
    quad = np.array([[30, 30], [300, 35], [310, 400], [20, 390]], np.float32)
    assert np.array_equal(validator(quad, (480, 640, 3)), quad)


def test_detection_reports_explicitly_heuristic_confidence():
    detector = getattr(doc_detect, "detect_document", None)
    assert callable(detector), "Missing diagnostic detector API"
    image, _ = scene()
    result = detector(image)
    assert result["quad"] is not None and 0 < result["confidence"] <= 1
    assert result["confidence_kind"] == "heuristic_not_probability"
    assert isinstance(result["method"], str) and result["method"] != "none"
    missing = detector(np.full((400, 600, 3), 255, np.uint8))
    assert missing["quad"] is None and missing["confidence"] == 0 and missing["method"] == "none"


def test_correct_document_preserves_input_when_nothing_is_detected():
    image = np.full((240, 320, 3), 245, np.uint8)
    quad, warped = doc_detect.correct_document(image)
    assert quad is None and warped is image


def test_transform_rejects_degenerate_points_with_clear_value_error():
    with pytest.raises(ValueError):
        doc_detect.four_point_transform(np.full((240, 320, 3), 245, np.uint8), np.zeros((4, 2), np.float32))


@pytest.mark.parametrize("image", [None, np.zeros((0, 0, 3), np.uint8), np.zeros((1,), np.uint8)])
def test_detector_handles_invalid_images_without_opencv_crash(image):
    detector = getattr(doc_detect, "detect_document", None)
    assert callable(detector), "Missing diagnostic detector API"
    assert detector(image)["quad"] is None


def clipped_scene(horizontal=False, offset=0, paper_level=225, with_paper=True):
    """Warm textured wood, a neutral glare patch and a receipt beyond both ends."""
    height, width = 640, 480
    yy, xx = np.indices((height, width))
    grain = (10*np.sin(xx/9 + yy/110) + 4*np.sin(yy/7)).astype(np.int16)
    image = np.clip(np.array([70, 115, 145])[None, None, :] + grain[:, :, None], 0, 255).astype(np.uint8)
    cv2.ellipse(image, (30, 280), (45, 90), 0, 0, 360, (231, 242, 248), -1)
    truth = np.array([[125+offset, 0], [342+offset, 0], [363+offset, height-1], [135+offset, height-1]], np.float32)
    if with_paper:
        cv2.fillConvexPoly(image, truth.astype(np.int32), (paper_level,)*3)
        for row in range(30, 620, 40):
            cv2.putText(image, "ITEM 12.50", (155+offset, row), cv2.FONT_HERSHEY_SIMPLEX, .4, (45,)*3, 1)
    if horizontal:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        truth = doc_detect.order_points(np.column_stack((height-1-truth[:, 1], truth[:, 0])))
    return image, truth


@pytest.mark.parametrize("settings", [{}, {"offset": -35, "paper_level": 180}, {"horizontal": True, "offset": 20}])
def test_color_regions_detect_only_visible_extent_of_clipped_receipts(settings):
    image, truth = clipped_scene(**settings)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None, settings
    assert iou(result["quad"], truth) > .94, result
    assert result["method"] == "clipped_paper_region"
    assert result["document_complete"] is False
    assert set(result["clipped_edges"]) == ({"left", "right"} if settings.get("horizontal") else {"top", "bottom"})
    assert result["warnings"]
    doc_detect.validate_quad(result["quad"], image.shape)
    _, warped = doc_detect.correct_document(image)
    assert warped.shape[0] <= max(image.shape[:2])+2


def test_real_clipped_overlapping_receipts_remove_wood_without_inventing_missing_paper():
    path = Path(__file__).parent / "fixtures" / "receipt_clipped_wood.png"
    image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (885, 734)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None
    assert set(result["clipped_edges"]) == {"top", "bottom"}
    assert result["document_complete"] is False
    assert result["overlap_possible"] is True
    assert result["overlap_handling"] == "visible_paper_union"
    quad = doc_detect.validate_quad(result["quad"], image.shape)
    # Loose visual reference for the *visible* paper, including its lower layer.
    reference = np.array([[166, 0], [553, 0], [585, 884], [170, 884]], np.float32)
    assert iou(quad, reference) > .94, quad
    for point in ((185, 10), (540, 10), (185, 870), (565, 850)):
        assert cv2.pointPolygonTest(quad, point, False) >= 0
    for point in ((80, 390), (620, 440)):
        assert cv2.pointPolygonTest(quad, point, False) < 0
    assert np.allclose(quad[:2, 1], 0) and np.allclose(quad[2:, 1], 884)


@pytest.mark.parametrize("horizontal", [False, True])
def test_wood_glare_without_receipt_is_not_a_partial_document(horizontal):
    image, _ = clipped_scene(horizontal=horizontal, with_paper=False)
    assert doc_detect.detect_document(image)["quad"] is None


def test_large_clipped_photo_keeps_exact_original_frame_cuts_after_resize():
    image, _ = clipped_scene()
    image = cv2.resize(image, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None
    assert set(result["clipped_edges"]) == {"top", "bottom"}
    assert np.allclose(result["quad"][:2, 1], 0)
    assert np.allclose(result["quad"][2:, 1], image.shape[0]-1)
