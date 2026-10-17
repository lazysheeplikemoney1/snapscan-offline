"""Reproduce Android-only regression fixtures without touching OCR/test data.

The feedback crop retains UI overlays and is not an original camera photo.
All independent color/moiré/frame examples are synthetic derivatives of an
older user-feedback photo, never advertised as additional camera samples.
"""
from pathlib import Path
import hashlib
import json

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "mobile/android-local/app/src/androidTest/assets/scan"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)


def variant(image, kind):
    gains = {"blue_mild": [1.12, 1, .90], "blue_medium": [1.25, 1, .82],
             "warm": [.86, .98, 1.10], "combined": [1.25, 1, .82]}
    result = image.astype(float) * gains.get(kind, [1, 1, 1])
    if kind in ("shadow", "combined"):
        result *= np.linspace(.68 if kind == "shadow" else .74, 1, image.shape[0])[:, None, None]
    if kind == "moire":
        yy, xx = np.indices(image.shape[:2])
        result *= (1 + .045*np.sin(2*np.pi*xx/3.2) + .025*np.sin(2*np.pi*yy/4.3))[:, :, None]
    result = np.clip(result, 0, 255).astype(np.uint8)
    if kind in ("border", "combined", "moire"):
        result = cv2.copyMakeBorder(result, 24, 24, 18, 18, cv2.BORDER_CONSTANT, value=(12, 12, 12))
    return result


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    base_path = ROOT / "backend/tests/fixtures/receipt_clipped_wood.png"
    photo_path = ROOT / "output/scan/blue-feedback/photo-region-with-ui-overlay.png"
    base = read(base_path)
    manifest = {"schema": 1, "test_set_read": False,
                "base_image": {"path": str(base_path.relative_to(ROOT)), "sha256": sha(base_path)},
                "feedback_crop": {"path": str(photo_path.relative_to(ROOT)), "sha256": sha(photo_path),
                                  "screenshot_crop_xyxy": [84, 454, 1178, 1912],
                                  "camera_original": False, "ui_overlay_retained": True}, "fixtures": []}
    def save(name, image, kind, expected, original=None):
        path = DEST / ("android_blue_" + name + ".png")
        path.write_bytes(original.read_bytes() if original is not None else cv2.imencode(".png", image)[1].tobytes())
        manifest["fixtures"].append({"name": name, "path": str(path.relative_to(ROOT)),
            "sha256": sha(path), "source_kind": kind, "expected_has_quad": expected,
            "size": [image.shape[1], image.shape[0]]})
    save("photo_with_ui", read(photo_path), "screenshot_photo_region_with_ui_overlay", True, photo_path)
    aliases = {"border": "black_border", "combined": "blue_shadow_border"}
    for kind in ("blue_mild", "blue_medium", "warm", "border", "shadow", "combined", "moire"):
        save(aliases.get(kind, kind), variant(base, kind), "rendered_no_ui_from_old_photo", True)
    yy, xx = np.indices((640, 480))
    grain = 10*np.sin(xx/9 + yy/110) + 4*np.sin(yy/7)
    wood = np.clip(np.array([70, 115, 145])[None, None, :] + grain[:, :, None], 0, 255).astype(np.uint8)
    cv2.ellipse(wood, (30, 280), (45, 90), 0, 0, 360, (231, 242, 248), -1)
    for kind in ("blue_mild", "blue_medium", "warm", "border", "combined", "moire"):
        save("wood_" + kind, variant(wood, kind), "synthetic_wood_glare_no_document", False)
    (ROOT / "reports/android_screen_fixture_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Prepared", len(manifest["fixtures"]), "androidTest-only fixtures; no production assets")


if __name__ == "__main__":
    main()
