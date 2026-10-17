# -*- coding: utf-8 -*-
"""断网冷启动HTTP验收（人工在第二终端执行，后端已在第一终端启动）。
流程：health -> 新图correct -> recognize -> history/list,search -> export/pdf,xlsx
新图：现生成 uploads/offline_new_coldstart.jpg（从未处理过），中文断网验证专用。
只用标准库urllib，不依赖外网。输出 evaluation/offline_coldstart_result.json
用法（项目根，第二终端）：
  C:/pycharm/python.exe evaluation/offline_coldstart_http.py
"""
import io, json, os, sys, time, urllib.request, urllib.parse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:8000"
OUT = os.path.join(ROOT, "evaluation", "offline_coldstart_result.json")
res = {"base": BASE, "ts": int(time.time()), "checks": {}}
def ck(k, ok, extra=None):
    res["checks"][k] = {"pass": bool(ok), **(extra or {})}

def get(p):
    with urllib.request.urlopen(BASE + p, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "ignore")

def post_multipart(p, filepath):
    import uuid
    boundary = uuid.uuid4().hex
    with open(filepath, "rb") as f:
        data = f.read()
    fname = os.path.basename(filepath)
    body = (("--" + boundary + "\r\n").encode() +
            ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % fname).encode() +
            "Content-Type: image/jpeg\r\n\r\n".encode() + data + ("\r\n--" + boundary + "--\r\n").encode())
    req = urllib.request.Request(BASE + p, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, r.read().decode("utf-8", "ignore")

def post_json(p, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + p, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "ignore")

# 0. 生成新图（PIL本机字体，断网可用）
new_img = os.path.join(ROOT, "uploads", "offline_new_coldstart.jpg")
try:
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for fp in ["C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc"]:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 40)
                break
            except Exception:
                pass
    img = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(img)
    d.text((30, 80), "断网冷启动验证新图2026-09-11", fill="black", font=font)
    img.save(new_img)
    ck("new_image", os.path.exists(new_img), {"file": new_img})
except Exception as e:
    ck("new_image", False, {"err": str(e)[:300]})

# 1. health
try:
    code, body = get("/api/health")
    j = json.loads(body)
    ck("health", code == 200 and j.get("status") == "ok", {"body": j})
except Exception as e:
    ck("health", False, {"err": str(e)[:300]})

# 2. correct
corr = None
try:
    code, body = post_multipart("/api/scan/correct", new_img)
    j = json.loads(body)
    corr = j
    ck("correct", j.get("ok") is True, {"has_quad": j.get("has_quad"), "elapsed_ms": j.get("elapsed_ms")})
except Exception as e:
    ck("correct", False, {"err": str(e)[:300]})

# 3. recognize（用原新图直调，保证新图链路）
ocr = None
try:
    code, body = post_multipart("/api/ocr/recognize", new_img)
    j = json.loads(body)
    ocr = j
    eng = str(j.get("engine", ""))
    ck("recognize", j.get("ok") is True and "rapid" in eng.lower() and "dummy" not in eng.lower(),
       {"engine": eng, "text": j.get("text", "")[:80], "n_boxes": len(j.get("boxes", []))})
except Exception as e:
    ck("recognize", False, {"err": str(e)[:300]})

# 4. history
try:
    code, body = get("/api/history/list?limit=5")
    j = json.loads(body)
    ck("history_list", j.get("ok") is True and len(j.get("items", [])) >= 1, {"count": len(j.get("items", []))})
    sid = (ocr or {}).get("id")
    code2, body2 = get(f"/api/history/{sid}")
    j2 = json.loads(body2)
    ck("history_detail", j2.get("ok") is True, {"id": sid})
    q = "冷启动"
    code3, body3 = get("/api/history/search?q=" + urllib.parse.quote(q))
    j3 = json.loads(body3)
    ck("history_search", j3.get("ok") is True and len(j3.get("items", [])) >= 1, {"q": q, "method": j3.get("method"), "hits": len(j3.get("items", []))})
except Exception as e:
    ck("history_list", False, {"err": str(e)[:300]})
    ck("history_detail", False, {})
    ck("history_search", False, {})

# 5. export
try:
    txt = (ocr or {}).get("text", "") or "断网冷启动验证新图"
    boxes = (ocr or {}).get("boxes", []) or [{"text": txt[:8], "score": 0.9, "box": [[0,0],[1,0],[1,1],[0,1]]}]
    code, body = post_json("/api/export/pdf", {"text": txt, "boxes": boxes})
    j = json.loads(body)
    ck("export_pdf", j.get("ok") is True, {"file": j.get("file")})
    code, body = post_json("/api/export/excel", {"text": txt, "boxes": boxes})
    j = json.loads(body)
    ck("export_excel", j.get("ok") is True, {"file": j.get("file")})
except Exception as e:
    ck("export_pdf", False, {"err": str(e)[:300]})
    ck("export_excel", False, {})

res["pass_all"] = all(v.get("pass") for v in res["checks"].values())
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False, indent=2))
print("saved:", OUT, "pass_all:", res["pass_all"])
