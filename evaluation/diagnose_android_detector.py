"""Diagnose only three approved legacy DEV pages using cached DB probabilities.

No holdout discovery, no text labels, and no training. Each run uses a new output
directory, retaining the previous detector evidence. The only variant knobs are
contour retrieval, candidate cap and (separately) box-score threshold.
"""
from pathlib import Path
import argparse
import hashlib
import json
import time

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parents[1]
IDS = ("real-010", "real-023", "real-022")


def stage(pred, post, retrieval, cap, score=.5, score_mode="fast"):
    mask = cv2.dilate((pred > .3).astype(np.uint8), np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(mask * 255, retrieval, cv2.CHAIN_APPROX_SIMPLE)
    stats = {"raw_contours": len(contours), "visited": min(len(contours), cap),
             "not_visited_due_to_cap": max(0, len(contours)-cap),
             "reject_short_before_unclip": 0, "reject_box_score": 0,
             "reject_short_after_unclip": 0, "reject_final_size": 0, "box_score_mode": score_mode}
    accepted, records = [], []
    for i, contour in enumerate(contours[:cap]):
        box, short = post.get_mini_boxes(contour)
        rect = cv2.boundingRect(contour)
        row = {"contour_index": i, "rect": list(rect), "short_side": float(short)}
        if short < 3:
            stats["reject_short_before_unclip"] += 1
            continue
        confidence = post.box_score_fast(pred, box.reshape(-1, 2)) if score_mode == "fast" else post.box_score_slow(pred, contour)
        row["box_score"] = float(confidence)
        if confidence < score:
            stats["reject_box_score"] += 1
            continue
        expanded, short = post.get_mini_boxes(post.unclip(box))
        if short < 5:
            stats["reject_short_after_unclip"] += 1
            continue
        expanded = np.clip(np.round(expanded), [0, 0], [pred.shape[1]-1, pred.shape[0]-1]).astype(np.int32)
        if int(np.linalg.norm(expanded[0]-expanded[1])) <= 3 or int(np.linalg.norm(expanded[0]-expanded[3])) <= 3:
            stats["reject_final_size"] += 1
            continue
        accepted.append(expanded)
        row["box"] = expanded.tolist()
        records.append(row)
    stats["accepted"] = len(accepted)
    if accepted:
        points = np.asarray(accepted)
        stats["accepted_y_range"] = [int(points[:, :, 1].min()), int(points[:, :, 1].max())]
        stats["accepted_upper_half"] = int(np.sum(points[:, :, 1].mean(axis=1) < pred.shape[0]/2))
    return stats, accepted, records


def save_image(path, image):
    path.write_bytes(cv2.imencode(path.suffix, image)[1].tobytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    engine = RapidOCR(rec_model_path=str(ROOT / "backend/models/rec_head_v1.onnx"), rec_batch_num=1,
                      intra_op_num_threads=2, inter_op_num_threads=1)
    post = engine.text_det.postprocess_op
    report = {"source_split": "dev", "only_approved_ids": list(IDS), "holdout_read": False,
              "model_parameters_changed": False, "probability_source": "actual_local_det_fp32_onnx",
              "variants": "single-variable comparisons on identical cached probability maps", "pages": []}
    for image_id in IDS:
        source = ROOT / "data/real/dev/dev/111" / (image_id + ".jpg")
        original = cv2.imdecode(np.fromfile(source, np.uint8), cv2.IMREAD_COLOR)
        prepared, ry, rx = engine.preprocess(original)
        prepared, _ = engine.maybe_add_letterbox(prepared, {})
        tensor = engine.text_det.get_preprocess(max(prepared.shape[:2]))(prepared)
        started = time.perf_counter()
        if args.cache:
            cache = args.cache if args.cache.is_absolute() else ROOT / args.cache
            pred = np.load(cache / (image_id + "-probability.npy"), allow_pickle=False)
        else:
            pred = engine.text_det.infer(tensor)[0][0, 0]
        np.save(output / (image_id + "-probability.npy"), pred, allow_pickle=False)
        det_image = cv2.resize(prepared, (pred.shape[1], pred.shape[0]))
        save_image(output / (image_id + "-det-input.jpg"), det_image)
        mask = cv2.dilate((pred > .3).astype(np.uint8), np.ones((2, 2), np.uint8))
        tree, hierarchy = cv2.findContours(mask * 255, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        internal = int(np.sum(hierarchy[0, :, 3] >= 0)) if hierarchy is not None else 0
        page = {"image_id": image_id, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "original_size": list(original.shape[1::-1]), "prepared_size": list(prepared.shape[1::-1]),
                "detector_size": list(pred.shape[1::-1]), "inference_ms_host": (time.perf_counter()-started)*1000,
                "hierarchical_contours": len(tree), "internal_contours": internal, "comparisons": {}}
        variants = (("list_cap1000", cv2.RETR_LIST, 1000, .5, "fast"),
                    ("list_all", cv2.RETR_LIST, 1000000, .5, "fast"),
                    ("external_cap1000", cv2.RETR_EXTERNAL, 1000, .5, "fast"),
                    ("external_all", cv2.RETR_EXTERNAL, 1000000, .5, "fast"),
                    ("list_cap1000_score03", cv2.RETR_LIST, 1000, .3, "fast"),
                    ("list_cap1000_slow", cv2.RETR_LIST, 1000, .5, "slow"))
        previews = []
        for name, retrieval, cap, score, score_mode in variants:
            stats, boxes, records = stage(pred, post, retrieval, cap, score, score_mode)
            page["comparisons"][name] = stats
            (output / (image_id + "-" + name + "-boxes.json")).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            overlay = det_image.copy()
            for box in boxes:
                cv2.polylines(overlay, [box], True, (0, 0, 255), 2)
            save_image(output / (image_id + "-" + name + ".jpg"), overlay)
            if name in ("list_cap1000", "list_cap1000_slow"):
                ratio = 950 / overlay.shape[0]
                small = cv2.resize(overlay, (int(overlay.shape[1]*ratio), 950), interpolation=cv2.INTER_AREA)
                header = np.full((50, small.shape[1], 3), 255, np.uint8)
                cv2.putText(header, name + " boxes=" + str(len(boxes)), (8, 32), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 0, 0), 1)
                previews.append(np.vstack([header, small]))
        save_image(output / (image_id + "-comparison.jpg"), np.hstack(previews))
        report["pages"].append(page)
        print(image_id, "raw", page["hierarchical_contours"], "internal", internal,
              {k: v["accepted"] for k, v in page["comparisons"].items()}, flush=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report", output / "report.json", flush=True)


if __name__ == "__main__":
    main()
