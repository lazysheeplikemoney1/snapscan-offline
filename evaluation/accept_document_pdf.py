"""Independent image-backed PDF acceptance on three fixed DEV documents.

Run with C:/pycharm/python.exe -B -X utf8 evaluation/accept_document_pdf.py --run
after export_searchable_pdf is implemented. No routes, database, uploads or test
images are used. Reports contain aggregate checks rather than recognized text.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from pypdf import PdfReader
from pypdf.generic import ContentStream

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MODEL_SHA = "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe"
SAMPLES = (("111", "real-015.jpg"), ("222", "real-037.jpg"), ("333", "real-071.jpg"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize(text):
    """Ignore extraction whitespace only, retaining exact Unicode characters."""
    return "".join((text or "").split())


def _pixel_difference(first, second):
    if first.size != second.size:
        return {"same_dimensions": False, "mean_absolute_error": None, "max_error": None}
    delta = np.abs(np.asarray(first.convert("RGB"), dtype=np.int16) - np.asarray(second.convert("RGB"), dtype=np.int16))
    return {"same_dimensions": True, "mean_absolute_error": float(delta.mean()), "max_error": int(delta.max()),
            "changed_pixel_fraction": float(np.any(delta != 0, axis=2).mean())}


def inspect_document_pdf(pdf_path, image_path, boxes):
    """Check the PDF representation independently of its export implementation."""
    reader = PdfReader(str(pdf_path))
    with Image.open(image_path) as image:
        reference = ImageOps.exif_transpose(image).convert("RGB")
    expected = normalize("\n".join(box.get("text", "") for box in boxes if box.get("text")))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    actual = normalize(extracted)
    page = reader.pages[0]
    width, height = float(page.mediabox.width), float(page.mediabox.height)
    ratio = width / height
    expected_ratio = reference.width / reference.height
    images = list(page.images)
    embedded_sizes = [list(item.image.size) for item in images]
    pixels = _pixel_difference(reference, images[0].image) if len(images) == 1 else {"same_dimensions": False}
    hidden, visible, mode, stack = 0, 0, 0, []
    for operands, operator in ContentStream(page.get_contents(), reader).operations:
        if operator == b"q":
            stack.append(mode)
        elif operator == b"Q":
            mode = stack.pop() if stack else 0
        elif operator == b"Tr":
            mode = int(operands[0])
        elif operator in (b"Tj", b"TJ", b"'", b'"'):
            if mode == 3:
                hidden += 1
            else:
                visible += 1
    queries = []
    for box in boxes:
        text = normalize(box.get("text", ""))
        chinese = re.findall(r"[\u3400-\u9fff]{4,}", text)
        if chinese:
            query = chinese[0][:8]
            if query not in queries:
                queries.append(query)
    search_checks = [{"query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                      "characters": len(query), "found_in_extracted_text": query in actual}
                     for query in queries[:5]]
    checks = {
        "single_page": len(reader.pages) == 1,
        "page_aspect_ratio_matches": abs(ratio - expected_ratio) < 1e-5,
        "longest_page_edge_842pt": abs(max(width, height) - 842) < 0.02,
        "no_page_rotation": int(page.get("/Rotate", 0)) % 360 == 0,
        "cropbox_matches_mediabox": all(abs(float(a) - float(b)) < 0.01 for a, b in zip(page.cropbox, page.mediabox)),
        "one_full_resolution_image": embedded_sizes == [list(reference.size)],
        "embedded_image_pixels_match": pixels.get("same_dimensions", False) and pixels.get("max_error") == 0,
        "complete_text_extraction": bool(expected) and actual == expected,
        "all_text_invisible": hidden > 0 and visible == 0,
        "chinese_search_terms_found": bool(search_checks) and all(query["found_in_extracted_text"] for query in search_checks),
    }
    missing = Counter(expected) - Counter(actual)
    return {"checks": checks, "pass_all": all(checks.values()), "page_count": len(reader.pages),
            "page_size_pt": [width, height], "input_size_px": list(reference.size),
            "embedded_image_sizes_px": embedded_sizes, "embedded_image_comparison": pixels,
            "expected_characters": len(expected), "extracted_characters": len(actual),
            "missing_character_codepoints": {f"U+{ord(ch):04X}": count for ch, count in missing.items()},
            "hidden_text_operations": hidden, "visible_text_operations": visible,
            "search_checks": search_checks, "viewer_ui_search_verified": False}


def available_renderer():
    if importlib.util.find_spec("fitz") is not None:
        return "pymupdf", None
    if importlib.util.find_spec("pypdfium2") is not None:
        return "pdfium", None
    if shutil.which("pdftoppm"):
        return "poppler", shutil.which("pdftoppm")
    raise RuntimeError("No local PDF renderer is available; install or provide Poppler/PyMuPDF/PDFium")


def render_page(pdf, png, *, size=None, renderer=None):
    kind, executable = renderer or available_renderer()
    reader = PdfReader(str(pdf))
    page = reader.pages[0]
    if size is None:
        scale = 1200 / max(float(page.mediabox.width), float(page.mediabox.height))
        size = (round(float(page.mediabox.width) * scale), round(float(page.mediabox.height) * scale))
    width, height = size
    if kind == "poppler":
        command = [executable, "-f", "1", "-l", "1", "-singlefile", "-png",
                   "-scale-to-x", str(width), "-scale-to-y", str(height), str(pdf), str(png.with_suffix(""))]
        subprocess.run(command, check=True, capture_output=True, timeout=90)
    elif kind == "pymupdf":
        import fitz
        with fitz.open(str(pdf)) as document:
            pdfpage = document[0]
            pdfpage.get_pixmap(matrix=fitz.Matrix(width / pdfpage.rect.width, height / pdfpage.rect.height), alpha=False).save(str(png))
    else:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(str(pdf))
        try:
            page = document[0]
            page.render(scale=width / page.get_width()).to_pil().convert("RGB").save(png)
            page.close()
        finally:
            document.close()
    return png


def protected_state():
    databases = {name: sha256(ROOT / name) if (ROOT / name).is_file() else None
                 for name in ("snapscan.db", "snapscan.db-wal", "snapscan.db-shm", "snapscan.db-journal")}
    uploads = ROOT / "uploads"
    files = {path.relative_to(uploads).as_posix(): [path.stat().st_size, path.stat().st_mtime_ns]
             for path in uploads.rglob("*") if path.is_file()} if uploads.exists() else {}
    return {"databases": databases, "uploads": files}


def _comparison_sheet(source, modern, legacy, output):
    width, height = 420, 600
    sheet = Image.new("RGB", (width * 3, height + 50), "#e8e8e8")
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate((("OCR input", source), ("Image-backed + hidden OCR", modern), ("Legacy text PDF", legacy))):
        draw.text((index * width + 12, 10), label, fill="black")
        with Image.open(path) as image:
            thumb = ImageOps.contain(image.convert("RGB"), (width - 20, height - 20))
        sheet.paste(thumb, (index * width + (width - thumb.width) // 2, 40 + (height - thumb.height) // 2))
    sheet.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run the three fixed DEV samples after the core export function is ready")
    args = parser.parse_args()
    renderer = available_renderer()
    if not args.run:
        print(json.dumps({"renderer": renderer, "ready_for_core_implementation": True}))
        return 0
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services import export
    if not callable(getattr(export, "export_searchable_pdf", None)):
        raise RuntimeError("Core export_searchable_pdf has not been implemented; no OCR was run")
    from app.services.io_utils import imread_unicode
    from app.services.ocr_engine import get_engine
    from app.core import config
    out = ROOT / "output/pdf"
    out.mkdir(parents=True, exist_ok=True)
    # No route/database imports are needed. Redirect config for defensive isolation.
    config.UPLOAD_DIR = str(out)
    config.DB_PATH = str(out / "unused-acceptance.sqlite3")
    before = protected_state()
    previous_model = os.environ.get("SNAPSCAN_OCR_MODEL")
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "purpose": "layout-preserving searchable PDF acceptance, not OCR accuracy evaluation",
              "renderer": renderer, "samples": [], "test_set_read": False, "database_used": False,
              "formal_uploads_used": False, "recognized_text_saved_in_report": False,
              "pdfs_contain_original_documents": True, "visual_review_completed": False,
              "physical_pdf_viewer_search_verified": False}
    started = time.perf_counter()
    try:
        os.environ["SNAPSCAN_OCR_MODEL"] = "rec_head_v1"
        engine = get_engine()
        if engine.model_info != {"id": "rec_head_v1", "sha256": EXPECTED_MODEL_SHA}:
            raise RuntimeError("Acceptance requires the actual trained model artifact")
        report["model"] = engine.model_info
        for category, filename in SAMPLES:
            source = ROOT / "data/real/dev/dev" / category / filename
            stem = source.stem
            original = imread_unicode(str(source))
            if original is None:
                raise RuntimeError("Cannot decode fixed DEV source")
            canonical = out / (stem + "-ocr-input.png")
            Image.fromarray(original[:, :, ::-1]).save(canonical)
            tic = time.perf_counter()
            recognized = engine.recognize(original)
            elapsed_ms = round((time.perf_counter() - tic) * 1000, 3)
            boxes = recognized["boxes"]
            if not boxes:
                raise RuntimeError("OCR did not produce any boxes")
            modern = out / (stem + "-searchable-layout.pdf")
            legacy = out / (stem + "-legacy-text.pdf")
            export.export_searchable_pdf(str(canonical), boxes, str(modern), title="SnapScan document PDF acceptance")
            export.export_txt_to_pdf(recognized["text"], boxes, str(legacy), title="SnapScan legacy text PDF comparison")
            inspection = inspect_document_pdf(modern, canonical, boxes)
            modern_png = out / (stem + "-searchable-layout.png")
            legacy_png = out / (stem + "-legacy-text.png")
            render_page(modern, modern_png, size=(original.shape[1], original.shape[0]), renderer=renderer)
            render_page(legacy, legacy_png, renderer=renderer)
            with Image.open(canonical) as expected, Image.open(modern_png) as actual:
                rendered = _pixel_difference(expected, actual)
            inspection["rendered_visual_comparison"] = rendered
            inspection["checks"]["rendered_visual_matches_input"] = rendered.get("same_dimensions", False) and rendered.get("mean_absolute_error", float("inf")) <= 1.0
            inspection["pass_all"] = all(inspection["checks"].values())
            comparison = out / (stem + "-pdf-comparison.png")
            _comparison_sheet(canonical, modern_png, legacy_png, comparison)
            old_reader = PdfReader(str(legacy))
            legacy_text = "\n".join(page.extract_text() or "" for page in old_reader.pages)
            report["samples"].append({"source_image_id": filename, "source_split": "dev",
                                      "source_path": source.relative_to(ROOT).as_posix(), "source_sha256": sha256(source),
                                      "ocr_input_path": canonical.relative_to(ROOT).as_posix(), "ocr_input_sha256": sha256(canonical),
                                      "ocr_elapsed_ms": elapsed_ms, "ocr_box_count": len(boxes),
                                      "searchable_pdf": modern.relative_to(ROOT).as_posix(), "searchable_pdf_sha256": sha256(modern),
                                      "legacy_text_pdf": legacy.relative_to(ROOT).as_posix(), "legacy_page_count": len(old_reader.pages),
                                      "legacy_text_extraction_complete": normalize(legacy_text) == normalize(recognized["text"]),
                                      "comparison_png": comparison.relative_to(ROOT).as_posix(), **inspection})
            print(json.dumps({"sample": filename, "pass_all": inspection["pass_all"], "checks": inspection["checks"]}), flush=True)
    finally:
        if previous_model is None:
            os.environ.pop("SNAPSCAN_OCR_MODEL", None)
        else:
            os.environ["SNAPSCAN_OCR_MODEL"] = previous_model
        report["user_database_and_uploads_unchanged"] = protected_state() == before
        report["duration_seconds"] = round(time.perf_counter() - started, 3)
        report["pass_all_automated_checks"] = len(report["samples"]) == len(SAMPLES) and all(sample["pass_all"] for sample in report["samples"]) and report["user_database_and_uploads_unchanged"]
        target = ROOT / "reports/document_pdf_acceptance.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["pass_all_automated_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
