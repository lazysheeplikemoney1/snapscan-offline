"""Render product scan modes on fixed DEV invoices; no OCR or final-test access."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.enhance import enhance_doc, enhance_scan, quality_metrics
from app.services.io_utils import imread_unicode, imwrite_unicode


def _legacy_unchanged():
    path = "backend/app/services/enhance.py"
    baseline = subprocess.run(["git", "show", "HEAD:" + path], cwd=ROOT, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout
    current = (ROOT / path).read_text(encoding="utf-8")
    names = {"shadow_normalize", "clahe_sharpen", "enhance_doc", "apply_stage", "blur_score", "is_blurry"}
    def functions(source):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name in names}
    return functions(baseline) == functions(current)


def _sheet(images, path, *, crop=None):
    cell_width = (crop[2] - crop[0]) if crop else 440
    cell_height = (crop[3] - crop[1]) if crop else 600
    sheet = Image.new("RGB", (len(images) * cell_width, cell_height + 38), "#dedede")
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(images.items()):
        x = index * cell_width
        draw.text((x + 10, 12), label, fill="black")
        source = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        thumb = source.crop(crop) if crop else ImageOps.contain(source, (cell_width - 12, cell_height - 12))
        sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, 38 + (cell_height - thumb.height) // 2))
    sheet.save(path)


def main():
    output = ROOT / "output/scan"
    output.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_unchanged()
    if not legacy:
        raise RuntimeError("Frozen experimental function definitions have changed")
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "legacy_experimental_functions_ast_unchanged": legacy,
              "source_split": "dev", "test_images_processed": 0, "ocr_run": False, "ocr_model_changed": False,
              "purpose": "visual enhancement preview, not an OCR accuracy or thermal-receipt generalization claim",
              "visual_review_completed": False, "samples": []}
    for filename in ("real-036.jpg", "real-043.jpg", "real-064.jpg"):
        source = ROOT / "data/real/dev/dev/222" / filename
        image = imread_unicode(str(source))
        if image is None:
            raise RuntimeError("Cannot decode fixed DEV image")
        images = {"Original color": image}
        times = {}
        for mode in ("clean", "bw"):
            tic = time.perf_counter()
            enhanced = enhance_scan(image, mode)
            times[mode] = round((time.perf_counter() - tic) * 1000, 3)
            images[mode] = enhanced
            path = output / f"enhance-{source.stem}-{mode}.png"
            if not imwrite_unicode(str(path), enhanced):
                raise RuntimeError("Failed to save preview image")
        images["Legacy A3 (frozen)"] = enhance_doc(image, "A3")
        full = output / f"enhance-{source.stem}-comparison.png"
        detail = output / f"enhance-{source.stem}-smalltext-100pct.png"
        height, width = image.shape[:2]
        left, top = round(width * .20), round(height * (.43 if width < height else .34))
        crop = (left, top, min(width, left + 780), min(height, top + 360))
        _sheet(images, full)
        _sheet(images, detail, crop=crop)
        report["samples"].append({"source_image_id": filename, "source_path": source.relative_to(ROOT).as_posix(),
                                  "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                  "input_shape": list(image.shape), "mode_shapes": {name: list(value.shape) for name, value in images.items()},
                                  "comparison_png": full.relative_to(ROOT).as_posix(), "smalltext_100pct_png": detail.relative_to(ROOT).as_posix(),
                                  "detail_roi_pixels": list(crop), "quality_original": quality_metrics(image),
                                  "quality_clean": quality_metrics(images["clean"]), "elapsed_ms": times})
        print(json.dumps({"sample": filename, "elapsed_ms": times, "shape": list(image.shape)}), flush=True)
    target = ROOT / "reports/scan_enhancement_preview.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
