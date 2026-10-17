# -*- coding: utf-8 -*-
"""冻结环境：记录CPU/Python/依赖版本/图片分辨率分布，供报告 latency 章节引用。"""
import json, os, platform, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
info = {
    "cpu": platform.processor() or "see-docs",
    "machine": platform.machine(),
    "python": sys.version.replace("\n"," "),
    "platform": platform.platform(),
}
try:
    import importlib.metadata as md
    pkgs = {}
    for p in ["fastapi","uvicorn","opencv-python","numpy","Pillow","rapidocr-onnxruntime","onnxruntime","reportlab","openpyxl","python-multipart","pypdf","jieba","scikit-learn","sqlalchemy"]:
        try: pkgs[p] = md.version(p)
        except Exception: pkgs[p] = "not-installed"
    info["packages"] = pkgs
except Exception as e:
    info["packages_err"] = str(e)
# 图片分辨率分布（synthetic）
try:
    import cv2
    from backend.app.services.io_utils import imread_unicode  # noqa
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app.services.io_utils import imread_unicode as reader
    sizes = []
    d = os.path.join(ROOT, "data", "synthetic")
    for f in sorted(os.listdir(d))[:50]:
        if f.endswith(".jpg"):
            im = reader(os.path.join(d, f))
            if im is not None: sizes.append(list(im.shape[:2]))
    info["synthetic_shapes_sample"] = sizes[:5]
    info["synthetic_n"] = len(sizes)
except Exception as e:
    info["shapes_err"] = str(e)
print(json.dumps(info, ensure_ascii=False, indent=2))
json.dump(info, open(os.path.join(ROOT, "evaluation", "env_freeze.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
