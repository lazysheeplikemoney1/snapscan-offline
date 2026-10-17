"""Frozen RapidOCR feature extraction and compatible CTC head replacement.

Weights use ONNX's [features, classes] convention: logits = features @ W + b.
Only NumPy/ONNX/ONNX Runtime/OpenCV are needed; this module does not train a
backbone or alter the supplied source model. Feature graphs exist in memory.
"""
import math
from pathlib import Path

import cv2
import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
import onnxruntime as ort


def _tensor(model, name):
    for tensor in model.graph.initializer:
        if tensor.name == name:
            return tensor
    for node in model.graph.node:
        if node.op_type == "Constant" and name in node.output:
            for attribute in node.attribute:
                if attribute.name == "value" and attribute.type == onnx.AttributeProto.TENSOR:
                    return attribute.t
    raise ValueError(f"Head tensor {name!r} is not a Constant or initializer")


def _charset(model):
    metadata = {item.key: item.value for item in model.metadata_props}
    if "character" not in metadata:
        raise ValueError("Model has no embedded 'character' vocabulary")
    characters = metadata["character"].splitlines()
    if not characters or len(set(characters)) != len(characters):
        raise ValueError("Embedded character vocabulary is empty or contains duplicates")
    # RapidOCR 1.4.4 CTCLabelDecode adds these around the model metadata list.
    return ["blank", *characters, " "]


def load_charset(model_path):
    """Return RapidOCR's exact class order, including blank=0 and final space."""
    return _charset(onnx.load(str(model_path)))


def _inspect(model):
    producers = {name: node for node in model.graph.node for name in node.output}
    candidates = []
    for output in model.graph.output:
        final = producers.get(output.name)
        if final is None or final.op_type != "Softmax":
            continue
        attributes = {item.name: helper.get_attribute_value(item) for item in final.attribute}
        if attributes.get("axis", 1) not in (2, -1):
            continue
        add = producers.get(final.input[0])
        if add is None or add.op_type != "Add":
            continue
        for index, name in enumerate(add.input):
            matmul = producers.get(name)
            if matmul is not None and matmul.op_type == "MatMul":
                candidates.append((output.name, final, add, matmul, add.input[1 - index]))
    if len(candidates) != 1:
        raise ValueError("Expected one final MatMul -> Add -> Softmax CTC head")
    output_name, softmax, add, matmul, bias_name = candidates[0]
    weight_name = matmul.input[1]
    weight = numpy_helper.to_array(_tensor(model, weight_name)).copy()
    bias = numpy_helper.to_array(_tensor(model, bias_name)).copy()
    if weight.ndim != 2 or bias.ndim != 1 or weight.shape[1] != bias.shape[0]:
        raise ValueError("Head must have W[features, classes] and b[classes]")
    if weight.dtype != np.float32 or bias.dtype != np.float32:
        raise ValueError("Only float32 recognition heads are supported")
    charset = _charset(model)
    if len(charset) != bias.shape[0]:
        raise ValueError("Head class count does not match the embedded CTC vocabulary")
    return {
        "weight": weight,
        "bias": bias,
        "weight_name": weight_name,
        "bias_name": bias_name,
        "feature_name": matmul.input[0],
        "logits_name": softmax.input[0],
        "output_name": output_name,
        "input_name": model.graph.input[0].name,
        "num_features": weight.shape[0],
        "num_classes": weight.shape[1],
        "charset": charset,
    }


def inspect_head(model_path):
    """Locate the deployed head automatically; return copied W/b and tensor names."""
    return _inspect(onnx.load(str(model_path)))


def create_feature_session(model_path, threads=4):
    """Expose only the pre-FC [N,T,F] tensor in an in-memory CPU ORT session."""
    if not isinstance(threads, int) or threads < 1:
        raise ValueError("threads must be a positive integer")
    model = onnx.load(str(model_path))
    info = _inspect(model)
    del model.graph.output[:]
    model.graph.output.append(helper.make_tensor_value_info(
        info["feature_name"], TensorProto.FLOAT, ["N", "T", info["num_features"]]
    ))
    onnx.checker.check_model(model)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(
        model.SerializeToString(), sess_options=options, providers=["CPUExecutionProvider"]
    )


def preprocess_crop(image, target_width=None):
    """Apply RapidOCR 1.4.4's single-crop BGR recognition normalization.

    Input is a uint8 HWC BGR crop; output is contiguous float32 [1,3,48,W].
    W is at least 320 and expands for long lines. Optional target_width is for
    right-padding a batch and may not shrink a line. Padding is zero *after*
    normalization, matching RapidOCR, rather than black uint8 pixel padding.
    """
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected a uint8 HWC BGR crop with three channels")
    height, width = image.shape[:2]
    if height < 1 or width < 1 or image.dtype != np.uint8:
        raise ValueError("Expected a nonempty uint8 BGR crop")
    ratio = width / float(height)
    natural_width = int(48 * max(320 / 48, ratio))
    if target_width is None:
        padded_width = natural_width
    elif not isinstance(target_width, (int, np.integer)) or target_width < natural_width:
        raise ValueError(f"target_width must be an integer >= {natural_width}")
    else:
        padded_width = int(target_width)
    resized_width = min(int(math.ceil(48 * ratio)), padded_width)
    resized = cv2.resize(image, (resized_width, 48)).astype(np.float32)
    resized = resized.transpose((2, 0, 1)) / 255
    resized -= 0.5
    resized /= 0.5
    padded = np.zeros((1, 3, 48, padded_width), np.float32)
    padded[0, :, :, :resized_width] = resized
    return padded


def export_head(model_path, W, b, out_path):
    """Copy an ONNX graph, replacing only float32 head W/b; preserve metadata.

    W has shape [features, classes], the transpose of torch.nn.Linear.weight.
    The original probability output name and Softmax remain unchanged.
    """
    source, target = Path(model_path), Path(out_path)
    if source.resolve() == target.resolve() or (target.exists() and source.samefile(target)):
        raise ValueError("Export destination must differ from the source model")
    model = onnx.load(str(source))
    info = _inspect(model)
    values = [(info["weight_name"], W, info["weight"].shape),
              (info["bias_name"], b, info["bias"].shape)]
    for name, value, shape in values:
        value = np.asarray(value, dtype=np.float32)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError(f"{name} must have shape {shape} and contain only finite values")
        original = _tensor(model, name)
        replacement = numpy_helper.from_array(np.ascontiguousarray(value), name=original.name)
        original.CopyFrom(replacement)
    onnx.checker.check_model(model)
    target.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(target))
    return target
