# -*- coding: utf-8 -*-
"""FTS三组对照：current FTS / LIKE / trigram。输出 fts_comparison.json。保持LIKE回退。"""
import json, os, sqlite3, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evaluation", "fts_comparison.json")
QUERIES = ["卷积神经网络", "神经网络", "图像特征", "特征提取", "发票", "离线智能", "128.50", "扫描仪"]
DOCS = ["卷积神经网络用于图像特征提取", "发票金额128.50元 日期2026-09-11", "离线智能扫描仪课程大作业"]
def build(tokenize=None):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE scans(id INTEGER PRIMARY KEY, text TEXT)")
    if tokenize:
        conn.execute(f"CREATE VIRTUAL TABLE scans_fts USING fts5(text, content='scans', content_rowid='id', tokenize='{tokenize}')")
    else:
        conn.execute("CREATE VIRTUAL TABLE scans_fts USING fts5(text, content='scans', content_rowid='id')")
    for i, d in enumerate(DOCS, 1):
        conn.execute("INSERT INTO scans(id,text) VALUES(?,?)", (i, d))
        conn.execute("INSERT INTO scans_fts(rowid,text) VALUES(?,?)", (i, d))
    return conn
conns = {}
try:
    conns["current"] = build(None)
    cur_ok = True
except Exception as e:
    cur_ok = "ERR:" + str(e)
try:
    conns["trigram"] = build("trigram")
    tri_ok = True
except Exception as e:
    conns["trigram"] = None
    tri_ok = "UNSUPPORTED:" + str(e)
rows = []
for q in QUERIES:
    r = {"query": q}
    # current
    try:
        t0 = time.perf_counter()
        hits = conns["current"].execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", (q,)).fetchall() if cur_ok is True else []
        r["fts_current_hit"] = len(hits) > 0
        r["fts_current_ms"] = round((time.perf_counter()-t0)*1000, 2)
    except Exception:
        r["fts_current_hit"] = False
        r["fts_current_ms"] = -1
    # like
    t0 = time.perf_counter()
    like_hits = conns["current"].execute("SELECT id FROM scans WHERE text LIKE ?", ("%" + q + "%",)).fetchall() if cur_ok is True else []
    r["like_hit"] = len(like_hits) > 0
    r["like_ms"] = round((time.perf_counter()-t0)*1000, 2)
    # trigram
    if conns["trigram"] is not None:
        try:
            t0 = time.perf_counter()
            th = conns["trigram"].execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", (q,)).fetchall()
            r["trigram_hit"] = len(th) > 0
            r["trigram_ms"] = round((time.perf_counter()-t0)*1000, 2)
        except Exception:
            r["trigram_hit"] = False
            r["trigram_ms"] = -1
    else:
        r["trigram_hit"] = "N/A"
        r["trigram_ms"] = -1
    rows.append(r)
res = {"queries": rows, "trigram_status": tri_ok, "decision": "保持LIKE回退，不删除"}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False)[:2000], "saved:", OUT)
