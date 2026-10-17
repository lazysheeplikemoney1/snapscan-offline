"""Fetch a small reviewed-source shortlist without crawling whole categories."""
import io
import json
from pathlib import Path
import time

from PIL import Image
from collect_web_dataset import CACHE, COMMONS_API, get, plain, sheet

DEST = CACHE / 'commons_shortlist'
DEST.mkdir(parents=True, exist_ok=True)
PAGE_IDS = [152096151,152082782,59277161,43705619,43705620,61553800,
            103061445,72804884,72794292,99645670,176518490,98050102]
response = get(COMMONS_API, params={
    'action':'query','pageids':'|'.join(map(str,PAGE_IDS)),'prop':'imageinfo',
    'iiprop':'url|extmetadata|size','iiurlwidth':1800,'format':'json'}).json()
rows=[]
for page in sorted(response['query']['pages'].values(),key=lambda p:PAGE_IDS.index(p['pageid'])):
    ii=page['imageinfo'][0]
    ext=ii.get('extmetadata',{})
    val=lambda key:plain(ext.get(key,{}).get('value',''))
    cid='tw'+str(PAGE_IDS.index(page['pageid'])+1).zfill(2)
    row={'candidate_id':cid,'page_id':page['pageid'],'title':page['title'],
         'license':val('LicenseShortName'),'license_url':val('LicenseUrl'),
         'creator':val('Artist'),'source_url':ii['descriptionurl'],
         'download_url':ii.get('thumburl') or ii['url'],'original_url':ii['url'],
         'description':val('ImageDescription'),'width':ii['width'],'height':ii['height'],
         'local_path':str(DEST/(cid+'.jpg'))}
    rows.append(row)
(DEST/'candidates.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
done=[]
for row in rows:
    path=Path(row['local_path'])
    try:
        if not path.exists():
            with Image.open(io.BytesIO(get(row['download_url']).content)) as im:
                im.convert('RGB').save(path,quality=97)
            time.sleep(1.2)
        done.append(row)
        print('downloaded',row['candidate_id'],flush=True)
    except Exception as error:
        print(type(error).__name__,str(error)[:120],flush=True)
        if getattr(getattr(error,'response',None),'status_code',None)==429:
            print('Stopping downloads to respect source rate limit',flush=True)
            break
(DEST/'manifest.json').write_text(json.dumps(done,ensure_ascii=False,indent=2),encoding='utf-8')
if done: sheet(done,DEST/'sheet.jpg',columns=3,cell=(600,460))
