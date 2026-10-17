"""Copy exact existing OCR evidence into androidTest assets only; no OCR run."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    dest = ROOT / "mobile/android-local/app/src/androidTest/assets/layout"
    dest.mkdir(parents=True, exist_ok=True)
    sources = {"text_layout_cases.json": ROOT / "evaluation/fixtures/text_layout_cases.json",
               "receipt-document-v201.json": ROOT / "output/scan/android-device/receipt-document.json",
               "receipt-ocr-v201.png": ROOT / "output/scan/android-device/receipt-ocr-persisted.png"}
    manifest = {"schema": 1, "test_set_read": False, "production_assets_changed": False,
                "source_kind": "prior_actual_android_ocr_evidence_not_user_pdf_coordinates", "files": []}
    for name, source in sources.items():
        data = source.read_bytes()
        (dest / name).write_bytes(data)
        manifest["files"].append({"asset": "layout/" + name, "source": str(source.relative_to(ROOT)),
                                  "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Prepared", len(sources), "reading-order androidTest assets")


if __name__ == "__main__":
    main()
