"""Prepare approved old DEV regression assets in the test APK, never main APK."""
from pathlib import Path
import gzip
import hashlib
import json
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    dest=ROOT/"mobile/android-local/app/src/androidTest/assets/book_ocr"
    dest.mkdir(parents=True,exist_ok=True)
    image=ROOT/"data/real/dev/dev/111/real-010.jpg"
    (dest/"real-010-old-dev.jpg").write_bytes(image.read_bytes())
    gt=next(json.loads(line) for line in (ROOT/"data/real/annotations_local_111.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(line)["image_id"]=="real-010.jpg")
    assert gt["split"]=="dev" and "[UNK]" not in gt["gt_text"]
    (dest/"real-010-gt.json").write_text(json.dumps(gt,ensure_ascii=False,indent=2),encoding="utf-8")
    probability=np.load(ROOT/"output/diagnostics/android-det-dev-010-023-022-v1/real-010-probability.npy",allow_pickle=False)
    raw=probability.astype("<f4").tobytes()
    # AAPT automatically expands assets ending in .gz and strips that suffix.
    # Keep the gzip stream under a neutral extension so the test reads it intact.
    (dest/"real-010-probability.f32gzip").write_bytes(gzip.compress(raw,mtime=0))
    manifest={"source_split":"dev","source_kind":"legacy_dev_regression_not_new_holdout","holdout_read":False,
              "image_id":"real-010.jpg","image_sha256":hashlib.sha256(image.read_bytes()).hexdigest(),
              "probability_raw_sha256":hashlib.sha256(raw).hexdigest(),"width":probability.shape[1],"height":probability.shape[0],
              "expected_contour_score_boxes":42,"cached_probability_source":"same actual detector before parameter changes",
              "production_assets_changed":False}
    (dest/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Prepared old DEV010 image, manual GT and lossless probability map in androidTest only")


if __name__=="__main__":main()
