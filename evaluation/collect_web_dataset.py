"""Download attributed, publicly licensed source photos for dataset curation.

Candidate images stay outside data/real until visually reviewed and selected.
This script does not treat OCR predictions as ground truth.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import html
import json
import re
import time

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'evaluation' / 'curation_cache'
HEADERS = {'User-Agent': 'SnapScanDatasetCuration/0.1 (educational OCR; attribution retained)'}
HF_REPO = 'https://huggingface.co/datasets/danielrosehill/Whiteboards'
COMMONS_API = 'https://commons.wikimedia.org/w/api.php'


def get(url, **kwargs):
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=(12, 50), **kwargs)
            if response.status_code == 429:
                raise requests.HTTPError('Source rate limited: stop and respect Retry-After', response=response)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            if getattr(getattr(error, 'response', None), 'status_code', None) == 429:
                raise
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def plain(value):
    return html.unescape(re.sub('<[^>]+>', '', value or '')).strip()


def sheet(items, out, columns=4, cell=(400, 340)):
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 16)
    canvas = Image.new('RGB', (columns * cell[0], ((len(items)+columns-1)//columns)*cell[1]), 'white')
    draw = ImageDraw.Draw(canvas)
    for index, item in enumerate(items):
        x, y = (index % columns)*cell[0], (index // columns)*cell[1]
        with Image.open(item['local_path']) as source:
            im = ImageOps.exif_transpose(source).convert('RGB')
            im.thumbnail((cell[0]-12, cell[1]-55))
            canvas.paste(im, (x+(cell[0]-im.width)//2, y+5))
        label = item.get('candidate_id', item.get('id', '')) + ' ' + item.get('title', item.get('category', ''))
        draw.text((x+5, y+cell[1]-47), label[:40], font=font, fill='black')
        draw.text((x+5, y+cell[1]-24), item.get('license', ''), font=font, fill='black')
    canvas.save(out)


def whiteboards():
    dest = CACHE / 'whiteboards'
    dest.mkdir(parents=True, exist_ok=True)
    info = get('https://huggingface.co/api/datasets/danielrosehill/Whiteboards').json()
    revision = info['sha']
    base = HF_REPO + '/resolve/' + revision
    readme = get(base + '/README.md').text
    (dest / 'README.source.md').write_text(readme, encoding='utf-8')
    metadata = get(base + '/data/metadata.jsonl').text
    (dest / 'metadata.source.jsonl').write_text(metadata, encoding='utf-8')
    rows = [json.loads(line) for line in metadata.splitlines() if line.strip()]
    def fetch(row):
        target = dest / row['file_name']
        url = base + '/data/' + row['file_name']
        if not target.exists():
            target.write_bytes(get(url).content)
        with Image.open(target) as im:
            im.verify()
        return {**row, 'source_url': HF_REPO, 'download_url': url, 'source_revision': revision,
                'license': 'CC-BY-4.0', 'creator': 'Daniel Rosehill', 'local_path': str(target),
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(fetch, rows))
    (dest / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for offset in range(0, len(result), 12):
        sheet(result[offset:offset+12], dest / f'sheet_{offset//12+1}.jpg')
    print('whiteboards downloaded:', len(result), flush=True)


def commons():
    dest = CACHE / 'commons'
    dest.mkdir(parents=True, exist_ok=True)
    queries = ['黑板', '白板', 'blackboard mathematics writing', 'whiteboard handwritten text',
               'receipt crumpled', 'document glare', 'paper wrinkled text', 'book shadow']
    candidates = {}
    for query in queries:
        result = get(COMMONS_API, params={
            'action':'query', 'generator':'search', 'gsrsearch':query+' filetype:bitmap',
            'gsrnamespace':6, 'gsrlimit':15, 'prop':'imageinfo', 'iiprop':'url|extmetadata|size',
            'iiurlwidth':1280, 'format':'json'}).json()
        for page in result.get('query', {}).get('pages', {}).values():
            ii = page.get('imageinfo', [{}])[0]
            ext = ii.get('extmetadata', {})
            val = lambda key: plain(ext.get(key, {}).get('value', ''))
            lic = val('LicenseShortName')
            if not any(x in lic.lower() for x in ['cc by', 'cc0', 'public domain']):
                continue
            if ii.get('width',0) < 600 or ii.get('height',0) < 400:
                continue
            candidates[page['pageid']] = {
                'page_id':page['pageid'], 'title':page['title'], 'query':query,
                'license':lic, 'license_url':val('LicenseUrl'), 'creator':val('Artist'),
                'source_url':ii.get('descriptionurl'), 'download_url':ii.get('thumburl') or ii.get('url'),
                'original_url':ii.get('url'), 'description':val('ImageDescription'),
                'width':ii.get('width'), 'height':ii.get('height')}
        print('commons search:', query, len(candidates), flush=True)
    def fetch(pair):
        index, row = pair
        cid = f'c{index:03}'
        target = dest / f'{cid}.jpg'
        try:
            if not target.exists():
                data = get(row['download_url']).content
                import io
                with Image.open(io.BytesIO(data)) as im:
                    im.convert('RGB').save(target, quality=95)
            with Image.open(target) as im:
                im.verify()
            return {**row, 'candidate_id':cid, 'local_path':str(target),
                    'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                    'derivation':'Wikimedia thumbnail converted to JPEG; no semantic alterations'}
        except Exception as error:
            print('download failed:', cid, type(error).__name__, str(error)[:100], flush=True)
    ordered = sorted(candidates.values(), key=lambda row:row['page_id'])
    # Cache metadata before downloads so interrupted runs remain auditable.
    for index, row in enumerate(ordered,1):
        row['candidate_id'] = f'c{index:03}'
        row['local_path'] = str(dest / f'c{index:03}.jpg')
    (dest / 'candidates.json').write_text(json.dumps(ordered,ensure_ascii=False,indent=2),encoding='utf-8')
    result = []
    for index, row in enumerate(ordered,1):
        target = Path(row['local_path'])
        if target.exists():
            with Image.open(target) as im:
                im.verify()
            result.append({**row, 'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                           'derivation':'Wikimedia thumbnail converted to JPEG; no semantic alterations'})
        # Additional downloads are selected manually after reviewing the cache.
    (dest / 'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for offset in range(0,len(result),12):
        sheet(result[offset:offset+12], dest / f'sheet_{offset//12+1}.jpg')
    print('commons candidates downloaded:',len(result),flush=True)


if __name__ == '__main__':
    import sys
    if '--commons' in sys.argv:
        commons()
    else:
        whiteboards()
