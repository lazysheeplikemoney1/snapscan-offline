"""Internal panels are not a safe automatic crop of a full input page."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import scan
from app.services import doc_detect


def internal_panel(width=260, height=130):
    image = np.full((720, 960, 3), 150, np.uint8)
    x, y = 350, 230
    cv2.rectangle(image, (x,y), (x+width,y+height), (245,)*3, -1)
    cv2.rectangle(image, (x+10,y+10), (x+width-10,y+height-10), (60,)*3, 1)
    for row in range(3):
        cv2.putText(image, "TABLE TITLE", (x+15,y+35+row*23),
                    cv2.FONT_HERSHEY_SIMPLEX, .35, (20,)*3, 1)
    return image, [[x,y],[x+width,y],[x+width,y+height],[x,y+height]]


@pytest.mark.parametrize("width,height", [(260,130),(150,240)])
def test_small_internal_table_or_title_keeps_the_full_input(width, height):
    image, _ = internal_panel(width, height)
    result = doc_detect.detect_document(image)
    assert result["quad"] is None, "An internal panel must not discard the rest of the page"
    assert result["method"] == "none" and result["confidence"] == 0
    assert result["rejection_reason"] == "candidate_too_small"
    assert 0 < result["candidate_area_ratio"] < .10
    assert result["minimum_area_ratio"] == .10
    assert "手动调整四角" in " ".join(result["warnings"])
    assert result["diagnostics"]["candidate_quad"]
    quad, corrected = doc_detect.correct_document(image)
    assert quad is None and corrected is image


def test_scan_api_small_automatic_panel_keeps_pixels_but_manual_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "UPLOAD_DIR", str(tmp_path))
    client = TestClient(app)
    image, quad = internal_panel()
    encoded = cv2.imencode(".png",image)[1].tobytes()
    files = {"file": ("internal-panel.png",encoded,"image/png")}
    automatic = client.post("/api/scan/correct", files=files, data={"mode":"color"})
    assert automatic.status_code == 200
    result = automatic.json()
    assert result["has_quad"] is False and result["quad"] is None
    assert result["size"] == result["source_size"] == [720,960]
    assert result["detection"]["rejection_reason"] == "candidate_too_small"
    assert any("小于整图的10%" in warning for warning in result["warnings"])
    preserved = cv2.imdecode(np.fromfile(tmp_path/Path(result["image"]).name,np.uint8),cv2.IMREAD_COLOR)
    np.testing.assert_array_equal(preserved,image)
    manual = client.post("/api/scan/correct", files=files, data={"mode":"color","quad":json.dumps(quad)})
    assert manual.status_code == 200
    selected = manual.json()
    assert selected["has_quad"] is True and selected["detection"]["method"] == "manual"
    assert selected["size"] == [131,261]
    assert "rejection_reason" not in selected["detection"]


@pytest.mark.parametrize("right,allowed", [(140,True),(139,False)])
def test_auto_guard_uses_polygon_area_and_allows_exactly_ten_percent(right, allowed):
    guard = getattr(doc_detect, "_guard_small_automatic_candidate", None)
    assert callable(guard)
    quad = np.array([[40,40],[right,40],[right,140],[40,140]],np.float32)
    source = {"quad":quad,"method":"paper_region","confidence":.8,
              "confidence_kind":"heuristic_not_probability","diagnostics":{}}
    result = guard(source,(200,500))
    assert (result["quad"] is not None) is allowed


def test_guard_uses_quad_area_instead_of_the_larger_axis_aligned_bounding_box():
    quad = np.array([[150,40],[225,100],[150,160],[75,100]],np.float32)
    # Polygon9%, bounding rectangle18%: only the observed quadrilateral counts.
    source = {"quad":quad,"method":"paper_region","confidence":.8,"diagnostics":{}}
    result = doc_detect._guard_small_automatic_candidate(source,(200,500))
    assert result["quad"] is None
    assert result["candidate_area_ratio"] == pytest.approx(.09)

