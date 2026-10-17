"""Merge reviewed annotations and audit the real/photo-source dataset.

No OCR model is run on the test split. Original local photos are unchanged.
The user-requested metadata_template.jsonl becomes the full annotation manifest;
the original two example rows are preserved as metadata_template.example.jsonl.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import shutil

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
REAL=ROOT/'data'/'real'
CACHE=ROOT/'evaluation'/'curation_cache'
REQUIRED={'image_id','document_type','difficulty','perspective','shadow','glare','blur','handwriting','split','gt_text'}
DIFFICULTIES={'正常','透视','阴影','反光','模糊','低照度','褶皱'}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def write_rows(path,rows):
    path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')


def assemble():
    rows=[]
    for name in ['annotations_local_111.jsonl','annotations_local_222.jsonl','annotations_local_333.jsonl',
                 'annotations_web_whiteboards.jsonl','annotations_web_commons.jsonl','annotations_web_hard.jsonl']:
        rows.extend(read_rows(REAL/name))
    helper=REAL/'annotations_books_help_014_020.jsonl'
    if helper.exists():
        override={row['image_id']:row for row in read_rows(helper)}
        rows=[override.get(row['image_id'],row) for row in rows]

    # Publish only the nine selected Commons photographs into the actual dataset.
    shortlist={row['candidate_id']:row for row in json.loads((CACHE/'commons_shortlist'/'manifest.json').read_text(encoding='utf-8'))}
    shortlist.update({row['candidate_id']:row for row in json.loads((CACHE/'commons'/'manifest.json').read_text(encoding='utf-8'))})
    for row in rows:
        if row['image_id'].startswith('web_board_') and not row['image_id'].startswith('web_board_hf'):
            cid=Path(row['image_id']).stem.replace('web_board_','')
            dest=REAL/row['image_path'];dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(shortlist[cid]['local_path'],dest)

    errors=[]
    by_hash=defaultdict(list)
    for row in rows:
        path=(REAL/row['image_path']).resolve()
        if not path.is_relative_to(REAL.resolve()): raise ValueError('Image path escapes dataset')
        if not path.exists():
            errors.append('missing image '+row['image_id']);continue
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            row['width'],row['height']=im.size
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if row.get('sha256') and row['sha256']!=digest:
            errors.append('hash mismatch '+row['image_id'])
        row['sha256']=digest
        row.setdefault('language','zh-Hans')
        row.setdefault('source_kind','user_provided_photo')
        row.setdefault('source_url',None)
        row.setdefault('license','user-provided; no public redistribution permission assumed')
        row.setdefault('source_group',row.get('source_group_id',row.get('content_group',row['image_id'])))
        row.setdefault('difficulties',[row['difficulty']])
        if row['difficulty'] not in row['difficulties']: row['difficulties'].append(row['difficulty'])
        row.setdefault('evaluation_requires_roi',bool(row.get('document_roi') or row.get('excluded_regions')))
        if missing:=REQUIRED-row.keys(): errors.append(f'{row["image_id"]}: missing {sorted(missing)}')
        if row['difficulty'] not in DIFFICULTIES: errors.append('unknown difficulty '+row['image_id'])
        if row['perspective'] not in ['none','mild','strong']: errors.append('unknown perspective '+row['image_id'])
        for key in ['shadow','glare','blur','handwriting']:
            if row[key] not in [0,1]: errors.append('invalid flag '+row['image_id']+' '+key)
        verified=row.get('annotation_status') in ['verified','verified_with_unk','reviewed','source_ground_truth']
        row['gt_verified']=bool(verified and isinstance(row['gt_text'],str) and row['gt_text'].strip())
        row['contains_unk']=isinstance(row['gt_text'],str) and '[UNK]' in row['gt_text']
        row['evaluation_eligible']=bool(row['gt_verified'] and not row['contains_unk'])
        by_hash[digest].append(row)

    # Known same-page/book/receipt captures stay in dev. Explicit source groups
    # preserve linkage even where perspective/brightness changes the file bytes.
    related=[['real-002.jpg','real-029.jpg'],['real-005.jpg','real-028.jpg'],
             ['real-013.jpg','real-032.jpg'],['real-021.jpg','real-030.jpg'],
             ['real-026.jpg','real-031.jpg'],['real-046.jpg','real-047.jpg'],
             ['real-048.jpg','real-051.jpg']]
    for group in related:
        for row in rows:
            if row['image_id'] in group: row['source_group']='local_same_document_'+group[0]
    duplicates=[]
    for digest,members in by_hash.items():
        if len(members)>1:
            duplicates.append([row['image_id'] for row in members])
            for row in members:
                row['duplicate_group']='sha256:'+digest
            for row in members[1:]:
                row['duplicate_of']=members[0]['image_id']
                row['evaluation_eligible']=False
            if len({row['split'] for row in members})>1:errors.append('exact duplicate across split '+digest)
    group_splits=defaultdict(set)
    for row in rows:group_splits[row['source_group']].add(row['split'])
    for group,splits in group_splits.items():
        if len(splits)>1:errors.append('source group crosses splits '+group)
    if len({row['image_id'] for row in rows})!=len(rows):errors.append('duplicate image IDs')
    if len({row['image_path'] for row in rows})!=len(rows):errors.append('duplicate image paths')
    if errors:raise ValueError('\n'.join(errors))

    target=REAL/'metadata_template.jsonl'
    backup=REAL/'metadata_template.example.jsonl'
    if not backup.exists():shutil.copyfile(target,backup)
    write_rows(target,rows)
    write_rows(REAL/'metadata.jsonl',rows)
    per_folder=defaultdict(dict)
    for row in rows:
        if row['gt_verified']:
            path=REAL/row['image_path']
            per_folder[path.parent][path.name]=row['gt_text']
    for folder,gt in per_folder.items():
        (folder/'gt.json').write_text(json.dumps(gt,ensure_ascii=False,indent=2),encoding='utf-8')

    test=[row for row in rows if row['split']=='test']
    coverage=Counter(d for row in test for d in set(row['difficulties']))
    pending=[row['image_id'] for row in rows if not row['gt_verified']]
    notes=[
        '原有105张图片保留在dev；新增40张网络实拍照片集中放入test，总计145张。原130张目标中的前三类各已有35张，因此不删除超出的原图。',
        '新增数据是公开图片难例补充集，含繁体中文、英文、法文。正式报告必须按语言、来源分别统计，不能据此声称中国用户手机实拍性能。',
        'test按缺失类别补充，含黑板白板30、票据8、书籍2，没有课件；尚不满足DATASET_SPEC.md所建议的四类按比例分层最终测试集。',
        'annotation_method区分原作者提供的人工真值和本次视觉转录；OCR草稿没有冒充真值。annotation_status=verified_with_unk表示所有可辨文字已经核对、少数不可辨行用[UNK]。',
        '带[UNK]的标注按规范保留，但evaluation_eligible=false。现有CER脚本仅删除GT未知行，不能自动对齐预测中的对应行，应先排除样本或完善区域级评测。',
        'evaluation_requires_roi=true的图需按document_roi、transcription_scope、excluded_regions处理后评分；现有全图评测脚本不会读取这些范围字段。',
        '原有票据含个人信息，35张保留privacy_review=true；原图和可读文字已保留用于本地核对，没有伪称图片已脱敏，不能直接作为脱敏数据公开。',
        '两张DocUNet图片来源页要求引用论文，未给出明确的公开再分发许可；已标redistribution_status，适用于当前本地研究，公开打包前需核实许可。',
        'metadata_template.jsonl为本次用户指定的完整标注文件；metadata.jsonl内容相同。原两行示例已备份至metadata_template.example.jsonl。'
    ]
    report={'total_images':len(rows),'split_counts':dict(Counter(row['split'] for row in rows)),
            'document_type_counts':dict(Counter(row['document_type'] for row in rows)),
            'language_counts':dict(Counter(row['language'] for row in rows)),
            'source_kind_counts':dict(Counter(row['source_kind'] for row in rows)),
            'annotation_status_counts':dict(Counter(row.get('annotation_status') for row in rows)),
            'gt_verified_count':sum(row['gt_verified'] for row in rows),'pending_gt':pending,
            'test_difficulty_coverage':dict(coverage),
            'test_every_difficulty_at_least_two':all(coverage[d]>=2 for d in DIFFICULTIES),
            'exact_duplicate_groups':duplicates,'unique_image_hashes':len(by_hash),
            'privacy_review_count':sum(bool(row.get('privacy_review')) for row in rows),
            'all_paths_and_images_valid':True,'split_hash_or_group_leakage':False,
            'annotation_complete':not pending,'ready_for_representative_final_report':False,
            'test_models_run_during_collection':False,'notes':notes}
    (REAL/'collection_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (REAL/'ANNOTATION_README.txt').write_text('\n'.join(notes)+'\n',encoding='utf-8')
    sources=REAL/'sources';sources.mkdir(exist_ok=True)
    for source,name in [(CACHE/'whiteboards'/'README.source.md','Whiteboards_README.source.md'),
                        (CACHE/'smartdoc'/'README.txt','SmartDoc-QA_README.source.txt'),
                        (CACHE/'smartdoc'/'SmartDoc-QA_Dataset_License.pdf','SmartDoc-QA_Dataset_License.pdf')]:
        shutil.copyfile(source,sources/name)
    print(json.dumps({k:v for k,v in report.items() if k not in ['notes','pending_gt','exact_duplicate_groups']},ensure_ascii=False,indent=2))
    print('pending_gt:',pending)


if __name__=='__main__':assemble()
