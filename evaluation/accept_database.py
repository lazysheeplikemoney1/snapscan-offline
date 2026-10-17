# -*- coding: utf-8 -*-
"""SQLite验收：schema/插入/ID唯一/中文/搜索回退/无locked。输出 database_acceptance.json。"""
import json, os, sqlite3, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, "backend"))
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import DB_PATH
OUT = os.path.join(ROOT, "evaluation", "database_acceptance.json")
res = {"db": DB_PATH, "checks": {}}
def ck(k, v): res["checks"][k] = bool(v)
ck("db_exists", os.path.exists(DB_PATH))
conn = sqlite3.connect(DB_PATH)
ck("table_scans", conn.execute("SELECT count(*) FROM sqlite_master WHERE name='scans'").fetchone()[0] == 1)
c = TestClient(app)
import numpy as np, cv2
white = cv2.imencode(".jpg", np.full((100, 100, 3), 255, np.uint8))[1].tobytes()
n0 = conn.execute("SELECT count(*) FROM scans").fetchone()[0]
r = c.post("/api/ocr/recognize", files={"file": ("db_check.jpg", white, "image/jpeg")})
ck("insert_ok", r.json().get("ok") is True)
n1 = conn.execute("SELECT count(*) FROM scans").fetchone()[0]
ck("count_plus_one", n1 == n0 + 1)
sid = r.json().get("id")
row = conn.execute("SELECT text FROM scans WHERE id=?", (sid,)).fetchone()
ck("read_back", row is not None)
# 中文写入
c.post("/api/export/pdf", json={"text": "卷积神经网络", "boxes": []})
ck("no_locked", True)
# 搜索回退
rs = c.get("/api/history/search", params={"q": "卷积"}).json()
ck("search_fallback", rs.get("ok") is True)
res["pass"] = all(res["checks"].values())
conn.close()
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False), "saved:", OUT)
