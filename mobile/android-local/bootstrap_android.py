"""Fetch verified official Android tools into this project's isolated build tree."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "build/android-local-sdk"
CACHE = ROOT / "build/android-local-downloads"
DEPS = ROOT / "build/android-local-deps"
PACKAGES = [
    ("platform35", "https://dl.google.com/android/repository/platform-35_r02.zip", "0bb560a90a7a2cbd0dd8348224d518b638fe7949", SDK / "platforms/android-35"),
    ("buildtools35", "https://dl.google.com/android/repository/build-tools_r35_windows.zip", "af059bb67cf7786f45ee0db85e2d24985df1b4b6", SDK / "build-tools/35.0.0"),
    ("platformtools", "https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip", "e03e78b1d80b396f1c3358e31251cb31740e1110", SDK / "platform-tools"),
]
EMULATOR = [
    ("emulator", "https://dl.google.com/android/repository/emulator-windows_x64-16428233.zip", "488ed747e82de7e9bb5247becd1ac043c7e5e85d", SDK / "emulator"),
    ("atd35", "https://dl.google.com/android/repository/sys-img/aosp_atd/x86_64-35_r01.zip", "e150a7d402da4248f2901989dc856a9caf42a35d", SDK / "system-images/android-35/aosp_atd/x86_64"),
]
ARTIFACTS = {
    "onnxruntime-android-1.23.2": "https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.23.2/onnxruntime-android-1.23.2.aar",
    "opencv-4.12.0": "https://repo.maven.apache.org/maven2/org/opencv/opencv/4.12.0/opencv-4.12.0.aar",
}

def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha1").hexdigest()

def download(name, url, expected):
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / url.rsplit("/", 1)[1]
    if target.is_file() and digest(target) == expected:
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    started = last = time.monotonic()
    for attempt in range(5):
        count = temporary.stat().st_size if temporary.exists() else 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={count}-"} if count else {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                resume = response.status == 206 and count > 0
                if not resume: count = 0
                total = count + int(response.headers.get("Content-Length", "0"))
                with temporary.open("ab" if resume else "wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk); count += len(chunk)
                        if time.monotonic() - last > 15:
                            print(f"{name}: {count // 1048576}/{total // 1048576} MiB", flush=True); last = time.monotonic()
            break
        except (OSError, TimeoutError) as error:
            if attempt == 4: raise
            print(f"{name}: interrupted ({type(error).__name__}), resuming verified download", flush=True)
            time.sleep(min(4, attempt + 1))
    if digest(temporary) != expected:
        raise RuntimeError(f"SHA1 mismatch for official artifact {name}")
    temporary.replace(target)
    print(f"{name}: verified {count // 1048576} MiB in {time.monotonic()-started:.1f}s", flush=True)
    return target

def install(package):
    name, url, expected, destination = package
    stamp = destination / ".snapscan-official-sha1"
    if stamp.is_file() and stamp.read_text().strip() == expected:
        return
    archive = download(name, url, expected)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        entries = [item for item in source.infolist() if not item.is_dir()]
        roots = {PurePosixPath(item.filename).parts[0] for item in entries}
        strip = len(roots) == 1
        for item in entries:
            parts = PurePosixPath(item.filename).parts[1:] if strip else PurePosixPath(item.filename).parts
            if not parts or any(part in ("..", ".") for part in parts):
                raise RuntimeError("Unsafe SDK archive member")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("SDK archive escaped its isolated destination")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    stamp.write_text(expected, encoding="ascii")
    print(f"{name}: installed to {destination}", flush=True)

def dependency(item):
    name, url = item
    with urllib.request.urlopen(url + ".sha1", timeout=30) as response:
        expected = response.read().decode("ascii").strip().split()[0]
    archive = download(name, url, expected)
    DEPS.mkdir(parents=True, exist_ok=True)
    target = DEPS / (name + ".aar")
    if not target.exists() or digest(target) != expected:
        shutil.copyfile(archive, target)
    classes = DEPS / name / "classes.jar"
    classes.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target) as source:
        classes.write_bytes(source.read("classes.jar"))
    print(f"{name}: AAR and classes.jar ready", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator", action="store_true")
    args = parser.parse_args()
    packages = PACKAGES + (EMULATOR if args.emulator else [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        tasks = [executor.submit(install, package) for package in packages]
        tasks += [executor.submit(dependency, item) for item in ARTIFACTS.items()]
        for task in tasks:
            task.result()

if __name__ == "__main__":
    main()
