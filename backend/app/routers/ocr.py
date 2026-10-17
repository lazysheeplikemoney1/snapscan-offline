"""离线OCR：POST /api/ocr/recognize"""
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse
import cv2, numpy as np, time, json
import os
from pathlib import Path
import uuid
from ..services.ocr_engine import get_engine, OCRUnavailableError
from ..services.io_utils import imwrite_unicode
from ..core.config import UPLOAD_DIR
from ..core.db import get_conn, has_search_index

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

@router.post("/recognize")
async def recognize(file: UploadFile = File(...)):
    t0 = time.time()
    raw = await file.read()
    try:
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
    except cv2.error:
        img = None
    if img is None:
        return {"ok": False, "msg": "图片解码失败"}
    try:
        engine = get_engine()
    except OCRUnavailableError as exc:
        return JSONResponse(status_code=503, content={"ok": False, "msg": str(exc)})
    result = engine.recognize(img)
    image_name = "scan_" + uuid.uuid4().hex + ".png"
    image_path = Path(UPLOAD_DIR) / image_name
    image_url = "/uploads/" + image_name
    created_image = False
    conn = None
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        # Reserve exclusively: cleanup can never remove an existing user file.
        with image_path.open("xb"):
            created_image = True
        if not imwrite_unicode(str(image_path), img) or not image_path.is_file() or image_path.stat().st_size == 0:
            raise OSError("保存识别图失败")
        elapsed = int((time.time()-t0)*1000)
        boxes_json = json.dumps(result["boxes"], ensure_ascii=False)
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO scans(created_at,filename,text,boxes,elapsed_ms,image,image_width,image_height,model,revision,original_boxes) "
            "VALUES(?,?,?,?,?,?,?,?,?,0,?)",
            (int(time.time()), file.filename, result["text"], boxes_json, elapsed, image_url,
             int(img.shape[1]), int(img.shape[0]), json.dumps(engine.model_info, ensure_ascii=False), boxes_json),
        )
        sid = cur.lastrowid
        if has_search_index(conn):
            conn.execute("INSERT INTO scans_fts(rowid,text) VALUES(?,?)", (sid, result["text"]))
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        if created_image:
            image_path.unlink(missing_ok=True)
        return JSONResponse(status_code=500, content={"ok": False, "msg": "保存识别图或记录失败，结果未保存"})
    finally:
        if conn is not None:
            conn.close()
    result.update({"ok": True, "id": sid, "elapsed_ms": elapsed,
                   "engine": engine.name, "model": engine.model_info,
                   "image": image_url, "image_width": int(img.shape[1]),
                   "image_height": int(img.shape[0]), "revision": 0})
    return result
