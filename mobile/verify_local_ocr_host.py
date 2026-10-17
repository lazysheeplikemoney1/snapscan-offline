"""Compile the production Java OCR and run real DEV inference on Windows.

Host-only OpenPnP OpenCV 4.9.0 provides Windows JNI; it is NOT an APK dependency.
The Android build uses the official OpenCV Android 4.12.0 artifact. Dependencies
must already be present in build/android-local/host-deps (no hidden downloads).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=root / "output/scan/imaging-real-036-clean.png")
    args = parser.parse_args()
    image = args.image.resolve()
    # The isolated acceptance product below comes exclusively from real DEV 036.
    allowed = (root / "output/scan/imaging-real-036-clean.png").resolve()
    if image != allowed:
        raise ValueError("This smoke harness is intentionally restricted to the reviewed DEV036 clean image")
    work = root / "build/android-local"
    deps, classes, stubs = work / "host-deps", work / "host-classes", work / "host-stubs"
    jars = [deps / name for name in ("onnxruntime-1.23.2.jar", "opencv-4.9.0-0.jar", "json-20240303.jar")]
    if not all(path.exists() for path in jars):
        raise FileNotFoundError("Host-only dependencies are not available; see the module docstring")
    context = stubs / "android/content/Context.java"
    assets = stubs / "android/content/res/AssetManager.java"
    context.parent.mkdir(parents=True, exist_ok=True)
    assets.parent.mkdir(parents=True, exist_ok=True)
    # Compile-time Android signatures only. The smoke test calls OcrEngine(File),
    # and all actual image processing/model inference uses native OpenCV/ORT.
    context.write_text("package android.content; public abstract class Context { public abstract android.content.res.AssetManager getAssets(); }\n", encoding="utf-8")
    assets.write_text("package android.content.res; public class AssetManager { public java.io.InputStream open(String name) throws java.io.IOException { throw new java.io.IOException(\"Android-only path\"); } }\n", encoding="utf-8")
    java_dir = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto/bin")
    # The bundled JDK ships MSVC 14.29; ORT 1.23.2's Windows DLL fails its
    # initialization with that copy. Use an isolated copy with this Windows
    # installation's current CRT. Never change HBuilderX or system DLLs.
    host_jdk = work / "host-jdk"
    if not host_jdk.exists():
        shutil.copytree(java_dir.parent, host_jdk)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(Path("C:/Windows/System32") / name, host_jdk / "bin" / name)
    java_dir = host_jdk / "bin"
    source = root / "mobile/android-local/app/src/main/java/com/snapscan/offline"
    tests = root / "mobile/android-local/host-tests/com/snapscan/offline"
    files = [source / name for name in ("OcrMath.java", "OcrEngine.java")]
    files += [tests / name for name in ("OcrContractTest.java", "OcrEngineSmoke.java")]
    android_jars = [root / "build/android-local-sdk/platforms/android-35/android.jar",
                    root / "build/android-local-deps/onnxruntime-android-1.23.2/classes.jar",
                    root / "build/android-local-deps/opencv-4.12.0/classes.jar"]
    android_compiled = all(path.exists() for path in android_jars)
    if android_compiled:
        subprocess.run([str(java_dir / "javac.exe"), "-encoding", "UTF-8", "-cp", ";".join(map(str, android_jars)),
                        "-d", str(work / "android-contract-classes"), *map(str, files[:2])], check=True)
    classpath = ";".join(map(str, jars))
    classes.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(java_dir / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
                    "-d", str(classes), str(context), str(assets), *map(str, files)], check=True)
    runtime = str(classes) + ";" + classpath
    subprocess.run([str(java_dir / "java.exe"), "-cp", runtime, "com.snapscan.offline.OcrContractTest"], check=True)
    output = work / "ocr-host-dev036.json"
    subprocess.run([str(java_dir / "java.exe"), "-Xmx1500m", "-cp", runtime,
                    "com.snapscan.offline.OcrEngineSmoke", str(root / "mobile/android-local/app/src/main/assets/models"),
                    str(image), str(output)], check=True)
    result = json.loads(output.read_text(encoding="utf-8"))
    # Re-run the deployed Python pipeline on this same DEV-only ndarray, without
    # importing routers, opening SQLite, or writing production uploads.
    import cv2
    import numpy as np
    sys.path.insert(0, str(root / "backend"))
    from app.services.ocr_engine import RapidEngine
    previous = os.environ.get("SNAPSCAN_OCR_MODEL")
    os.environ["SNAPSCAN_OCR_MODEL"] = "rec_head_v1"
    try:
        pixels = cv2.imdecode(np.frombuffer(image.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        reference = RapidEngine().recognize(pixels)
    finally:
        if previous is None:
            os.environ.pop("SNAPSCAN_OCR_MODEL", None)
        else:
            os.environ["SNAPSCAN_OCR_MODEL"] = previous
    comparison = {"same_box_count": len(result["boxes"]) == len(reference["boxes"]),
                  "python_boxes": len(reference["boxes"]), "java_boxes": len(result["boxes"]),
                  "python_characters": len(reference["text"]), "java_characters": len(result["text"]),
                  "exact_text": result["text"] == reference["text"]}
    if comparison["same_box_count"]:
        comparison["exact_text_lines"] = sum(a["text"] == b["text"] for a, b in zip(result["boxes"], reference["boxes"]))
        delta = np.abs(np.asarray([r["box"] for r in result["boxes"]]) - np.asarray([r["box"] for r in reference["boxes"]]))
        comparison["box_coordinate_mean_abs_px"] = float(delta.mean())
        comparison["box_coordinate_max_abs_px"] = float(delta.max())
        comparison["maximum_score_difference"] = max(abs(a["score"] - b["score"]) for a, b in zip(result["boxes"], reference["boxes"]))
    report = {"source_kind": "real_dev_processed", "source_image_id": "real-036.jpg",
              "input": image.relative_to(root).as_posix(), "test_set_read": False,
              "input_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "runtime": "real JVM + native ONNX Runtime 1.23.2 + host-only OpenCV 4.9.0",
              "android_runtime_executed": False, "phone_latency_claim": False,
              "model": result["model"], "boxes": len(result["boxes"]), "characters": len(result["text"]),
              "caller_input_unchanged": result["caller_input_unchanged"],
              "host_elapsed_ms": result["host_elapsed_ms"],
              "contract_checks_passed": 12,
              "android_api35_official_dependencies_compile_verified": android_compiled,
              "python_rapidocr_comparison": comparison,
              "db_unclip_note": "Analytic enclosing rectangle equals the ideal rectangular round-offset enclosure; PyClipper intermediate integer quantization is not reproduced.",
              "dictionary_jni_note": "ORT Java 1.23.2 metadata modified-UTF8 damages supplementary CJK and line separators. Decoder instead reads the SHA-verified Python-exported UTF8 JSON dictionary.",
              "windows_host_runtime_note": "Bundled JDK ships MSVC14.29. Isolated build copy uses existing Windows system CRT; original JDK/system files remain unchanged.",
              "recognized_text_artifact": output.relative_to(root).as_posix()}
    target = root / "reports/android_offline_ocr_host.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Report: " + str(target))


if __name__ == "__main__":
    main()
