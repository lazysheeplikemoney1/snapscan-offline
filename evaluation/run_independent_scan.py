"""Freeze new annotations, run unchanged native Java scan/OCR, and score by scope.

Run --freeze only after human annotation is complete; run --evaluate only after
the candidate is frozen. Preserve the first independent result. If its failures
inform a later fix, capture the repeat separately and label it regression only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections import defaultdict

import cv2
import numpy as np
from independent_scan_metrics import text_metric, evaluate_regions, clean_to_source

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/independent_20261016'
OUT=ROOT/'output/diagnostics/independent-scan-20261016'
LOCK=DATA/'evaluation_lock.json'

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def records():
    samples=[]
    for category in ('books','slides','receipts'):
        for line in (DATA/(category+'.jsonl')).read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            sid=row.get('image_id',row.get('sample_id'))
            path=Path(row['image_path'])
            if not path.is_absolute():
                path=(ROOT/path) if (ROOT/path).exists() else DATA/path
            regions=row.get('regions',row.get('scoring_regions',[]))
            regions=[{**r,'region_id':r.get('region_id',r.get('label',str(i)))} for i,r in enumerate(regions)]
            samples.append({**row,'image_id':sid,'input':str(path.resolve()),'category':category,'regions':regions})
    return samples

def freeze():
    assert not LOCK.exists(), 'Existing evaluation lock must not be replaced'
    rows=records(); assert len({r['image_id'] for r in rows})==len(rows)
    old=[json.loads(l) for l in (ROOT/'data/real/metadata_template.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    old_hashes={r.get('sha256') for r in old}
    old_urls={r.get('source_url') for r in old if r.get('source_url')}
    entries=[]
    for row in rows:
        path=Path(row['input']); digest=sha(path)
        assert digest==row['sha256'],row['image_id']
        assert digest not in old_hashes,'Exact duplicate of old corpus: '+row['image_id']
        url=row.get('source_url',row.get('source',{}).get('image_url'))
        assert url not in old_urls,'Duplicate source URL: '+row['image_id']
        image=cv2.imdecode(np.fromfile(path,np.uint8),cv2.IMREAD_COLOR)
        assert image is not None and max(image.shape[:2])<=4096
        if not row.get('full_page_gt',False):
            assert row['regions'] and all(r['text'] and '[UNK]' not in r['text'] for r in row['regions'])
            assert all(text_metric('', r['text'])['N'] > 0 for r in row['regions'])
        else:
            assert '[UNK]' not in row['gt_text'] and text_metric('',row['gt_text'])['N'] > 0
        entries.append({'image_id':row['image_id'],'sha256':digest,'decoded_bgr_sha256':hashlib.sha256(image.tobytes()).hexdigest(),
                        'width':image.shape[1],'height':image.shape[0]})
    write(OUT/'inputs.json',rows)
    write(LOCK,{'purpose':'Independent evaluation only, no local fine-tuning or parameter selection',
                'runtime_inputs_sha256':sha(OUT/'inputs.json'),
                'pretrained_training_overlap':'unknown; this lock excludes only this project training and tuning data',
                'annotation_files':{c+'.jsonl':sha(DATA/(c+'.jsonl')) for c in ('books','slides','receipts')},
                'samples':entries,'old_corpus_metadata_sha256':sha(ROOT/'data/real/metadata_template.jsonl'),
                'duplicate_checks':'Exact file SHA256 and exact source URL against existing metadata; no old locked-test image/GT OCR performed',
                'images':len(rows)})
    print('FROZEN',len(rows),'images; lock SHA256',sha(LOCK),flush=True)

def verify_lock():
    lock=read(LOCK)
    assert sha(OUT/'inputs.json')==lock['runtime_inputs_sha256'],'Runtime input list changed after freezing'
    for name,digest in lock['annotation_files'].items(): assert sha(DATA/name)==digest,name
    rows=records(); byid={r['image_id']:r for r in rows}
    assert read(OUT/'inputs.json')==rows,'Runtime input list differs from verified annotations'
    for entry in lock['samples']: assert sha(byid[entry['image_id']]['input'])==entry['sha256']
    return rows

def evaluate():
    rows=verify_lock(); runtime=read(ROOT/'output/diagnostics/book-ocr/runtime.json')
    deps=runtime['classpath'].split(';',1)[1]
    source=ROOT/'mobile/android-local/app/src/main/java/com/snapscan/offline'
    names=('OcrEngine.java','OcrMath.java','TextLayout.java','ScanProcessor.java')
    candidate_sha={n:sha(source/n) for n in names}
    evidence={'candidate_source_sha256':candidate_sha,'lock_sha256':sha(LOCK),'runtime':'Windows host JVM, ORT1.23.2/OpenCV4.9; not physical phone',
              'baseline_source_sha256':{n:sha(OUT/'baseline-source'/n) for n in names},
              'model_manifest':read(Path(runtime['models'])/'model_manifest.json'),
              'dependencies_sha256':{Path(p).name:sha(p) for p in deps.split(';')},
              'metric_source_sha256':sha(ROOT/'evaluation/tools/metrics.py'),
              'regional_metric_source_sha256':sha(ROOT/'evaluation/independent_scan_metrics.py')}
    write(OUT/'runtime.json',evidence)
    for variant,src in [('baseline',OUT/'baseline-source'),('candidate',source)]:
        result_dir=OUT/variant
        assert not result_dir.exists(),'Do not rerun a viewed holdout silently: '+variant
        classes=OUT/(variant+'-classes'); classes.mkdir(exist_ok=True)
        stubs=ROOT/'build/android-local/host-stubs'
        files=[stubs/'android/content/Context.java',stubs/'android/content/res/AssetManager.java',*[src/n for n in names],ROOT/'evaluation/java/ScanBenchmarkHost.java']
        subprocess.run([str(ROOT/'build/android-local/host-jdk/bin/javac.exe'),'-encoding','UTF-8','-cp',deps,'-d',str(classes),*map(str,files)],check=True)
        subprocess.run([runtime['java'],'-Xmx1800m','-cp',str(classes)+';'+deps,'com.snapscan.offline.ScanBenchmarkHost',runtime['models'],str(OUT/'inputs.json'),str(result_dir)],check=True)
    assert candidate_sha=={n:sha(source/n) for n in names},'Production source changed during independent evaluation'
    summarize()

def aggregate(rows):
    total={k:sum(r[k] for r in rows) for k in ('N','P','S','D','I')}
    return {'images':len(rows),**total,'corpus_cer':(total['S']+total['D']+total['I'])/total['N'] if total['N'] else None,
            'exact_images_in_declared_scope':sum(r['exact'] for r in rows)}

def summarize(variants=None):
    samples=verify_lock(); details=[]; groups=defaultdict(list)
    if variants is None:
        variants=['baseline','candidate']
        if (OUT/'android').is_dir(): variants.append('android')
    for sample in samples:
        for variant in variants:
            record=read(OUT/variant/(sample['image_id']+'.json'))
            for mode in ('raw','clean'):
                output=record.get(mode,{})
                result=output.get('result',{'text':'','boxes':[]})
                language=sample.get('language','unknown')
                scopes=[]
                if sample.get('full_page_gt'):
                    scopes.append(('full_visible_page',text_metric(result['text'],sample['gt_text'])))
                if sample['regions']:
                    scopes.append(('annotated_regions',evaluate_regions(result,sample['regions'],clean_to_source(record['scan']) if mode=='clean' and 'scan' in record else None)))
                for scope,metric in scopes:
                    row={'image_id':sample['image_id'],'category':sample['category'],'language':language,'scope':scope,'variant':variant,'mode':mode,
                         'boxes':len(result.get('boxes',[])),'elapsed_ms':output.get('elapsed_ms'),'has_quad':record.get('scan',{}).get('has_quad'),
                         'error':output.get('error',record.get('scan_error') if mode=='clean' else None),**metric}
                    details.append(row); groups[(sample['category'],language,scope,variant,mode)].append(row)
    runtime=read(OUT/'runtime.json')
    evaluation_role=runtime.get('evaluation_role','Independent evaluation after OCR changes were frozen; no training or tuning on this set')
    report={'scope':'Full input pages are inferred; partial-GT cases are scored spatially within declared regions, without ROI reinference',
            'evaluation_role':evaluation_role,
            'normalization':'Frozen evaluation/tools/metrics.py: remove spaces/tabs/newlines; preserve case/punctuation',
            'regional_assignment':'Each prediction once, maximum area overlap among regions containing its center or >=50% of its area; inverse perspective for clean mode',
            'limits':['Region CER does not evaluate inter-region reading order or unannotated image text.','Digital pages and scans do not demonstrate phone-camera robustness.',evaluation_role,'Pretrained data overlap is unknown.'],
            'groups':[dict(zip(('category','language','scope','variant','mode'),key),**aggregate(value)) for key,value in groups.items()],
            'samples':details,'lock_sha256':sha(LOCK),'runtime':runtime}
    write(OUT/'report.json',report)
    print(json.dumps(report['groups'],ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--freeze',action='store_true');group.add_argument('--evaluate',action='store_true');group.add_argument('--summarize',action='store_true')
    args=parser.parse_args()
    if args.freeze: freeze()
    elif args.evaluate: evaluate()
    else: summarize()
