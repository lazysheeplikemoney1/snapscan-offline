# -*- coding: utf-8 -*-
"""Synthetic数据集检查：50张存在/可读/尺寸>0/gt覆盖/文本非空。输出 dataset_check_synthetic.json。"""
import json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services.io_utils import imread_unicode
DATA = os.path.join(ROOT, "data", "synthetic")
GT = os.path.join(ROOT, "data", "synthetic", "gt_synthetic.json")
OUT = os.path.join(ROOT, "evaluation", "dataset_check_synthetic.json")
gt = json.load(open(GT, encoding="utf-8"))
files = sorted([f for f in os.listdir(DATA) if f.endswith(".jpg")])
bad = []
for fn in files:
    im = imread_unicode(os.path.join(DATA, fn))
    if im is None or im.shape[0] == 0 or fn not in gt or not gt[fn].strip():
        bad.append(fn)
res = {"data": "data/synthetic", "n_images": len(files), "n_gt": len(gt),
       "missing_gt": [f for f in files if f not in gt],
       "unreadable": bad, "pass": (len(files) == 50 and not bad and len(gt) == 50)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(res, ensure_ascii=False), "saved:", OUT)
