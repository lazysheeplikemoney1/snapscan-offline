"""Adapter checks use generated arrays/models, never the locked test dataset."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import onnxruntime as ort

try:
    from training import onnx_head
except ImportError:
    onnx_head = None


MODEL = Path(__file__).resolve().parents[1] / "backend/models/fp32/rec_fp32.onnx"


def make_small_model(path, constants):
    weight = np.array([[0.1, 0.2, 0.3, 0.4], [-0.2, 0.6, 0.3, -0.1]], np.float32)
    bias = np.array([0.3, 0.2, -0.2, 0.1], np.float32)
    tensors = [numpy_helper.from_array(weight, "weight"), numpy_helper.from_array(bias, "bias")]
    nodes = []
    if constants:
        nodes.extend(helper.make_node("Constant", [], [t.name], value=t) for t in tensors)
    nodes.extend([
        helper.make_node("MatMul", ["features", "weight"], ["product"]),
        helper.make_node("Add", ["bias", "product"], ["logits"]),
        helper.make_node("Softmax", ["logits"], ["probabilities"], axis=2),
    ])
    graph = helper.make_graph(
        nodes, "tiny_ctc_head",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, ["N", "T", 2])],
        [helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["N", "T", 4])],
        initializer=[] if constants else tensors,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 12)], ir_version=8)
    helper.set_model_props(model, {"character": "a\nb", "provenance": "generated fixture"})
    onnx.save(model, path)
    return weight, bias


def inference_session(path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class HeadAdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(onnx_head, "training.onnx_head adapter has not been implemented")

    def test_inspects_deployed_constant_head(self):
        info = onnx_head.inspect_head(MODEL)
        self.assertEqual(info["feature_name"], "transpose_51.tmp_0")
        self.assertEqual(info["weight_name"], "linear_85.w_0")
        self.assertEqual(info["bias_name"], "linear_85.b_0")
        self.assertEqual(info["weight"].shape, (120, 6625))
        self.assertEqual(info["bias"].shape, (6625,))
        self.assertEqual(info["input_name"], "x")
        self.assertEqual(info["output_name"], "softmax_11.tmp_0")
        self.assertEqual(info["num_features"], 120)
        self.assertEqual(info["num_classes"], 6625)

    def test_resolves_initializer_and_constant_weights_with_reversed_add(self):
        with tempfile.TemporaryDirectory() as directory:
            for constants in (False, True):
                with self.subTest(constants=constants):
                    path = Path(directory) / "fixture.onnx"
                    weight, bias = make_small_model(path, constants)
                    info = onnx_head.inspect_head(path)
                    np.testing.assert_array_equal(info["weight"], weight)
                    np.testing.assert_array_equal(info["bias"], bias)
                    self.assertEqual(info["feature_name"], "features")

    def test_loads_metadata_charset_with_correct_blank_and_space(self):
        charset = onnx_head.load_charset(MODEL)
        self.assertEqual(len(charset), 6625)
        self.assertEqual(charset[0], "blank")
        self.assertEqual(charset[1], "'")
        self.assertEqual(charset[-1], " ")
        self.assertEqual(len(set(charset)), len(charset))

    def test_preprocessing_matches_installed_rapidocr_exactly(self):
        from rapidocr_onnxruntime.ch_ppocr_rec.text_recognize import TextRecognizer
        reference = TextRecognizer.__new__(TextRecognizer)
        reference.rec_image_shape = [3, 48, 320]
        rng = np.random.default_rng(12)
        for height, width in [(19, 61), (80, 915), (31, 557)]:
            with self.subTest(shape=(height, width)):
                image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
                ratio = max(320 / 48, width / height)
                expected = reference.resize_norm_img(image, ratio)[None]
                actual = onnx_head.preprocess_crop(image)
                self.assertEqual(actual.dtype, np.float32)
                np.testing.assert_array_equal(actual, expected)

    def test_preprocessing_preserves_bgr_and_normalized_zero_padding(self):
        image = np.zeros((24, 24, 3), np.uint8)
        image[:, :, 0] = 255
        actual = onnx_head.preprocess_crop(image)
        self.assertEqual(actual.shape, (1, 3, 48, 320))
        np.testing.assert_array_equal(actual[0, :, 0, 0], [1, -1, -1])
        np.testing.assert_array_equal(actual[:, :, :, 48:], 0)

    def test_preprocessing_expands_long_lines_and_explicit_batch_width(self):
        image = np.zeros((24, 1500, 3), np.uint8)
        self.assertEqual(onnx_head.preprocess_crop(image).shape, (1, 3, 48, 3000))
        padded = onnx_head.preprocess_crop(image, target_width=3072)
        self.assertEqual(padded.shape, (1, 3, 48, 3072))
        with self.assertRaises(ValueError):
            onnx_head.preprocess_crop(image, target_width=320)

    def test_preprocessing_rejects_empty_or_non_bgr_inputs(self):
        for image in (np.zeros((0, 5, 3), np.uint8), np.zeros((5, 5), np.uint8)):
            with self.subTest(shape=image.shape), self.assertRaises(ValueError):
                onnx_head.preprocess_crop(image)

    def test_exposed_features_reconstruct_probabilities_at_dynamic_widths(self):
        info = onnx_head.inspect_head(MODEL)
        feature_session = onnx_head.create_feature_session(MODEL, threads=2)
        self.assertEqual([out.name for out in feature_session.get_outputs()], [info["feature_name"]])
        original_session = inference_session(MODEL)
        rng = np.random.default_rng(3)
        for width, steps in [(320, 40), (640, 80)]:
            with self.subTest(width=width):
                image = rng.uniform(-1, 1, (1, 3, 48, width)).astype(np.float32)
                features = feature_session.run(None, {"x": image})[0]
                self.assertEqual(features.shape, (1, steps, 120))
                logits = features @ info["weight"] + info["bias"]
                logits -= logits.max(axis=-1, keepdims=True)
                reconstructed = np.exp(logits)
                reconstructed /= reconstructed.sum(axis=-1, keepdims=True)
                expected = original_session.run(None, {"x": image})[0]
                np.testing.assert_allclose(reconstructed, expected, atol=2e-5, rtol=2e-4)

    def test_identity_export_keeps_predictions_metadata_and_source_unchanged(self):
        before = hashlib.sha256(MODEL.read_bytes()).hexdigest()
        info = onnx_head.inspect_head(MODEL)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "identity.onnx"
            onnx_head.export_head(MODEL, info["weight"], info["bias"], target)
            original = onnx.load(MODEL)
            exported = onnx.load(target)
            self.assertEqual(original.SerializeToString(), exported.SerializeToString())
            image = np.zeros((1, 3, 48, 320), np.float32)
            expected = inference_session(MODEL).run(None, {"x": image})[0]
            actual = inference_session(target).run(None, {"x": image})[0]
            np.testing.assert_array_equal(actual, expected)
        self.assertEqual(before, hashlib.sha256(MODEL.read_bytes()).hexdigest())

    def test_changed_head_export_updates_both_storage_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            for constants in (False, True):
                with self.subTest(constants=constants):
                    source = Path(directory) / "source.onnx"
                    target = Path(directory) / "changed.onnx"
                    weight, bias = make_small_model(source, constants)
                    changed_weight, changed_bias = weight + np.float32(0.4), bias * np.float32(2)
                    onnx_head.export_head(source, changed_weight, changed_bias, target)
                    actual = onnx_head.inspect_head(target)
                    np.testing.assert_array_equal(actual["weight"], changed_weight)
                    np.testing.assert_array_equal(actual["bias"], changed_bias)
                    self.assertEqual(onnx_head.load_charset(target), ["blank", "a", "b", " "])
                    features = np.ones((1, 2, 2), np.float32)
                    logits = features @ changed_weight + changed_bias
                    expected = np.exp(logits - logits.max(axis=-1, keepdims=True))
                    expected /= expected.sum(axis=-1, keepdims=True)
                    actual_prob = inference_session(target).run(None, {"features": features})[0]
                    np.testing.assert_allclose(actual_prob, expected, atol=1e-7)

    def test_export_rejects_wrong_shapes_nonfinite_values_and_source_overwrite(self):
        info = onnx_head.inspect_head(MODEL)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "invalid.onnx"
            with self.assertRaises(ValueError):
                onnx_head.export_head(MODEL, info["weight"].T, info["bias"], target)
            bias = info["bias"].copy()
            bias[0] = np.nan
            with self.assertRaises(ValueError):
                onnx_head.export_head(MODEL, info["weight"], bias, target)
            with self.assertRaises(ValueError):
                onnx_head.export_head(MODEL, info["weight"], info["bias"], MODEL)


if __name__ == "__main__":
    unittest.main()
