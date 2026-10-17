"""Independent lighting/frame regressions, plus a screenshot-derived diagnosis.

receipt_screen_photo_region_ui.png is NOT the original camera photo. It is the
photo area [x84:1178, y454:1912] of a 1260x2800 user screenshot, retaining green
UI lines/circles. Tests must not infer true camera accuracy from this fixture.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services import doc_detect


FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return cv2.imdecode(np.fromfile(FIXTURES / name, np.uint8), cv2.IMREAD_COLOR)


def overlap(first, second):
    first, second = np.asarray(first, np.float32), np.asarray(second, np.float32)
    intersection, _ = cv2.intersectConvexConvex(first, second)
    return intersection / (cv2.contourArea(first) + cv2.contourArea(second) - intersection)


def lighting_variant(image, kind):
    """Global camera illuminant, optional broad shadow and dark display bezel."""
    gains = {"blue_mild": [1.12, 1, .90], "blue_medium": [1.25, 1, .82],
             "warm": [.86, .98, 1.10], "combined": [1.25, 1, .82]}
    output = image.astype(float) * gains.get(kind, [1, 1, 1])
    if kind in ("shadow", "combined"):
        output *= np.linspace(.68 if kind == "shadow" else .74, 1, image.shape[0])[:, None, None]
    if kind == "moire":
        yy, xx = np.indices(image.shape[:2])
        output *= (1 + .045*np.sin(2*np.pi*xx/3.2) + .025*np.sin(2*np.pi*yy/4.3))[:, :, None]
    output = np.clip(output, 0, 255).astype(np.uint8)
    offset = np.array([0, 0], np.float32)
    if kind in ("border", "combined", "moire"):
        output = cv2.copyMakeBorder(output, 24, 24, 18, 18, cv2.BORDER_CONSTANT, value=(12, 12, 12))
        offset[:] = [18, 24]
    return output, offset


@pytest.mark.parametrize("kind", ["blue_mild", "blue_medium", "warm", "shadow", "border", "combined", "moire"])
def test_relative_paper_color_and_internal_screen_cuts_keep_visible_receipt(kind):
    image, offset = lighting_variant(read("receipt_clipped_wood.png"), kind)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None, kind
    reference = np.array([[166, 0], [560, 0], [584, 884], [171, 884]], np.float32) + offset
    assert overlap(result["quad"], reference) > .94, (kind, result)
    doc_detect.validate_quad(result["quad"], image.shape)
    assert result["document_complete"] is False
    assert set(result["clipped_edges"]) == {"top", "bottom"}
    if kind in ("border", "combined", "moire"):
        assert set(result["content_cut_edges"]) == {"top", "bottom"}
        assert float(np.min(result["quad"][:, 1])) >= 20
        assert float(np.max(result["quad"][:, 1])) <= image.shape[0]-20


def test_screenshot_photo_region_ignores_green_ui_rectangle_and_preserves_paper():
    image = read("receipt_screen_photo_region_ui.png")
    assert image.shape[:2] == (1458, 1094)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None
    reference = np.array([[140, 30], [785, 10], [808, 1457], [210, 1457]], np.float32)
    assert overlap(result["quad"], reference) > .91, result
    assert cv2.contourArea(result["quad"])/(image.shape[0]*image.shape[1]) < .72
    for point in ((180, 120), (750, 130), (260, 1300), (780, 1300)):
        assert cv2.pointPolygonTest(result["quad"], point, False) >= 0
    for point in ((90, 650), (930, 650)):
        assert cv2.pointPolygonTest(result["quad"], point, False) < 0


def wood_only():
    yy, xx = np.indices((640, 480))
    grain = 10*np.sin(xx/9 + yy/110) + 4*np.sin(yy/7)
    image = np.clip(np.array([70, 115, 145])[None, None, :] + grain[:, :, None], 0, 255).astype(np.uint8)
    cv2.ellipse(image, (30, 280), (45, 90), 0, 0, 360, (231, 242, 248), -1)
    return image


@pytest.mark.parametrize("kind", ["blue_mild", "blue_medium", "warm", "border", "combined", "moire"])
def test_wood_glare_with_color_cast_or_display_border_never_becomes_paper(kind):
    image, _ = lighting_variant(wood_only(), kind)
    assert doc_detect.detect_document(image)["quad"] is None


@pytest.mark.parametrize("text", [False, True])
def test_adaptive_fallback_does_not_invent_inner_receipt_in_full_image_panel(text):
    # A full sheet on a dark desk and a same-shaped text panel are visually
    # ambiguous. Preserve the existing complete-paper detector's behavior;
    # only the *new fallback* must avoid inventing an inner clipped receipt.
    image = np.full((640, 480, 3), (230, 205, 180), np.uint8)
    if text:
        for y in range(60, 600, 40):
            cv2.putText(image, "FULL SCREEN PANEL", (40, y), cv2.FONT_HERSHEY_SIMPLEX, .6, (30, 30, 30), 1)
    image = cv2.copyMakeBorder(image, 24, 24, 18, 18, cv2.BORDER_CONSTANT, value=(12, 12, 12))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = np.maximum.reduce([cv2.Canny(blurred, low, high) for low, high in ((10, 35), (30, 90), (60, 180))])
    distance = cv2.distanceTransform(255-edges, cv2.DIST_L2, 3)
    assert doc_detect._adaptive_paper_candidate(image, gray, distance, edges) is None


def test_large_complete_sheet_on_dark_desk_keeps_existing_detection():
    image = np.full((720, 960, 3), 30, np.uint8)
    reference = np.array([[35, 35], [925, 35], [925, 685], [35, 685]], np.float32)
    cv2.fillConvexPoly(image, reference.astype(np.int32), (235, 235, 235))
    for y in range(100, 650, 40):
        cv2.putText(image, "PAPER DOCUMENT 123", (90, y), cv2.FONT_HERSHEY_SIMPLEX, .8, (30, 30, 30), 1)
    result = doc_detect.detect_document(image)
    assert result["quad"] is not None
    assert overlap(result["quad"], reference) > .98
    assert result["method"] in ("multi_edge", "paper_region")
