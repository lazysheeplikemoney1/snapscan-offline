"""Spatially score frozen reference regions from full-page OCR, never ROI crops."""
import cv2
import numpy as np
from tools.metrics import normalize_text, edit_ops


def text_metric(prediction, reference):
    pred, gt = normalize_text(prediction), normalize_text(reference)
    s, d, i = edit_ops(pred, gt)
    return {"N":len(gt), "P":len(pred), "S":s, "D":d, "I":i,
            "cer":(s+d+i)/len(gt) if gt else None, "exact":pred == gt}


def evaluate_regions(result, regions, inverse=None):
    assignments = [[] for _ in regions]
    polygons = [np.asarray(r["polygon"], dtype=np.float32).reshape(-1,2) for r in regions]
    unscored = []
    boxes = result.get("boxes", [])
    reading_order = [i for row in result.get("text_rows", []) for i in row]
    if sorted(reading_order) != list(range(len(boxes))):
        reading_order = list(range(len(boxes)))
    for index in reading_order:
        p = np.asarray(boxes[index]["box"], dtype=np.float32).reshape(-1,2)
        if inverse is not None:
            p = cv2.perspectiveTransform(p[None], inverse)[0]
        p = cv2.convexHull(p).reshape(-1,2)
        area = max(cv2.contourArea(p), 1e-9)
        center = tuple(map(float, p.mean(axis=0)))
        candidates = []
        for j, region in enumerate(polygons):
            overlap = float(cv2.intersectConvexConvex(p, cv2.convexHull(region))[0]) / area
            if overlap >= .5 or cv2.pointPolygonTest(region, center, False) >= 0:
                candidates.append((overlap, -j, j))
        if candidates:
            assignments[max(candidates)[2]].append(index)
        else:
            unscored.append(index)
    details = []
    for region, indices in zip(regions, assignments):
        prediction = "\n".join(boxes[i]["text"] for i in indices)
        details.append({"region_id":region["region_id"], "box_indices":indices,
                        "prediction":prediction, **text_metric(prediction,region["text"])})
    total = {key:sum(r[key] for r in details) for key in ("N","P","S","D","I")}
    total.update(cer=(total["S"]+total["D"]+total["I"])/total["N"] if total["N"] else None,
                 exact=all(r["exact"] for r in details), regions=details, unscored_prediction_boxes=unscored)
    return total


def clean_to_source(scan):
    if not scan["has_quad"]:
        return None
    w,h=scan["width"],scan["height"]
    # ScanProcessor.warp maps paper corners to the output pixel-corner rectangle.
    destination=np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],np.float32)
    return cv2.getPerspectiveTransform(destination,np.asarray(scan["quad"],np.float32))
