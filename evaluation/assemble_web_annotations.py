"""Materialize reviewed web-source images and their explicit annotation provenance."""
from pathlib import Path
import hashlib
import json
import re
import shutil

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
REAL=ROOT/'data'/'real'
CACHE=ROOT/'evaluation'/'curation_cache'


def dump_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')


def base_row(path, dtype, difficulty, language, text):
    return {'image_id':path.name,'image_path':path.relative_to(REAL).as_posix(),
            'document_type':dtype,'difficulty':difficulty,'difficulties':[difficulty],
            'perspective':'none','shadow':0,'glare':0,'blur':0,'handwriting':0,
            'split':'test','gt_text':text,'language':language,'source_kind':'public_web_photo',
            'annotation_status':'source_ground_truth','annotation_method':'published_human_transcription',
            'transcription_scope':'document text in full photograph',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def whiteboards():
    folder=REAL/'test'/'web_boards';folder.mkdir(parents=True,exist_ok=True)
    source=json.loads((CACHE/'whiteboards'/'manifest.json').read_text(encoding='utf-8'))
    rows=[]
    for item in source:
        path=folder/('web_board_hf'+item['id']+'.png')
        if not path.exists():
            with Image.open(item['local_path']) as im:
                im.convert('RGB').save(path)
        number=int(item['id'])
        row=base_row(path,'黑板白板','透视' if number in [1,2,3,4,5,6,7,11,13,14,18,19,21] else '正常',
                     'en',item['transcription'])
        # Glare is visibly present in these two close-ups; no artificial degradation is applied.
        if number in [15,16]:
            row.update(difficulty='反光',difficulties=['反光'],glare=1)
        if row['difficulty']=='透视': row['perspective']='mild'
        row.update(handwriting=1,source_url=item['source_url'],download_url=item['download_url'],
                   source_revision=item['source_revision'],license=item['license'],
                   source_sha256=item['sha256'],
                   license_url='https://creativecommons.org/licenses/by/4.0/',creator=item['creator'],
                   source_group='rosehill_same_author_same_board',source_sample_id=item['id'],
                   source_category=item['category'],derivation='WebP decoded to lossless PNG, full frame unchanged',
                   notes='Original author ground truth retained, including diagram arrows/markdown conventions. Some source images are progressive close-ups; all remain in the same split.')
        rows.append(row)
    dump_jsonl(REAL/'annotations_web_whiteboards.jsonl',rows)
    print('materialized whiteboards',len(rows))


def hard_examples():
    folder=REAL/'test'/'web_hard';folder.mkdir(parents=True,exist_ok=True)
    src=CACHE/'smartdoc'
    names=json.loads((src/'selection.json').read_text(encoding='utf-8'))
    rows=[]
    for member in names:
        name=Path(member).name
        doc=int(re.search(r'_D(\d+)_',name)[1])
        if doc in [21,22]: continue  # Flat receipts were previewed but do not fill the hard-case quota.
        path=folder/f'web_hard_smartdoc_d{doc}.jpg'
        shutil.copyfile(src/name,path)
        text=(src/f'page_{doc}.txt').read_text(encoding='utf-8-sig').replace('\r\n','\n').strip()
        difficulty='低照度' if doc in [23,24] else '模糊' if doc in [25,26] else '阴影' if doc in [27,28] else '透视'
        row=base_row(path,'票据',difficulty,'fr',text)
        row.update(shadow=int(difficulty=='阴影'),blur=int(difficulty=='模糊'),
                   perspective='mild' if difficulty=='透视' else 'none',
                   source_url='https://zenodo.org/records/5293201',
                   download_url='https://zenodo.org/records/5293201/files/Dataset%20SmartDoc-QA.zip?download=1',
                   source_archive_member=member,source_gt_member=f'Dataset SmartDoc-QA/Ground_truth/page_{doc}.txt',
                   source_group=f'smartdoc_original_document_{doc}',source_sample_id=f'D{doc}',
                   creator='Nayef, Luqman, Prum, Eskenazi, Chazalon, Ogier (L3i Laboratory)',
                   license='CC-BY-4.0',license_url='https://creativecommons.org/licenses/by/4.0/',
                   capture_device='Samsung Galaxy S4',
                   notes='Source authors provide manually keyed document transcriptions. Native sideways capture and source typography preserved; French stress subset must be reported separately from Chinese.')
        rows.append(row)

    book1='''10 ENGLISH
Personalizing your brushing experience
Philips Sonicare automatically starts in the default
Clean mode. To personalize your brushing:
1 Prior to turning on the Philips Sonicare,
press the personalized brushing button to
cycle through the modes and routines.
The green LED indicates the selected mode
or routine.
Brushing modes
Clean mode
Standard mode for superior teeth cleaning.
White mode
2 minutes of Clean mode, with an additional
30 seconds of White mode to focus on your
visible front teeth.
White mode brushing instructions
1 Brush the first 2 minutes as explained in
section ‘Brushing instructions’.
2 After the 2 minutes of Clean mode, the White
mode starts with a change in brushing sound
and motion. This is your signal to start brushing
the upper front teeth for 15 seconds.
3 At the next beep and pause, move to the
bottom front teeth for the final 15 seconds
of brushing.
Massage mode
Mode for gentle gum stimulation.'''
    book46='''CHAPTER 6 How to Pass
The law requires that we drive on the right side of the road. When we are allowed
to pass other vehicles, we usually pass on the left. You are permitted to pass on the
right only in certain circumstances and it must be done only when necessary and safe.
When you pass other vehicles or change lanes to keep away from hazards, do so
with caution and only when necessary. You must not exceed the speed limit to pass
another vehicle.
When you pass a motorcycle, remember to give the motorcycle the same full-
lane width as other vehicles are allowed. Never move into the same lane space as a
motorcycle, even if the lane is wide and the motorcycle is to one side.
The law requires you to use turn signals or hand signals at least 100 feet (30 m)
before you make a lane change. You must never pass a vehicle that has stopped at a
crosswalk to allow a pedestrian to go across.
HOW TO PASS ON THE LEFT
The left lane is normally used to pass other vehicles. You can not pass a vehicle
on the left if:
Your lane has a solid yellow center line.
You can not safely return to the right lane before you reach a solid yellow
center line for the right lane.
You can not safely return to the right lane before any oncoming vehicle
comes within 200 feet (60 m) of you.
You approach a curve or the top of a hill on a two-way road and can not see
around or over it.
52'''
    for doc,text in [(1,book1),(46,book46)]:
        path=folder/f'web_hard_docunet_{doc}_2.jpg'
        shutil.copyfile(CACHE/'docunet'/f'{doc}_2.jpg',path)
        row=base_row(path,'书籍','褶皱','en',text)
        row.update(perspective='mild',difficulties=['褶皱','透视'],
                   annotation_status='verified',annotation_method='manual_visual_transcription',
                   source_url='https://www3.cs.stonybrook.edu/~cvl/docunet.html',
                   download_url='http://vision.cs.stonybrook.edu/~kema/docwarp/original.zip',
                   source_archive_member=f'original/{doc}_2.jpg',source_group=f'docunet_document_{doc}',
                   creator='Ke Ma, Zhixin Shu, Xue Bai, Jue Wang, Dimitris Samaras',
                   license='Research benchmark; citation requested; no explicit redistribution license stated',
                   license_url='https://www3.cs.stonybrook.edu/~cvl/docunet.html',
                   redistribution_status='not_cleared_for_public_redistribution',
                   notes='Visually transcribed from photographed printed page, corrected against the page rather than copying tess_gt OCR errors. Decorative bullet shapes excluded; printed numbers included. Genuine crumpled/folded paper, not a synthesized deformation.')
        rows.append(row)
    dump_jsonl(REAL/'annotations_web_hard.jsonl',rows)
    print('materialized hard cases',len(rows))


if __name__=='__main__':
    whiteboards()
    hard_examples()
