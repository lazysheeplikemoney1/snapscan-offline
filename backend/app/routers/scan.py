# -*- coding: utf-8 -*-
"""Prepare a reviewable document crop; OCR runs only after user confirmation."""
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Literal, Optional
from pathlib import Path
import cv2, numpy as np, time, os, uuid, json
from ..services.doc_detect import detect_document, validate_quad, four_point_transform
from ..services.enhance import enhance_scan, quality_metrics
from ..services.io_utils import imwrite_unicode
from ..core.config import UPLOAD_DIR

router = APIRouter(prefix="/api/scan", tags=["scan"])

@router.post("/correct")
async def correct(file: UploadFile = File(...), mode: Literal["color", "clean", "bw"] = Form("clean"),
                  quad: Optional[str] = Form(None)):
    t0 = time.time()
    raw = await file.read()
    if not raw:
        return {"ok": False, "msg": "空文件"}
    buf = np.frombuffer(raw, np.uint8)
    if buf.size == 0:
        return {"ok": False, "msg": "空文件"}
    try:
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return {"ok": False, "msg": "图片解码失败"}
    if img is None:
        return {"ok": False, "msg": "图片解码失败"}
    if quad is not None:
        try:
            points = validate_quad(json.loads(quad), img.shape)
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(422, "裁切四角无效：请按左上、右上、右下、左下顺序选择图内完整区域") from exc
        detection = {"quad": points, "confidence": 1.0, "method": "manual",
                     "confidence_kind": "manual_not_a_prediction"}
    else:
        detection = detect_document(img)
        points = detection["quad"]
    try:
        warped = four_point_transform(img, points) if points is not None else img
        enhanced = enhance_scan(warped, mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    quality = quality_metrics(warped)
    warnings = list(detection.get("warnings", [])) + list(quality.get("warnings", []))
    if points is None:
        warnings.insert(0, "未找到可靠的文档边界，当前保留整图；请调整四角或明确选择保留整图")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fid = uuid.uuid4().hex
    # Only previews are downscaled. The OCR input remains the full-resolution crop.
    def preview(image):
        h, w = image.shape[:2]
        factor = min(1., 1600. / max(h, w))
        return cv2.resize(image, (max(1, round(w*factor)), max(1, round(h*factor))), interpolation=cv2.INTER_AREA) if factor < 1 else image
    names = {"original_image": f"original_{fid}.jpg", "cropped_image": f"cropped_{fid}.jpg",
             "image": f"corrected_{fid}.png"}
    created = []
    try:
        for key, pixels in (("original_image", preview(img)), ("cropped_image", preview(warped)), ("image", enhanced)):
            path = Path(UPLOAD_DIR) / names[key]
            with path.open("xb"):
                created.append(path)
            params = [cv2.IMWRITE_JPEG_QUALITY, 95] if path.suffix == ".jpg" else []
            if not imwrite_unicode(str(path), pixels, params) or path.stat().st_size == 0:
                raise OSError("image write failed")
    except OSError as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise HTTPException(500, "扫描图保存失败，请重试；本次处理未保存") from exc
    summary = {key: value for key, value in detection.items() if key != "quad"}
    return {
        "ok": True,
        "has_quad": points is not None,
        "quad": np.asarray(points).tolist() if points is not None else None,
        **{key: "/uploads/" + name for key, name in names.items()},
        "size": list(enhanced.shape[:2]),
        "source_size": list(img.shape[:2]), "mode": mode,
        "detection": summary, "quality": quality, "warnings": warnings,
        "elapsed_ms": int((time.time()-t0)*1000),
    }
