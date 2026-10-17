"""Isolated APP acceptance for deployed OCR weights; never reads the real test set.

Run with C:/pycharm/python.exe -B -X utf8 evaluation/accept_model_integration.py.
Only aggregate metrics and source IDs are written to the report. OCR text,
search terms, API response bodies and downloaded artifacts remain in memory or
the temporary directory, which is removed when this process exits.
"""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "model_integration_acceptance.json"
EXPECTED_SHA = {
    "rec_head_v1": "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe",
    "pretrained": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
}


class CheckFailed(RuntimeError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def user_state():
    """Fingerprint the actual user DB and upload tree without changing either."""
    databases = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = ROOT / ("snapscan.db" + suffix)
        databases[path.name] = sha256(path) if path.is_file() else None
    uploads = ROOT / "uploads"
    files = {}
    if uploads.exists():
        for path in uploads.rglob("*"):
            if path.is_file():
                stat = path.stat()
                files[path.relative_to(uploads).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    return {"databases": databases, "uploads": files}


def normalize(text):
    return "".join(text.split())


def main():
    started = time.perf_counter()
    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "purpose": "APP integration acceptance; output volume and latency are not accuracy metrics",
        "checks": {}, "samples": [], "comparisons": [],
        "privacy": {"ocr_text_saved": False, "receipt_text_saved": False,
                    "test_set_read": False},
    }

    def check(name, condition, **metrics):
        ok = bool(condition)
        report["checks"][name] = {"pass": ok, **metrics}
        print(f"{'PASS' if ok else 'FAIL'} {name}", flush=True)
        if not ok:
            raise CheckFailed(name)

    def error(name, exc):
        # Do not persist exception messages: libraries can embed private text.
        report["checks"][name] = {"pass": False, "exception_type": type(exc).__name__}
        print(f"FAIL {name} ({type(exc).__name__})", flush=True)

    before = user_state()
    previous_model = os.environ.get("SNAPSCAN_OCR_MODEL")
    try:
        if "app.core.db" in sys.modules or "app.main" in sys.modules:
            raise RuntimeError("Run acceptance in a fresh Python process")
        with tempfile.TemporaryDirectory(prefix="snapscan_model_acceptance_") as temporary:
            temp = Path(temporary)
            upload_dir = temp / "uploads"
            upload_dir.mkdir()
            sys.path.insert(0, str(ROOT / "backend"))
            # This MUST precede importing app.main or app.core.db (init_db runs
            # during db import, and routes bind these config values at import).
            from app.core import config
            config.DB_PATH = str(temp / "isolated.sqlite3")
            config.UPLOAD_DIR = str(upload_dir)
            from app.main import app
            from app.core import db
            from app.routers import scan, export_router
            from app.services import ocr_engine
            from fastapi.testclient import TestClient
            import cv2
            import numpy as np
            from pypdf import PdfReader
            import openpyxl

            check("isolated_runtime_paths",
                  Path(db.DB_PATH).parent == temp
                  and Path(scan.UPLOAD_DIR) == upload_dir
                  and Path(export_router.UPLOAD_DIR) == upload_dir
                  and any(getattr(route, "path", None) == "/uploads"
                          and Path(route.app.directory) == upload_dir for route in app.routes))
            report["environment"] = {"python": sys.version.split()[0], "executable": sys.executable}
            for package in ("rapidocr-onnxruntime", "onnxruntime", "fastapi", "httpx", "pypdf", "openpyxl"):
                report["environment"][package] = importlib.metadata.version(package)

            selected = {}
            for line in (ROOT / "training/data/val.jsonl").read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                kind = row["document_type"]
                if kind not in ("书籍", "票据", "课件") or kind in selected:
                    continue
                source = (ROOT / "data/real" / row["source_image_path"]).resolve()
                if (row.get("split") != "val" or row.get("source_split") != "dev"
                        or not source.is_relative_to((ROOT / "data/real/dev").resolve())):
                    raise RuntimeError("Fixture must be a complete dev source of a validation crop")
                selected[kind] = {"sample_id": row["source_image_id"],
                                  "document_type": kind, "path": source,
                                  "source_sha256": row["source_sha256"]}
            check("three_validation_dev_sources", set(selected) == {"书籍", "票据", "课件"},
                  sample_count=len(selected))
            check("deployed_artifact_matches_training_export",
                  sha256(ROOT / "backend/models/rec_head_v1.onnx") == EXPECTED_SHA["rec_head_v1"]
                  == sha256(ROOT / "training/runs/rec_head_v1/rec_finetuned.onnx"))

            manifest = json.loads((ROOT / "backend/models/manifest.json").read_text(encoding="utf-8"))
            raw_inputs = {}
            raw_new = {}
            record_ids = []

            def select_model(client, model_id, tag):
                os.environ["SNAPSCAN_OCR_MODEL"] = model_id
                ocr_engine._singleton = None
                gc.collect()
                response = client.get("/api/health")
                body = response.json()
                model = body.get("ocr", {}).get("model", {})
                check(tag + ".health", response.status_code == 200 and body.get("status") == "ok"
                      and body.get("ocr", {}).get("ready") is True
                      and model == {"id": model_id, "sha256": EXPECTED_SHA[model_id]})
                engine = ocr_engine.get_engine()
                actual_path = Path(engine.engine.text_rec.session.session._model_path).resolve()
                expected_path = (Path(ocr_engine.MODELS_DIR) / manifest["models"][model_id]["path"]).resolve()
                check(tag + ".actual_ort_model", actual_path == expected_path
                      and sha256(actual_path) == EXPECTED_SHA[model_id],
                      model_id=model_id, sha256=sha256(actual_path),
                      providers=engine.engine.text_rec.session.session.get_providers())

            def recognize(client, payload, sample_id, model_id, tag):
                start = time.perf_counter()
                response = client.post("/api/ocr/recognize",
                                       files={"file": (sample_id, payload, "image/jpeg")})
                duration = (time.perf_counter() - start) * 1000
                body = response.json()
                boxes = body.get("boxes", [])
                valid_boxes = bool(boxes) and all(
                    isinstance(box.get("text"), str) and bool(box["text"].strip())
                    and isinstance(box.get("score"), (float, int))
                    and math.isfinite(box["score"]) and 0 <= box["score"] <= 1
                    and np.asarray(box.get("box")).shape == (4, 2)
                    and np.isfinite(np.asarray(box["box"], dtype=float)).all()
                    for box in boxes)
                model = body.get("model", {})
                check(tag, response.status_code == 200 and body.get("ok") is True
                      and "rapid" in str(body.get("engine", "")).lower()
                      and model == {"id": model_id, "sha256": EXPECTED_SHA[model_id]}
                      and bool(body.get("text", "").strip()) and valid_boxes
                      and body.get("text") == "\n".join(box["text"] for box in boxes),
                      model_id=model_id, text_chars=len(body.get("text", "")),
                      boxes=len(boxes), http_ms=round(duration, 3),
                      server_ms=body.get("elapsed_ms"))
                record_ids.append(body["id"])
                return body, duration

            with TestClient(app) as client:
                select_model(client, "rec_head_v1", "trained")
                for kind in ("书籍", "票据", "课件"):
                    item = selected[kind]
                    sid = item["sample_id"]
                    tag = sid.rsplit(".", 1)[0]
                    try:
                        raw = item["path"].read_bytes()
                        raw_inputs[sid] = raw
                        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
                        check(tag + ".original_dev_source", image is not None
                              and hashlib.sha256(raw).hexdigest() == item["source_sha256"],
                              height=int(image.shape[0]) if image is not None else 0,
                              width=int(image.shape[1]) if image is not None else 0)
                        report["samples"].append({"sample_id": sid, "document_type": kind,
                                                  "source_split": "dev", "selected_from": "val",
                                                  "height": int(image.shape[0]), "width": int(image.shape[1])})

                        response = client.post("/api/scan/correct",
                                               files={"file": (sid, raw, "image/jpeg")})
                        corrected = response.json()
                        check(tag + ".correct", response.status_code == 200
                              and corrected.get("ok") is True
                              and str(corrected.get("image", "")).startswith("/uploads/"),
                              has_quad=corrected.get("has_quad"), size=corrected.get("size"))
                        download = client.get(corrected["image"])
                        decoded = cv2.imdecode(np.frombuffer(download.content, np.uint8), cv2.IMREAD_COLOR)
                        check(tag + ".download_corrected", download.status_code == 200
                              and decoded is not None and list(decoded.shape[:2]) == corrected["size"])
                        ocr, _ = recognize(client, download.content, sid, "rec_head_v1", tag + ".recognize_corrected")

                        response = client.get(f"/api/history/{ocr['id']}")
                        history = response.json()
                        detail = history.get("item", {})
                        check(tag + ".history_detail", response.status_code == 200
                              and history.get("ok") is True and detail.get("text") == ocr["text"]
                              and json.loads(detail.get("boxes", "[]")) == ocr["boxes"])
                        response = client.get("/api/history/list", params={"limit": 100})
                        check(tag + ".history_list", response.status_code == 200
                              and response.json().get("ok") is True
                              and ocr["id"] in [row["id"] for row in response.json().get("items", [])])
                        words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", ocr["text"])
                        query = words[0][:4] if words else ocr["boxes"][0]["text"][:2]
                        response = client.get("/api/history/search", params={"q": query})
                        check(tag + ".history_search", response.status_code == 200
                              and response.json().get("ok") is True
                              and ocr["id"] in [row["id"] for row in response.json().get("items", [])],
                              hits=len(response.json().get("items", [])))

                        payload = {"text": ocr["text"], "boxes": ocr["boxes"]}
                        response = client.post("/api/export/pdf", json=payload)
                        exported = response.json()
                        check(tag + ".export_pdf", response.status_code == 200 and exported.get("ok") is True)
                        response = client.get(exported["file"])
                        check(tag + ".download_pdf", response.status_code == 200
                              and response.content.startswith(b"%PDF"))
                        reader = PdfReader(io.BytesIO(response.content))
                        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
                        check(tag + ".pdf_text_layer", len(reader.pages) > 0
                              and normalize(extracted) == normalize(ocr["text"]),
                              pages=len(reader.pages), extracted_chars=len(extracted))
                        response = client.post("/api/export/excel", json=payload)
                        exported = response.json()
                        check(tag + ".export_xlsx", response.status_code == 200 and exported.get("ok") is True)
                        response = client.get(exported["file"])
                        check(tag + ".download_xlsx", response.status_code == 200
                              and response.content.startswith(b"PK"))
                        workbook = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True, data_only=True)
                        worksheet = workbook.active
                        rows = list(worksheet.iter_rows(values_only=True))
                        check(tag + ".xlsx_rows", len(rows) == len(ocr["boxes"]) + 1
                              and all(row[1] == box["text"] and abs(float(row[2])-box["score"]) < 1e-12
                                      for row, box in zip(rows[1:], ocr["boxes"])),
                              rows=len(rows), expected_rows=len(ocr["boxes"]) + 1)
                        workbook.close()
                        raw_new[sid] = recognize(client, raw, sid, "rec_head_v1", tag + ".recognize_original_trained")
                    except Exception as exc:
                        error(tag + ".pipeline_exception", exc)

                select_model(client, "pretrained", "pretrained")
                for kind in ("书籍", "票据", "课件"):
                    item = selected[kind]
                    sid = item["sample_id"]
                    tag = sid.rsplit(".", 1)[0]
                    try:
                        old, old_ms = recognize(client, raw_inputs[sid], sid, "pretrained", tag + ".recognize_original_pretrained")
                        new, new_ms = raw_new[sid]
                        check(tag + ".same_input_comparison", old["model"]["id"] == "pretrained"
                              and new["model"]["id"] == "rec_head_v1"
                              and len(old["text"]) > 0 and len(new["text"]) > 0)
                        report["comparisons"].append({
                            "sample_id": sid, "document_type": kind,
                            "input_sha256": hashlib.sha256(raw_inputs[sid]).hexdigest(),
                            "pretrained": {"text_chars": len(old["text"]), "boxes": len(old["boxes"]),
                                           "http_ms": round(old_ms, 3), "server_ms": old["elapsed_ms"]},
                            "rec_head_v1": {"text_chars": len(new["text"]), "boxes": len(new["boxes"]),
                                            "http_ms": round(new_ms, 3), "server_ms": new["elapsed_ms"]},
                            "text_equal": old["text"] == new["text"],
                            "text_char_delta": len(new["text"]) - len(old["text"]),
                            "http_ms_delta": round(new_ms - old_ms, 3),
                        })
                    except Exception as exc:
                        error(tag + ".comparison_exception", exc)

                response = client.get("/api/history/list", params={"limit": 100})
                items = response.json().get("items", [])
                check("isolated_history_record_count", len(record_ids) == 9 and len(items) == 9
                      and set(record_ids) == {row["id"] for row in items}, rows=len(items), expected_rows=9)
                select_model(client, "rec_head_v1", "restored_trained")
                report["privacy"]["temporary_database_and_downloads_removed"] = True
    except Exception as exc:
        error("acceptance_exception", exc)
    finally:
        if previous_model is None:
            os.environ.pop("SNAPSCAN_OCR_MODEL", None)
        else:
            os.environ["SNAPSCAN_OCR_MODEL"] = previous_model
        if "app.services.ocr_engine" in sys.modules:
            sys.modules["app.services.ocr_engine"]._singleton = None
        try:
            check("user_database_and_uploads_unchanged", user_state() == before)
        except CheckFailed:
            pass
        report["duration_seconds"] = round(time.perf_counter() - started, 3)
        report["check_count"] = len(report["checks"])
        report["passed_count"] = sum(row["pass"] for row in report["checks"].values())
        report["failed_count"] = report["check_count"] - report["passed_count"]
        report["pass_all"] = report["failed_count"] == 0
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("check_count", "passed_count", "failed_count", "pass_all", "duration_seconds")}), flush=True)
        print(f"Report: {OUT}", flush=True)
    return 0 if report["pass_all"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
