"""Run screen-recapture regressions using installed host JNI; no downloads."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    root = Path(__file__).resolve().parents[3]
    project = root / "mobile/android-local"
    work = root / "build/android-local"
    java = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto/bin")
    classpath = ";".join(str(work / "host-deps" / name) for name in
                         ("opencv-4.9.0-0.jar", "json-20240303.jar"))
    classes = work / "blue-scan-host-classes"
    classes.mkdir(parents=True, exist_ok=True)
    source = project / "app/src/main/java/com/snapscan/offline/ScanProcessor.java"
    test = project / "scan-host-tests/com/snapscan/offline/BlueScanRegressionTest.java"
    subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
                    "-d", str(classes), str(source), str(test)], cwd=root, check=True)
    subprocess.run([str(java / "java.exe"), "-Xmx1500m", "-cp", str(classes) + ";" + classpath,
                    "com.snapscan.offline.BlueScanRegressionTest", str(root)], cwd=root, check=True)
    report_path = root / "reports/android_blue_scan_host.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  fixture_manifest_sha256=hashlib.sha256((root / "reports/android_screen_fixture_manifest.json").read_bytes()).hexdigest(),
                  test_set_read=False, camera_original_available=False,
                  reproduction_command="C:/pycharm/python.exe -B -X utf8 mobile/android-local/scan-host-tests/run_screen_host.py")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
