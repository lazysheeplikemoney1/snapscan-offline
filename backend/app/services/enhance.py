# -*- coding: utf-8 -*-
"""图像预处理阶段拆分（用于实验A消融，OCR模型保持一致）.
A0: raw（不做任何处理）
A1: raw + Perspective（在 doc_detect.correct_document 里做）
A2: A1 + Shadow normalization（中值滤波估计背景）
A3: A2 + CLAHE/Sharpen
每个函数纯粹、可单独开关，禁止在预处理函数里顺手换模型。
"""
import cv2
import numpy as np

def shadow_normalize(img):
    """去阴影：灰度 -> 中值滤波估计背景 -> 除法归一化。返回BGR（方便下游统一）。"""
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    bg = cv2.medianBlur(gray, 31)
    bg = np.where(bg == 0, 1, bg)
    norm = np.clip((gray.astype(np.float32) / bg.astype(np.float32)) * 255, 0, 255).astype(np.uint8)
    return cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR)

def clahe_sharpen(img, clip_limit=2.0):
    """对比度+锐化。输入BGR，内部转灰度处理再转回BGR。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    en = clahe.apply(gray)
    out = cv2.cvtColor(en, cv2.COLOR_GRAY2BGR)
    blur = cv2.GaussianBlur(out, (0, 0), 1.0)
    out = cv2.addWeighted(out, 1.4, blur, -0.4, 0)
    return out

def enhance_doc(img, mode="A3"):
    """兼容老接口。mode: A2=仅去阴影, A3=去阴影+CLAHE/锐化, auto=A3。"""
    if mode in ("A3", "auto"):
        return clahe_sharpen(shadow_normalize(img))
    elif mode == "A2":
        return shadow_normalize(img)
    elif mode in ("A0", "A1", "none"):
        return img
    return clahe_sharpen(shadow_normalize(img))

def apply_stage(img, stage):
    """供消融脚本调用：A2/A3 作用于已矫正图；A0/A1 直接透传（矫正在外层控制）。"""
    if stage == "A0" or stage == "A1":
        return img
    elif stage == "A2":
        return shadow_normalize(img)
    elif stage == "A3":
        return clahe_sharpen(shadow_normalize(img))
    raise ValueError("unknown stage: " + str(stage))

def blur_score(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def is_blurry(img, thresh=80.0):
    return blur_score(img) < thresh


# Product scan modes are deliberately separate from the frozen A0-A3 pipeline.
def _scan_bgr(img):
    if not isinstance(img, np.ndarray) or img.dtype != np.uint8 or img.size == 0:
        raise ValueError("扫描图必须为非空 uint8 图像")
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 3:
        return img.copy()
    raise ValueError("扫描图必须为灰度或三通道 BGR 图像")


def _scan_illumination(gray):
    """Estimate broad illumination without filtering the original-size ink.

    Closing fills dark text in the reduced background estimate; it is never
    applied to the output. The large, smooth field cannot sharpen ringing or
    erase a one-pixel stroke in the full-resolution source image.
    """
    height, width = gray.shape
    ratio = min(1.0, 1400.0 / max(height, width))
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA) if ratio < 1 else gray
    short = min(small.shape)
    kernel = max(15, min(65, int(short / 25) | 1))
    field = cv2.morphologyEx(small, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (kernel, kernel)))
    field = cv2.GaussianBlur(field.astype(np.float32), (0, 0), max(3.0, min(16.0, short / 48.0)))
    if field.shape != gray.shape:
        field = cv2.resize(field, (width, height), interpolation=cv2.INTER_LINEAR)
    return np.maximum(field, gray.astype(np.float32))


def enhance_scan(img, mode="clean"):
    """Product scan appearance, without changing resolution, geometry or input.

    ``color`` is an exact original-color copy. ``clean`` gently flattens broad
    illumination while retaining colored stamps. ``bw`` is an optional binary
    reading copy. No denoising, erosion or sharpening is applied to source ink.
    These appearances cannot restore information lost to blur or glare.
    """
    if mode not in ("color", "clean", "bw"):
        raise ValueError("扫描模式必须为 color、clean 或 bw")
    bgr = _scan_bgr(img)
    if mode == "color":
        return bgr
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    background = _scan_illumination(gray)
    # Bound amplification so dark borders and low-light sensor noise stay dark.
    gain = np.clip(245.0 / np.maximum(background, 48.0), 0.85, 2.5)
    balanced = np.clip(bgr.astype(np.float32) * gain[:, :, None], 0, 255).astype(np.uint8)
    # A global monotonic curve adds mild contrast without local halos or erosion.
    curve = np.rint(255 * (np.arange(256, dtype=np.float32) / 255) ** 1.08).astype(np.uint8)
    cleaned = cv2.LUT(balanced, curve)
    if mode == "clean":
        return cleaned
    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY)
    block = max(31, min(151, (min(gray.shape) // 24) | 1))
    # Estimate a conservative Sauvola threshold from a gently smoothed copy.
    # Classify the original full-size pixels: averaging the output itself would
    # remove the single-pixel strokes that thermal receipts often depend on.
    statistics = cv2.GaussianBlur(gray.astype(np.float32), (3, 3), .65)
    mean = cv2.boxFilter(statistics, cv2.CV_32F, (block, block), normalize=True)
    squared = cv2.boxFilter(statistics * statistics, cv2.CV_32F, (block, block), normalize=True)
    deviation = np.sqrt(np.maximum(squared - mean * mean, 0))
    threshold = mean * (1 + .07 * (deviation / 128.0 - 1)) - 1.0
    binary = np.where(gray > threshold, 255, 0).astype(np.uint8)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def quality_metrics(img):
    """Resolution-bounded advisory signals, not calibrated capture rejection.

    Small text, blank margins, screen moire and document content influence the
    Laplacian score. Thresholds only produce suggestions; they never block OCR.
    """
    bgr = _scan_bgr(img)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ratio = min(1.0, 1200.0 / max(gray.shape))
    if ratio < 1:
        gray = cv2.resize(gray, (max(1, round(gray.shape[1] * ratio)),
                                 max(1, round(gray.shape[0] * ratio))), interpolation=cv2.INTER_AREA)
    detail = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.median(gray))
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    blurry = detail < 35.0
    low_light = brightness < 70.0
    low_contrast = contrast < 25.0
    warnings = []
    if blurry:
        warnings.append("细节较少，可能模糊或文字过小；建议放大核对，必要时靠近重拍")
    if low_light:
        warnings.append("整体偏暗，建议改善照明并避开手部阴影")
    if low_contrast:
        warnings.append("文字与背景对比较低，建议核对小字或尝试黑白模式")
    return {"blur_score": round(detail, 3), "is_blurry": bool(blurry),
            "median_brightness": round(brightness, 3), "contrast_p95_p05": round(contrast, 3),
            "low_light": bool(low_light), "low_contrast": bool(low_contrast),
            "quality_gate": "advisory_only", "thresholds_calibrated": False,
            "analysis_max_edge": 1200, "warnings": warnings}
