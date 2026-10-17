"""Isolated HTTP acceptance: DEV invoice + explicitly synthetic narrow receipt.

Run in a fresh process with C:/pycharm/python.exe -B -X utf8 evaluation/accept_scan_imaging.py.
Only this script's output/scan, output/pdf and reports artifacts are persistent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluation.accept_document_pdf import inspect_document_pdf, protected_state, render_page

MANUAL_REAL_QUAD = [[376, 534], [3401, 550], [3394, 2439], [420, 2515]]
MODEL_SHA = "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe"


def quad_iou(first, second):
    first, second = np.asarray(first, np.float32), np.asarray(second, np.float32)
    intersection, _ = cv2.intersectConvexConvex(first, second)
    union = cv2.contourArea(first) + cv2.contourArea(second) - intersection
    return float(intersection / union) if union > 0 else 0.0


def make_narrow_receipt():
    """Independent full paper geometry; first and last lines identify both ends."""
    height, width = 1200, 900
    yy, xx = np.mgrid[:height, :width]
    scene = np.dstack([54 + xx * .01, 66 + yy * .005, 78 + xx * .008]).astype(np.uint8)
    paper = np.full((1000, 240, 3), (235, 240, 244), np.uint8)
    cv2.putText(paper, "SYNTHETIC RECEIPT", (10, 27), cv2.FONT_HERSHEY_SIMPLEX, .48, (25, 25, 25), 1, cv2.LINE_AA)
    cv2.putText(paper, "FIRST LINE / TOP", (10, 58), cv2.FONT_HERSHEY_SIMPLEX, .45, (25, 25, 25), 1, cv2.LINE_AA)
    for index in range(23):
        cv2.putText(paper, f"ITEM {index+1:02d}      {index+1}.80", (12, 105 + index * 33), cv2.FONT_HERSHEY_SIMPLEX, .43,
                    (35, 35, 35), 1, cv2.LINE_AA)
    cv2.line(paper, (10, 902), (230, 902), (40, 40, 40), 1)
    cv2.putText(paper, "TOTAL  283.40", (12, 936), cv2.FONT_HERSHEY_SIMPLEX, .55, (25, 25, 25), 1, cv2.LINE_AA)
    cv2.putText(paper, "LAST LINE / BOTTOM", (10, 978), cv2.FONT_HERSHEY_SIMPLEX, .43, (25, 25, 25), 1, cv2.LINE_AA)
    quad = np.array([[294, 83], [566, 118], [604, 1108], [250, 1070]], np.float32)
    transform = cv2.getPerspectiveTransform(np.array([[0, 0], [239, 0], [239, 999], [0, 999]], np.float32), quad)
    warped = cv2.warpPerspective(paper, transform, (width, height))
    mask = cv2.warpPerspective(np.full((1000, 240), 255, np.uint8), transform, (width, height))
    scene[mask > 0] = warped[mask > 0]
    cv2.putText(scene, "SYNTHETIC FIXTURE - NOT A REAL PHOTO", (25, 1180), cv2.FONT_HERSHEY_SIMPLEX, .6,
                (200, 210, 220), 1, cv2.LINE_AA)
    return scene, quad


def _decode(raw):
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode API image")
    return image


def _save(path, bgr):
    Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(path)


def _image_only_reference(image_path, pdf_path):
    """Independent visible-page reference, without any OCR text layer."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    width, height = image.size
    scale = 842.0 / max(width, height)
    pdf = canvas.Canvas(str(pdf_path), pagesize=(width * scale, height * scale))
    pdf.drawImage(ImageReader(image), 0, 0, width=width * scale, height=height * scale)
    pdf.showPage()
    pdf.save()


def _sheet(items, output, width=410, height=580):
    sheet = Image.new("RGB", (width * len(items), height + 38), "#e2e2e2")
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(items):
        draw.text((index * width + 8, 12), label, fill="black")
        thumb = ImageOps.contain(Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)), (width - 14, height - 14))
        sheet.paste(thumb, (index * width + (width - thumb.width) // 2, 38 + (height - thumb.height) // 2))
    sheet.save(output)


def main():
    from fastapi.testclient import TestClient
    if "app.main" in sys.modules or "app.core.db" in sys.modules:
        raise RuntimeError("Run imaging acceptance in a fresh process before importing the app")
    sys.dont_write_bytecode = True
    output, pdf_output = ROOT / "output/scan", ROOT / "output/pdf"
    output.mkdir(parents=True, exist_ok=True)
    pdf_output.mkdir(parents=True, exist_ok=True)
    target = ROOT / "reports/scan_imaging_acceptance.json"
    before, started = protected_state(), time.perf_counter()
    previous_model = os.environ.get("SNAPSCAN_OCR_MODEL")
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "checks": {}, "test_set_read": False,
              "real_source": "data/real/dev/dev/222/real-036.jpg", "synthetic_fixture_explicitly_labeled": True,
              "ocr_accuracy_or_cer_claim": False, "recognized_text_saved_in_report": False,
              "temporary_database_and_uploads": True, "visual_review_completed": False}

    def check(name, condition, **evidence):
        report["checks"][name] = {"pass": bool(condition), **evidence}
        print(("PASS " if condition else "FAIL ") + name, flush=True)
        if not condition:
            raise RuntimeError("Acceptance check failed: " + name)

    try:
        os.environ["SNAPSCAN_OCR_MODEL"] = "rec_head_v1"
        with tempfile.TemporaryDirectory(prefix="snapscan-imaging-acceptance-") as temporary:
            temp = Path(temporary)
            uploads = temp / "uploads"
            uploads.mkdir()
            sys.path.insert(0, str(ROOT / "backend"))
            from app.core import config
            config.DB_PATH, config.UPLOAD_DIR = str(temp / "isolated.sqlite3"), str(uploads)
            from app.main import app
            from app.core import db
            from app.routers import scan, ocr, export_router
            check("isolated_storage_bound_before_app_import", Path(db.DB_PATH).parent == temp and
                  all(Path(module.UPLOAD_DIR) == uploads for module in (scan, ocr, export_router)))
            source = ROOT / report["real_source"]
            raw = source.read_bytes()
            original = _decode(raw)
            report["source_sha256"] = hashlib.sha256(raw).hexdigest()
            report["manual_quad"] = MANUAL_REAL_QUAD
            report["manual_quad_provenance"] = "independent visual selection of the complete bright invoice boundary"

            def count_rows():
                connection = db.get_conn()
                try:
                    return connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
                finally:
                    connection.close()

            with TestClient(app) as client:
                def download(path):
                    response = client.get(path)
                    check("download_" + str(len(report["checks"])), response.status_code == 200)
                    return response.content

                def correct(image_bytes, label, mode=None, quad=None):
                    data = {}
                    if mode is not None:
                        data["mode"] = mode
                    if quad is not None:
                        data["quad"] = json.dumps(quad)
                    response = client.post("/api/scan/correct", files={"file": (label + ".png", image_bytes, "image/png")}, data=data)
                    check(label + ".correct_http", response.status_code == 200 and response.json().get("ok") is True)
                    body = response.json()
                    images = {key: _decode(download(body[key])) for key in ("original_image", "cropped_image", "image")}
                    check(label + ".reviewable_previews", all(image.size > 0 for image in images.values()) and
                          max(images["original_image"].shape[:2]) <= 1600 and max(images["cropped_image"].shape[:2]) <= 1600)
                    return body, images, download(body["image"])

                count_before = count_rows()
                auto, auto_images, _ = correct(raw, "real_auto")
                overlap = quad_iou(auto["quad"], MANUAL_REAL_QUAD) if auto.get("has_quad") else 0.0
                check("real_auto.complete_invoice_detected", auto.get("has_quad") is True and overlap >= .90,
                      quad=auto.get("quad"), overlap_with_visual_manual_quad=round(overlap, 6), detection=auto.get("detection"))
                check("real_auto.default_clean_and_source_geometry", auto.get("mode") == "clean" and auto.get("source_size") == list(original.shape[:2]))
                manual, modes, encoded = {}, {}, {}
                for mode in ("color", "clean", "bw"):
                    body, images, png = correct(raw, "real_manual_" + mode, mode, MANUAL_REAL_QUAD)
                    check("real_manual_" + mode + ".explicit_manual_quad", body.get("detection", {}).get("method") == "manual" and
                          body.get("has_quad") is True and body.get("quad") == MANUAL_REAL_QUAD)
                    manual[mode], modes[mode], encoded[mode] = body, images["image"], png
                check("real_manual.mode_dimensions_equal", len({image.shape for image in modes.values()}) == 1,
                      shapes={mode: list(image.shape) for mode, image in modes.items()})
                colors = modes["color"].astype(np.int16)
                red = (colors[:, :, 2] > colors[:, :, 1] + 45) & (colors[:, :, 2] > colors[:, :, 0] + 45) & (colors[:, :, 2] > 120)
                check("real_manual.original_color_retains_red_stamps", int(red.sum()) > 1000, red_pixels=int(red.sum()))
                check("real_manual.bw_is_strict_binary", set(np.unique(modes["bw"])).issubset({0, 255}) and
                      np.array_equal(modes["bw"][:, :, 0], modes["bw"][:, :, 2]))

                fixture, expected_quad = make_narrow_receipt()
                fixture_png = cv2.imencode(".png", fixture)[1].tobytes()
                synthetic, synthetic_images, _ = correct(fixture_png, "synthetic_narrow", "clean")
                synthetic_overlap = quad_iou(synthetic["quad"], expected_quad) if synthetic.get("has_quad") else 0.0
                narrow = synthetic_images["image"]
                check("synthetic_narrow.full_receipt_crop", synthetic.get("has_quad") is True and synthetic_overlap >= .90 and narrow.shape[1] / narrow.shape[0] < .45,
                      expected_quad=expected_quad.tolist(), predicted_quad=synthetic.get("quad"), polygon_iou=round(synthetic_overlap, 6),
                      output_shape=list(narrow.shape), source_kind="synthetic")
                check("previews_do_not_create_history", count_rows() == count_before)
                for name, pixels in (("original", auto_images["original_image"]), ("cropped", modes["color"]),
                                     ("clean", modes["clean"]), ("bw", modes["bw"])):
                    _save(output / ("imaging-real-036-" + name + ".png"), pixels)
                _sheet([("Original full-frame preview", auto_images["original_image"]), ("Automatic crop / clean", auto_images["image"]),
                        ("Manual crop / original color", modes["color"]), ("Manual crop / clean", modes["clean"]),
                        ("Manual crop / bw", modes["bw"])], output / "imaging-real-036-comparison.png")
                _save(output / "imaging-real-036-auto-clean.png", auto_images["image"])
                _sheet([("REAL DEV real-036 / full original preview", auto_images["original_image"]),
                        ("REAL DEV real-036 / automatic crop + clean", auto_images["image"])],
                       output / "imaging-real-036-before-after.png", width=900, height=800)
                _save(output / "imaging-synthetic-narrow-original.png", fixture)
                _save(output / "imaging-synthetic-narrow-clean.png", narrow)
                _sheet([("SYNTHETIC / full source", fixture), ("SYNTHETIC / automatic clean crop", narrow)],
                       output / "imaging-synthetic-narrow-comparison.png")
                health = client.get("/api/health")
                model = health.json().get("ocr", {}).get("model")
                check("trained_model_ready", health.status_code == 200 and model == {"id": "rec_head_v1", "sha256": MODEL_SHA})
                result = client.post("/api/ocr/recognize", files={"file": ("manual-clean.png", encoded["clean"], "image/png")})
                body = result.json()
                check("real_clean.real_trained_ocr_nonempty", result.status_code == 200 and body.get("ok") is True and bool(body.get("text", "").strip()) and
                      bool(body.get("boxes")) and body.get("model") == model,
                      text_characters=len(body.get("text", "")), boxes=len(body.get("boxes", [])))
                detail = client.get("/api/history/" + str(body["id"]))
                document = detail.json().get("document", {})
                persisted = _decode(download(document["image"]))
                check("real_clean.history_image_is_exact_ocr_input", np.array_equal(persisted, modes["clean"]) and
                      document.get("image_width") == modes["clean"].shape[1] and document.get("image_height") == modes["clean"].shape[0])
                check("real_clean.history_text_and_boxes_match", document.get("text") == body["text"] and document.get("boxes") == body["boxes"])
                pdf_response = client.post("/api/export/pdf", json={"scan_id": body["id"], "revision": document["revision"], "mode": "searchable"})
                check("real_clean.searchable_pdf_http", pdf_response.status_code == 200 and pdf_response.json().get("mode") == "searchable")
                pdf = pdf_output / "imaging-real-036-searchable.pdf"
                pdf.write_bytes(download(pdf_response.json()["file"]))
                inspected = inspect_document_pdf(pdf, output / "imaging-real-036-clean.png", body["boxes"])
                report["pdf_inspection"] = inspected
                check("real_clean.pdf_image_and_chinese_text_complete", inspected["pass_all"])
                render = temp / "pdf-render.png"
                render_page(pdf, render, size=(modes["clean"].shape[1], modes["clean"].shape[0]))
                with Image.open(render) as raster:
                    rendered = np.array(raster.convert("RGB"))[:, :, ::-1]
                _save(output / "imaging-real-036-pdf-render.png", rendered)
                raw_delta = np.abs(rendered.astype(np.int16)-modes["clean"].astype(np.int16))
                report["pdf_renderer_vs_source_pixels"] = {
                    "mean_absolute_error": float(raw_delta.mean()), "maximum_difference": int(raw_delta.max()),
                    "pixel_exact": bool(np.array_equal(rendered, modes["clean"])),
                    "explanation": "Poppler resamples the non-integer PDF page mapping. Source pixels are instead required to match the embedded image exactly; visible output must match an independent image-only PDF rendered at the same size."}
                reference_pdf, reference_png = temp / "image-only-reference.pdf", temp / "image-only-reference.png"
                _image_only_reference(output / "imaging-real-036-clean.png", reference_pdf)
                render_page(reference_pdf, reference_png, size=(modes["clean"].shape[1], modes["clean"].shape[0]))
                with Image.open(reference_png) as raster:
                    reference = np.array(raster.convert("RGB"))[:, :, ::-1]
                reference_delta = np.abs(rendered.astype(np.int16)-reference.astype(np.int16))
                check("real_clean.pdf_render_matches_independent_image_only_reference", np.array_equal(rendered, reference),
                      mean_absolute_error=float(reference_delta.mean()), maximum_difference=int(reference_delta.max()))
                check("isolated_history_has_only_one_scan", count_rows() == count_before + 1)
                report["model"] = model
                report["pdf"] = pdf.relative_to(ROOT).as_posix()
                report["comparisons"] = ["output/scan/imaging-real-036-comparison.png", "output/scan/imaging-synthetic-narrow-comparison.png",
                                         "output/scan/imaging-real-036-before-after.png"]
        report["temporary_storage_removed"] = True
    finally:
        if previous_model is None:
            os.environ.pop("SNAPSCAN_OCR_MODEL", None)
        else:
            os.environ["SNAPSCAN_OCR_MODEL"] = previous_model
        report["user_database_and_uploads_unchanged"] = protected_state() == before
        report["duration_seconds"] = round(time.perf_counter()-started, 3)
        report["pass_all_automated_checks"] = bool(report["checks"]) and all(item["pass"] for item in report["checks"].values()) and report.get("temporary_storage_removed", False) and report["user_database_and_uploads_unchanged"]
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return 0 if report["pass_all_automated_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
