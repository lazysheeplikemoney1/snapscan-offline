"""Windows中文路径下 cv2.imread/imwrite 会失败，统一用 fromfile/imdecode/tofile封装。"""
import cv2
import numpy as np

def imread_unicode(p, flags=cv2.IMREAD_COLOR):
    try:
        arr = np.fromfile(p, dtype=np.uint8)
        if arr.size == 0:
            return None
        return cv2.imdecode(arr, flags)
    except Exception:
        return None

def imwrite_unicode(p, img, params=None):
    try:
        ext = "." + p.rsplit(".", 1)[-1] if "." in p else ".jpg"
        ok, buf = cv2.imencode(ext, img, params or [])
        if not ok:
            return False
        buf.tofile(p)
        return True
    except Exception as e:
        print("[io_utils] imwrite fail:", e)
        return False
