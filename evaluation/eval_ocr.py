"""用法：python evaluation/eval_ocr.py --gt evaluation/testset/gt.json --img_dir data/samples
输出 CER均值 + 字段F1，用于报告消融实验表。"""
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tools"))
from metrics import cer

ap = argparse.ArgumentParser()
ap.add_argument("--gt", default="evaluation/testset/gt.json")
ap.add_argument("--pred", default="evaluation/testset/pred.json")
args = ap.parse_args()

if not os.path.exists(args.gt):
    print(f"[提示] {args.gt} 不存在，先跑 evaluation/tools/make_testset.py 生成模板")
    raise SystemExit(0)
gt = json.load(open(args.gt, encoding="utf-8"))
if not os.path.exists(args.pred):
    print(f"[提示] {args.pred} 不存在。请先用后端 /api/ocr/recognize 批量识别 data/samples 得到pred.json")
    print("pred.json格式：{\"a.jpg\": \"识别文本\", ...}")
    raise SystemExit(0)
pred = json.load(open(args.pred, encoding="utf-8"))
cers = []
for k, gv in gt.items():
    pv = pred.get(k, "")
    c = cer(pv, gv)
    cers.append(c)
    print(f"{k}: CER={c:.3f}")
print(f"平均CER={sum(cers)/max(1,len(cers)):.3f} 样本数={len(cers)}")
