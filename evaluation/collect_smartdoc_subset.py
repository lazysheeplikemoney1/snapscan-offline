"""Read selected original SmartDoc-QA ZIP members using bounded HTTP ranges.

The 13.7 GB archive is never downloaded in full. CC BY 4.0 source metadata
and the original archive member paths are retained for attribution.
"""
import io
import json
from pathlib import Path
import re
import struct
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'evaluation' / 'curation_cache' / 'smartdoc'
DEST.mkdir(parents=True, exist_ok=True)
URL = 'https://zenodo.org/records/5293201/files/Dataset%20SmartDoc-QA.zip?download=1'
SIZE = 13659581479


class RemoteZIP(io.RawIOBase):
    def __init__(self):
        self.position = 0
        self.cache = []
        tail = DEST / 'zip_tail.bin'
        if tail.exists():
            data = tail.read_bytes()
            self.cache.append((SIZE-len(data),data))

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence==0 else (self.position+offset if whence==1 else SIZE+offset)
        if self.position < 0: raise ValueError('Negative seek')
        return self.position

    def read(self, count=-1):
        if count < 0: count=SIZE-self.position
        count = min(count,SIZE-self.position)
        if count <= 0: return b''
        start=self.position
        for offset,data in self.cache:
            if offset<=start and start+count<=offset+len(data):
                self.position+=count
                return data[start-offset:start-offset+count]
        amount=min(SIZE-start,max(count,65536))
        if amount > 32*1024*1024: raise ValueError('Refusing unexpectedly large ZIP range')
        r=requests.get(URL,headers={'Range':f'bytes={start}-{start+amount-1}',
                       'User-Agent':'SnapScanDatasetCuration/0.1'},timeout=(15,60),stream=True)
        try:
            if r.status_code!=206: raise IOError(f'Server did not honor bounded range: {r.status_code}')
            actual=r.headers.get('Content-Range','')
            match=re.match(r'bytes (\d+)-(\d+)/(\d+)',actual)
            if not match or int(match[1])!=start:
                raise IOError('Unexpected Content-Range: '+actual)
            data=r.raw.read(amount+1)
            if len(data)!=amount: raise IOError('Incomplete/oversized range')
        finally:
            r.close()
        self.cache.append((start,data))
        self.position+=count
        return data[:count]


if __name__=='__main__':
    import sys
    remote=RemoteZIP()
    with zipfile.ZipFile(remote) as archive:
        entries=[{'name':x.filename,'size':x.file_size,'compressed_size':x.compress_size,
                  'offset':x.header_offset,'crc':x.CRC} for x in archive.infolist()]
        (DEST/'archive_index.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
        print('ZIP members:',len(entries),flush=True)
        if '--index' in sys.argv:
            for x in entries:
                if '/Ground_truth/' in x['name'] or x['name'].endswith(('README.txt','License.pdf')):
                    print(x['name'],x['size'],flush=True)
            print('Example image members:',flush=True)
            for x in [e for e in entries if e['name'].lower().endswith('.jpg')][:8]:
                print(x['name'],flush=True)
        else:
            selection=json.loads((DEST/'selection.json').read_text(encoding='utf-8'))
            for index,name in enumerate(selection):
                target=DEST/Path(name).name
                if not target.exists(): target.write_bytes(archive.read(name))
                print('extracted',index+1,target.name,flush=True)
