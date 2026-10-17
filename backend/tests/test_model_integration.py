"""Verify the deployed weights and fail clearly instead of storing dummy OCR."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import db
from app.services import ocr_engine

TRAINED_SHA = "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe"
BASE_SHA = "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"


@pytest.fixture(autouse=True)
def isolated_engine_and_history(monkeypatch, tmp_path):
    monkeypatch.delenv("SNAPSCAN_OCR_MODEL", raising=False)
    monkeypatch.setattr(ocr_engine, "_singleton", None)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "history.db"))
    db.init_db()


def test_default_health_identifies_the_trained_model():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    model = response.json().get("ocr", {}).get("model", {})
    assert model.get("id") == "rec_head_v1"
    assert model.get("sha256") == TRAINED_SHA
    engine = ocr_engine.get_engine()
    actual_path = Path(engine.engine.text_rec.session.session._model_path)
    assert hashlib.sha256(actual_path.read_bytes()).hexdigest() == TRAINED_SHA


def test_explicit_pretrained_rollback(monkeypatch):
    monkeypatch.setenv("SNAPSCAN_OCR_MODEL", "pretrained")
    engine = ocr_engine.get_engine()
    assert getattr(engine, "model_info", {}).get("id") == "pretrained"
    actual_path = Path(engine.engine.text_rec.session.session._model_path)
    assert hashlib.sha256(actual_path.read_bytes()).hexdigest() == BASE_SHA


@pytest.mark.parametrize("bad_file", [None, b"damaged onnx"])
def test_missing_or_corrupt_model_is_not_silently_replaced(monkeypatch, tmp_path, bad_file):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "manifest.json").write_text(json.dumps({"models": {
        "rec_head_v1": {"path": "rec_head_v1.onnx", "sha256": TRAINED_SHA}
    }}), encoding="utf-8")
    if bad_file is not None:
        (model_dir / "rec_head_v1.onnx").write_bytes(bad_file)
    monkeypatch.setattr(ocr_engine, "MODELS_DIR", str(model_dir))
    with pytest.raises(RuntimeError, match="模型"):
        ocr_engine.get_engine()


def test_unknown_model_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("SNAPSCAN_OCR_MODEL", "typo")
    with pytest.raises(RuntimeError, match="模型"):
        ocr_engine.get_engine()


def test_load_failure_returns_503_without_writing_history(monkeypatch):
    def unavailable():
        raise RuntimeError("模型不可用")
    monkeypatch.setattr(ocr_engine, "RapidEngine", unavailable)
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 503
    assert health.json()["ocr"]["ready"] is False
    raw = cv2.imencode(".png", np.full((100, 200, 3), 255, np.uint8))[1].tobytes()
    response = client.post("/api/ocr/recognize", files={"file": ("page.png", raw, "image/png")})
    assert response.status_code == 503
    assert response.json()["ok"] is False
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
