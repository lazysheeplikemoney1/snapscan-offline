"""Offline model bundle must come from the deployed weights and embedded dictionary."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModelBundleTests(unittest.TestCase):
    def test_bundle_preserves_weights_and_ctc_dictionary(self):
        script = ROOT / "mobile/prepare_local_models.py"
        self.assertTrue(script.exists(), "Offline asset preparation is not implemented")
        spec = importlib.util.spec_from_file_location("prepare_local_models", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            manifest = module.build_resources(ROOT, Path(directory))
            self.assertEqual(manifest["model"]["id"], "rec_head_v1")
            self.assertEqual(manifest["model"]["sha256"], "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe")
            dictionary = json.loads((Path(directory) / "characters.json").read_text(encoding="utf-8"))
            self.assertTrue(b"\r\n" not in (Path(directory) / "characters.json").read_bytes(), "Asset hashes must be stable across Windows/Linux")
            self.assertEqual(len(dictionary), 6625)
            self.assertEqual(dictionary[0], "blank")
            self.assertEqual(dictionary[-1], " ")
            self.assertEqual(dictionary[1], "'")
            self.assertIn("中", dictionary)
            self.assertIn("𣇉", dictionary, "Supplementary CJK must survive JSON export without JNI metadata decoding")
            for filename in ("det_fp32.onnx", "rec_head_v1.onnx", "cls.onnx", "characters.json"):
                self.assertTrue((Path(directory) / filename).is_file())
                self.assertIn(filename, manifest["files"])


if __name__ == "__main__":
    unittest.main()
