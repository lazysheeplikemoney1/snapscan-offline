"""Reading rows reference immutable OCR indices; no model or image is needed."""
import copy
import json
import math
from pathlib import Path

import pytest

from app.services.layout import build_layout


def box(text, x, center, width=40, height=10):
    return {"text": text, "box": [[x, center-height/2], [x+width, center-height/2],
                                  [x+width, center+height/2], [x, center+height/2]], "score": .9}


def test_rows_use_original_indices_and_short_height_not_top_coordinate():
    boxes = [box("12.00", 100, 25, height=8), box("下一行", 10, 60), box("项目", 10, 25, height=30)]
    original = copy.deepcopy(boxes)
    assert build_layout(boxes) == {"rows": [[2, 0], [1]], "text": "项目  12.00\n下一行", "mode": "spatial"}
    assert boxes == original


@pytest.mark.parametrize("scale", [.1, 1, 12])
def test_slanted_rows_are_scale_independent(scale):
    boxes = [box("RIGHT", 140, 25), box("NEXT", 10, 70), box("LEFT", 10, 25, height=20)]
    theta = .22
    for item in boxes:
        item["box"] = [[scale*(x*math.cos(theta)-y*math.sin(theta)),
                        scale*(x*math.sin(theta)+y*math.cos(theta))] for x, y in item["box"]]
    assert build_layout(boxes)["rows"] == [[2, 0], [1]]


def test_fixed_seed_does_not_chain_three_nearby_centers_into_one_row():
    boxes = [box("A", 0, 10), box("B", 100, 15), box("C", 200, 20)]
    assert build_layout(boxes)["rows"] == [[0, 1], [2]]


def test_tiny_side_note_cannot_seed_a_row_that_splits_body_fields():
    boxes = []
    for center in (20, 60, 100, 140):
        item = box("基准行", 10, center, width=100, height=18)
        item["box"] = [[x*math.cos(-.05)-y*math.sin(-.05), x*math.sin(-.05)+y*math.cos(-.05)] for x, y in item["box"]]
        boxes.append(item)
    boxes.extend([
        {"text": "边", "box": [[12, 741], [20, 741], [20, 752], [12, 752]]},
        {"text": "左字段", "box": [[30, 741], [126, 738], [127, 756], [30, 759]]},
        {"text": "右字段", "box": [[292, 735], [392, 735], [392, 753], [292, 753]]},
    ])
    assert any(5 in row and 6 in row for row in build_layout(boxes)["rows"])


def test_empty_rows_preserve_indices_without_extra_text_lines_or_trimming():
    boxes = [box("", 0, 10), box(" a\nb ", 0, 40), box("B", 100, 40)]
    assert build_layout(boxes) == {"rows": [[0], [1, 2]], "text": " a\nb   B", "mode": "spatial"}
    assert build_layout([]) == {"rows": [], "text": "", "mode": "original_order"}


@pytest.mark.parametrize("bad", [None, [], [[0, 0]]*4,
    [[0, 0], [20, 10], [20, 0], [0, 10]],
    [[0, 0], [20, 0], [20, float("nan")], [0, 10]],
    [[0, 0], ["20", 0], [20, 10], [0, 10]],
    [[False, 0], [20, 0], [20, 10], [0, 10]],
    [[0, 0], [1, 20], [11, 20], [10, 0]],
])
def test_any_bad_coordinate_falls_back_for_the_whole_document(bad):
    boxes = [box("RIGHT", 100, 10), {"text": "BAD", "box": bad}, box("LEFT", 0, 10)]
    assert build_layout(boxes) == {"rows": [[0], [1], [2]], "text": "RIGHT\nBAD\nLEFT", "mode": "original_order"}


def test_shared_cross_language_layout_contract():
    fixture = Path(__file__).resolve().parents[2] / "evaluation/fixtures/text_layout_cases.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases["cases"]
    for case in cases:
        result = build_layout(case["boxes"])
        assert result == {"rows": case["expected_rows"], "text": case["expected_text"], "mode": case["expected_mode"]}, case["name"]
