"""Build a development APK with local resources and verified Camera registration.

No cloud packaging, existing credentials, or device installation is used. Run
--prepare first; --source-ready explicitly enables compilation and packaging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

CAMERA_PROPERTIES = "assets/data/dcloud_properties.xml"
CAMERA_CLASS = "io.dcloud.js.camera.CameraFeatureImpl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compiler_succeeded(returncode: int, output: str) -> bool:
    return returncode == 0 and "Build complete" in output and "此功能需要先登录" not in output


def is_signature(name: str) -> bool:
    if not name.upper().startswith("META-INF/"):
        return False
    leaf = name.upper().split("/")[-1]
    return leaf == "MANIFEST.MF" or leaf.startswith("SIG-") or leaf.endswith((".SF", ".RSA", ".DSA", ".EC"))


def resource_files(resources: Path) -> dict[str, Path]:
    resources = resources.resolve()
    files = {}
    for path in sorted(resources.rglob("*")):
        if path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(resources):
                raise ValueError("Resource must be a regular file within the resource directory")
            files[path.relative_to(resources).as_posix()] = path
    if not {"manifest.json", "app-service.js"}.issubset(files):
        raise ValueError("Compiled resources require manifest.json and app-service.js")
    return files


def register_camera(properties: bytes) -> bytes:
    """Register the already bundled Camera class; never replace native code."""
    root = ET.fromstring(properties)
    features = root.find("features")
    if root.tag != "properties" or features is None:
        raise ValueError("Native feature registry is missing its features element")
    camera = features.findall("feature[@name='Camera']")
    if camera:
        if len(camera) != 1 or camera[0].get("value") != CAMERA_CLASS:
            raise ValueError("Native Camera registration is duplicate or points to an unexpected class")
        return properties
    ET.SubElement(features, "feature", {"name": "Camera", "value": CAMERA_CLASS})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_unsigned_apk(base: Path, resources: Path, output: Path, appid: str) -> None:
    if output.resolve() == base.resolve():
        raise ValueError("Refusing to overwrite original APK")
    files = resource_files(resources)
    prefix = f"assets/apps/{appid}/www/"
    with zipfile.ZipFile(base) as source:
        names = source.namelist()
        if len(set(names)) != len(names):
            raise ValueError("Original APK contains duplicate ZIP names")
        if not any(name.startswith(prefix) for name in names):
            raise ValueError("Original APK does not contain the expected appid")
        camera_properties = register_camera(source.read(CAMERA_PROPERTIES))
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w") as target:
            for item in source.infolist():
                if not item.filename.startswith(prefix) and not is_signature(item.filename):
                    target.writestr(item, camera_properties if item.filename == CAMERA_PROPERTIES else source.read(item))
            for name, path in files.items():
                target.write(path, prefix + name, compress_type=zipfile.ZIP_DEFLATED)


def verify_payload(base: Path, candidate: Path, resources: Path, appid: str) -> dict:
    prefix = f"assets/apps/{appid}/www/"
    with zipfile.ZipFile(base) as original, zipfile.ZipFile(candidate) as built:
        def native_hashes(archive):
            return {item.filename: hashlib.sha256(archive.read(item)).hexdigest()
                    for item in archive.infolist()
                    if not item.filename.startswith(prefix) and not is_signature(item.filename)}
        old, new = native_hashes(original), native_hashes(built)
        changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
        expected = dict(old)
        expected[CAMERA_PROPERTIES] = hashlib.sha256(register_camera(original.read(CAMERA_PROPERTIES))).hexdigest()
        actual_resources = {item.filename[len(prefix):]: hashlib.sha256(built.read(item)).hexdigest()
                            for item in built.infolist() if item.filename.startswith(prefix) and not item.is_dir()}
        expected_resources = {name: sha256(path) for name, path in resource_files(resources).items()}
        return {
            "native_entries_unchanged": not changed,
            "native_changes_match_expected": expected == new,
            "native_entry_count": len(old),
            "changed_native_entries": changed,
            "resource_entries_match": actual_resources == expected_resources,
            "resource_entry_count": len(actual_resources),
            "resource_hashes": actual_resources,
            "native_abis": sorted({name.split("/")[1] for name in built.namelist() if name.startswith("lib/")}),
        }


def run(command: list[str], log: Path, *, env=None, timeout=180, cwd=None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env=env, timeout=timeout, cwd=cwd)
    log.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Command failed (exit {result.returncode}); inspect {log.name}")
    return result


def prepare_signing(work: Path, java_bin: Path) -> tuple[Path, Path]:
    signing = work / "signing"
    signing.mkdir(parents=True, exist_ok=True)
    key, password = signing / "snapscan-development.p12", signing / "password.txt"
    if key.exists():
        if not password.exists():
            raise RuntimeError("Development keystore exists without its generated password file")
        return key, password
    if not password.exists():
        secret = secrets.token_urlsafe(36)
        descriptor = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secret)
    env = os.environ.copy()
    env["SNAPSCAN_BUILD_KEY_PASSWORD"] = password.read_text(encoding="utf-8")
    run([str(java_bin / "keytool.exe"), "-genkeypair", "-noprompt", "-alias", "snapscan-dev",
         "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650", "-storetype", "PKCS12",
         "-keystore", str(key), "-storepass:env", "SNAPSCAN_BUILD_KEY_PASSWORD",
         "-keypass:env", "SNAPSCAN_BUILD_KEY_PASSWORD", "-dname", "CN=SnapScan Local Development"],
        work / "key-generation.log", env=env)
    return key, password


def source_hashes(project: Path) -> dict[str, str]:
    excluded = {"unpackage", "node_modules", ".git", "tests"}
    return {path.relative_to(project).as_posix(): sha256(path)
            for path in sorted(project.rglob("*"))
            if path.is_file() and not (set(path.relative_to(project).parts) & excluded)}


def main() -> int:
    if __package__:
        from .apk_capabilities import inspect_apk_capabilities
    else:
        from apk_capabilities import inspect_apk_capabilities

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Only create ignored local development signing material")
    parser.add_argument("--source-ready", action="store_true", help="Confirm source is frozen; compile and produce candidate APK")
    parser.add_argument("--hbuilderx", type=Path, default=Path("E:/HBuilderX/HBuilderX"))
    parser.add_argument("--variant", choices=("camera-fix", "document-workspace", "scan-imaging"), default="camera-fix",
                        help="Keep earlier release APKs/reports while building the selected feature set")
    args = parser.parse_args()
    if not args.prepare and not args.source_ready:
        parser.error("Use --prepare or --source-ready")
    root = Path(__file__).resolve().parents[1]
    project = root / "mobile/uniapp"
    work = root / "build/android_candidate"
    work.mkdir(parents=True, exist_ok=True)
    plugins = args.hbuilderx / "plugins"
    java_bin = plugins / "amazon-corretto/bin"
    safe_pack = plugins / "app-safe-pack"
    java, signer = str(java_bin / "java.exe"), str(safe_pack / "apksigner.jar")
    key, password = prepare_signing(work, java_bin)
    if not args.source_ready:
        print("Local development signing directory prepared; no compilation or APK changes.")
        return 0

    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("uniStatistics", {}).get("enable") is not False:
        raise RuntimeError("Local development build requires explicit uniStatistics.enable=false")
    appid = manifest["appid"]
    base = root / "releases/离线智能扫描仪-v0.1.apk"
    base_hash = sha256(base)
    base_capabilities = inspect_apk_capabilities(base, appid)
    # Resource replacement cannot add missing native implementations or Android permissions.
    for capability in ("camera_class_defined", "gallery_class_defined", "android_camera_permission",
                       "file_provider_present", "gallery_feature_registered"):
        if not base_capabilities[capability]:
            raise RuntimeError("Native base cannot support camera/gallery: " + capability)
    before = source_hashes(project)
    # The installed publish CLI requires an account even for local appResource.
    # Invoke that same installed compiler directly with its documented-in-code
    # environment; no account, cloud packaging, or dependency download is used.
    build_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    resources = work / ("compile-" + build_id) / "www"
    compiler_environment = {
        "NODE_ENV": "production", "UNI_PLATFORM": "app-plus", "UNI_INPUT_DIR": str(project),
        "UNI_OUTPUT_DIR": str(resources), "UNI_HBUILDERX_PLUGINS": str(plugins),
        "HX_Version": "5.26.2026091802", "UNI_APP_PLATFORM": "android", "UNI_APPID": appid,
        "NO_COLOR": "1", "NODE_SKIP_PLATFORM_CHECK": "1", "UNI_CLOUD_SPACES": "[]",
        "UNI_HBUILDERX_LANGID": "zh_CN", "UNI_MINIMIZE": "true", "VSCODE_NLS_CONFIG": '{"locale":"zh_CN"}',
    }
    environment = os.environ.copy()
    environment.update(compiler_environment)
    compiler = plugins / "uniapp-cli/bin/uniapp-cli.js"
    compile_command = [str(plugins / "node/node.exe"), str(compiler)]
    compile_result = run(compile_command, work / "direct-production-compile.log", env=environment,
                         cwd=plugins / "uniapp-cli", timeout=420)
    compile_text = compile_result.stdout + "\n" + compile_result.stderr
    if not compiler_succeeded(compile_result.returncode, compile_text):
        raise RuntimeError("Compiler did not report successful production output")
    if "已开启 uni统计" in compile_text:
        raise RuntimeError("Compiler unexpectedly enabled uni statistics")
    if source_hashes(project) != before:
        raise RuntimeError("Source changed during compilation; wait until it is frozen and rerun")
    files = resource_files(resources)
    compiled_manifest = json.loads((resources / "manifest.json").read_text(encoding="utf-8-sig"))
    if compiled_manifest.get("id", compiled_manifest.get("appid")) != appid:
        raise RuntimeError("Compiled manifest appid does not match source")
    if not {"Camera", "Gallery"}.issubset(compiled_manifest.get("permissions", {})):
        raise RuntimeError("Compiled application manifest must declare Camera and Gallery")
    javascript = "\n".join(path.read_text(encoding="utf-8") for name, path in files.items() if name.endswith(".js"))
    integration_assertions = {
        "current_default_backend": "http://192.168.58.120:8000" in javascript,
        "old_fixed_backend_absent": "192.168.56.120" not in javascript,
        "backend_address_storage": "snapscan_backend_base" in javascript and "setStorageSync" in javascript,
        "save_address_action": "setBase" in javascript and "saveBase" in javascript,
        "health_endpoint": "/api/health" in javascript,
        "actual_ocr_ready_check": ".ocr.ready" in javascript,
        "actual_model_response": ".ocr.model" in javascript,
        "actual_model_id_display": ".model.id" in javascript,
        "actual_model_sha256_display": ".sha256" in javascript,
        "uni_statistics_explicitly_disabled": manifest["uniStatistics"]["enable"] is False,
        "uni_statistics_not_auto_imported": "已开启 uni统计" not in compile_text,
    }
    if not all(integration_assertions.values()):
        raise RuntimeError("Compiled integration assertions failed: " + ", ".join(k for k, v in integration_assertions.items() if not v))
    if args.variant in ("document-workspace", "scan-imaging"):
        document_assertions = {"document_save": "saveDocument" in javascript,
                               "document_history": "/api/history/" in javascript,
                               "searchable_pdf": "searchable" in javascript,
                               "image_coordinates": "image_width" in javascript,
                               "revision_export": "scan_id" in javascript and "revision" in javascript}
        integration_assertions.update(document_assertions)
        if not all(document_assertions.values()):
            raise RuntimeError("Document workspace missing from compiled resources")
    if args.variant == "scan-imaging":
        imaging_assertions = {"scan_source_preview": "original_image" in javascript and "source_size" in javascript,
                              "manual_quad_upload": "quad" in javascript and "formData" in javascript,
                              "save_scan_image": "saveImageToPhotosAlbum" in javascript,
                              "scan_effects": '"clean"' in javascript and '"bw"' in javascript}
        integration_assertions.update(imaging_assertions)
        if not all(imaging_assertions.values()):
            raise RuntimeError("Scan imaging missing from compiled resources")
    unsigned, aligned = work / "candidate-unsigned.apk", work / "candidate-aligned.apk"
    output = work / (args.variant + "-signed.apk")
    release_output = root / ("releases/SnapScan-rec-head-v1-" + args.variant + ".apk")
    build_unsigned_apk(base, resources, unsigned, appid)
    run([str(safe_pack / "zipalign/zipalign.exe"), "-f", "-p", "4", str(unsigned), str(aligned)],
        work / "zipalign.log")
    run([java, "-jar", signer, "sign", "--ks", str(key), "--ks-key-alias", "snapscan-dev",
         "--ks-pass", "file:" + str(password),
         "--v1-signing-enabled", "true", "--v2-signing-enabled", "true", "--out", str(output), str(aligned)],
        work / "signing.log")
    verified = run([java, "-jar", signer, "verify", "--verbose", "--print-certs", str(output)],
                   work / "signature-verification.log")
    base_verified = run([java, "-jar", signer, "verify", "--print-certs", str(base)],
                        work / "base-signature-verification.log")
    run([str(safe_pack / "zipalign/zipalign.exe"), "-c", "-p", "4", str(output)], work / "zipalign-check.log")
    payload = verify_payload(base, output, resources, appid)
    if not payload["native_changes_match_expected"] or not payload["resource_entries_match"] or sha256(base) != base_hash:
        raise RuntimeError("APK payload verification or original preservation failed")
    capabilities = inspect_apk_capabilities(output, appid)
    if not capabilities["camera_static_ready"]:
        raise RuntimeError("Final APK camera capability checks failed: " + json.dumps(capabilities, ensure_ascii=True))
    cert_pattern = r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)"
    certificate = re.search(cert_pattern, verified.stdout).group(1)
    base_certificate = re.search(cert_pattern, base_verified.stdout).group(1)
    if certificate == base_certificate:
        raise RuntimeError("Expected independent development certificate")
    previous_dev = root / "releases/SnapScan-rec-head-v1-dev.apk"
    previous_certificate = None
    previous_capabilities = None
    if previous_dev.exists():
        previous_verification = run([java, "-jar", signer, "verify", "--print-certs", str(previous_dev)],
                                    work / "previous-dev-signature-verification.log")
        previous_certificate = re.search(cert_pattern, previous_verification.stdout).group(1)
        previous_capabilities = inspect_apk_capabilities(previous_dev, appid)
        if certificate != previous_certificate:
            raise RuntimeError("Camera fix must retain the previous development APK signing certificate")
    # Only expose an APK after every signature, resource and native capability check passes.
    temporary_release = release_output.with_suffix(".apk.tmp")
    temporary_release.write_bytes(output.read_bytes())
    temporary_release.replace(release_output)
    wgt = root / ("releases/SnapScan-rec-head-v1-" + args.variant + "-resources.wgt")
    with zipfile.ZipFile(wgt, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": args.variant.replace("-", "_") + "_built_and_static_capabilities_verified",
        "build_method": "local_production_resources_and_existing_camera_feature_registration_repair",
        "cloud_source_upload": False,
        "source_project": project.relative_to(root).as_posix(),
        "source_hashes": before,
        "compile_exit_code": compile_result.returncode,
        "compile_command": compile_command,
        "compiler_environment": compiler_environment,
        "compiler_package_sha256": sha256(plugins / "uniapp-cli/package.json"),
        "compiler_entry_sha256": sha256(compiler),
        "publish_cli_limitation": "appResource publish requested login; direct installed production compiler was used instead",
        "compiled_integration_assertions": integration_assertions,
        "resource_directory": resources.relative_to(root).as_posix(),
        "base_apk": {"path": base.relative_to(root).as_posix(), "sha256": base_hash, "unchanged": True,
                     "certificate_sha256": base_certificate},
        "candidate_apk": {"path": release_output.relative_to(root).as_posix(), "sha256": sha256(release_output),
                          "size_bytes": output.stat().st_size, "certificate_sha256": certificate,
                          "development_signing": True, "can_update_original_signed_app": False,
                          "same_signer_as_previous_dev": certificate == previous_certificate,
                          "v1_verified": "Verified using v1 scheme (JAR signing): true" in verified.stdout,
                          "v2_verified": "Verified using v2 scheme (APK Signature Scheme v2): true" in verified.stdout,
                          "zipalign_verified": True, "native_version_name": "0.1.0", "native_version_code": 100},
        "resource_archive": {"path": wgt.relative_to(root).as_posix(), "sha256": sha256(wgt),
                             "unsigned": True, "deployed": False},
        "payload_verification": payload,
        "base_capabilities": base_capabilities,
        "previous_development_capabilities": previous_capabilities,
        "camera_capabilities": capabilities,
        "camera_registration_repair": {"entry": CAMERA_PROPERTIES, "implementation": CAMERA_CLASS,
                                       "dex_and_android_manifest_unchanged": payload["native_changes_match_expected"]},
        "device_installed": False, "device_uninstalled": False,
        "real_device_launch_verified": False, "camera_verified": False,
        "dcloud_runtime_authorization": "unknown_requires_real_device_validation",
        "limitations": ["Static capability checks do not replace physical-device capture/permission validation.",
                        "Development test APK signed differently from original; cannot directly update an existing original installation.",
                        "Only arm64-v8a native libraries inherited from original APK.",
                        "Native Android version remains 0.1.0 (100); filename distinguishes this candidate.",
                        "Web resources compiled by HBuilderX 5.26; inherited runtime compatibility still requires device validation.",
                        "No device installation, uninstallation, camera test, or end-to-end phone acceptance was performed.",
                        "Unsigned WGT cannot repair native Camera registration; install the fixed APK, not a WGT-only update.",
                        "The source uniStatistics setting disables web statistics; inherited native metadata is unchanged."],
    }
    report_path = root / ("reports/android_" + args.variant.replace("-", "_") + "_verification.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "apk": report["candidate_apk"], "report": str(report_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
