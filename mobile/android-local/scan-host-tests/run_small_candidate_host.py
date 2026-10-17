"""Synthetic auto-crop safety regression; no OCR or evaluation-set inference."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--red-source", type=Path, help="Optional preserved pre-fix ScanProcessor.java; verify it fails the new regression first")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    project = root / "mobile/android-local"
    work = root / "build/android-local"
    java = work / "host-jdk/bin"
    classpath = ";".join(str(work / "host-deps" / name) for name in
                         ("opencv-4.9.0-0.jar", "json-20240303.jar"))
    classes = work / "small-candidate-host-classes"
    classes.mkdir(parents=True, exist_ok=True)
    source = project / "app/src/main/java/com/snapscan/offline/ScanProcessor.java"
    test = project / "scan-host-tests/com/snapscan/offline/SmallCandidateRegressionTest.java"
    red_evidence = None
    if args.red_source:
        red_classes = work / "small-candidate-red-classes"
        red_classes.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
                        "-d", str(red_classes), str(args.red_source), str(test)], cwd=root, check=True)
        result = subprocess.run([str(java / "java.exe"), "-Xmx1500m", "-cp", str(red_classes) + ";" + classpath,
                                 "com.snapscan.offline.SmallCandidateRegressionTest", str(root)], cwd=root,
                                text=True, encoding="utf-8", capture_output=True)
        if result.returncode == 0 or "internal candidate must retain full page" not in result.stderr:
            raise AssertionError("Preserved pre-fix source did not fail for the expected small-candidate assertion: " + result.stderr)
        red_evidence = root / "output/diagnostics/scan-small-candidate-regression-20261016/red-complete-page.json"
        red_evidence.parent.mkdir(parents=True, exist_ok=True)
        red_evidence.write_text(json.dumps({"scope": "Synthetic complete page with header/body/footer and an internal high-confidence box; no OCR",
                                           "original_source_sha256": hashlib.sha256(args.red_source.read_bytes()).hexdigest(),
                                           "test_sha256": hashlib.sha256(test.read_bytes()).hexdigest(),
                                           "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
        print("Verified original-source RED for high-confidence internal crop")
    subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", classpath,
                    "-d", str(classes), str(source), str(test)], cwd=root, check=True)
    subprocess.run([str(java / "java.exe"), "-Xmx1500m", "-cp", str(classes) + ";" + classpath,
                    "com.snapscan.offline.SmallCandidateRegressionTest", str(root)], cwd=root, check=True)
    target = root / "reports/android_small_candidate_host.json"
    report = json.loads(target.read_text(encoding="utf-8"))
    report["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    report["frozen_ocr_sha256"] = {name: hashlib.sha256((source.parent / name).read_bytes()).hexdigest()
                                    for name in ("OcrEngine.java", "OcrMath.java", "TextLayout.java")}
    if red_evidence:
        report["red_evidence"] = red_evidence.relative_to(root).as_posix()
    report["reproduction_command"] = "C:/pycharm/python.exe -B -X utf8 mobile/android-local/scan-host-tests/run_small_candidate_host.py"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
