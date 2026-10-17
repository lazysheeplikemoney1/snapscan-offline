"""Read the completed frozen-input Android benchmark, with run identity checks."""
import json
import argparse
import re
import shutil
import subprocess
from pathlib import Path
import run_independent_scan as evaluation
from run_independent_scan import ROOT, OUT, LOCK, sha, read, write, verify_lock, summarize

ADB=ROOT/'build/android-local-sdk/platform-tools/adb.exe'
PACKAGE='com.snapscan.offline'

def adb(*args):
    return subprocess.run([str(ADB),'-s','emulator-5580',*args],check=True,capture_output=True).stdout

def main():
    global OUT
    parser=argparse.ArgumentParser()
    parser.add_argument('--scan-regression',action='store_true',help='Record post-inspection scan-guard regression separately; never overwrite the independent result')
    args=parser.parse_args()
    if args.scan_regression:
        original=OUT
        OUT=ROOT/'output/diagnostics/independent-scan-20261016-guard-regression'
        assert not OUT.exists(),'Do not replace a previous regression capture'
        OUT.mkdir()
        shutil.copy2(original/'inputs.json',OUT/'inputs.json')
        runtime=read(original/'runtime.json')
        runtime['evaluation_role']='Regression after the small-candidate guard used failure evidence from this dataset; not an unseen scan evaluation'
        runtime['original_independent_report_sha256']=sha(original/'report.json')
        runtime['original_independent_report']=str((original/'report.json').relative_to(ROOT))
        source=ROOT/'mobile/android-local/app/src/main/java/com/snapscan/offline'
        original_sources=runtime['candidate_source_sha256']
        current_sources={name:sha(source/name) for name in original_sources}
        for name in ('OcrEngine.java','OcrMath.java','TextLayout.java'):
            assert current_sources[name]==original_sources[name], 'OCR must remain frozen during scan-guard regression'
        runtime['candidate_source_sha256']=current_sources
        runtime['collection_source_sha256']=sha(Path(__file__))
        runtime['summary_source_sha256']=sha(Path(evaluation.__file__))
        runtime['runtime']='Android 15 API35 x86_64 emulator, final offline package source; not physical phone'
        write(OUT/'runtime.json',runtime)
        (OUT/'candidate-source').mkdir()
        for name in current_sources:
            shutil.copy2(source/name,OUT/'candidate-source'/name)
        evaluation.OUT=OUT
    samples=verify_lock()
    installed=adb('shell','dumpsys','package',PACKAGE).decode()
    assert 'android.permission.INTERNET' not in installed
    version=int(re.search(r'versionCode=(\d+)',installed).group(1))
    assert version==203
    device=read(ROOT/'reports/android_local_device_verification.json')
    snapshot=read(ROOT/'build/android-local-source-snapshot.json')
    assert device['version_code']==version and device['source_sha256']==snapshot['sha256']
    assert device['failures']==device['errors']==device['skipped']==0
    assert any(row['class'].endswith('.IndependentScanAndroidTest') for row in device['test_cases'])
    connectivity={key:adb('shell','settings','get','global',key).decode().strip() for key in ('wifi_on','mobile_data')}
    assert set(connectivity.values())=={'0'}
    prefix='cache/independent-scan-20261016/'
    complete=json.loads(adb('exec-out','run-as',PACKAGE,'cat',prefix+'complete.json'))
    assert complete['version_code']==version and complete['images']==len(samples) and complete['lock_sha256']==sha(LOCK)
    records=[]
    for sample in samples:
        sid=sample['image_id']; assert re.fullmatch('[A-Za-z0-9_.-]+',sid)
        data=adb('exec-out','run-as',PACKAGE,'cat',prefix+sid+'.json'); row=json.loads(data)
        assert row['run_id']==complete['run_id'] and row['image_id']==sid and row['version_code']==version
        assert row['lock_sha256']==sha(LOCK) and row['input_sha256']==sample['sha256']
        records.append((sid,data))
    target=OUT/'android'; assert not target.exists(),'Do not replace a prior captured run'
    target.mkdir()
    for sid,data in records: (target/(sid+'.json')).write_bytes(data)
    write(target/'complete.json',{**complete,'connectivity':connectivity,'physical_phone':False,
        'source_sha256':snapshot['sha256'],'device_report_sha256':sha(ROOT/'reports/android_local_device_verification.json'),
        'test_xml':device['test_xml']})
    summarize(variants=['android'] if args.scan_regression else None)

if __name__=='__main__': main()
