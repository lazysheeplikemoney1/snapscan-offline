"""Collect actual emulator artifacts without a PowerShell text redirection of binary data.

Run after connectedDebugAndroidTest with
-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true.
This only reads the independently installed test app and leaves its database intact.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "mobile/android-local"
ADB = ROOT / "build/android-local-sdk/platform-tools/adb.exe"
PACKAGE = "com.snapscan.offline"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def reading_order_reference(path):
    name = path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"reading-order-proof-[a-z0-9_-]+\.(png|jpg|pdf|xlsx|json|txt)", name) or path not in ("/data/user/0/" + PACKAGE + "/cache/" + name, "/data/data/" + PACKAGE + "/cache/" + name):
        raise RuntimeError("Reading-order proof references a file outside the named app test cache")
    return "cache/" + name, name

def book_ocr_references(proof, version_code):
    if proof.get("version_code") != version_code or proof.get("source_split") != "dev" or proof.get("holdout_read") is not False:
        raise RuntimeError("Book OCR proof must belong to this APK and the old DEV regression only")
    names = {"source_image": "source.jpg", "prediction_json": "prediction.json", "text": "text.txt", "overlay": "overlay.jpg"}
    if set(proof.get("artifacts", {})) != set(names):
        raise RuntimeError("Book OCR proof must contain exactly its four named artifacts")
    references = {}
    for key, suffix in names.items():
        name = "book-ocr-proof-" + suffix
        path = proof["artifacts"][key].get("path")
        if path not in ("/data/user/0/" + PACKAGE + "/cache/" + name, "/data/data/" + PACKAGE + "/cache/" + name):
            raise RuntimeError("Book OCR proof references a file outside its exact named test cache")
        references[key] = ("cache/" + name, name)
    return references

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default="emulator-5580")
    args = parser.parse_args()
    output = ROOT / "output/scan/android-device"
    output.mkdir(parents=True, exist_ok=True)
    def adb(*command, binary=False):
        result = subprocess.run([str(ADB), "-s", args.serial, *command], capture_output=True, check=True)
        return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()
    device = {key: adb("shell", "getprop", prop) for key, prop in {
        "api": "ro.build.version.sdk", "android": "ro.build.version.release", "abi": "ro.product.cpu.abi",
        "model": "ro.product.model", "fingerprint": "ro.build.fingerprint"}.items()}
    device["serial"] = args.serial
    device["wifi_on"] = adb("shell", "settings", "get", "global", "wifi_on")
    device["mobile_data"] = adb("shell", "settings", "get", "global", "mobile_data")
    if device["wifi_on"] != "0" or device["mobile_data"] != "0":
        raise RuntimeError("Disable emulator Wi-Fi and mobile data before collecting offline validation")
    package_dump = adb("shell", "dumpsys", "package", PACKAGE)
    (output / "installed-package.txt").write_text(package_dump, encoding="utf-8")
    if "android.permission.INTERNET" in package_dump:
        raise RuntimeError("Installed application unexpectedly declares INTERNET permission")
    version_code = int(re.search(r"versionCode=(\d+)", package_dump).group(1))
    proof_bytes = adb("exec-out", "run-as", PACKAGE, "cat", "cache/workflow-proof.json", binary=True)
    workflow_proof = json.loads(proof_bytes)
    if workflow_proof["version_code"] != version_code:
        raise RuntimeError("Workflow proof belongs to a different installed APK version")
    proof_path = output / "workflow-proof.json"
    proof_path.write_bytes(proof_bytes)
    (output / "connectivity.txt").write_text(adb("shell", "dumpsys", "connectivity"), encoding="utf-8")
    (output / "webview.txt").write_text(adb("shell", "dumpsys", "webviewupdate"), encoding="utf-8")
    payload = adb("exec-out", "run-as", PACKAGE, "tar", "-cf", "-", "files/local-scans", "databases", binary=True)
    archive = output / "device-private-test-data.tar"
    archive.write_bytes(payload)
    private = output / "private"
    private.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        for entry in tar:
            if not (entry.name.startswith("files/local-scans/") or entry.name == "files/local-scans" or entry.name.startswith("databases/") or entry.name == "databases"):
                raise RuntimeError("Unexpected path in test-data archive")
            target = private / entry.name
            if not target.resolve().is_relative_to(private.resolve()) or entry.issym() or entry.islnk():
                raise RuntimeError("Unsafe path in test-data archive")
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
    database = private / "databases/snapscan-local.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM documents WHERE id=?", (workflow_proof["document"]["id"],)).fetchone()
        if row is None:
            raise RuntimeError("The real receipt workflow test did not leave its corrected record")
        stored_document = dict(row)
    for key in ("boxes", "original_boxes", "model"):
        stored_document[key] = json.loads(stored_document[key])
    if stored_document != workflow_proof["stored_document"]:
        raise RuntimeError("Device SQLite record differs from the exact successful workflow proof")
    document = workflow_proof["document"]
    for key in ("id", "revision", "image", "image_width", "image_height", "boxes", "original_boxes", "model"):
        if document[key] != stored_document[key]:
            raise RuntimeError("API reconstruction changed an immutable saved-document field: " + key)
    (output / "receipt-document.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "receipt-stored-document.json").write_text(json.dumps(stored_document, ensure_ascii=False, indent=2), encoding="utf-8")
    files = private / "files/local-scans"
    def proven_file(key):
        entry = workflow_proof["artifacts"][key]
        name = Path(entry["path"]).name
        if entry["path"] != "/uploads/" + name or not re.fullmatch(r"[a-z]+_[0-9a-f]{32}\.(png|jpg|pdf|xlsx)", name):
            raise RuntimeError("Workflow proof contains an invalid private file reference")
        path = files / name
        if not path.is_file() or sha(path) != entry["sha256"]:
            raise RuntimeError("Workflow proof file is absent or changed: " + key)
        return path
    clean = proven_file("corrected")
    persisted = proven_file("ocr")
    if clean.name != document["filename"] or "/uploads/" + persisted.name != document["image"]:
        raise RuntimeError("Workflow scan paths disagree with the saved SQLite document")
    if not clean.is_file() or not persisted.is_file() or sha(clean) != sha(persisted):
        raise RuntimeError("Receipt scan PNG and the stored OCR image must match exactly")
    chosen = {"receipt-clean.png": clean, "receipt-ocr-persisted.png": persisted}
    from pypdf import PdfReader
    from PIL import Image, ImageChops
    with Image.open(persisted) as source_image:
        reference = source_image.convert("RGB")
    pdf_file = proven_file("pdf")
    pdf = PdfReader(pdf_file)
    if len(pdf.pages) != 1 or "离线校对测试" not in (pdf.pages[0].extract_text() or "") or len(pdf.pages[0].images) != 1:
        raise RuntimeError("Proven receipt PDF lacks the correct searchable page and scan image")
    embedded = pdf.pages[0].images[0].image.convert("RGB")
    if embedded.size != reference.size or ImageChops.difference(embedded, reference).getbbox() is not None:
        raise RuntimeError("Proven receipt PDF changed the exact saved image pixels")
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    excel_file = proven_file("excel")
    with zipfile.ZipFile(excel_file) as book:
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows = sheet.findall("x:sheetData/x:row", namespace)[1:]
        texts = [row.findall("x:c", namespace)[1].find("x:is/x:t", namespace).text or "" for row in rows]
        if texts != [box["text"] for box in document["boxes"]]:
            raise RuntimeError("Proven receipt Excel text differs from the saved revision")
    chosen["receipt-searchable.pdf"] = pdf_file
    chosen["receipt-ocr-details.xlsx"] = excel_file
    artifacts = {}
    for name, original in chosen.items():
        destination = output / name
        shutil.copyfile(original, destination)
        artifacts[name] = {"device_path": "files/local-scans/" + original.name, "bytes": destination.stat().st_size, "sha256": sha(destination)}
    proof = output / "unicode-proof-text.pdf"
    proof.write_bytes(adb("exec-out", "run-as", PACKAGE, "cat", "cache/unicode-proof-text.pdf", binary=True))
    artifacts[proof.name] = {"device_path": "cache/unicode-proof-text.pdf", "bytes": proof.stat().st_size, "sha256": sha(proof)}
    screen_bytes = adb("exec-out", "run-as", PACKAGE, "cat", "cache/screen-proof.json", binary=True)
    screen_proof = json.loads(screen_bytes)
    screen_path = output / "screen-proof.json"
    screen_path.write_bytes(screen_bytes)
    screen_artifacts = {}
    for case in screen_proof["cases"]:
        for key, entry in case.get("artifacts", {}).items():
            path = entry["path"]
            name = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"screen-proof-[a-z0-9_-]+\.(png|jpg)", name) or path not in ("/data/user/0/" + PACKAGE + "/cache/" + name, "/data/data/" + PACKAGE + "/cache/" + name):
                raise RuntimeError("Screen proof references a file outside the app test cache")
            data = adb("exec-out", "run-as", PACKAGE, "cat", "cache/" + name, binary=True)
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise RuntimeError("Screen proof artifact changed: " + name)
            destination = output / name
            destination.write_bytes(data)
            screen_artifacts[name] = {"case": case["name"], "source_kind": case["source_kind"], "device_path": "cache/" + name, "bytes": len(data), "sha256": entry["sha256"]}
    reading_bytes = adb("exec-out", "run-as", PACKAGE, "cat", "cache/reading-order-proof.json", binary=True)
    reading_proof = json.loads(reading_bytes)
    if reading_proof["version_code"] != version_code:
        raise RuntimeError("Reading-order proof belongs to a different installed APK version")
    reading_path = output / "reading-order-proof.json"
    reading_path.write_bytes(reading_bytes)
    reading_artifacts = {}
    required_reading_files = {"source_image", "text_pdf", "searchable_pdf", "excel", "layout_json", "text", "rows_json"}
    if not required_reading_files.issubset(reading_proof["artifacts"]):
        raise RuntimeError("Reading-order proof must contain exact PDF, image, text and row artifacts")
    for key, entry in reading_proof["artifacts"].items():
        device_path, name = reading_order_reference(entry["path"])
        data = adb("exec-out", "run-as", PACKAGE, "cat", device_path, binary=True)
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError("Reading-order artifact changed: " + name)
        destination = output / name
        destination.write_bytes(data)
        reading_artifacts[key] = {"path": str(destination.relative_to(ROOT)), "device_path": device_path, "sha256": entry["sha256"], "bytes": len(data)}
    layout = json.loads((ROOT / reading_artifacts["layout_json"]["path"]).read_bytes())
    if layout["id"] != reading_proof["document_id"] or layout["revision"] != reading_proof["revision"] or layout["text_rows"] != reading_proof["text_rows"]:
        raise RuntimeError("Reading-order layout differs from its exact isolated-database proof")
    if (ROOT / reading_artifacts["text"]["path"]).read_bytes().decode("utf-8") != layout["text"] or json.loads((ROOT / reading_artifacts["rows_json"]["path"]).read_bytes()) != layout["text_rows"]:
        raise RuntimeError("Reading-order plain text or row file differs from the Android layout")
    xmls = []
    count = failures = errors = skipped = 0
    tests = []
    for original in (PROJECT / "app/build/outputs/androidTest-results/connected/debug").glob("TEST-*.xml"):
        destination = output / original.name
        shutil.copyfile(original, destination)
        suite = ET.parse(destination).getroot()
        count += int(suite.get("tests", 0)); failures += int(suite.get("failures", 0)); errors += int(suite.get("errors", 0)); skipped += int(suite.get("skipped", 0))
        tests.extend({"class": t.get("classname"), "method": t.get("name"), "seconds": t.get("time")} for t in suite.findall("testcase"))
        xmls.append({"path": str(destination.relative_to(ROOT)), "sha256": sha(destination)})
    if not count or failures or errors or skipped:
        raise RuntimeError("Final connected-test XML must show a nonempty fully passing suite")
    if not any(t["class"] == PACKAGE + ".BookOcrRegressionAndroidTest" and t["method"] == "actualOldDevBookRecoversOpeningMiddleEndingWithManualGroundTruth" for t in tests):
        raise RuntimeError("Current connected-test XML must include the actual old DEV book OCR regression")
    book_bytes = adb("exec-out", "run-as", PACKAGE, "cat", "cache/book-ocr-proof.json", binary=True)
    book_proof = json.loads(book_bytes)
    book_references = book_ocr_references(book_proof, version_code)
    book_path = output / "book-ocr-proof.json"
    book_path.write_bytes(book_bytes)
    book_artifacts = {}
    for key, (device_path, name) in book_references.items():
        entry = book_proof["artifacts"][key]
        data = adb("exec-out", "run-as", PACKAGE, "cat", device_path, binary=True)
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise RuntimeError("Book OCR artifact changed: " + name)
        destination = output / name
        destination.write_bytes(data)
        book_artifacts[key] = {"path": str(destination.relative_to(ROOT)), "device_path": device_path, "sha256": entry["sha256"], "bytes": len(data)}
    unit_count = unit_failures = 0
    unit_xmls = []
    for original in (PROJECT / "app/build/test-results/testDebugUnitTest").glob("TEST-*.xml"):
        destination = output / ("unit-" + original.name)
        shutil.copyfile(original, destination)
        suite = ET.parse(destination).getroot()
        unit_count += int(suite.get("tests", 0))
        unit_failures += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        unit_xmls.append({"path": str(destination.relative_to(ROOT)), "sha256": sha(destination)})
    if not unit_count or unit_failures:
        raise RuntimeError("Final Gradle unit-test XML must show a nonempty passing suite")
    snapshot_file = ROOT / "build/android-local-source-snapshot.json"
    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
    shutil.copyfile(snapshot_file, output / "source-snapshot.json")
    apks = {str(p.relative_to(ROOT)): {"sha256": sha(p), "bytes": p.stat().st_size} for p in [PROJECT / "app/build/outputs/apk/debug/app-debug.apk", PROJECT / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"]}
    report = {"device": device, "physical_phone_tested": False, "internet_permission": False, "test_variant": "debug", "tests": count, "failures": failures, "errors": errors, "skipped": skipped,
              "test_cases": tests, "test_xml": xmls, "source_sha256": snapshot["sha256"], "test_apks": apks,
              "artifacts": artifacts, "private_test_data_archive": {"path": str(archive.relative_to(ROOT)), "sha256": sha(archive)},
              "version_code": version_code, "version_name": workflow_proof["version_name"],
              "workflow_proof": {"path": str(proof_path.relative_to(ROOT)), "sha256": sha(proof_path)},
              "screen_proof": {"path": str(screen_path.relative_to(ROOT)), "sha256": sha(screen_path), "cases": len(screen_proof["cases"]), "photo_is_camera_original": screen_proof["photo_is_camera_original"], "artifacts": screen_artifacts},
              "reading_order_proof": {"path": str(reading_path.relative_to(ROOT)), "sha256": sha(reading_path), "source_kind": reading_proof["source_kind"], "database_scope": reading_proof["database_scope"], "legacy_read_wrote_db": reading_proof["legacy_read_wrote_db"], "artifacts": reading_artifacts},
              "book_ocr_proof": {"path": str(book_path.relative_to(ROOT)), "sha256": sha(book_path), "source_image_id": book_proof["source_image_id"], "source_split": book_proof["source_split"], "holdout_read": False, "CER": book_proof["CER"], "normalized_gt_chars": book_proof["normalized_gt_chars"], "boxes": book_proof["boxes"], "artifacts": book_artifacts, "limits": "Old DEV regression only; this is not new heldout accuracy."},
              "unit_tests": {"tests": unit_count, "failures": unit_failures, "xml": unit_xmls},
              "artifact_selection": {"document_id": document["id"], "revision": document["revision"], "rule": "Exact successful Android workflow-proof.json paths/hashes and SQLite snapshot; PDF image pixels and Excel row text verified again."},
              "device_database_preserved": True, "test_photos_are_test_apk_assets_only": True,
              "limits": ["System camera result/recreation callbacks are simulated in instrumentation; physical iQOO camera and album flows remain unverified.", "Android x86_64 CPU runtime is exercised; arm64-v8a is packaged and structurally verified, not executed on a physical phone."]}
    report_path = ROOT / "reports/android_local_device_verification.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "tests": count, "source_sha256": snapshot["sha256"], "artifacts": artifacts}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
