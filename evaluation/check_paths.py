# -*- coding: utf-8 -*-
"""路径可移植检查：后端核心只允许BASE_DIR相对路径。输出 path_check.json。"""
import json, os, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evaluation", "path_check.json")
ALLOW_DIRS = ["mobile", "docs", "evaluation"]  # 移动端配置/文档示例IP允许
hits = []
for r, _, fs in os.walk(os.path.join(ROOT, "backend")):
    for f in fs:
        if not f.endswith(".py"): continue
        p = os.path.join(r, f)
        t = open(p, encoding="utf-8").read()
        for pat in ["E:\\", "E:/", "C:\\pycharm", "192.168.137.1"]:
            if pat in t:
                hits.append({"file": os.path.relpath(p, ROOT), "pattern": pat})
res = {"backend_hardcoded_hits": hits, "pass": len(hits) == 0,
       "note": "mobile/docs中的192.168.137.1为用户配置示例，不计入后端缺陷"}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False), "saved:", OUT)
