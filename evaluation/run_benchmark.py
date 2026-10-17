# -*- coding: utf-8 -*-
"""一键基准：批量识别 data/samples -> pred.json -> CER + 耗时"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from app.services.ocr_engine import get_engine
from app.services.enhance import enhance_doc
from app.services.doc_detect import correct_document
from app.services.io_utils import imread_unicode
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from metrics import cer

ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="baseline")
ap.add_argument("--limit", type=int, default=50)
args = ap.parse_args()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, "data", "samples")
GT_PATH = os.path.join(ROOT, "evaluation", "testset", "gt.json")

engine = get_engine()
print("engine:", engine.name)
gt = json.load(open(GT_PATH, encoding="utf-8"))
pred, times, cers = {}, [], []
files = sorted([f for f in os.listdir(IMG_DIR) if f.lower().endswith((".jpg", ".png"))])[:args.limit]
for fn in files:
    img = imread_unicode(os.path.join(IMG_DIR, fn))
    if img is None:
        print(fn + " read fail, skip")
        continue
    t0 = time.time()
    if args.tag == "enhance":
        _, img2 = correct_document(img)
        img2 = enhance_doc(img2)
    else:
        img2 = img
    r = engine.recognize(img2)
    dt = int((time.time() - t0) * 1000)
    txt = r["text"].replace(" ", "")
    pred[fn] = txt
    times.append(dt)
    if fn in gt:
        c = cer(txt.replace(" ", ""), gt[fn].replace(" ", ""))
        cers.append(c)
    print((fn + " " + str(dt) + "ms CER=" + ("%.3f" % cers[-1] if cers else "0") + " | " + txt[:40]).encode("gbk", "ignore").decode("gbk"))

json.dump(pred, open(os.path.join(ROOT, "evaluation", "testset", "pred_" + args.tag + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
avg_cer = sum(cers) / max(1, len(cers))
avg_t = sum(times) / max(1, len(times))
bench_out = {"tag": args.tag, "engine": engine.name, "n": len(files), "avg_cer": avg_cer, "avg_ms": avg_t}
json.dump(bench_out, open(os.path.join(ROOT, "evaluation", "benchmark_" + args.tag + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(("[" + args.tag + "] n=" + str(len(files)) + " avgCER=%.3f" % avg_cer + " avgMS=%.0f" % avg_t).encode("gbk", "ignore").decode("gbk"))
