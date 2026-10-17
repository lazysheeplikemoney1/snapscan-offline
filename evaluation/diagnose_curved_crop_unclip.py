"""OLD DEV only: single-variable contour-vs-rectangle padding experiment.

Both variants use the same detector map and .5 contour-score/.5 text gates.
Only the distance used to expand the rectangle changes. No direction fallback,
new-image feedback, model training, site-package edits or holdout inputs.
"""
from pathlib import Path
import json
import math
import sys
import argparse

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation/tools"))
sys.path.insert(0, str(ROOT / "backend"))
from metrics import normalize_text, edit_ops
from app.services.layout import build_layout

INPUTS = (("111", "real-010"), ("111", "real-023"), ("111", "real-022"),
          ("222", "real-036"), ("222", "real-037"), ("333", "real-071"), ("333", "real-090"))


def expand(box, distance):
    box = np.asarray(box, np.float64)
    u, v = box[1]-box[0], box[3]-box[0]
    u = u/np.linalg.norm(u)*distance
    v = v/np.linalg.norm(v)*distance
    return np.asarray([box[0]-u-v, box[1]+u-v, box[2]+u+v, box[3]-u+v], np.float32)


def boxes_from_probability(pred, post, mode):
    mask = cv2.dilate((pred > .3).astype(np.uint8), np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(mask*255, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes, distances = [], []
    for contour in contours[:1000]:
        box, short = post.get_mini_boxes(contour)
        if short < 3 or post.box_score_slow(pred, contour) < .5:
            continue
        if mode == "contour_distance":
            area, perimeter = abs(cv2.contourArea(contour)), cv2.arcLength(contour, True)
        else:
            area, perimeter = abs(cv2.contourArea(box)), cv2.arcLength(box, True)
        if perimeter <= 0:
            continue
        distance = area*1.6/perimeter
        expanded, short = post.get_mini_boxes(expand(box, distance).reshape(-1, 1, 2))
        if short < 5:
            continue
        expanded = np.clip(np.round(expanded), [0, 0], [pred.shape[1]-1, pred.shape[0]-1]).astype(np.float32)
        if int(np.linalg.norm(expanded[0]-expanded[1])) <= 3 or int(np.linalg.norm(expanded[0]-expanded[3])) <= 3:
            continue
        boxes.append(expanded)
        distances.append(distance)
    return boxes, distances


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT/args.output
    output.mkdir(parents=True, exist_ok=False)
    engine = RapidOCR(rec_model_path=str(ROOT/"backend/models/rec_head_v1.onnx"), rec_batch_num=1,
                      intra_op_num_threads=2, inter_op_num_threads=1)
    groundtruth = {}
    for folder in ("111", "222", "333"):
        for line in (ROOT/"data/real"/("annotations_local_"+folder+".jsonl")).read_text(encoding="utf-8").splitlines():
            row=json.loads(line);groundtruth[row["image_id"]] = row
    report={"source_split":"dev", "holdout_read":False, "single_variable":"rectangle expansion distance area/perimeter source", "cases":[]}
    for folder, image_id in INPUTS:
        path = ROOT/"data/real/dev/dev"/folder/(image_id+".jpg")
        if not path.is_file():
            continue
        original = cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_COLOR)
        prepared, ry, rx = engine.preprocess(original)
        prepared, record = engine.maybe_add_letterbox(prepared,{"preprocess":{"ratio_h":ry,"ratio_w":rx}})
        cache=ROOT/"output/diagnostics/android-det-dev-010-023-022-v1"/(image_id+"-probability.npy")
        if folder=="111" and cache.is_file():
            pred=np.load(cache,allow_pickle=False)
        else:
            tensor=engine.text_det.get_preprocess(max(prepared.shape[:2]))(prepared)
            pred=engine.text_det.infer(tensor)[0][0,0]
        case={"image_id":image_id+".jpg", "folder":folder, "variants":{}}
        gt=normalize_text(groundtruth[image_id+".jpg"].get("gt_text"))
        for mode in ("rectangle_distance","contour_distance"):
            boxes,distances=boxes_from_probability(pred,engine.text_det.postprocess_op,mode)
            boxes=engine.sorted_boxes(np.asarray(boxes))
            crops=engine.get_crop_img_list(prepared, boxes)
            crops,cls,_=engine.text_cls(crops)
            recognized,_=engine.text_rec(crops)
            raw=[]
            for box, (text,score) in zip(boxes,recognized):
                if score>=.5 and text:
                    scaled=np.asarray(box,float);scaled[:,0]*=rx;scaled[:,1]=(scaled[:,1]-record['padding_1']['top'])*ry
                    raw.append({"box":scaled.tolist(),"text":text,"score":float(score)})
            layout=build_layout(raw)
            s,d,i=edit_ops(normalize_text(layout['text']),gt)
            summary={"det_boxes":len(boxes),"recognized_boxes":len(raw),"pred_chars":len(normalize_text(layout['text'])),
                     "S":s,"D":d,"I":i,"N":len(gt),"CER":(s+d+i)/len(gt) if gt else None,
                     "mean_distance":float(np.mean(distances)),"max_distance":float(max(distances))}
            case['variants'][mode]=summary
            (output/(image_id+'-'+mode+'.json')).write_text(json.dumps({"boxes":raw,**layout,"metrics":summary},ensure_ascii=False,indent=2),encoding='utf-8')
            if image_id=='real-022':
                for index,crop in enumerate(crops[:7]):(output/(image_id+'-'+mode+'-crop'+str(index)+'.png')).write_bytes(cv2.imencode('.png',crop)[1].tobytes())
            print(image_id,mode,summary,flush=True)
        report['cases'].append(case)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
