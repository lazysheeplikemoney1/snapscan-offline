# -*- coding: utf-8 -*-
"""实验A：图像预处理消融（同一OCR FP32模型，保持完全一致）。
A0 raw -> OCR；A1 +透视；A2 +去阴影；A3 +CLAHE/锐化。
用法：
  python evaluation/run_ablation_A.py --data data/synthetic --gt data/synthetic/gt_synthetic.json --out evaluation/ablation_A_synthetic.json
  python evaluation/run_ablation_A.py --data data/real/test --gt data/real/test/gt.json --out evaluation/ablation_A_realtest.json
输出：各stage的 mean/median/P50/P95 + CER + per-image明细。最终报告只引用 real/test 行。
"""
import argparse, json, os, sys, time, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from app.services.ocr_engine import get_engine
from app.services.pipeline import preprocess_for_stage, STAGES
from app.services.io_utils import imread_unicode
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from metrics import cer, corpus_cer, normalize_text, edit_ops

def pct(vals, q):
    if not vals: return 0.0
    s = sorted(vals)
    k = (len(s)-1) * q
    f = int(k); c = min(f+1, len(s)-1)
    return s[f] + (s[c]-s[f]) * (k-f)

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data/synthetic")
ap.add_argument("--gt", default="data/synthetic/gt_synthetic.json")
ap.add_argument("--out", default="evaluation/ablation_A_synthetic.json")
args = ap.parse_args()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = args.data if os.path.isabs(args.data) else os.path.join(ROOT, args.data)
GT = args.gt if os.path.isabs(args.gt) else os.path.join(ROOT, args.gt)
OUT = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)

engine = get_engine()
print("engine:", engine.name)
gt = json.load(open(GT, encoding="utf-8")) if os.path.exists(GT) else {}
files = sorted([f for f in os.listdir(DATA) if f.lower().endswith((".jpg", ".png"))])
print("n_images:", len(files), "gt:", len(gt))

# 预热一次，排除模型首次加载计入耗时
_warm = os.path.join(DATA, files[0]) if files else None
if _warm:
    _img = imread_unicode(_warm)
    if _img is not None:
        try: engine.recognize(_img)
        except Exception: pass

result = {"engine": engine.name, "data": args.data, "n": len(files), "stages": {}}
for st in STAGES:
    cers, times, details = [], [], []
    n_quad = 0
    for fn in files:
        img = imread_unicode(os.path.join(DATA, fn))
        if img is None: continue
        t0 = time.perf_counter()
        proc, meta = preprocess_for_stage(img, st)
        if meta.get("has_quad"): n_quad += 1
        r = engine.recognize(proc)
        dt = (time.perf_counter()-t0)*1000
        txt = r["text"].replace(" ", "")
        times.append(dt)
        c = None
        if fn in gt:
            c = cer(txt, gt[fn].replace(" ", ""))
            cers.append(c)
        details.append({"file": fn, "ms": round(dt,1), "cer": (None if c is None else round(c,4)), "pred": txt})
    pairs = []
    for d, fn in zip(details, files):
        if fn in gt:
            # 用归一化后文本重算corpus（与cer一致）
            pairs.append((d["pred"], gt[fn]))
    cc = corpus_cer(pairs)
    stat = {
        "n": len(times),
        "corpus_cer": cc["corpus_cer"], "corpus_S": cc["S"], "corpus_D": cc["D"], "corpus_I": cc["I"], "corpus_gt_chars": cc["gt_chars"],
        "n_with_gt": len(cers),
        "quad_rate": (n_quad/max(1,len(times))),
        "cer_mean": (sum(cers)/len(cers) if cers else None),
        "cer_median": (statistics.median(cers) if cers else None),
        "cer_p95": (pct(cers,0.95) if cers else None),
        "ms_mean": (sum(times)/len(times) if times else None),
        "ms_median": (statistics.median(times) if times else None),
        "ms_p95": (pct(times,0.95) if times else None),
        "details": details,
    }
    result["stages"][st] = stat
    print(("%s n=%d CER mean=%.4f median=%.4f P95=%.4f | ms mean=%.0f median=%.0f P95=%.0f quad=%.2f" % (
        st, stat["n"], stat["cer_mean"] or -1, stat["cer_median"] or -1, stat["cer_p95"] or -1,
        stat["ms_mean"] or -1, stat["ms_median"] or -1, stat["ms_p95"] or -1, stat["quad_rate"])).encode("gbk","ignore").decode("gbk"))
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("saved:", OUT)
