"""Local reading-order/PDF acceptance on an existing Android OCR artifact.

No OCR rerun, app database import, uploads write, locked test access or network.
Reports contain geometry/aggregate metrics; private text remains in local PDFs.
"""
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIRECTORY = ROOT / "releases/versions/0.2.1-local-code201/output/scan/android-device"
BASELINE_DOCUMENT_SHA256 = "cae13201b6564f761f60cbd0a6915503f33c86ec33a724b1815bd3035a91d735"
sys.path.insert(0, str(ROOT / "backend"))
from app.services.layout import build_layout
from app.services.export import export_txt_to_pdf, export_searchable_pdf, _positioned_lines
from accept_document_pdf import render_page


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def protected_state():
    databases = {name: digest(ROOT / name) for name in ("snapscan.db", "snapscan.db-wal", "snapscan.db-shm", "snapscan.db-journal")}
    uploads = ROOT / "uploads"
    return {"databases": databases, "uploads": {p.relative_to(uploads).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns] for p in uploads.rglob("*") if p.is_file()}}


def nonspace(text):
    return "".join(text.split())


def main():
    before_workspace = protected_state()
    # The current device evidence is refreshed by later APK acceptances. Keep
    # this pre-layout baseline fixed, or its canonical text hides the old issue.
    source = BASELINE_DIRECTORY / "receipt-document.json"
    image = source.with_name("receipt-ocr-persisted.png")
    source_hash, image_hash = digest(source), digest(image)
    assert source_hash == BASELINE_DOCUMENT_SHA256, "Archived pre-layout evidence changed"
    document = json.loads(source.read_text(encoding="utf-8"))
    boxes, before = document["boxes"], copy.deepcopy(document["boxes"])
    layout = build_layout(boxes)
    flat = [index for row in layout["rows"] for index in row]
    assert sorted(flat) == list(range(len(boxes))) and boxes == before
    assert any(87 in row and 88 in row for row in layout["rows"])
    ordered_text = "".join(boxes[index]["text"] for index in flat)
    assert Counter(nonspace(layout["text"])) == Counter(nonspace(ordered_text))
    folder = ROOT / "output/pdf/reading-order-feedback"
    folder.mkdir(parents=True, exist_ok=True)
    text_pdf = folder / "backend-receipt-text.pdf"
    searchable_pdf = folder / "backend-receipt-searchable.pdf"
    legacy_pdf = folder / "backend-receipt-legacy-box-lines.pdf"
    export_txt_to_pdf(layout["text"], boxes, str(text_pdf))
    export_searchable_pdf(image, boxes, searchable_pdf)
    # Anonymous no-box text deliberately keeps the previous representation.
    export_txt_to_pdf(document["text"], [], str(legacy_pdf))
    text_reader, searchable_reader, legacy_reader = map(PdfReader, (text_pdf, searchable_pdf, legacy_pdf))
    text = "".join(page.extract_text() for page in text_reader.pages)
    searchable_text = "".join(page.extract_text() for page in searchable_reader.pages)
    assert nonspace(text) == nonspace(layout["text"])
    assert nonspace(searchable_text) == nonspace(ordered_text)
    assert len(searchable_reader.pages) == 1
    with Image.open(image) as original:
        original = original.convert("RGB")
        width, height = original.size
        assert searchable_reader.pages[0].images[0].image.convert("RGB").tobytes() == original.tobytes()
    lines = _positioned_lines(boxes, width, height)
    assert [line[0] for line in lines] == [boxes[index]["text"] for index in flat]
    for pdf in (text_pdf, searchable_pdf, legacy_pdf):
        render_page(pdf, pdf.with_suffix(".png"))
    assert source_hash == digest(source) and image_hash == digest(image)
    assert protected_state() == before_workspace
    report = {"source_kind": "archived code201 pre-layout Android document; no OCR rerun",
              "source": source.relative_to(ROOT).as_posix(), "source_sha256": source_hash,
              "source_image": image.relative_to(ROOT).as_posix(), "source_image_sha256": image_hash,
              "locked_test_accessed": False, "workspace_db_uploads_unchanged": True,
              "box_count": len(boxes), "display_row_count": len(layout["rows"]),
              "multi_fragment_rows": sum(len(row)>1 for row in layout["rows"]),
              "mode": layout["mode"], "original_box_indices_and_values_unchanged": boxes == before,
              "all_indices_once": True, "body_fields_87_88_same_row": True,
              "rows_sha256": hashlib.sha256(json.dumps(layout["rows"], separators=(",", ":")).encode()).hexdigest(),
              "text_pdf_pages": len(text_reader.pages), "legacy_box_line_pdf_pages": len(legacy_reader.pages),
              "searchable_pdf_pages": len(searchable_reader.pages), "searchable_image_pixels_identical": True,
              "text_pdf_text_matches_layout": True, "searchable_stream_matches_reading_order": True,
              "artifacts": [p.relative_to(ROOT).as_posix() for p in (text_pdf, searchable_pdf, legacy_pdf)],
              "visual_review_completed": False, "transcribed_private_text_in_report": False,
              "limitations": ["Heuristic receipt rows; no table-cell or semantic document structure recovery.",
                              "The user's supplied three-page text PDF has no OCR boxes, so its original positions cannot be recovered from that PDF.",
                              "Desktop backend artifact verification, not a new Android/iQOO device run."]}
    target = ROOT / "reports/backend_text_layout.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("box_count", "display_row_count", "text_pdf_pages", "legacy_box_line_pdf_pages", "rows_sha256", "workspace_db_uploads_unchanged")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
