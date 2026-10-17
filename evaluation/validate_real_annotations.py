"""Independently validate the delivered manifest; never run test-set OCR."""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import sys

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data'/'real'
MANIFEST=BASE/'metadata_template.jsonl'
REQUIRED={'image_id','document_type','difficulty','perspective','shadow','glare','blur','handwriting','split','gt_text'}


def reject_duplicate_keys(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Repeated JSON key '+key)
        result[key]=value
    return result


def main():
    rows=[json.loads(line,object_pairs_hook=reject_duplicate_keys)
          for line in MANIFEST.read_text(encoding='utf-8').splitlines() if line.strip()]
    errors=[];pending=[];hash_splits=defaultdict(set);groups=defaultdict(set)
    for index,row in enumerate(rows,1):
        if not REQUIRED<=row.keys():errors.append(f'line {index}: required fields missing');continue
        path=(BASE/row['image_path']).resolve()
        if not path.is_relative_to(BASE.resolve()):errors.append('unsafe image path');continue
        if not path.is_file():errors.append('missing image '+row['image_id']);continue
        with Image.open(path) as im:
            im.load()
            if im.size!=(row['width'],row['height']):errors.append('size mismatch '+row['image_id'])
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=row['sha256']:errors.append('hash mismatch '+row['image_id'])
        hash_splits[sha].add(row['split']);groups[row['source_group']].add(row['split'])
        if not isinstance(row['gt_text'],str) or not row['gt_text'].strip() or not row['gt_verified']:
            pending.append(row['image_id'])
        if row['source_kind']=='public_web_photo':
            for key in ['source_url','download_url','license','creator']:
                if not row.get(key):errors.append('missing provenance '+row['image_id']+' '+key)
        if row.get('document_roi'):
            x0,y0,x1,y1=row['document_roi']
            if not (0<=x0<x1<=row['width'] and 0<=y0<y1<=row['height']):errors.append('invalid ROI '+row['image_id'])
        for key in ['shadow','glare','blur','handwriting']:
            if type(row[key]) is not int or row[key] not in [0,1]:errors.append('invalid flag '+row['image_id'])
    files={str(path.relative_to(BASE).as_posix()) for folder in [
        BASE/'dev'/'dev'/'111',BASE/'dev'/'dev'/'222',BASE/'dev'/'dev'/'333',
        BASE/'test'/'web_boards',BASE/'test'/'web_hard'] for path in folder.iterdir()
        if path.suffix.lower() in ['.jpg','.jpeg','.png','.webp']}
    if files!={row['image_path'] for row in rows}:errors.append('photo inventory and manifest do not agree')
    if len(rows)!=145 or len({row['image_id'] for row in rows})!=145:errors.append('expected145 distinct image IDs')
    if any(len(splits)>1 for splits in hash_splits.values()):errors.append('exact image hash crosses dev/test')
    if any(len(splits)>1 for splits in groups.values()):errors.append('source document group crosses dev/test')
    if MANIFEST.read_bytes()!=(BASE/'metadata.jsonl').read_bytes():errors.append('metadata aliases diverge')
    test=[row for row in rows if row['split']=='test']
    coverage=Counter(d for row in test for d in set(row['difficulties']))
    for label in ['正常','透视','阴影','反光','模糊','低照度','褶皱']:
        if coverage[label]<2:errors.append('test difficulty below2: '+label)
    counts=Counter(row['document_type'] for row in rows)
    if any(counts[label]<30 for label in ['书籍','票据','课件','黑板白板']):errors.append('document category below30')
    report={'manifest_rows':len(rows),'readable_photo_files':len(files),
            'all_images_have_annotations':files=={row['image_path'] for row in rows},
            'all_gt_completed':not pending,'pending_gt':pending,'errors':errors,
            'pass_structure_and_provenance':not errors,
            'pass_annotation_completion':not errors and not pending,
            'rows_with_unk':sum(bool(row['contains_unk']) for row in rows),
            'evaluation_eligible_unique_rows':sum(row['evaluation_eligible'] for row in rows),
            'test_difficulty_coverage':dict(coverage),
            'privacy_review_rows':sum(bool(row.get('privacy_review')) for row in rows),
            'test_source_and_language_balance_requires_separate_reporting':True}
    (BASE/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not errors and not pending:
        lock={'locked_on_local_date':'2026-10-15','purpose':'Photo and annotation integrity; no test-set tuning performed',
              'images':[{'image_id':row['image_id'],'sha256':row['sha256'],
                         'gt_sha256':hashlib.sha256(row['gt_text'].encode('utf-8')).hexdigest(),
                         'source_group':row['source_group']} for row in test]}
        target=BASE/'test'/'test_lock.json'
        if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=lock:
            raise ValueError('Existing test lock differs; do not silently replace a frozen test set')
        target.write_text(json.dumps(lock,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not errors and not pending else 1


if __name__=='__main__':sys.exit(main())
