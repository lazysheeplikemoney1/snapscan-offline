"""Run explicitly approved OLD DEV inputs, with frozen metric semantics.

The baseline differs from current Java by exactly one call argument: scoring
the min-area rectangle rather than the original contour. No old prediction or
holdout output is reused. All evidence goes to a fresh directory.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation/tools"))
from metrics import normalize_text, edit_ops


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-train-inputs", action="store_true")
    parser.add_argument("--current-only", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    source_dir = ROOT / "mobile/android-local/app/src/main/java/com/snapscan/offline"
    src = source_dir / "OcrEngine.java"
    java = Path("E:/HBuilderX/HBuilderX/plugins/amazon-corretto/bin")
    # Existing isolated host-JDK copy resolves the bundled JDK's older MSVC CRT
    # incompatibility with desktop ORT. Never replace system/original JDK files.
    runner = ROOT / "build/android-local/host-jdk/bin/java.exe"
    assert runner.is_file()
    deps = ROOT / "build/android-local/host-deps"
    cp = ";".join(str(p) for p in [deps/"json-20240303.jar", deps/"opencv-4.9.0-0.jar", deps/"onnxruntime-1.23.2.jar", ROOT/"build/android-local-sdk/platforms/android-35/android.jar"])
    audit = ROOT / "output/diagnostics/book-train-audit/inputs.json"
    inputs = json.loads(audit.read_text(encoding="utf-8"))["inputs"]
    if not args.all_train_inputs:
        by_id = {i["sample_id"]: i for i in inputs}
        inputs = [by_id.get(image_id, {"sample_id": image_id, "source": "data/real/dev/dev/111/"+image_id,
                  "input": str(ROOT / "data/real/dev/dev/111" / image_id),
                  "source_sha256": hashlib.sha256((ROOT / "data/real/dev/dev/111" / image_id).read_bytes()).hexdigest()})
                  for image_id in ("real-010.jpg", "real-023.jpg", "real-022.jpg")]
    assert len(inputs) == (27 if args.all_train_inputs else 3)
    assert all(i["sample_id"].startswith("real-") and "/111/" in str(i["source"]).replace("\\", "/") for i in inputs)
    manifest = output / "inputs.json"
    manifest.write_text(json.dumps({"source_split": "dev", "holdout_read": False, "inputs": inputs}, ensure_ascii=False, indent=2), encoding="utf-8")
    annotations = [json.loads(line) for line in (ROOT / "data/real/annotations_local_111.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    groundtruth = {item["image_id"]: item for item in annotations}
    original_source = src.read_text(encoding="utf-8")
    variants = ["contour"] if args.all_train_inputs or args.current_only else ["rectangle_baseline", "contour"]
    reports = {}
    for variant in variants:
        work = output / variant
        work.mkdir()
        target = work / "source/com/snapscan/offline/OcrEngine.java"
        target.parent.mkdir(parents=True)
        code = original_source
        if variant == "rectangle_baseline":
            old = "polygonScore(probability,contours.get(index).toArray())"
            assert code.count(old) == 1
            code = code.replace(old, "polygonScore(probability,corners)")
        target.write_text(code, encoding="utf-8")
        classes = work / "classes"
        classes.mkdir()
        sources = [target, source_dir / "OcrMath.java", source_dir / "TextLayout.java", ROOT / "mobile/android-local/host-tests/com/snapscan/offline/OcrDetectorPageRun.java"]
        subprocess.run([str(java/"javac.exe"), "-encoding", "UTF-8", "-cp", cp, "-d", str(classes)] + list(map(str, sources)), cwd=ROOT, check=True)
        predictions = work / "predictions"
        subprocess.run([str(runner), "-Dfile.encoding=UTF-8", "-Xmx1800m", "-cp", str(classes)+";"+cp,
                        "com.snapscan.offline.OcrDetectorPageRun", str(ROOT / "mobile/android-local/app/src/main/assets/models"), str(manifest), str(predictions)], cwd=ROOT, check=True)
        total = dict(S=0, D=0, I=0, N=0)
        rows = []
        for item in inputs:
            image_id = item["sample_id"]
            gt = groundtruth[image_id].get("gt_text")
            complete = gt is not None and "[UNK]" not in gt
            prediction = json.loads((predictions / (image_id+".json")).read_text(encoding="utf-8"))
            predicted = normalize_text(prediction["text"])
            normalized_gt = normalize_text(gt)
            s,d,i = edit_ops(predicted, normalized_gt)
            row = {"image_id": image_id, "gt_complete": complete, "boxes": len(prediction["boxes"]), "pred_chars": len(predicted), "N": len(normalized_gt), "S": s, "D": d, "I": i,
                   "CER": (s+d+i)/len(normalized_gt) if normalized_gt else None}
            rows.append(row)
            if complete:
                for key, value in zip(("S", "D", "I", "N"), (s,d,i,len(normalized_gt))): total[key] += value
        total["CER"] = (total["S"]+total["D"]+total["I"])/total["N"] if total["N"] else None
        reports[variant] = {"source_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(), "pages": rows,
                            "complete_gt_pages": sum(r["gt_complete"] for r in rows), "complete_gt_corpus": total}
        print(variant, total, flush=True)
    report = {"holdout_read": False, "source_split": "dev", "purpose": "old-dev detector-score diagnostic, not new-generalization accuracy",
              "metric": "unchanged evaluation/tools/metrics.py normalize_text + edit_ops; incomplete GT excluded from aggregate",
              "metric_sha256": hashlib.sha256((ROOT / "evaluation/tools/metrics.py").read_bytes()).hexdigest(),
              "inputs_sha256": hashlib.sha256(audit.read_bytes()).hexdigest(), "input_count": len(inputs), "variants": reports}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Report", output / "report.json", flush=True)


if __name__ == "__main__":
    main()
