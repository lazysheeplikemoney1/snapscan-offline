# -*- coding: utf-8 -*-
"""实验B（可行性）：B0=Det FP32+Rec FP32；B1=Det INT8+Rec FP32；B2=Det FP32+Rec INT8；B3=Det INT8+Rec INT8。
相同输入/相同预处理A3/相同synthetic数据。比较 model_size_mb / corpus_cer / mean/median/P95 / load_success。
策略：CNN优先static（需synthetic校准集），Rec若含LSTM/Transformer则dynamic。不覆盖原FP32模型。
输出：evaluation/quant_B_synthetic.json。不预设-60%/<1%。
"""
import argparse, json, os, sys, time, statistics, shutil
import onnx  # preimport，避免cwd遮蔽导致循环导入
ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data/synthetic")
ap.add_argument("--limit", type=int, default=20)
ap.add_argument("--calib", type=int, default=10)
args = ap.parse_args()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = args.data if os.path.isabs(args.data) else os.path.join(ROOT, args.data)
OUT = os.path.join(ROOT, "evaluation", "quant_B_synthetic.json")
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "evaluation", "tools"))
from metrics import corpus_cer, normalize_text

def pct(v, q):
    s = sorted(v); k = (len(s)-1)*q; f = int(k); c = min(f+1, len(s)-1)
    return s[f]+(s[c]-s[f])*(k-f) if s else 0

# 1. 定位RapidOCR onnx
import rapidocr_onnxruntime as R
base = os.path.dirname(R.__file__)
cands = []
for r, _, fs in os.walk(os.path.expanduser("~/.rapidocr")):
    for f in fs:
        if f.endswith(".onnx"): cands.append(os.path.join(r, f))
for r, _, fs in os.walk(base):
    for f in fs:
        if f.endswith(".onnx"): cands.append(os.path.join(r, f))
# 分类det/rec
det_fp32 = next((p for p in cands if "det" in os.path.basename(p).lower()), None)
rec_fp32 = next((p for p in cands if "rec" in os.path.basename(p).lower()), None)
res = {"data": args.data, "limit": args.limit, "models_found": cands, "combos": {}}
print("det:", det_fp32)
print("rec:", rec_fp32)
if not det_fp32 or not rec_fp32:
    res["status"] = "BLOCKED: 找不到det/rec onnx，无法量化"
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("BLOCKED saved:", OUT)
    raise SystemExit(0)

# 2. 隔离复制到 models/fp32
fp32dir = os.path.join(ROOT, "backend", "models", "fp32")
int8dir = os.path.join(ROOT, "backend", "models", "int8")
os.makedirs(fp32dir, exist_ok=True); os.makedirs(int8dir, exist_ok=True)
shutil.copy(det_fp32, os.path.join(fp32dir, "det_fp32.onnx"))
shutil.copy(rec_fp32, os.path.join(fp32dir, "rec_fp32.onnx"))

def mb(p): return round(os.path.getsize(p)/1024/1024, 2)
res["fp32_size"] = {"det_mb": mb(det_fp32), "rec_mb": mb(rec_fp32)}

# 3. 量化：det static（CNN），rec dynamic（序列结构）。校准集只用synthetic前calib张。
from onnxruntime.quantization import quantize_dynamic, QuantType
try:
    from onnxruntime.quantization import quantize_static, CalibrationDataReader
    has_static = True
except Exception as e:
    has_static = False
    res["static_import"] = "UNSUPPORTED:" + str(e)

from app.services.io_utils import imread_unicode
import numpy as np, cv2
files = sorted([f for f in os.listdir(DATA) if f.lower().endswith((".jpg", ".png"))])
calib_files = files[:args.calib]

det_int8 = os.path.join(int8dir, "det_int8_dynamic.onnx")
rec_int8 = os.path.join(int8dir, "rec_int8_dynamic.onnx")
status = {}
try:
    quantize_dynamic(det_fp32, det_int8, weight_type=QuantType.QInt8)
    status["det_dynamic"] = "OK"
except Exception as e:
    status["det_dynamic"] = "FAILED:" + str(e)[:300]
try:
    quantize_dynamic(rec_fp32, rec_int8, weight_type=QuantType.QInt8)
    status["rec_dynamic"] = "OK"
except Exception as e:
    status["rec_dynamic"] = "FAILED:" + str(e)[:300]
res["quantize_status"] = status
res["int8_size"] = {}
for k, p in [("det_mb", det_int8), ("rec_mb", rec_int8)]:
    if os.path.exists(p): res["int8_size"][k] = mb(p)

# 4. B0-B3：用RapidOCR RapidOCR(det,rec,cls)显式加载对应onnx，A3预处理，corpus_cer+耗时
from rapidocr_onnxruntime import RapidOCR
from app.services.pipeline import preprocess_for_stage
GT = os.path.join(ROOT, "data", "synthetic", "gt_synthetic.json")
gt = json.load(open(GT, encoding="utf-8")) if os.path.exists(GT) else {}
test_files = files[:args.limit]
# warm + 计时函数
import onnxruntime as ort
def bench(det_p, rec_p, tag):
    import yaml, tempfile
    base_cfg = os.path.join(os.path.dirname(R.__file__), "config.yaml")
    cfg = yaml.safe_load(open(base_cfg, encoding="utf-8")) if os.path.exists(base_cfg) else {"Global": {}, "Det": {}, "Rec": {}, "Cls": {}}
    # rapidocr内部用update_model_path拼接包内models/，这里直接给绝对路径最稳
    cfg.setdefault("Det", {})["model_path"] = det_p
    cfg.setdefault("Rec", {})["model_path"] = rec_p
    # cls保持默认
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    yaml.safe_dump(cfg, tmp, allow_unicode=True)
    tmp.close()
    try:
        eng = RapidOCR(config_path=tmp.name)
    except Exception as e:
        return {"status": "FAILED load:" + str(e)[:300], "tag": tag}
    try:
        im0 = imread_unicode(os.path.join(DATA, test_files[0]))
        p0, _ = preprocess_for_stage(im0, "A3")
        eng(p0)
    except Exception:
        pass
    pairs, times = [], []
    for fn in test_files:
        im = imread_unicode(os.path.join(DATA, fn))
        if im is None: continue
        proc, _ = preprocess_for_stage(im, "A3")
        t0 = time.perf_counter()
        try:
            r, _ = eng(proc)
        except Exception as e:
            return {"status": "FAILED infer:" + str(e)[:300], "tag": tag}
        dt = (time.perf_counter()-t0)*1000
        txt = normalize_text("\n".join([t for _, t, _ in (r or [])]))
        times.append(dt)
        if fn in gt: pairs.append((txt, gt[fn]))
    cc = corpus_cer(pairs)
    return {"status": "OK", "tag": tag, "n": len(times),
            "corpus_cer": cc["corpus_cer"], "S": cc["S"], "D": cc["D"], "I": cc["I"], "gt_chars": cc["gt_chars"],
            "ms_mean": sum(times)/max(1,len(times)), "ms_median": statistics.median(times) if times else 0,
            "ms_p95": pct(times, 0.95), "det": det_p, "rec": rec_p,
            "det_mb": mb(det_p) if os.path.exists(det_p) else -1,
            "rec_mb": mb(rec_p) if os.path.exists(rec_p) else -1}

combos = {
    "B0": (det_fp32, rec_fp32),
    "B1": (det_int8 if os.path.exists(det_int8) else det_fp32, rec_fp32),
    "B2": (det_fp32, rec_int8 if os.path.exists(rec_int8) else rec_fp32),
    "B3": (det_int8 if os.path.exists(det_int8) else det_fp32, rec_int8 if os.path.exists(rec_int8) else rec_fp32),
}
for tag, (d, r_) in combos.items():
    need_int8_det = tag in ("B1", "B3")
    need_int8_rec = tag in ("B2", "B3")
    det_is_fp32 = (d == det_fp32)
    rec_is_fp32 = (r_ == rec_fp32)
    if (need_int8_det and det_is_fp32) or (need_int8_rec and rec_is_fp32):
        res["combos"][tag] = {"status": "NOT_RUN: INT8模型生成失败，未用FP32冒充", "tag": tag,
                              "quantize_status": status, "det": d, "rec": r_}
        print(tag, "NOT_RUN")
        continue
    print("bench", tag, d, r_)
    res["combos"][tag] = bench(d, r_, tag)
    print(tag, res["combos"][tag].get("status"), "corpus=", res["combos"][tag].get("corpus_cer"))
res["note"] = "dynamic为可行性验证；static需校准集，real/test禁用校准。结论以实测为准。"
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("saved:", OUT)
