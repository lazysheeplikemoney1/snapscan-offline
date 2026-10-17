"""Create the Android OCR assets from verified, already-local deployed models.

No downloads, training, quantization or test-dataset access. Run with the project's
Python environment (onnx and rapidocr_onnxruntime must already be installed).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

EXPECTED = {
    "det_fp32.onnx": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "rec_head_v1.onnx": "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe",
    "cls.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}


def build_resources(root: Path, destination: Path) -> dict:
    import onnx
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    if spec is None or spec.origin is None:
        raise RuntimeError("The verified local RapidOCR 1.4.4 installation is required")
    package = Path(spec.origin).parent
    sources = {
        "det_fp32.onnx": root / "backend/models/fp32/det_fp32.onnx",
        "rec_head_v1.onnx": root / "backend/models/rec_head_v1.onnx",
        "cls.onnx": package / "models/ch_ppocr_mobile_v2.0_cls_infer.onnx",
    }
    for name, source in sources.items():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != EXPECTED[name]:
            raise ValueError(f"Refusing unexpected model bytes: {name}")
    recognition = onnx.load(sources["rec_head_v1.onnx"])
    characters = {item.key: item.value for item in recognition.metadata_props}["character"].splitlines()
    dictionary = ["blank", *characters, " "]
    classes = recognition.graph.output[0].type.tensor_type.shape.dim[2].dim_value
    if len(dictionary) != classes or classes != 6625:
        raise ValueError("Embedded CTC dictionary does not match the deployed output classes")
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        shutil.copyfile(source, destination / name)
    (destination / "characters.json").write_bytes((json.dumps(dictionary, ensure_ascii=False) + "\n").encode("utf-8"))
    manifest = {
        "schema_version": 1,
        "model": {"id": "rec_head_v1", "sha256": EXPECTED["rec_head_v1.onnx"]},
        "files": {},
        "dictionary": {"classes": classes, "blank_index": 0, "space_index": 6624,
                       "source": "rec_head_v1.onnx custom_metadata.character.splitlines(); prepend blank and append space"},
        "pipeline": {"channel_order": "BGR", "tensor_layout": "NCHW", "normalization": "(pixel/255-0.5)/0.5",
                     "rec_height": 48, "rec_min_width": 320, "rec_batch_size": 1,
                     "ctc_greedy": True, "text_score": 0.5,
                     "det_thresh": 0.3, "det_box_thresh": 0.5, "det_unclip_ratio": 1.6,
                     "det_limit_side_len": 736, "det_limit_type": "min", "global_max_side_len": 2000,
                     "cls_shape": [3, 48, 192], "cls_rotation_threshold": 0.9},
        "attributions": [
            {"name": "RapidOCR 1.4.4 / PP-OCRv4", "license": "Apache-2.0",
             "source": "https://github.com/RapidAI/RapidOCR", "license_url": "https://github.com/RapidAI/RapidOCR/blob/main/LICENSE"},
            {"name": "PaddleOCR", "license": "Apache-2.0", "source": "https://github.com/PaddlePaddle/PaddleOCR",
             "license_url": "https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE"}],
    }
    for name in [*sources, "characters.json"]:
        content = (destination / name).read_bytes()
        manifest["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (destination / "model_manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    target = args.output or project / "mobile/android-local/app/src/main/assets/models"
    result = build_resources(project, target)
    print(json.dumps({"destination": str(target), "model": result["model"], "files": result["files"]}, ensure_ascii=False, indent=2))
