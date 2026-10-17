# -*- coding: utf-8 -*-
"""PDF验收：存在/页数/pypdf可搜中文。输出 pdf_acceptance.json。"""
import json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services.export import export_txt_to_pdf
from pypdf import PdfReader
SAMPLE = "卷积神经网络用于图像特征提取\n发票金额128.50元 日期2026-09-11\n离线智能扫描仪课程大作业"
OUT_PDF = os.path.join(ROOT, "uploads", "accept_pdf_check.pdf")
OUT_JSON = os.path.join(ROOT, "evaluation", "pdf_acceptance.json")
export_txt_to_pdf(SAMPLE, [], OUT_PDF)
rd = PdfReader(OUT_PDF)
txt = "\n".join([(p.extract_text() or "") for p in rd.pages])
keys = ["卷积神经网络", "发票金额", "离线智能"]
res = {"file": OUT_PDF, "pages": len(rd.pages),
       "checks": {k: (k in txt) for k in keys},
       "pass": len(rd.pages) >= 1 and all(k in txt for k in keys),
       "note": "pypdf可提取=文本层存在；最终Ctrl+F GUI人工补验"}
json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False), "saved:", OUT_JSON)
