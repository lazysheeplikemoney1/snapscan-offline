const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const root = path.resolve(__dirname, '..');
function setup(handler) {
  const operations = [], network = [];
  const storage = new Map([['snapscan_backend_base', 'http://old-computer:8000']]);
  const window = { SnapScanNative: { request(id, operation, payload) {
    const data = JSON.parse(payload); operations.push({operation, data});
    queueMicrotask(() => window.__snapscanNativeResult(id, handler ? handler(operation, data) :
      { statusCode: 200, data: { ok: true, status: 'ok', filePath: 'https://appassets.androidplatform.net/uploads/source_a.jpg', ocr:{ready:true,model:{id:'rec_head_v1'}} } }));
  } } };
  const uni = { getStorageSync:k=>storage.get(k), setStorageSync:(k,v)=>storage.set(k,v), removeStorageSync:k=>storage.delete(k),
    request:o=>{ network.push(o); throw new Error('Unexpected network'); }, uploadFile:o=>{network.push(o); throw new Error('Unexpected upload');},
    downloadFile:()=>{throw new Error('Unexpected download');}, navigateTo:o=>operations.push({operation:'navigate',data:o}), showToast:()=>{}, previewImage:()=>{} };
  const context = {window,uni,console,Promise,Error,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'utils/api.js'),'utf8').replace('export const api =','globalThis.api ='),context);
  return { api:context.api,window,uni,operations,network,storage };
}
function page(file,h) {
  const source=fs.readFileSync(path.join(root,file),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1].replace(/import \{ api \} from [^;]+;/,'').replace(/import \{ buildTextLayout \} from [^;]+;/,'').replace('export default','globalThis.component =');
  const context={api:h.api,uni:h.uni,console,Promise,Error};
  vm.runInNewContext(fs.readFileSync(path.join(root,'utils/text_layout.js'),'utf8').replace('export function buildTextLayout','function buildTextLayout'),context);
  vm.runInNewContext(source,context);
  const instance=context.component.data();
  for(const [k,v] of Object.entries(context.component.methods||{})) instance[k]=v.bind(instance);
  for(const [k,v] of Object.entries(context.component.computed||{})) Object.defineProperty(instance,k,{get:()=>v.call(instance)});
  return instance;
}
test('native engine ignores old LAN settings and cannot switch to a remote service',()=>{
  const h=setup(); assert.equal(h.api.isNative,true); assert.equal(h.api.BASE,'https://appassets.androidplatform.net');
  assert.throws(()=>h.api.setBase('http://another:8000'),/本地|离线/); assert.equal(h.api.resetBase(),h.api.BASE);
});
test('all processing, storage and export operations go through native bridge without HTTP',async()=>{
  const h=setup(); await h.api.health(); await h.api.correct('/uploads/a.jpg',{mode:'bw',quad:[[1,2],[3,2],[3,4],[1,4]]});
  await h.api.recognize('/uploads/b.png'); await h.api.history('小票'); await h.api.detail(4); await h.api.saveDocument(4,['合计933'],2);
  await h.api.toPDF('合计933',[],{scan_id:4,revision:3,mode:'searchable'}); await h.api.toExcel('合计933',[],{scan_id:4,revision:3});
  assert.deepEqual(h.operations.map(x=>x.operation),['health','correct','recognize','history','detail','saveDocument','toPDF','toExcel']);
  assert.equal(h.operations[1].data.mode,'bw'); assert.equal(h.operations[3].data.q,'小票'); assert.equal(h.operations[5].data.revision,2); assert.equal(h.network.length,0);
});
test('native model failure surfaces and never falls back to a LAN upload',async()=>{
  const h=setup(()=>({statusCode:503,data:{ok:false,msg:'本机模型校验失败'}})); await assert.rejects(h.api.recognize('/uploads/a.png'),/本机模型校验失败/); assert.equal(h.network.length,0);
});
test('offline app origin stays local even if the native bridge cannot initialize',async()=>{
  const h=setup(); h.window.location={origin:'https://appassets.androidplatform.net'}; delete h.window.SnapScanNative;
  assert.equal(h.api.isNative,true); assert.equal(h.api.BASE,'https://appassets.androidplatform.net');
  await assert.rejects(h.api.health(),/本地引擎不可用/); assert.equal(h.network.length,0);
});
test('native camera picker passes the local photo to scan processing',async()=>{
  const h=setup(),p=page('pages/index/index.vue',h); let source; p.scan=fp=>{source=fp;}; await p.choose('camera');
  assert.equal(h.operations[0].operation,'chooseImage'); assert.equal(h.operations[0].data.source,'camera'); assert.match(source,/\/uploads\/source_a.jpg$/);
});
test('native scan image is checked locally before OCR without downloading over HTTP',async()=>{
  const h=setup(),p=page('pages/index/index.vue',h); Object.assign(p,{scanBase:h.api.BASE,processedImage:'/uploads/corrected_a.png'});
  const fp=await p.downloadCorrected(); assert.equal(h.operations[0].operation,'localFile'); assert.equal(fp,h.api.BASE+'/uploads/corrected_a.png');
});
test('native save uses Android album operation and preserves OCR for explicit confirmation',async()=>{
  const h=setup(),p=page('pages/index/index.vue',h); Object.assign(p,{scanBase:h.api.BASE,reviewReady:true,boundaryConfirmed:true,processedImage:'/uploads/corrected_a.png'});
  await p.saveScanImage(); assert.deepEqual(h.operations.map(x=>x.operation),['localFile','saveImage']); assert.match(p.tip,/已保存/);
});
test('native export opens and shares real local files rather than copying an unusable URL',async()=>{
  const h=setup(),p=page('pages/result/result.vue',h); p.fullUrl=h.api.BASE+'/uploads/export_a.pdf'; await p.openLink(); await p.shareFile();
  assert.deepEqual(h.operations.map(x=>x.operation),['openFile','shareFile']);
});
