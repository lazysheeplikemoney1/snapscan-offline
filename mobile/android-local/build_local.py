"""Build the independent, network-permission-free Android APK with isolated tools."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT.parents[1]
SDK = ROOT / "build/android-local-sdk"
JDK = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto")
GRADLE = Path("C:/Users/86197/.gradle/wrapper/dists/gradle-8.10.2-bin/a04bxjujx95o3nb99gddekhwo/gradle-8.10.2/bin/gradle.bat")

def package_metadata(badging):
    line = next((line for line in badging.splitlines() if line.startswith("package:")), "")
    fields = dict(re.findall(r"(\w+)='([^']*)'", line))
    if fields.get("name") != "com.snapscan.offline" or not fields.get("versionCode", "").isdigit() or not fields.get("versionName"):
        raise RuntimeError("APK package or version metadata is missing or invalid")
    return {"version_code": int(fields["versionCode"]), "version_name": fields["versionName"]}

def reject_test_fixtures(names):
    markers = ("receipt-feedback", "receipt_clipped_wood", "real-037", "independent-20261016", "book-dev-", "book_ocr/")
    if any(any(marker in name for marker in markers) for name in names):
        raise RuntimeError("Refusing to publish Android test photo fixtures in the application APK")

def ascii_workspace():
    tag = hashlib.sha256(str(ROOT).encode("utf-8")).hexdigest()[:10]
    base = Path(os.environ.get("TEMP", "C:/Temp")) / ("SnapScanAndroid-" + tag)
    base.mkdir(parents=True, exist_ok=True)
    alias = base / "workspace"
    if alias.exists():
        if alias.resolve() != ROOT.resolve():
            raise RuntimeError("Existing ASCII workspace alias belongs to a different directory")
    else:
        command = "New-Item -ItemType Junction -Path '" + str(alias).replace("'", "''") + "' -Target '" + str(ROOT).replace("'", "''") + "' | Out-Null"
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True)
    return base, alias

def source_files():
    sources = [PROJECT / name for name in ("settings.gradle", "build.gradle", "gradle.properties", "app/build.gradle")]
    return sources + [path for path in (PROJECT / "app/src").rglob("*") if path.is_file()]

def source_snapshot():
    hashes = {source.relative_to(PROJECT).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest() for source in source_files()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"sha256": digest, "files": hashes}

def stage_project(base):
    stage = base / "project"
    stage.mkdir(exist_ok=True)
    sources = source_files()
    wanted = {source.relative_to(PROJECT).as_posix() for source in sources}
    for previous in stage.rglob("*"):
        relative = previous.relative_to(stage).as_posix()
        if previous.is_file() and relative.startswith("app/src/") and relative not in wanted:
            assert previous.resolve().is_relative_to(stage.resolve())
            previous.unlink()
    for source in sources:
        destination = stage / source.relative_to(PROJECT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
            shutil.copy2(source, destination)
    return stage

def windows_long_path(path):
    return "\\\\?\\" + str(path.absolute()) if os.name == "nt" else str(path)

def environment():
    env = os.environ.copy()
    env.update(JAVA_HOME=str(JDK), ANDROID_HOME=str(SDK), ANDROID_SDK_ROOT=str(SDK))
    return env

def prepare_assets():
    subprocess.run([sys.executable, str(ROOT / "mobile/prepare_local_font.py")], cwd=ROOT, check=True)
    plugins = Path("E:/HBuilderX/HBuilderX/plugins")
    env = environment()
    env.update(NODE_ENV="production", UNI_PLATFORM="h5", UNI_INPUT_DIR=str(ROOT / "mobile/uniapp"),
               UNI_OUTPUT_DIR=str(PROJECT / "app/src/main/assets/www"), UNI_HBUILDERX_PLUGINS=str(plugins),
               HX_Version="5.26.2026091802", UNI_APP_PLATFORM="android", UNI_APPID="__UNI__751ED2B",
               NO_COLOR="1", NODE_SKIP_PLATFORM_CHECK="1", UNI_CLOUD_SPACES="[]", UNI_HBUILDERX_LANGID="zh_CN", UNI_MINIMIZE="true", VSCODE_NLS_CONFIG='{"locale":"zh_CN"}')
    subprocess.run([str(plugins / "node/node.exe"), str(plugins / "uniapp-cli/bin/uniapp-cli.js")], cwd=plugins / "uniapp-cli", env=env, check=True)
    if not (PROJECT / "app/src/main/assets/www/index.html").is_file():
        raise RuntimeError("H5 compiler did not produce the packaged entry point")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Validate and copy the release APK into releases/")
    parser.add_argument("--skip-assets", action="store_true", help="Reuse an already compiled H5 UI and prepared fonts")
    parser.add_argument("--gradle-arg", action="append", default=[])
    parser.add_argument("tasks", nargs="*", default=["testDebugUnitTest", "assembleDebug", "assembleDebugAndroidTest"])
    args = parser.parse_args()
    if args.bootstrap:
        subprocess.run([sys.executable, str(PROJECT / "bootstrap_android.py")], check=True)
    if not (SDK / "platforms/android-35/android.jar").is_file():
        raise RuntimeError("Run bootstrap_android.py before building; do not use the stale global SDK environment")
    if not args.skip_assets:
        prepare_assets()
    snapshot = source_snapshot()
    base, alias = ascii_workspace()
    stage = stage_project(base)
    local = "sdk.dir=" + (alias / "build/android-local-sdk").as_posix() + "\n"
    (PROJECT / "local.properties").write_text(local, encoding="utf-8")
    (stage / "local.properties").write_text(local, encoding="utf-8")
    tasks = args.tasks or ["testDebugUnitTest", "assembleDebug", "assembleDebugAndroidTest"]
    if args.publish and any("debug" in task.lower() or "test" in task.lower() for task in tasks):
        raise RuntimeError("Publish release separately; debug tests retain x86_64 while the phone release is arm64-only")
    gradle_args = [*args.gradle_arg, *(["-PsnapscanReleaseArm64=true"] if args.publish else [])]
    env = environment()
    env.update(ANDROID_HOME=str(alias / "build/android-local-sdk"), ANDROID_SDK_ROOT=str(alias / "build/android-local-sdk"))
    result = subprocess.run([str(GRADLE), "--no-daemon", "--console=plain", "-Dorg.gradle.java.home=" + str(JDK), "-PsnapscanProjectRoot=" + str(alias), *gradle_args, *tasks], cwd=stage, env=env)
    for name in ("outputs", "reports", "test-results"):
        folder = stage / "app/build" / name
        if folder.exists():
            shutil.copytree(windows_long_path(folder), windows_long_path(PROJECT / "app/build" / name), dirs_exist_ok=True)
    if result.returncode:
        raise RuntimeError(f"Gradle exited {result.returncode}; source preserved, build directory: {stage}")
    if source_snapshot() != snapshot:
        raise RuntimeError("Android sources changed during the build; rebuild the frozen source before publishing")
    (ROOT / "build/android-local-source-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.publish:
        return
    apk = stage / "app/build/outputs/apk/release/app-release.apk"
    if not apk.is_file():
        raise RuntimeError("--publish requires a freshly assembled release APK")
    tools = SDK / "build-tools/35.0.0"
    signed = subprocess.run([str(JDK / "bin/java.exe"), "-jar", str(tools / "lib/apksigner.jar"), "verify", "--verbose", "--print-certs", str(apk)], capture_output=True, text=True, encoding="utf-8", env=environment(), check=True)
    permission_output = subprocess.run([str(tools / "aapt.exe"), "dump", "permissions", str(apk)], capture_output=True, text=True, encoding="utf-8", env=environment(), check=True).stdout
    metadata = package_metadata(subprocess.run([str(tools / "aapt.exe"), "dump", "badging", str(apk)], capture_output=True, text=True, encoding="utf-8", env=environment(), check=True).stdout)
    if "android.permission.INTERNET" in permission_output:
        raise RuntimeError("Refusing to publish an APK with INTERNET permission")
    cert = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)", signed.stdout)
    if not cert or cert.group(1) != "030c2d44aec5e439712db164fd60552d000d74367c3eaaab5a0029cfe1a691e8":
        raise RuntimeError("APK does not retain the established development certificate")
    with zipfile.ZipFile(apk) as archive:
        names = set(archive.namelist())
        required = ["assets/www/index.html", "assets/models/det_fp32.onnx", "assets/models/rec_head_v1.onnx", "assets/models/characters.json", "assets/models/model_manifest.json", "assets/fonts/SnapScanSansSC.ttf", "assets/licenses/ONNXRuntime-LICENSE.txt", "assets/licenses/OpenCV-LICENSE.txt"]
        for abi in ("arm64-v8a",):
            required.extend(f"lib/{abi}/{name}" for name in ("libonnxruntime.so", "libonnxruntime4j_jni.so", "libopencv_java4.so"))
        if any(name not in names for name in required):
            raise RuntimeError("Required local UI, model or CPU runtime is absent from the APK")
        if any(name.startswith("lib/") and not name.startswith("lib/arm64-v8a/") for name in names):
            raise RuntimeError("Phone release unexpectedly contains a non-arm64 native runtime")
        reject_test_fixtures(names)
        model_hash = hashlib.sha256(archive.read("assets/models/rec_head_v1.onnx")).hexdigest()
        if model_hash != "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe":
            raise RuntimeError("Packaged trained model differs from the selected deployment")
    subprocess.run([str(tools / "zipalign.exe"), "-c", "-P", "16", "4", str(apk)], env=environment(), check=True)
    version_label = re.sub(r"[^A-Za-z0-9._-]", "_", metadata["version_name"].removesuffix("-local"))
    versioned_release = ROOT / ("releases/SnapScan-offline-local-" + version_label + ".apk")
    if versioned_release.exists() and hashlib.sha256(versioned_release.read_bytes()).digest() != hashlib.sha256(apk.read_bytes()).digest():
        raise RuntimeError("A different APK already occupies this versioned release; preserve it and increment the release version")
    if not versioned_release.exists():
        shutil.copyfile(apk, versioned_release)
    release = ROOT / "releases/SnapScan-offline-local.apk"
    temporary = release.with_suffix(".apk.tmp")
    shutil.copyfile(apk, temporary); temporary.replace(release)
    report = {"build_only": True, "device_validation": "reported separately", "apk": str(release.relative_to(ROOT)),
              "versioned_apk": str(versioned_release.relative_to(ROOT)),
              "sha256": hashlib.sha256(release.read_bytes()).hexdigest(), "bytes": release.stat().st_size,
              "certificate_sha256": cert.group(1), "internet_permission": False, "model_sha256": model_hash,
              "source_sha256": snapshot["sha256"], "source_snapshot": "build/android-local-source-snapshot.json", "test_photo_fixtures_in_apk": False,
              "abi": ["arm64-v8a"], **metadata, "jdk": str(JDK), "sdk": str(SDK), "gradle": str(GRADLE)}
    (ROOT / "reports/android_local_build_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
