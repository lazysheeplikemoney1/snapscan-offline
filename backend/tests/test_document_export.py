"""Check the document seen by the user, not merely the existence of a file."""
import math

import pytest
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ContentStream
from openpyxl import load_workbook

from app.services import export


@pytest.fixture
def page_image(tmp_path):
    path = tmp_path / "page.png"
    image = Image.new("RGB", (400, 600), "#fff5dc")
    image.paste("#19534a", (20, 30, 380, 65))
    image.paste("#b55c2e", (200, 150, 370, 450))
    image.save(path)
    return path


def searchable(image, boxes, output):
    assert hasattr(export, "export_searchable_pdf"), "Missing image-backed searchable PDF"
    return export.export_searchable_pdf(image, boxes, output)


def test_pdf_preserves_image_page_ratio_and_all_chinese_text(page_image, tmp_path):
    boxes = [
        {"text": "课件标题 SnapScan", "box": [[20, 30], [380, 30], [380, 65], [20, 65]]},
        {"text": "含税金额 00128.50", "box": [[30, 100], [320, 110], [319, 130], [29, 120]]},
    ]
    output = tmp_path / "searchable.pdf"
    searchable(page_image, boxes, output)
    reader = PdfReader(output)
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) / float(page.mediabox.height) == pytest.approx(2 / 3)
    assert float(page.mediabox.height) == pytest.approx(842)
    assert len(page.images) == 1
    assert page.images[0].image.convert("RGB").tobytes() == Image.open(page_image).convert("RGB").tobytes()
    text = page.extract_text()
    assert all(box["text"] in text for box in boxes)
    operations = ContentStream(page.get_contents(), reader).operations
    modes = [int(args[0]) for args, op in operations if op == b"Tr"]
    assert modes and set(modes) == {3}, "Text must be invisible so glyphs do not obscure the scan"
    positions = [list(map(float, args)) for args, op in operations if op == b"Tm"]
    assert any(abs(matrix[1]) > .001 for matrix in positions), "Tilted OCR text needs its own transform"
    assert all(math.isfinite(value) for matrix in positions for value in matrix)


def test_blank_scan_remains_an_image_without_fabricated_text(page_image, tmp_path):
    output = tmp_path / "blank.pdf"
    searchable(page_image, [], output)
    page = PdfReader(output).pages[0]
    assert len(page.images) == 1
    assert page.extract_text().strip() == ""


@pytest.mark.parametrize("box", [
    [[0, 0]] * 4,
    [[0, 0], [100, 0]],
    [[0, 0], [float('nan'), 0], [20, 20], [0, 20]],
    [[0, 0], [500, 0], [500, 20], [0, 20]],
])
def test_invalid_coordinates_fail_instead_of_exporting_misplaced_text(page_image, tmp_path, box):
    with pytest.raises(ValueError):
        searchable(page_image, [{"text": "有字", "box": box}], tmp_path / "bad.pdf")


def test_corrected_multiline_text_is_not_lost(page_image, tmp_path):
    output = tmp_path / "edited.pdf"
    searchable(page_image, [{"text": "第一行\nSecond line", "box": [[20, 30], [380, 30], [380, 100], [20, 100]]}], output)
    extracted = PdfReader(output).pages[0].extract_text()
    assert "第一行" in extracted and "Second line" in extracted


def test_excel_ocr_text_is_literal_and_not_a_formula(tmp_path):
    output = tmp_path / "literal.xlsx"
    export.export_boxes_to_excel([{"text": "=1+2", "score": .8, "box": []}, {"text": "00128.50"}], output)
    sheet = load_workbook(output).active
    assert sheet["B2"].data_type == "s"
    assert sheet["B2"].value == "=1+2"
    assert sheet["B3"].value == "00128.50"
