from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt, StrictStr
from typing import List
import json

from ..core.db import get_conn, has_search_index
from ..services.documents import document_from_row
from ..services.layout import build_layout
router = APIRouter(prefix="/api/history", tags=["history"])

@router.get("/list")
def list_history(limit: int = 50):
    conn = get_conn()
    rows = conn.execute("SELECT id, created_at, filename, substr(text,1,200) as snippet, elapsed_ms FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return {"ok": True, "items": [dict(r) for r in rows]}

@router.get("/search")
def search(q: str, limit: int = 20):
    """Merge FTS and literal substrings so partial Chinese hits are not lost."""
    conn = get_conn()
    method = "like_fallback"
    try:
        fts_rows = []
        if has_search_index(conn):
            try:
                fts_rows = conn.execute("SELECT s.id,s.created_at,s.filename,substr(s.text,1,200) as snippet FROM scans_fts f JOIN scans s ON s.id=f.rowid WHERE scans_fts MATCH ? LIMIT ?", (q, limit)).fetchall()
            except Exception:
                method = "like_exception"
        literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_rows = conn.execute("SELECT id,created_at,filename,substr(text,1,200) as snippet FROM scans WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?", ("%" + literal + "%", limit)).fetchall()
        merged = {row["id"]: dict(row) for row in [*fts_rows, *like_rows]}
        if fts_rows:
            method = "fts+like" if len(merged) > len(fts_rows) else "fts"
        items = sorted(merged.values(), key=lambda value: value["id"], reverse=True)
        return {"ok": True, "method": method, "items": items[:limit] if limit >= 0 else items}
    finally:
        conn.close()

@router.get("/{sid}")
def detail(sid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "msg": "not found"}
    return {"ok": True, "item": dict(row), "document": document_from_row(row)}


class Correction(BaseModel):
    texts: List[StrictStr]
    revision: StrictInt = Field(ge=0)


@router.patch("/{sid}")
def correct_texts(sid: int, correction: Correction):
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        document = document_from_row(row)
        if correction.revision != document["revision"]:
            raise HTTPException(status_code=409, detail="记录已更新，请刷新后再保存")
        boxes = document["boxes"]
        if not boxes or not all(isinstance(box, dict) and isinstance(box.get("box"), list)
                                and len(box["box"]) == 4 for box in boxes):
            raise HTTPException(status_code=422, detail="原记录没有可编辑的识别框，请重新识别")
        if len(correction.texts) != len(boxes):
            raise HTTPException(status_code=422, detail="纠错行数必须与识别框数量相同")
        corrected = [{**box, "text": text} for box, text in zip(boxes, correction.texts)]
        text = build_layout(corrected)["text"]
        indexed = has_search_index(conn)
        # External-content FTS DELETE must see the old scans.text, before UPDATE.
        if indexed:
            conn.execute("DELETE FROM scans_fts WHERE rowid=?", (sid,))
        changed = conn.execute("UPDATE scans SET text=?,boxes=?,revision=revision+1 WHERE id=? AND revision=?",
                               (text, json.dumps(corrected, ensure_ascii=False), sid, correction.revision))
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="记录已更新，请刷新后再保存")
        if indexed:
            conn.execute("INSERT INTO scans_fts(rowid,text) VALUES(?,?)", (sid, text))
        updated = conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone()
        conn.commit()
        return {"ok": True, "document": document_from_row(updated)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        return JSONResponse(status_code=500, content={"ok": False, "msg": "保存纠错失败，原记录未修改"})
    finally:
        conn.close()
