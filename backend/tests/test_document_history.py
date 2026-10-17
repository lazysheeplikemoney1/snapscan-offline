"""Document persistence/correction regressions, using temporary storage only."""
import copy
import importlib
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OLD_BOXES = [
    {"text": "旧金额12.00", "score": 0.91, "box": [[1, 1], [14, 1], [14, 5], [1, 5]]},
    {"text": "日期2026-10-15", "score": 0.82, "box": [[1, 6], [14, 6], [14, 10], [1, 10]]},
]
MODEL = {"id": "fixture-rec", "sha256": "a" * 64}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    # db.init_db runs at import: config must be patched first.
    config = importlib.import_module("app.core.config")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    db = importlib.import_module("app.core.db")
    monkeypatch.setattr(db, "DB_PATH", config.DB_PATH)
    db.init_db()
    ocr = importlib.import_module("app.routers.ocr")
    history = importlib.import_module("app.routers.history")
    monkeypatch.setattr(ocr, "UPLOAD_DIR", str(uploads), raising=False)

    class FixtureEngine:
        name = "rapidocr-fixture"
        model_info = MODEL
        def recognize(self, image):
            return {"text": "\n".join(box["text"] for box in OLD_BOXES), "boxes": copy.deepcopy(OLD_BOXES)}

    monkeypatch.setattr(ocr, "get_engine", FixtureEngine)
    app = FastAPI()
    app.mount("/uploads", StaticFiles(directory=uploads), name="uploads")
    app.include_router(ocr.router)
    app.include_router(history.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"client": client, "db": db, "ocr": ocr, "uploads": uploads, "tmp": tmp_path}


def recognize(runtime):
    image = np.arange(16 * 24 * 3, dtype=np.uint16).reshape(16, 24, 3).astype(np.uint8)
    raw = cv2.imencode(".png", image)[1].tobytes()
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("原始拍照.png", raw, "image/png")})
    assert response.status_code == 200, response.text
    return response.json(), image


def row(runtime, sid):
    with runtime["db"].get_conn() as connection:
        return dict(connection.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone())


def hits(runtime, query):
    response = runtime["client"].get("/api/history/search", params={"q": query})
    assert response.status_code == 200
    return {item["id"] for item in response.json()["items"]}


def test_legacy_schema_migration_is_idempotent_and_preserves_rows(runtime, monkeypatch):
    legacy = runtime["tmp"] / "legacy.sqlite3"
    with sqlite3.connect(legacy) as connection:
        connection.execute("CREATE TABLE scans(id INTEGER PRIMARY KEY,created_at INTEGER,filename TEXT,text TEXT,boxes TEXT,elapsed_ms INTEGER)")
        connection.execute("INSERT INTO scans VALUES(7,123,'旧图.jpg','原文',?,30)", (json.dumps(OLD_BOXES),))
    monkeypatch.setattr(runtime["db"], "DB_PATH", str(legacy))
    runtime["db"].init_db()
    runtime["db"].init_db()
    value = row(runtime, 7)
    assert value["filename"] == "旧图.jpg" and value["text"] == "原文"
    assert value["revision"] == 0
    assert value["image"] is None
    assert json.loads(value["original_boxes"]) == OLD_BOXES
    documents = importlib.import_module("app.services.documents")
    document = documents.load_document(7)
    assert document["boxes"] == OLD_BOXES and document["model"] == {}
    assert document["image"] is None and document["image_width"] is None
    assert documents.load_document(999) is None


def test_ocr_persists_exact_decoded_image_model_and_original_boxes(runtime):
    body, image = recognize(runtime)
    assert re.fullmatch(r"/uploads/scan_[0-9a-f]{32}\.png", body["image"])
    assert (body["image_width"], body["image_height"], body["revision"]) == (24, 16, 0)
    download = runtime["client"].get(body["image"])
    restored = cv2.imdecode(np.frombuffer(download.content, np.uint8), cv2.IMREAD_COLOR)
    assert download.status_code == 200 and np.array_equal(restored, image)
    value = row(runtime, body["id"])
    assert json.loads(value["original_boxes"]) == OLD_BOXES
    assert json.loads(value["model"]) == MODEL
    detail = runtime["client"].get(f"/api/history/{body['id']}").json()
    assert isinstance(detail["item"]["boxes"], str)
    assert detail["document"]["boxes"] == OLD_BOXES
    assert detail["document"]["model"] == MODEL


def test_corrections_persist_text_keep_geometry_and_replace_search_index(runtime):
    body, _ = recognize(runtime)
    sid = body["id"]
    texts = ["新金额15.00", "日期已核对"]
    response = runtime["client"].patch(f"/api/history/{sid}", json={"texts": texts, "revision": 0})
    assert response.status_code == 200, response.text
    document = response.json()["document"]
    assert document["revision"] == 1 and document["text"] == "\n".join(texts)
    assert document["original_boxes"] == OLD_BOXES
    assert document["image"] == body["image"]
    for old, new, text in zip(OLD_BOXES, document["boxes"], texts):
        assert new == {**old, "text": text}
    assert sid in hits(runtime, "新金额") and sid not in hits(runtime, "旧金额")
    with runtime["db"].get_conn() as connection:
        assert connection.execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", ('"新金额15"',)).fetchall()
        assert not connection.execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", ('"旧金额12"',)).fetchall()
    documents = importlib.import_module("app.services.documents")
    assert documents.load_document(sid) == document
    assert runtime["client"].get(f"/api/history/{sid}").json()["document"] == document


def test_stale_revision_is_409_and_does_not_overwrite(runtime):
    body, _ = recognize(runtime)
    url = f"/api/history/{body['id']}"
    accepted = runtime["client"].patch(url, json={"texts": ["已修订", "第二行"], "revision": 0})
    assert accepted.status_code == 200
    rejected = runtime["client"].patch(url, json={"texts": ["过期覆盖", "第二行"], "revision": 0})
    assert rejected.status_code == 409
    assert row(runtime, body["id"])["text"] == "已修订\n第二行"


@pytest.mark.parametrize("payload", [
    {"texts": ["只有一行"], "revision": 0},
    {"texts": ["a", "b"], "revision": -1},
    {"texts": ["a", "b"], "revision": True},
    {"texts": [1, "b"], "revision": 0},
])
def test_invalid_corrections_are_422_without_changing_document(runtime, payload):
    body, _ = recognize(runtime)
    original = row(runtime, body["id"])
    response = runtime["client"].patch(f"/api/history/{body['id']}", json=payload)
    assert response.status_code == 422
    assert row(runtime, body["id"]) == original


def test_missing_document_is_404(runtime):
    response = runtime["client"].patch("/api/history/999", json={"texts": [], "revision": 0})
    assert response.status_code == 404


def test_legacy_document_without_boxes_is_readable_but_not_editable(runtime):
    with runtime["db"].get_conn() as connection:
        sid = connection.execute("INSERT INTO scans(text,boxes,model) VALUES('旧文本','[]',NULL)").lastrowid
    detail = runtime["client"].get(f"/api/history/{sid}").json()
    assert detail["document"]["image"] is None
    assert detail["document"]["model"] == {}
    assert detail["item"]["model"] is None
    response = runtime["client"].patch(f"/api/history/{sid}", json={"texts": [], "revision": 0})
    assert response.status_code == 422 and "识别框" in response.text


def test_no_fts_still_saves_and_searches_corrected_document(runtime):
    with runtime["db"].get_conn() as connection:
        connection.execute("DROP TABLE scans_fts")
    body, _ = recognize(runtime)
    response = runtime["client"].patch(f"/api/history/{body['id']}", json={"texts": ["离线纠错", "正常"], "revision": 0})
    assert response.status_code == 200
    assert body["id"] in hits(runtime, "离线纠错")


def test_failed_correction_rolls_back_document_and_old_fts_index(runtime):
    body, _ = recognize(runtime)
    with runtime["db"].get_conn() as connection:
        connection.execute("CREATE TRIGGER reject_edit BEFORE UPDATE ON scans BEGIN SELECT RAISE(ABORT,'fixture reject'); END")
    response = runtime["client"].patch(f"/api/history/{body['id']}", json={"texts": ["回滚测试", "新行"], "revision": 0})
    assert response.status_code == 500
    assert row(runtime, body["id"])["revision"] == 0
    assert body["id"] in hits(runtime, "旧金额") and body["id"] not in hits(runtime, "回滚测试")
    with runtime["db"].get_conn() as connection:
        assert connection.execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", ('"旧金额12"',)).fetchall()


@pytest.mark.parametrize("write_error", [False, True])
def test_image_save_failure_creates_no_record_and_removes_only_own_file(runtime, monkeypatch, write_error):
    sentinel = runtime["uploads"] / "existing.png"
    sentinel.write_bytes(b"existing user file")
    def fail(path, image):
        Path(path).write_bytes(b"partial image")
        if write_error:
            raise OSError("fixture write error")
        return False
    monkeypatch.setattr(runtime["ocr"], "imwrite_unicode", fail, raising=False)
    raw = cv2.imencode(".png", np.zeros((16, 24, 3), np.uint8))[1].tobytes()
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("page.png", raw, "image/png")})
    assert response.status_code == 500
    with runtime["db"].get_conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert list(runtime["uploads"].iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"existing user file"


def test_filename_collision_never_overwrites_or_deletes_existing_image(runtime, monkeypatch):
    existing = runtime["uploads"] / ("scan_" + "a" * 32 + ".png")
    existing.write_bytes(b"existing user image")
    monkeypatch.setattr(runtime["ocr"].uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    raw = cv2.imencode(".png", np.zeros((16, 24, 3), np.uint8))[1].tobytes()
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("page.png", raw, "image/png")})
    assert response.status_code == 500
    assert existing.read_bytes() == b"existing user image"
    with runtime["db"].get_conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_jpeg_exif_orientation_keeps_saved_pixels_and_box_coordinates_consistent(runtime, monkeypatch):
    from PIL import Image
    source = np.zeros((10, 18, 3), np.uint8)
    source[:5, :9] = [255, 30, 10]
    source[5:, 9:] = [10, 230, 70]
    exif = Image.Exif()
    exif[274] = 6  # Camera orientation: the decoder rotates the pixels 90 degrees.
    buffer = io.BytesIO()
    Image.fromarray(source).save(buffer, format="JPEG", quality=95, exif=exif)
    raw = buffer.getvalue()
    expected = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert expected.shape[:2] == (18, 10)
    captured = {}
    class OrientedEngine:
        name = "rapidocr-fixture"
        model_info = MODEL
        def recognize(self, image):
            captured["image"] = image.copy()
            height, width = image.shape[:2]
            return {"text": "旋转原图", "boxes": [{"text": "旋转原图", "score": 0.9,
                    "box": [[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]]}]}
    monkeypatch.setattr(runtime["ocr"], "get_engine", OrientedEngine)
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("camera.jpg", raw, "image/jpeg")})
    assert response.status_code == 200
    document = response.json()
    restored = cv2.imdecode(np.frombuffer(runtime["client"].get(document["image"]).content, np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(captured["image"], expected)
    assert np.array_equal(restored, expected)
    assert (document["image_width"], document["image_height"]) == (10, 18)
    assert document["boxes"][0]["box"] == [[0, 0], [9, 0], [9, 17], [0, 17]]


def test_database_insert_failure_removes_new_image(runtime):
    with runtime["db"].get_conn() as connection:
        connection.execute("CREATE TRIGGER reject_save BEFORE INSERT ON scans BEGIN SELECT RAISE(ABORT,'fixture reject'); END")
    raw = cv2.imencode(".png", np.zeros((16, 24, 3), np.uint8))[1].tobytes()
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("page.png", raw, "image/png")})
    assert response.status_code == 500
    assert list(runtime["uploads"].iterdir()) == []


def test_search_merges_literal_matches_when_fts_already_has_a_hit(runtime):
    ids = []
    with runtime["db"].get_conn() as connection:
        for text in ("invoice", "preinvoicepost"):
            sid = connection.execute("INSERT INTO scans(text,boxes) VALUES(?,'[]')", (text,)).lastrowid
            connection.execute("INSERT INTO scans_fts(rowid,text) VALUES(?,?)", (sid, text))
            ids.append(sid)
    assert hits(runtime, "invoice") == set(ids)


def test_search_treats_percent_as_literal_character(runtime):
    with runtime["db"].get_conn() as connection:
        wanted = connection.execute("INSERT INTO scans(text,boxes) VALUES('税率100%','[]')").lastrowid
        connection.execute("INSERT INTO scans(text,boxes) VALUES('税率100元','[]')")
    assert hits(runtime, "100%") == {wanted}


@pytest.mark.parametrize("raw", [b"", b"not an image"])
def test_invalid_upload_creates_no_records_or_images(runtime, raw):
    response = runtime["client"].post("/api/ocr/recognize", files={"file": ("bad.jpg", raw, "image/jpeg")})
    assert response.status_code == 200 and response.json()["ok"] is False
    with runtime["db"].get_conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert list(runtime["uploads"].iterdir()) == []
