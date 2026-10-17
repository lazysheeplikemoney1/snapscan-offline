"""加载经过哈希核验的本地 OCR 模型，并报告实际使用的版本。"""
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

from ..core.config import MODELS_DIR
from .layout import layout_fields


class OCRUnavailableError(RuntimeError):
    """Selected model cannot be loaded; callers must not store a fake result."""


class RapidEngine:
    name = "rapidocr-onnxruntime"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        from .ocr_postprocess import ContourDBPostProcess

        model_id = os.environ.get("SNAPSCAN_OCR_MODEL", "rec_head_v1")
        manifest = json.loads((Path(MODELS_DIR) / "manifest.json").read_text(encoding="utf-8"))
        if model_id not in manifest["models"]:
            raise ValueError(f"未知模型版本：{model_id}")
        selected = manifest["models"][model_id]
        model_path = (Path(MODELS_DIR) / selected["path"]).resolve()
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if digest != selected["sha256"]:
            raise ValueError(f"模型 {model_id} 的 SHA-256 不匹配，请重新部署")
        # 与训练/导出验证保持相同的逐行缩放与 padding；det/cls 使用包内本地权重。
        self.engine = RapidOCR(
            rec_model_path=str(model_path), rec_batch_num=1,
            # Score actual text contours: a curved line's enclosing rectangle
            # contains blank pixels and can discard an otherwise confident line.
            det_score_mode="slow",
            intra_op_num_threads=4, inter_op_num_threads=1,
        )
        self.engine.text_det.postprocess_op = ContourDBPostProcess.from_existing(
            self.engine.text_det.postprocess_op
        )
        self.model_info = {"id": model_id, "sha256": digest}

    def recognize(self, img):
        # RapidOCR 接受 ndarray(BGR)
        res, _ = self.engine(img)
        boxes = []
        if res:
            for box, txt, conf in res:
                boxes.append({"box": [[float(x), float(y)] for x, y in box], "text": txt, "score": float(conf)})
        return {"boxes": boxes, **layout_fields(boxes)}

_singleton = None
_engine_lock = Lock()


def get_engine():
    global _singleton
    with _engine_lock:
        if _singleton is None:
            try:
                _singleton = RapidEngine()
            except Exception as exc:
                raise OCRUnavailableError(
                    f"OCR模型加载失败：{exc}。请检查 backend/models/README.md 后重启后端。"
                ) from exc
    return _singleton
