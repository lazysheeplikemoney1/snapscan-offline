"""Reproduce ScanProcessor with real host JNI and compile official Android API.

No downloads or device execution. All dependencies must already be in build/.
The test consumes only local user-feedback regression and selected DEV images.
"""
from pathlib import Path
import hashlib
import json
import subprocess


def main():
    root = Path(__file__).resolve().parents[3]
    project = root / "mobile/android-local"
    work = root / "build/android-local"
    java = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto/bin")
    jars = [work / "host-deps/opencv-4.9.0-0.jar", work / "host-deps/json-20240303.jar"]
    source = project / "app/src/main/java/com/snapscan/offline/ScanProcessor.java"
    test = project / "scan-host-tests/com/snapscan/offline/ScanProcessorTest.java"
    classes = work / "scan-host-classes"
    android_classes = work / "scan-android-classes"
    classes.mkdir(parents=True, exist_ok=True)
    android_classes.mkdir(parents=True, exist_ok=True)
    classpath = ";".join(map(str, jars))
    subprocess.run([str(java / "javac.exe"), "-J-Duser.language=en", "-encoding", "UTF-8", "-cp", classpath,
                    "-d", str(classes), str(source), str(test)], check=True, cwd=root)
    subprocess.run([str(java / "java.exe"), "-Duser.language=en", "-Xmx1500m", "-cp", str(classes) + ";" + classpath,
                    "com.snapscan.offline.ScanProcessorTest", str(root)], check=True, cwd=root)
    android = [root / "build/android-local-deps/opencv-4.12.0/classes.jar",
               root / "build/android-local-sdk/platforms/android-35/android.jar"]
    subprocess.run([str(java / "javac.exe"), "-J-Duser.language=en", "-encoding", "UTF-8", "-source", "8", "-target", "8",
                    "-cp", ";".join(map(str, android)), "-d", str(android_classes), str(source)], check=True, cwd=root)
    target = root / "reports/android_scan_host.json"
    report = json.loads(target.read_text(encoding="utf-8"))
    report.pop("private_text_saved", None)
    report.update(android_4_12_compile_passed=True, production_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  transcribed_private_text_in_report=False, local_fixture_contains_private_image=True,
                  reproduction_command="C:/pycharm/python.exe -B -X utf8 mobile/android-local/scan-host-tests/run_scan_host.py",
                  visual_review={"feedback_clean": "Visible text and connected lower sheet retained; wood removed, thin paper edges may remain.",
                                 "dev037_clean": "Whole invoice outer sheet and red stamps retained; original screen moire remains."})
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
