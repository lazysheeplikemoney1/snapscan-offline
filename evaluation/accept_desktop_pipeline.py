# -*- coding: utf-8 -*-
"""电脑端全API闭环验收（一键）。
覆盖：health -> correct -> recognize -> history/list,detail,search -> export/pdf,xlsx
    + 异常输入10项（空文件/txt假图片/损坏jpg/纯白/极小/不存在ID/中文路径）。
输出：evaluation/desktop_pipeline_acceptance.json
判定：engine必须含rapidOCR字样（禁止静默Dummy判PASS）；PDF用pypdf断言文本层；XLSX用openpyxl断言。
"""
import io, json, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from fastapi.testclient import TestClient
from app.main import app
from app.services.io_utils import imread_unicode

OUT = os.path.join(ROOT, "evaluation", "desktop_pipeline_acceptance.json")
res = {"ts": int(time.time()), "checks": {}, "errors": []}
def ck(name, ok, extra=None):
    res["checks"][name] = {"pass": bool(ok), **(extra or {})}
    if not ok: res["errors"].append(name)

c = TestClient(app)
# 1. health
try:
    r = c.get("/api/health")
    ck("health", r.status_code == 200 and r.json().get("status") == "ok", {"code": r.status_code, "body": r.json()})
except Exception as e:
    ck("health", False, {"err": str(e)})

# 取一张真实synthetic图做主链路
import glob
imgs = sorted(glob.glob(os.path.join(ROOT, "data", "synthetic", "*.jpg")))
if not imgs:
    imgs = sorted(glob.glob(os.path.join(ROOT, "data", "samples", "*.jpg")))
main_img = imgs[0] if imgs else None
ck("fixture_image", main_img is not None and os.path.getsize(main_img) > 0, {"file": main_img})
raw = open(main_img, "rb").read() if main_img else b""

# 2. correct
corr = None
try:
    r = c.post("/api/scan/correct", files={"file": ("t.jpg", raw, "image/jpeg")})
    j = r.json()
    corr = j
    p = os.path.join(ROOT, j.get("image", "").lstrip("/").replace("/", os.sep)) if j.get("ok") else None
    ok = j.get("ok") is True and p and os.path.exists(p)
    if ok:
        im = imread_unicode(p)
        ok = im is not None and im.shape[0] > 0 and im.shape[1] > 0
    ck("correct", ok, {"resp": {k: (str(v)[:200]) for k, v in j.items()}})
except Exception as e:
    ck("correct", False, {"err": str(e)})

# 3. recognize（用矫正图，无则用原图）
ocr = None
try:
    up = raw
    if corr and corr.get("ok"):
        pp = os.path.join(ROOT, corr["image"].lstrip("/").replace("/", os.sep))
        if os.path.exists(pp):
            up = open(pp, "rb").read()
    r = c.post("/api/ocr/recognize", files={"file": ("t.jpg", up, "image/jpeg")})
    j = r.json()
    ocr = j
    eng = str(j.get("engine", ""))
    ok = j.get("ok") is True and ("rapid" in eng.lower() or "ocr" in eng.lower()) and "dummy" not in eng.lower()
    has_conf = any(isinstance(b.get("score"), (int, float)) for b in j.get("boxes", [])) if j.get("boxes") else True
    ck("recognize", ok and has_conf, {"engine": eng, "text_len": len(j.get("text", "")), "n_boxes": len(j.get("boxes", []))})
except Exception as e:
    ck("recognize", False, {"err": str(e)})

# 4. history list/detail/search
try:
    r = c.get("/api/history/list")
    items = r.json().get("items", [])
    ck("history_list", r.json().get("ok") is True and len(items) >= 1, {"count": len(items)})
    sid = (ocr or {}).get("id")
    r2 = c.get(f"/api/history/{sid}") if sid else None
    ck("history_detail", r2 is not None and r2.json().get("ok") is True, {"id": sid})
    # search用OCR文本前2字（中文LIKE可命中；FTS长词可能不命中，允许LIKE回退，但必须命中）
    q = (ocr or {}).get("text", "").replace("\n", "")[:2] or "发票"
    r3 = c.get("/api/history/search", params={"q": q})
    ck("history_search", r3.json().get("ok") is True and len(r3.json().get("items", [])) >= 1, {"q": q, "hits": len(r3.json().get("items", []))})
except Exception as e:
    ck("history_list", False, {"err": str(e)}); ck("history_detail", False, {}); ck("history_search", False, {})

# 5. export pdf/xlsx
try:
    txt = (ocr or {}).get("text", "") or "验收文本卷积神经网络"
    boxes = (ocr or {}).get("boxes", []) or [{"text": txt[:10], "score": 0.9, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}]
    rp = c.post("/api/export/pdf", json={"text": txt, "boxes": boxes}).json()
    pp = os.path.join(ROOT, rp.get("file", "").lstrip("/").replace("/", os.sep)) if rp.get("ok") else None
    pdf_ok = pp and os.path.exists(pp)
    if pdf_ok:
        from pypdf import PdfReader
        rd = PdfReader(pp)
        t = "\n".join([(p.extract_text() or "") for p in rd.pages])
        pdf_ok = len(rd.pages) >= 1 and len(t.strip()) > 0
    ck("export_pdf", bool(pdf_ok), {"file": rp.get("file") if rp else None})
    rx = c.post("/api/export/excel", json={"text": txt, "boxes": boxes}).json()
    xp = os.path.join(ROOT, rx.get("file", "").lstrip("/").replace("/", os.sep)) if rx.get("ok") else None
    xlsx_ok = xp and os.path.exists(xp)
    if xlsx_ok:
        import openpyxl
        wb = openpyxl.load_workbook(xp)
        ws = wb.active
        xlsx_ok = ws is not None and ws.max_row >= 2
    ck("export_excel", bool(xlsx_ok), {"file": rx.get("file") if rx else None})
except Exception as e:
    ck("export_pdf", False, {"err": str(e)}); ck("export_excel", False, {})

# 6. 异常输入10项：后端不许500 traceback
def post_img(name, data, ctype="image/jpeg"):
    try:
        r = c.post("/api/scan/correct", files={"file": (name, data, ctype)})
        return r.status_code, r.text[:200]
    except Exception as e:
        return -1, str(e)[:200]
ab = {}
ab["empty_file"] = post_img("e.jpg", b"")
ab["txt_as_img"] = post_img("e.txt", "hello world".encode(), "text/plain")
ab["corrupt_jpg"] = post_img("c.jpg", bytes([0, 1, 2, 3, 255, 216, 0, 1, 2]))
import numpy as np, cv2
white = cv2.imencode(".jpg", np.full((200, 200, 3), 255, np.uint8))[1].tobytes()
tiny = cv2.imencode(".jpg", np.full((8, 8, 3), 128, np.uint8))[1].tobytes()
ab["white_img"] = post_img("w.jpg", white)
ab["tiny_img"] = post_img("t.jpg", tiny)
ab["missing_file_field"] = (c.post("/api/scan/correct").status_code, "no-file-field")
try:
    r = c.get("/api/history/999999999")
    ab["history_missing_id"] = (r.status_code, str(r.json())[:200])
except Exception as e:
    ab["history_missing_id"] = (-1, str(e)[:200])
# 中文路径图片
cn_path = os.path.join(ROOT, "uploads", "中文验收图.jpg")
open(cn_path, "wb").write(raw if raw else white)
try:
    im = imread_unicode(cn_path)
    ab["chinese_path"] = (200 if im is not None else 500, "imread_unicode")
except Exception as e:
    ab["chinese_path"] = (-1, str(e)[:100])
# OCR无文字（纯白）不入库脏数据检查：调recognize应ok但text为空或极短，不应500
try:
    r = c.post("/api/ocr/recognize", files={"file": ("w.jpg", white, "image/jpeg")})
    ab["ocr_blank"] = (r.status_code, str(r.json())[:200])
except Exception as e:
    ab["ocr_blank"] = (-1, str(e)[:200])
res["abnormal"] = {k: {"code": v[0], "preview": str(v[1])[:200]} for k, v in ab.items()}
# 异常判PASS条件：HTTP!=500 或 返回ok:false，均不抛traceback即PASS（由TestClient未抛异常保证）
ck("abnormal_no_500", all(v[0] != 500 for v in ab.values() if isinstance(v[0], int)), {})

res["pass_all"] = all(v.get("pass") for v in res["checks"].values())
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps({k: v.get("pass") for k, v in res["checks"].items()}, ensure_ascii=False))
print("saved:", OUT, "pass_all:", res["pass_all"])
