"""Start an isolated API35 AOSP-ATD emulator, preserving all pre-existing AVDs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from build_local import ROOT, SDK, environment, ascii_workspace

NAME = "SnapScanLocal35"
AVDS = ROOT / "build/android-local-avd"
AVD = AVDS / (NAME + ".avd")
SERIAL = "emulator-5580"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-boot", action="store_true")
    parser.add_argument("--software", action="store_true", help="TCG fallback; never changes host virtualization settings")
    parser.add_argument("--wipe", action="store_true", help="Reset only this script's isolated, explicitly named AVD")
    args = parser.parse_args()
    _, alias = ascii_workspace()
    sdk_alias = alias / "build/android-local-sdk"
    avds_alias = alias / "build/android-local-avd"
    emulator = sdk_alias / "emulator/emulator.exe"
    if not emulator.is_file():
        raise RuntimeError("Run bootstrap_android.py --emulator first")
    env = environment()
    env.update(ANDROID_AVD_HOME=str(avds_alias), ANDROID_USER_HOME=str(alias / "build/android-local-user"), ANDROID_HOME=str(sdk_alias), ANDROID_SDK_ROOT=str(sdk_alias))
    AVDS.mkdir(parents=True, exist_ok=True); AVD.mkdir(exist_ok=True)
    (AVDS / (NAME + ".ini")).write_text(f"avd.ini.encoding=UTF-8\npath={avds_alias / (NAME + '.avd')}\ntarget=android-35\n", encoding="utf-8")
    system = sdk_alias / "system-images/android-35/aosp_atd/x86_64"
    config = {"AvdId":NAME,"avd.ini.displayname":"SnapScan isolated API35","abi.type":"x86_64","hw.cpu.arch":"x86_64",
              "hw.cpu.ncore":"4","hw.ramSize":"4096","hw.lcd.width":"1080","hw.lcd.height":"1920","hw.lcd.density":"420",
              "hw.keyboard":"yes","hw.gpu.enabled":"yes","hw.gpu.mode":"swiftshader","disk.dataPartition.size":"3G",
              "image.sysdir.1":str(system)+os.sep,"tag.id":"aosp_atd","tag.display":"AOSP ATD","target":"android-35",
              "PlayStore.enabled":"false","hw.camera.back":"none","hw.camera.front":"none","showDeviceFrame":"no"}
    (AVD / "config.ini").write_text("\n".join(f"{key}={value}" for key,value in config.items())+"\n", encoding="utf-8")
    print(subprocess.run([str(emulator), "-accel-check"], env=env, capture_output=True, text=True).stdout, flush=True)
    log = ROOT / "build/android-local-emulator.log"
    with log.open("ab") as output:
        command = [str(emulator), "-avd", NAME, "-ports", "5580,5581", "-no-window", "-no-audio", "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader", "-accel", "off" if args.software else "on", "-no-metrics", "-show-kernel"]
        if args.wipe:
            assert AVD.resolve().is_relative_to((ROOT / "build/android-local-avd").resolve())
            command.append("-wipe-data")
        process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    (ROOT / "build/android-local-emulator-process.json").write_text(json.dumps({"pid":process.pid,"serial":SERIAL,"avd":str(AVD),"log":str(log)}), encoding="utf-8")
    print(f"Started independent {SERIAL}, pid={process.pid}, log={log}", flush=True)
    if not args.wait_boot:
        return
    adb = SDK / "platform-tools/adb.exe"
    start = time.monotonic()
    while time.monotonic()-start < 600:
        if process.poll() is not None:
            raise RuntimeError("Emulator exited; inspect its isolated log")
        result = subprocess.run([str(adb), "-s", SERIAL, "shell", "getprop", "sys.boot_completed"], env=env, capture_output=True, text=True)
        if result.stdout.strip()=="1":
            print(f"Android35 boot complete in {time.monotonic()-start:.1f}s", flush=True); return
        print(f"Waiting for isolated Android35 boot ({int(time.monotonic()-start)}s)", flush=True); time.sleep(10)
    raise RuntimeError("Isolated emulator boot timed out; no device validation is claimed")

if __name__ == "__main__":
    main()
