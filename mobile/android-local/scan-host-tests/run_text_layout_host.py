"""Run no-model Java layout contract tests from shared cross-language fixtures."""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    root = Path(__file__).resolve().parents[3]
    project = root / "mobile/android-local"
    work = root / "build/android-local"
    java = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto/bin")
    jar = work / "host-deps/json-20240303.jar"
    classes = work / "layout-host-classes"
    classes.mkdir(parents=True, exist_ok=True)
    source = project / "app/src/main/java/com/snapscan/offline/TextLayout.java"
    test = project / "scan-host-tests/com/snapscan/offline/TextLayoutContractTest.java"
    subprocess.run([str(java / "javac.exe"), "-encoding", "UTF-8", "-cp", str(jar),
                    "-d", str(classes), str(source), str(test)], cwd=root, check=True)
    subprocess.run([str(java / "java.exe"), "-Dfile.encoding=UTF-8", "-cp", str(classes) + ";" + str(jar),
                    "com.snapscan.offline.TextLayoutContractTest", str(root)], cwd=root, check=True)
    report_path = root / "reports/android_text_layout_host.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.update(runtime="actual Java geometry and org.json on host; Android pending",
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  fixture_sha256=hashlib.sha256((root / "evaluation/fixtures/text_layout_cases.json").read_bytes()).hexdigest(),
                  test_set_read=False,
                  reproduction_command="C:/pycharm/python.exe -B -X utf8 mobile/android-local/scan-host-tests/run_text_layout_host.py")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
