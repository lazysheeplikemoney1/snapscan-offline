"""Audit the delivered native APK against the exact local assets and source tree.

This is a package audit, not a replacement for Android instrumentation or a phone
benchmark. Android runtime results are recorded separately.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "mobile/android-local"
ASSETS = PROJECT / "app/src/main/assets"
APK = ROOT / "releases/SnapScan-offline-local.apk"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit() -> dict:
    # aapt on Windows cannot open APK paths containing Chinese characters.
    # Reuse the verified ASCII junction created by build_local.py.
    alias = Path(tempfile.gettempdir()) / ("SnapScanAndroid-" + sha(str(ROOT).encode("utf-8"))[:10]) / "workspace"
    tool_root = ROOT
    if not str(ROOT).isascii():
        assert alias.exists() and alias.resolve() == ROOT.resolve(), "Run build_local.py to prepare the workspace alias"
        tool_root = alias
    aapt = tool_root / "build/android-local-sdk/build-tools/35.0.0/aapt.exe"
    tool_apk = tool_root / APK.relative_to(ROOT)
    permissions = subprocess.run([str(aapt), "dump", "permissions", str(tool_apk)], check=True, capture_output=True).stdout.decode("utf-8")
    badging = subprocess.run([str(aapt), "dump", "badging", str(tool_apk)], check=True, capture_output=True).stdout.decode("utf-8")
    assert "android.permission.INTERNET" not in permissions, permissions
    assert "android.permission.CAMERA" in permissions, permissions
    gradle = (PROJECT / "app/build.gradle").read_text(encoding="utf-8")
    version_code = int(re.search(r"\bversionCode\s+(\d+)", gradle).group(1))
    version_name = re.search(r"\bversionName\s+['\"]([^'\"]+)['\"]", gradle).group(1)
    assert f"versionCode='{version_code}'" in badging and f"versionName='{version_name}'" in badging and "targetSdkVersion:'35'" in badging, badging
    assert "sdkVersion:'26'" in badging, badging
    models, fonts, libraries, ui = {}, {}, {}, {}
    with zipfile.ZipFile(APK) as archive:
        names = set(archive.namelist())
        assert "assets/www/index.html" in names
        assert not any("receipt-feedback" in name or "receipt_clipped_wood" in name or name.startswith("assets/scan/") for name in names), "Test images leaked into the production package"
        fixture_hashes = {sha(path.read_bytes()) for path in (PROJECT / "app/src/androidTest/assets").rglob("*") if path.is_file()}
        for name in names:
            if name.startswith("assets/") and name.lower().endswith((".jpg", ".jpeg", ".png")):
                assert sha(archive.read(name)) not in fixture_hashes, "Test image leaked into production: " + name
        manifest = json.loads(archive.read("assets/models/model_manifest.json"))
        for name, expected in manifest["files"].items():
            data = archive.read("assets/models/" + name)
            digest = sha(data)
            assert digest == expected["sha256"] == sha((ASSETS / "models" / name).read_bytes()), name
            assert len(data) == expected["bytes"], name
            models[name] = {"sha256": digest, "bytes": len(data)}
        assert models["rec_head_v1.onnx"]["sha256"] == "d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe"
        for folder, destination in (("fonts", fonts), ("www", ui)):
            expected_names = set()
            for path in sorted((ASSETS / folder).rglob("*")):
                if not path.is_file():
                    continue
                relative = "assets/" + path.relative_to(ASSETS).as_posix()
                expected_names.add(relative)
                digest = sha(path.read_bytes())
                assert sha(archive.read(relative)) == digest, "Stale bundled asset: " + relative
                destination[relative] = {"sha256": digest, "bytes": path.stat().st_size}
            actual_names = {name for name in names if name.startswith("assets/" + folder + "/") and not name.endswith("/")}
            assert actual_names == expected_names, "Unexpected or missing bundled " + folder
        assert any(name.endswith(".ttf") for name in fonts), "PDF font missing"
        assert any("OFL" in name for name in fonts), "PDF font license missing"
        packaged_abis = sorted({name.split("/")[1] for name in names if name.startswith("lib/") and name.endswith(".so")})
        assert packaged_abis == ["arm64-v8a"], "The phone release should contain only the phone CPU runtime"
        for abi in packaged_abis:
            for library in ("libc++_shared.so", "libonnxruntime.so", "libonnxruntime4j_jni.so", "libopencv_java4.so"):
                name = f"lib/{abi}/{library}"
                data = archive.read(name)
                assert data[:6] == b"\x7fELF\x02\x01", "Expected 64-bit little endian ELF: " + name
                offset = struct.unpack_from("<Q", data, 32)[0]
                entry_size, count = struct.unpack_from("<HH", data, 54)
                aligns = [struct.unpack_from("<Q", data, offset + i * entry_size + 48)[0] for i in range(count) if struct.unpack_from("<I", data, offset + i * entry_size)[0] == 1]
                assert aligns and all(value >= 16384 and value % 16384 == 0 for value in aligns), name
                libraries[name] = {"sha256": sha(data), "bytes": len(data), "load_segment_alignment": aligns}
        for license_name in ("ONNXRuntime-LICENSE.txt", "ONNXRuntime-ThirdPartyNotices.txt", "OpenCV-LICENSE.txt", "RapidOCR-LICENSE.txt", "PaddleOCR-LICENSE.txt"):
            assert archive.getinfo("assets/licenses/" + license_name).file_size > 0
    sources = {}
    for directory in (PROJECT / "app/src/main/java", ROOT / "mobile/uniapp/pages", ROOT / "mobile/uniapp/utils"):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                sources[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
    report = {"passed": True, "scope": "release APK integrity, permissions and exact packaged assets; runtime reported separately",
              "apk": str(APK.relative_to(ROOT)), "sha256": sha(APK.read_bytes()), "bytes": APK.stat().st_size,
              "internet_permission": False, "camera_permission": True, "min_sdk": 26, "target_sdk": 35, "version_code": version_code, "version_name": version_name,
              "models": models, "fonts": fonts, "ui_assets": ui, "native_libraries": libraries, "apk_abis": packaged_abis,
              "test_images_in_release": False, "source_sha256": sources,
              "phone_hardware_tested": False, "phone_latency_claimed": False}
    output = ROOT / "reports/android_local_package_acceptance.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "apk": report["apk"], "sha256": report["sha256"], "bytes": report["bytes"], "models": len(models), "fonts_and_licenses": len(fonts), "ui_assets": len(ui), "native_libraries": len(libraries)}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    audit()
