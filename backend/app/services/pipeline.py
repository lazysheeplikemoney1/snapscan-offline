# -*- coding: utf-8 -*-
"""统一消融入口：输入原图 -> 按阶段输出待OCR图。保证实验A各组除预处理外完全一致。"""
from .doc_detect import correct_document
from .enhance import apply_stage

STAGES = ("A0", "A1", "A2", "A3")

def preprocess_for_stage(img, stage):
    """A0: raw直出；A1: 仅透视矫正；A2: 矫正+去阴影；A3: 矫正+去阴影+CLAHE/锐化。
    返回 (processed, meta)，meta记录 has_quad 供分析透视成功率。"""
    if stage not in STAGES:
        raise ValueError("stage must be one of " + str(STAGES))
    if stage == "A0":
        return img, {"has_quad": False, "stage": "A0"}
    quad, warped = correct_document(img)
    if stage == "A1":
        return warped, {"has_quad": quad is not None, "stage": "A1"}
    out = apply_stage(warped, stage)
    return out, {"has_quad": quad is not None, "stage": stage}
