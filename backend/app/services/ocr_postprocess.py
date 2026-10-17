"""Project-local RapidOCR 1.4.4 DB postprocessing with contour-based padding.

The bitmap, candidates, confidence scores and filters follow the upstream
DBPostProcess. Only the expansion distance uses the original contour's area
and perimeter, so a curved line's empty enclosing space does not add padding.

Adapted from RapidOCR's DBPostProcess (SWHL / PaddleOCR contributors),
distributed under the Apache License, Version 2.0.
https://www.apache.org/licenses/LICENSE-2.0
"""
import cv2
import numpy as np
from rapidocr_onnxruntime.ch_ppocr_det.utils import DBPostProcess


def contour_unclip_distance(contour, ratio):
    """Return A*ratio/P for a valid closed contour, otherwise no candidate."""
    try:
        points = np.asarray(contour, dtype=np.float32).reshape(-1, 2)
        ratio = float(ratio)
    except (TypeError, ValueError):
        return None
    if len(points) < 3 or not np.isfinite(points).all() or not np.isfinite(ratio) or ratio <= 0:
        return None
    area = abs(cv2.contourArea(points))
    perimeter = cv2.arcLength(points, True)
    if not np.isfinite(area) or not np.isfinite(perimeter) or area <= 0 or perimeter <= 0:
        return None
    distance = area * ratio / perimeter
    return distance if np.isfinite(distance) else None


def unclip_rectangle_by_distance(box, distance):
    """Expand ordered TL/TR/BR/BL corners along the rectangle's unit axes.

This matches Android's analytic rectangle expansion. Upstream PyClipper uses
intermediate integer coordinates, so straight boxes may differ by one pixel.
"""
    distance = float(distance)
    if not np.isfinite(distance) or distance < 0:
        raise ValueError("Unclip distance must be finite and nonnegative")
    points = np.asarray(box, dtype=np.float64).reshape(4, 2)
    if not np.isfinite(points).all():
        raise ValueError("Rectangle coordinates must be finite")
    u, v = points[1] - points[0], points[3] - points[0]
    width, height = np.linalg.norm(u), np.linalg.norm(v)
    if width == 0 or height == 0 or distance == 0:
        return points.copy()
    u, v = u / width * distance, v / height * distance
    return points + np.array([-u-v, u-v, u+v, -u+v])


class ContourDBPostProcess(DBPostProcess):
    """Keep upstream DB semantics while avoiding inflated rectangle padding."""

    @classmethod
    def from_existing(cls, postprocess):
        result = cls(thresh=postprocess.thresh, box_thresh=postprocess.box_thresh,
                     max_candidates=postprocess.max_candidates,
                     unclip_ratio=postprocess.unclip_ratio, score_mode=postprocess.score_mode,
                     use_dilation=postprocess.dilation_kernel is not None)
        result.min_size = postprocess.min_size
        result.dilation_kernel = (None if postprocess.dilation_kernel is None
                                  else postprocess.dilation_kernel.copy())
        return result

    def boxes_from_bitmap(self, pred, bitmap, dest_width, dest_height):
        height, width = bitmap.shape
        outputs = cv2.findContours((bitmap * 255).astype(np.uint8),
                                   cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = outputs[-2]
        boxes, scores = [], []
        for contour in contours[:self.max_candidates]:
            points, short_side = self.get_mini_boxes(contour)
            if short_side < self.min_size:
                continue
            score = (self.box_score_fast(pred, points.reshape(-1, 2))
                     if self.score_mode == "fast" else self.box_score_slow(pred, contour))
            if self.box_thresh > score:
                continue
            distance = contour_unclip_distance(contour, self.unclip_ratio)
            if distance is None:
                continue
            expanded = unclip_rectangle_by_distance(points, distance)
            box, short_side = self.get_mini_boxes(expanded.astype(np.float32))
            if short_side < self.min_size + 2:
                continue
            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
            boxes.append(box.astype(np.int32))
            scores.append(score)
        return np.array(boxes, dtype=np.int32), scores
