"""导出接口：POST /api/export/pdf /api/export/excel"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Literal, Optional
from pathlib import Path
from PIL import Image
import uuid, os, re
from ..services.export import export_txt_to_pdf, export_boxes_to_excel, export_searchable_pdf
from ..services.documents import load_document
from ..core.config import UPLOAD_DIR

router = APIRouter(prefix="/api/export", tags=["export"])

class ExpReq(BaseModel):
    text: str = ""
    boxes: List[Dict] = Field(default_factory=list)
    scan_id: Optional[int] = Field(default=None, gt=0)
    revision: Optional[int] = Field(default=None, ge=0)
    mode: Optional[Literal["searchable", "text"]] = None


def _document(req):
    if req.scan_id is None:
        return None
    document = load_document(req.scan_id)
    if document is None:
        raise HTTPException(404, "扫描记录不存在")
    if req.revision is None:
        raise HTTPException(422, "导出保存的文档时必须提供修订号")
    if req.revision != document["revision"]:
        raise HTTPException(409, "文档已更新，请重新打开后导出")
    return document


def _scan_image(document):
    reference = document.get("image") or ""
    if not re.fullmatch(r"/uploads/scan_[0-9a-f]{32}\.png", reference):
        raise HTTPException(409, "此记录没有可用的扫描图，请重新扫描或选择纯文本 PDF")
    root = Path(UPLOAD_DIR).resolve()
    path = (root / reference.removeprefix("/uploads/")).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(409, "扫描图已不可用，请重新扫描或选择纯文本 PDF")
    try:
        with Image.open(path) as image:
            if image.size != (document.get("image_width"), document.get("image_height")):
                raise ValueError("size mismatch")
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "扫描图与文字坐标不一致，请重新扫描") from exc
    return path


def _output(extension):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"export_{uuid.uuid4().hex}.{extension}"
    return name, Path(UPLOAD_DIR) / name

@router.post("/pdf")
def to_pdf(req: ExpReq):
    document = _document(req)
    mode = req.mode or ("searchable" if document else "text")
    image_path = None
    if mode == "searchable":
        if document is None:
            raise HTTPException(422, "保留版式 PDF 需要已保存的扫描记录")
        image_path = _scan_image(document)
    text, boxes = (document["text"], document["boxes"]) if document else (req.text, req.boxes)
    fname, path = _output("pdf")
    try:
        if mode == "searchable":
            export_searchable_pdf(image_path, boxes, path)
        else:
            export_txt_to_pdf(text, boxes, str(path))
    except (ValueError, OSError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "file": "/uploads/" + fname, "mode": mode,
            "revision": document["revision"] if document else None}

@router.post("/excel")
def to_excel(req: ExpReq):
    document = _document(req)
    boxes = document["boxes"] if document else req.boxes
    if not document and req.text and not boxes:
        boxes = [{"text": line} for line in req.text.splitlines()]
    fname, path = _output("xlsx")
    try:
        export_boxes_to_excel(boxes, path)
    except (ValueError, OSError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "file": "/uploads/" + fname, "kind": "ocr_rows",
            "revision": document["revision"] if document else None}
