# -*- coding: utf-8 -*-
"""验收：PDF可搜索性 + FTS中文检索（评审意见13/14）。
用法：python evaluation/accept_pdf_fts.py
- PDF：生成含中文样本 -> 用pypdf提取文本 -> 断言关键词可搜（模拟Ctrl+F）
- FTS：建临时库插3条中文 -> 查"卷积神经网络/神经网络/图像特征/特征提取" -> 输出命中情况
  FTS5中文默认按字符/词边界与英文不同，此脚本如实记录行为，不美化结论。
"""
import os, sys, sqlite3
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services.export import export_txt_to_pdf

SAMPLE = "卷积神经网络用于图像特征提取\n发票金额128.50元 日期2026-09-11\n离线智能扫描仪课程大作业"
pdf_path = os.path.join(ROOT, "uploads", "accept_sample.pdf")
export_txt_to_pdf(SAMPLE, [], pdf_path)
print("pdf:", pdf_path)

from pypdf import PdfReader
rd = PdfReader(pdf_path)
txt = "\n".join([(p.extract_text() or "") for p in rd.pages])
checks = ["卷积神经网络", "发票金额", "离线智能"]
print("--- PDF可搜索验收 ---")
for k in checks:
    print(("PASS" if k in txt else "FAIL") + " 搜:" + k)
print("extract_preview:", txt[:60].replace("\n","/"))

print("--- FTS中文验收 ---")
conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE scans(id INTEGER PRIMARY KEY, text TEXT)")
try:
    conn.execute("CREATE VIRTUAL TABLE scans_fts USING fts5(text, content='scans', content_rowid='id')")
    use_fts = True
except Exception as e:
    print("FTS5不可用，回退LIKE:", e)
    use_fts = False
docs = ["卷积神经网络用于图像特征提取", "发票金额128.50元 日期2026-09-11", "离线智能扫描仪课程大作业"]
for i, d in enumerate(docs, 1):
    conn.execute("INSERT INTO scans(id,text) VALUES(?,?)", (i, d))
    if use_fts:
        try: conn.execute("INSERT INTO scans_fts(rowid,text) VALUES(?,?)", (i, d))
        except Exception as e: print("fts insert fail:", e)
queries = ["卷积神经网络", "神经网络", "图像特征", "特征提取", "发票", "128.50"]
for q in queries:
    try:
        if use_fts:
            rows = conn.execute("SELECT rowid FROM scans_fts WHERE scans_fts MATCH ?", (q,)).fetchall()
            # FTS无命中则回退LIKE并标注，便于报告如实写
            if not rows:
                rows2 = conn.execute("SELECT id FROM scans WHERE text LIKE ?", ("%" + q + "%",)).fetchall()
                print("QUERY %s -> FTS:0 LIKE:%d （建议报告写：FTS未命中，回退LIKE命中%d）" % (q, len(rows2), len(rows2)))
            else:
                print("QUERY %s -> FTS:%d" % (q, len(rows)))
        else:
            rows = conn.execute("SELECT id FROM scans WHERE text LIKE ?", ("%" + q + "%",)).fetchall()
            print("QUERY %s -> LIKE:%d" % (q, len(rows)))
    except Exception as e:
        try:
            rows = conn.execute("SELECT id FROM scans WHERE text LIKE ?", ("%" + q + "%",)).fetchall()
            print("QUERY %s -> FTS异常回退LIKE:%d (%s)" % (q, len(rows), e))
        except Exception as e2:
            print("QUERY %s -> FAIL %s" % (q, e2))
print("DONE. 若FTS对长中文词不命中， search 接口已有LIKE回退，保持该设计并在报告如实说明。")
