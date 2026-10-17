const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.resolve(__dirname, "..");

function setup(overrides = {}, storage = new Map()) {
  const calls = [];
  const uni = {
    getStorageSync: (key) => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: (key) => storage.delete(key),
    request: (options) => {
      calls.push({ kind: "request", ...options });
      options.success({ statusCode: 200, data: { status: "ok", ocr: { ready: true, model: { id: "rec_head_v1", sha256: "abcdef0123456789" } } } });
    },
    uploadFile: (options) => {
      calls.push({ kind: "upload", ...options });
      options.success({ statusCode: 200, data: JSON.stringify({ ok: true, text: "中文", boxes: [], model: { id: "rec_head_v1", sha256: "abcdef0123456789" } }) });
    },
    downloadFile: (options) => options.success({ statusCode: 200, tempFilePath: "/tmp/corrected.jpg" }),
    navigateTo: (options) => calls.push({ kind: "navigate", ...options }),
    showLoading: () => {}, hideLoading: () => {}, showToast: () => {},
    setClipboardData: (options) => calls.push({ kind: "clipboard", ...options }),
    previewImage: (options) => calls.push({ kind: "preview", ...options }),
    ...overrides
  };
  const source = fs.readFileSync(path.join(root, "utils/api.js"), "utf8")
    .replace("export const api =", "globalThis.api =");
  const context = { uni, console, Promise, Error };
  vm.runInNewContext(source, context, { filename: "api.js" });
  return { api: context.api, uni, calls, storage };
}

function page(file, harness) {
  const source = fs.readFileSync(path.join(root, file), "utf8").match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/import \{ api \} from [^;]+;/, "")
    .replace(/import \{ buildTextLayout \} from [^;]+;/, "")
    .replace("export default", "globalThis.component =");
  const context = { api: harness.api, uni: harness.uni, console, Promise, Error };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/text_layout.js"), "utf8")
    .replace("export function buildTextLayout", "function buildTextLayout"), context);
  vm.runInNewContext(source, context, { filename: file });
  const component = context.component;
  const instance = component.data();
  Object.entries(component.methods || {}).forEach(([key, value]) => { instance[key] = value.bind(instance); });
  Object.entries(component.computed || {}).forEach(([key, value]) => {
    Object.defineProperty(instance, key, { get: () => value.call(instance) });
  });
  return { instance, component };
}

test("tilted receipt rows stay together in copied text without changing correction indices", () => {
  const h = setup();
  const { instance } = page("pages/result/result.vue", h);
  const cell = (text, x, y, width) => ({ text, score: 0.99,
    box: [[x, y - 0.05 * x], [x + width, y - 0.05 * (x + width)],
      [x + width, y + 24 - 0.05 * (x + width)], [x, y + 24 - 0.05 * x]] });
  const boxes = [cell("单价", 250, 100, 80), cell("数量", 380, 100, 80),
    cell("价格（￥）", 510, 100, 110), cell("商品", 10, 100, 150),
    cell("58.00", 250, 150, 80), cell("6/份", 380, 150, 80),
    cell("348.00", 510, 150, 110), cell("1.养生海参盅", 10, 150, 190)];
  instance.applyDocument({ id: 17, revision: 0, boxes, image: "/uploads/scan_a.png", image_width: 700, image_height: 400 }, false);
  const before = JSON.stringify(instance.boxes.map(b => b.box));
  assert.equal(instance.text, "商品  单价  数量  价格（￥）\n1.养生海参盅  58.00  6/份  348.00");
  instance.editLine(7, { detail: { value: "1.校对后的菜名" } });
  instance.copy();
  assert.equal(h.calls.at(-1).data, "商品  单价  数量  价格（￥）\n1.校对后的菜名  58.00  6/份  348.00");
  assert.equal(instance.boxes[0].text, "单价");
  assert.equal(instance.boxes[7].text, "1.校对后的菜名");
  assert.equal(JSON.stringify(instance.boxes.map(b => b.box)), before);
  assert.deepEqual(Array.from(instance.readingItems, item => item.index), [3, 0, 1, 2, 7, 4, 5, 6]);
  const restored = page("pages/result/result.vue", h).instance;
  restored.applyDocument({ id: 17, revision: 0, boxes }, true);
  assert.equal(restored.boxes[7].text, "1.校对后的菜名");
  assert.equal(restored.text, instance.text);
  assert.equal(restored.dirty, true);
});

test("saving spatially displayed corrections keeps the original box index payload", async () => {
  let saved;
  let document = { id: 23, revision: 0, boxes: [
    { text: "12.00", box: [[220, 10], [300, 10], [300, 30], [220, 30]] },
    { text: "商品名", box: [[10, 10], [150, 10], [150, 30], [10, 30]] }
  ] };
  const h = setup({ request: options => {
    saved = options.data.texts;
    document = { ...document, revision: 1, boxes: document.boxes.map((b, i) => ({ ...b, text: saved[i] })) };
    options.success({ statusCode: 200, data: { document } });
  } });
  const { instance } = page("pages/result/result.vue", h);
  instance.documentBase = h.api.BASE;
  instance.applyDocument(document, false);
  assert.equal(instance.readingItems[0].index, 1);
  instance.editLine(instance.readingItems[0].index, { detail: { value: "校对商品" } });
  await instance.ensureSaved();
  assert.deepEqual(Array.from(saved), ["12.00", "校对商品"]);
  assert.equal(instance.boxes[1].text, "校对商品");
  assert.equal(instance.text, "校对商品  12.00");
  assert.equal(instance.dirty, false);
  assert.equal(instance.revision, 1);
});

test("saved backend address persists and every endpoint uses its latest value", async () => {
  const h = setup();
  assert.equal(h.api.BASE, "http://192.168.58.120:8000");
  assert.equal(typeof h.api.setBase, "function");
  assert.equal(h.api.setBase("  https://scanner.example:8443/  "), "https://scanner.example:8443");
  assert.equal(setup({}, h.storage).api.BASE, "https://scanner.example:8443");
  await h.api.health();
  await h.api.correct("/camera.jpg");
  await h.api.recognize("/camera.jpg");
  await h.api.history("发票");
  await h.api.toPDF("文本", []);
  await h.api.toExcel("文本", []);
  assert.equal(h.calls.length, 6);
  assert.ok(h.calls.every((call) => call.url.startsWith("https://scanner.example:8443/api/")));
  h.api.setBase("http://192.168.58.99:8000");
  await h.api.recognize("/camera.jpg");
  assert.equal(h.calls.at(-1).url, "http://192.168.58.99:8000/api/ocr/recognize");
});

test("invalid backend URLs cannot overwrite a saved address and reset restores the default", () => {
  const h = setup();
  assert.equal(typeof h.api.setBase, "function");
  h.api.setBase("http://localhost:8000");
  for (const address of ["", "ftp://host:8000", "http://", "http://user:pass@host", "http://host/path", "http://host?x=1", "http://host#x", "http://bad host", "http://host:0", "http://host:65536", "http://999.1.2.3:8000"]) {
    assert.throws(() => h.api.setBase(address), /地址|HTTP|http|端口/);
    assert.equal(h.api.BASE, "http://localhost:8000");
  }
  assert.equal(h.api.resetBase(), "http://192.168.58.120:8000");
  assert.equal(h.api.BASE, "http://192.168.58.120:8000");
});

test("503 OCR upload rejects with the backend model-load failure message", async () => {
  const h = setup({ uploadFile: (options) => options.success({ statusCode: 503, data: '{"ok":false,"msg":"识别模型加载失败"}' }) });
  await assert.rejects(h.api.recognize("/camera.jpg"), /识别模型加载失败/);
});

test("200 uploads with ok:false reject instead of becoming successful results", async () => {
  const h = setup({ uploadFile: (options) => options.success({ statusCode: 200, data: '{"ok":false,"msg":"图片解码失败"}' }) });
  await assert.rejects(h.api.correct("/camera.jpg"), /图片解码失败/);
  await assert.rejects(h.api.recognize("/camera.jpg"), /图片解码失败/);
});

test("invalid upload JSON is caught and rejects the promise", async () => {
  let callback;
  const h = setup({ uploadFile: (options) => { callback = options.success; } });
  const pending = h.api.recognize("/camera.jpg");
  const rejected = assert.rejects(pending, /JSON|返回/);
  assert.doesNotThrow(() => callback({ statusCode: 502, data: "<html>gateway failure</html>" }));
  await rejected;
});

test("request errors preserve a useful JSON message and model metadata is returned unchanged", async () => {
  const failure = setup({ request: (options) => options.success({ statusCode: 503, data: { ok: false, msg: "模型文件不存在" } }) });
  await assert.rejects(failure.api.health(), /模型文件不存在/);
  const h = setup();
  const result = await h.api.recognize("/camera.jpg");
  assert.equal(result.model.id, "rec_head_v1");
  assert.equal(result.model.sha256, "abcdef0123456789");
});

test("homepage reports model readiness instead of treating HTTP health alone as ready", async () => {
  const h = setup({ request: (options) => options.success({ statusCode: 200, data: { status: "ok", ocr: { ready: false, error: "模型校验失败" } } }) });
  const { instance } = page("pages/index/index.vue", h);
  await instance.check();
  assert.equal(instance.netOk, false);
  assert.match(instance.tip, /模型校验失败|模型未就绪/);
  h.uni.request = (options) => options.success({ statusCode: 200, data: { status: "ok", ocr: { ready: true, model: { id: "rec_head_v1", sha256: "abc" } } } });
  await instance.check();
  assert.equal(instance.netOk, true);
  assert.match(instance.tip, /rec_head_v1/);
});

test("real degraded health 503 surfaces ocr.error on the homepage", async () => {
  const h = setup({ request: (options) => options.success({ statusCode: 503, data: {
    status: "degraded", ocr: { ready: false, error: "识别模型SHA256校验失败" }
  } }) });
  await assert.rejects(h.api.health(), /识别模型SHA256校验失败/);
  const { instance } = page("pages/index/index.vue", h);
  await instance.check();
  assert.equal(instance.netOk, false);
  assert.match(instance.tip, /识别模型SHA256校验失败/);
});

test("homepage saves and resets the address through the real API", async () => {
  const h = setup();
  const { instance } = page("pages/index/index.vue", h);
  assert.equal(typeof instance.saveBase, "function");
  instance.baseInput = "http://192.168.58.33:8000/";
  await instance.saveBase();
  assert.equal(h.api.BASE, "http://192.168.58.33:8000");
  assert.equal(instance.base, h.api.BASE);
  await instance.resetBase();
  assert.equal(h.api.BASE, "http://192.168.58.120:8000");
});

test("a pending readiness check cannot race with a new camera scan", () => {
  let chosen = 0;
  const h = setup({ chooseImage: () => { chosen++; } });
  const { instance } = page("pages/index/index.vue", h);
  instance.checking = true;
  instance.choose("camera");
  assert.equal(chosen, 0);
});

for (const [method, sourceType, reason] of [
  ["shoot", "camera", "chooseImage:fail 当前运行的基座未包含原生插件 Camera"],
  ["pick", "album", "chooseImage:fail Permission denied: READ_MEDIA_IMAGES"]
]) {
  test(`${sourceType} picker surfaces native failure without uploading or opening results`, () => {
    let selectedSource;
    const h = setup({ chooseImage: (options) => {
      selectedSource = options.sourceType[0];
      if (options.fail) options.fail({ errMsg: reason });
    } });
    const { instance } = page("pages/index/index.vue", h);
    instance.tip = "后端及识别模型已就绪";
    instance[method]();
    assert.equal(selectedSource, sourceType);
    assert.ok(instance.tip.includes(reason), "the original native failure must be visible");
    assert.equal(h.calls.filter((call) => call.kind === "upload" || call.kind === "navigate").length, 0);
    assert.equal(h.storage.has("last_result"), false);
    assert.equal(instance.busy, false);
  });
}

test("canceling camera or album selection preserves the current status", () => {
  for (const reason of ["chooseImage:fail cancel", "chooseImage:fail 用户取消"]) {
    const h = setup({ chooseImage: (options) => {
      if (options.fail) options.fail({ errMsg: reason });
    } });
    const { instance } = page("pages/index/index.vue", h);
    instance.tip = "后端及识别模型已就绪";
    instance.shoot();
    instance.pick();
    assert.equal(instance.tip, "后端及识别模型已就绪");
    assert.equal(h.calls.length, 0);
    assert.equal(instance.busy, false);
  }
});

test("a synchronous native picker exception is shown without escaping the click handler", () => {
  const h = setup({ chooseImage: () => { throw new Error("Camera module is not registered"); } });
  const { instance } = page("pages/index/index.vue", h);
  assert.doesNotThrow(() => instance.shoot());
  assert.match(instance.tip, /Camera module is not registered/);
  assert.equal(h.calls.length, 0);
  assert.equal(instance.busy, false);
});

test("successful camera and album selection still pass the selected photo to scanning", () => {
  const h = setup({ chooseImage: (options) => {
    options.success({ tempFilePaths: [`/${options.sourceType[0]}.jpg`] });
  } });
  const { instance } = page("pages/index/index.vue", h);
  const photos = [];
  instance.scan = (file) => photos.push(file);
  instance.shoot();
  instance.pick();
  assert.deepEqual(photos, ["/camera.jpg", "/album.jpg"]);
});

test("recognition failure after downloading a corrected photo never opens the result page", async () => {
  const h = scannerHarness({ ocrError: true });
  const { instance } = page("pages/index/index.vue", h);
  assert.equal(typeof instance.scan, "function");
  await instance.scan("/camera.jpg");
  assert.equal(typeof instance.confirmRecognize, "function");
  await instance.confirmRecognize();
  assert.equal(h.calls.filter((call) => call.kind === "navigate").length, 0);
  assert.equal(h.storage.has("last_result"), false);
  assert.match(instance.tip, /识别模型加载失败/);
  assert.equal(instance.busy, false);
});

test("failed correction never uploads for OCR or navigates", async () => {
  let uploads = 0;
  const h = setup({ uploadFile: (options) => { uploads++; options.success({ statusCode: 200, data: '{"ok":false,"msg":"矫正失败"}' }); } });
  const { instance } = page("pages/index/index.vue", h);
  assert.equal(typeof instance.scan, "function");
  await instance.scan("/camera.jpg");
  assert.equal(uploads, 1);
  assert.equal(h.calls.filter((call) => call.kind === "navigate").length, 0);
  assert.match(instance.tip, /矫正失败/);
});

test("HTTP download failure stops extraction without silently recognizing the original photo", async () => {
  const h = scannerHarness({ downloadError: true });
  const { instance } = page("pages/index/index.vue", h);
  assert.equal(typeof instance.scan, "function");
  await instance.scan("/camera.jpg");
  assert.equal(typeof instance.confirmRecognize, "function");
  await instance.confirmRecognize();
  assert.equal(h.scanUploads.length, 1);
  assert.equal(h.storage.has("last_result"), false);
  assert.equal(h.calls.filter((call) => call.kind === "navigate").length, 0);
  assert.match(instance.tip, /下载失败/);
});

test("result page retains model provenance alongside the existing export controls", () => {
  const h = setup();
  h.storage.set("last_result", { text: "已识别", boxes: [], engine: "rapidocr-onnxruntime", model: { id: "rec_head_v1", sha256: "1234567890abcdef" } });
  const { instance, component } = page("pages/result/result.vue", h);
  component.onShow.call(instance);
  assert.equal(instance.model && instance.model.id, "rec_head_v1");
  assert.equal(instance.model.sha256, "1234567890abcdef");
  assert.equal(typeof instance.expPDF, "function");
  assert.equal(typeof instance.expExcel, "function");
  assert.equal(typeof instance.openLink, "function");
  assert.equal(instance.busy, false);
});

function documentHarness(options = {}, storage = new Map()) {
  let document = { id: 7, revision: 0, text: "原文\n待校对", image: "/uploads/scan_a.png", image_width: 1000, image_height: 2000,
    boxes: [{ text: "原文", score: 0.98, box: [[100, 200], [500, 200], [500, 250], [100, 250]] },
      { text: "待校对", score: 0.61, box: [[100, 300], [600, 300], [600, 350], [100, 350]] }], model: { id: "rec_head_v1", sha256: "abcdef" } };
  if (options.document) document = { ...document, ...options.document };
  const operations = [];
  const h = setup({ request: (request) => {
    operations.push(request);
    if (request.method === "PATCH") {
      if (options.saveError) return request.success({ statusCode: options.saveError, data: { ok: false, msg: "版本冲突，请重新打开文档" } });
      document = { ...document, revision: document.revision + 1,
        boxes: document.boxes.map((box, i) => ({ ...box, text: request.data.texts[i] })), text: request.data.texts.join("\n") };
      return request.success({ statusCode: 200, data: { ok: true, document } });
    }
    if (request.url.includes("/api/export/")) return request.success({ statusCode: 200, data: { ok: true, file: "/uploads/export.pdf" } });
    if (options.detailError) return request.success({ statusCode: 404, data: { ok: false, msg: "文档不存在" } });
    request.success({ statusCode: 200, data: { ok: true, document, item: document } });
  } }, storage);
  h.operations = operations;
  h.storage.set("last_result", JSON.parse(JSON.stringify(document)));
  return h;
}

async function openDocument(h, id) {
  const result = page("pages/result/result.vue", h);
  if (result.component.onLoad) await result.component.onLoad.call(result.instance, id ? { id: String(id) } : {});
  await result.component.onShow.call(result.instance);
  return result;
}

test("document API uses detail, revision-checked saves and export provenance", async () => {
  const h = documentHarness();
  assert.equal(typeof h.api.detail, "function");
  await h.api.detail(7);
  await h.api.saveDocument(7, ["校正", "待校对"], 0);
  await h.api.toPDF("校正", [], { scan_id: 7, revision: 1, mode: "searchable" });
  await h.api.toExcel("校正", [], { scan_id: 7, revision: 1 });
  assert.equal(h.operations[0].url, h.api.BASE + "/api/history/7");
  assert.equal(h.operations[1].method, "PATCH");
  assert.deepEqual(JSON.parse(JSON.stringify(h.operations[1].data)), { texts: ["校正", "待校对"], revision: 0 });
  assert.equal(h.operations[2].data.mode, "searchable");
  assert.equal(h.operations[2].data.revision, 1);
  assert.equal(h.operations[3].data.scan_id, 7);
});

test("line corrections update aggregated text, survive onShow and restore a local draft", async () => {
  const h = documentHarness();
  const { instance, component } = await openDocument(h);
  assert.equal(typeof instance.editLine, "function");
  instance.editLine(0, { detail: { value: "校正文字" } });
  assert.equal(instance.text, "校正文字\n待校对");
  assert.equal(instance.boxes[0].text, "校正文字");
  assert.equal(instance.dirty, true);
  await component.onShow.call(instance);
  assert.equal(instance.text, "校正文字\n待校对");
  const restored = await openDocument(documentHarness({}, h.storage), 7);
  assert.equal(restored.instance.boxes[0].text, "校正文字");
  assert.equal(restored.instance.dirty, true);
});

test("drafts are isolated by backend address and document id", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  assert.equal(typeof instance.editLine, "function");
  instance.editLine(0, { detail: { value: "本地草稿" } });
  h.api.setBase("http://192.168.58.99:8000");
  const other = await openDocument(documentHarness({}, h.storage), 7);
  assert.equal(other.instance.boxes[0].text, "原文");
  const another = documentHarness({ document: { id: 8 } }, h.storage);
  const opened = await openDocument(another, 8);
  assert.equal(opened.instance.boxes[0].text, "原文");
});

test("dirty PDF export saves first and exports the returned revision and edited boxes", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  assert.equal(typeof instance.editLine, "function");
  instance.editLine(0, { detail: { value: "已纠正" } });
  await instance.expPDF();
  assert.deepEqual(h.operations.map((request) => request.method), ["PATCH", "POST"]);
  assert.equal(h.operations[1].data.scan_id, 7);
  assert.equal(h.operations[1].data.revision, 1);
  assert.equal(h.operations[1].data.mode, "searchable");
  assert.equal(h.operations[1].data.boxes[0].text, "已纠正");
  assert.equal(instance.dirty, false);
  assert.ok(instance.fullUrl.endsWith("/uploads/export.pdf"));
});

test("Excel also saves corrections first and uses the authoritative document revision", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  assert.equal(typeof instance.editLine, "function");
  instance.editLine(1, { detail: { value: "第二行纠正" } });
  await instance.expExcel();
  assert.deepEqual(h.operations.map((request) => request.method), ["PATCH", "POST"]);
  assert.equal(h.operations[1].data.revision, 1);
  assert.equal(h.operations[1].data.boxes[1].text, "第二行纠正");
});

for (const saveError of [409, 503]) {
  test(`save failure ${saveError} retains the draft and never exports stale data`, async () => {
    const h = documentHarness({ saveError });
    const { instance } = await openDocument(h);
    assert.equal(typeof instance.editLine, "function");
    instance.editLine(0, { detail: { value: "不要丢失" } });
    await instance.expPDF();
    assert.equal(h.operations.length, 1);
    assert.equal(h.operations[0].method, "PATCH");
    assert.equal(instance.boxes[0].text, "不要丢失");
    assert.equal(instance.dirty, true);
    assert.match(instance.err, /版本冲突/);
    assert.equal(instance.fullUrl, "");
    assert.equal(instance.busy, false);
  });
}

test("history opens a specific document without replacing the last scan", async () => {
  const h = documentHarness();
  const original = JSON.stringify(h.storage.get("last_result"));
  const { instance } = page("pages/history/history.vue", h);
  assert.equal(typeof instance.open, "function");
  instance.open({ id: 7 });
  assert.equal(h.calls.at(-1).url, "/pages/result/result?id=7");
  assert.equal(JSON.stringify(h.storage.get("last_result")), original);
  const result = await openDocument(h, 7);
  assert.equal(result.instance.id, 7);
  assert.equal(h.operations[0].url, h.api.BASE + "/api/history/7");
});

test("detail errors show a clear error instead of a different last scan", async () => {
  const h = documentHarness({ detailError: true });
  const { instance } = await openDocument(h, 999);
  assert.match(instance.err, /文档不存在/);
  assert.equal(instance.id, null);
  assert.equal(instance.text, "");
});

test("old records without a scan image offer text PDF and keep raw box compatibility", async () => {
  const h = documentHarness({ document: { image: null, image_width: null, image_height: null } });
  const { instance } = await openDocument(h);
  await instance.expPDF();
  assert.equal(h.operations[0].data.mode, "text");
  assert.equal(instance.imageUrl, "");
  const template = fs.readFileSync(path.join(root, "pages/result/result.vue"), "utf8");
  assert.match(template, /旧记录.*扫描图/);
});

test("the selected line uses image coordinates and the scan opens in the native viewer", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  assert.equal(typeof instance.selectLine, "function");
  instance.selectLine(0);
  assert.match(instance.highlightStyle, /left:\s*10%/);
  assert.match(instance.highlightStyle, /top:\s*10%/);
  instance.previewScan();
  assert.equal(h.calls.at(-1).kind, "preview");
  assert.equal(h.calls.at(-1).urls[0], h.api.BASE + "/uploads/scan_a.png");
  assert.equal(instance.lowScoreCount, 1);
});

test("changing servers while editing blocks saving or exporting to another document", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  assert.equal(typeof instance.editLine, "function");
  instance.editLine(0, { detail: { value: "服务 A 草稿" } });
  h.api.setBase("http://192.168.58.99:8000");
  await instance.expExcel();
  assert.equal(h.operations.length, 0);
  assert.match(instance.err, /后端地址.*改变|后端地址.*变更/);
  assert.equal(instance.dirty, true);
});

test("reverting all edits removes the obsolete draft instead of restoring abandoned changes", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  instance.editLine(0, { detail: { value: "放弃的改动" } });
  instance.editLine(0, { detail: { value: "原文" } });
  assert.equal(instance.dirty, false);
  const reopened = await openDocument(documentHarness({}, h.storage), 7);
  assert.equal(reopened.instance.boxes[0].text, "原文");
  assert.equal(reopened.instance.dirty, false);
});

test("successful scans remember the source service before a backend address change", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/camera.jpg");
  assert.equal(typeof instance.confirmRecognize, "function");
  await instance.confirmRecognize();
  assert.equal(h.storage.get("last_result").backend_base, h.api.BASE);
  h.api.setBase("http://192.168.58.99:8000");
  const result = await openDocument(h);
  assert.match(result.instance.err, /后端地址已改变/);
  assert.equal(result.instance.id, null);
});

test("an invalid detail id never silently falls back to the previous scan", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h, "invalid");
  assert.match(instance.err, /文档编号/);
  assert.equal(instance.id, null);
  assert.equal(h.operations.length, 0);
});

test("saving a correction persists the returned revision and removes its local draft", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  instance.editLine(0, { detail: { value: "保存的校对" } });
  await instance.saveChanges();
  assert.equal(instance.revision, 1);
  assert.equal(instance.dirty, false);
  assert.equal(h.storage.get(instance.draftKey()), undefined);
  const reopened = await openDocument(h);
  assert.equal(reopened.instance.boxes[0].text, "保存的校对");
  assert.equal(reopened.instance.revision, 1);
});

test("a newer server revision does not silently apply an outdated local draft", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  instance.editLine(0, { detail: { value: "旧版本草稿" } });
  const newer = await openDocument(documentHarness({ document: { revision: 2 } }, h.storage), 7);
  assert.equal(newer.instance.boxes[0].text, "原文");
  assert.equal(newer.instance.dirty, false);
  assert.match(newer.instance.draftNotice, /旧版本草稿/);
  assert.equal(h.storage.get(instance.draftKey()).texts[0], "旧版本草稿");
});

test("history network failures are visible and leave no misleading stale results", async () => {
  const h = setup({ request: (options) => options.fail({ errMsg: "连接已断开" }) });
  const { instance } = page("pages/history/history.vue", h);
  instance.items = [{ id: 1 }];
  await instance.load();
  assert.match(instance.err, /连接已断开/);
  assert.equal(instance.items.length, 0);
  assert.equal(instance.loading, false);
});

test("copied aggregate text is always computed from the current line boxes", async () => {
  const h = documentHarness();
  const { instance } = await openDocument(h);
  instance.boxes[0].text = "当前逐行文字";
  instance.copy();
  assert.equal(h.calls.at(-1).data, "当前逐行文字\n待校对");
});

test("a legacy detail with JSON-encoded boxes and no image can still save corrections", async () => {
  const h = documentHarness({ document: { image: null } });
  const { instance } = await openDocument(h);
  instance.applyDocument({ id: 7, revision: 0, text: "原文\n待校对", boxes: JSON.stringify(instance.boxes) }, true);
  instance.editLine(0, { detail: { value: "旧记录已纠正" } });
  await instance.saveChanges();
  assert.equal(instance.dirty, false);
  assert.equal(instance.boxes[0].text, "旧记录已纠正");
  assert.equal(instance.revision, 1);
});

for (const method of ["expPDF", "expExcel"]) {
  test(`${method} rechecks the service after a pending save before issuing an export`, async () => {
    const h = documentHarness();
    const { instance } = await openDocument(h);
    instance.editLine(0, { detail: { value: "保存到服务 A" } });
    const saveDocument = h.api.saveDocument;
    let release;
    let notifySaved;
    const saved = new Promise((resolve) => { notifySaved = resolve; });
    h.api.saveDocument = async (...args) => {
      const response = await saveDocument(...args);
      await new Promise((resolve) => { release = resolve; notifySaved(); });
      return response;
    };
    const pending = instance[method]();
    await saved;
    h.api.setBase("http://192.168.58.99:8000");
    release();
    await pending;
    assert.equal(h.operations.length, 1, "only the save to service A may be sent");
    assert.equal(h.operations[0].method, "PATCH");
    assert.equal(h.operations[0].url, "http://192.168.58.120:8000/api/history/7");
    assert.equal(instance.revision, 1);
    assert.equal(instance.dirty, false);
    assert.match(instance.err, /后端地址已改变/);
    assert.equal(instance.fullUrl, "");
    assert.equal(instance.busy, false);
  });
}

function scannerHarness(options = {}) {
  const scanUploads = [], downloads = [];
  const h = setup({
    uploadFile: (request) => {
      scanUploads.push(request);
      if (request.url.endsWith("/correct")) {
        const quad = request.formData && request.formData.quad ? JSON.parse(request.formData.quad) : [[100,100],[900,100],[900,1900],[100,1900]];
        const response = { ok: true, has_quad: true, quad, image: "/uploads/processed.png", cropped_image: "/uploads/cropped.png",
          original_image: "/uploads/original.png", size: [1800,800], source_size: [2000,1000], mode: request.formData && request.formData.mode || "clean",
          detection: { confidence: 0.8, method: "contour" }, warnings: [], ...(request.formData && request.formData.quad ? {} : options.correction || {}) };
        if (options.correctHandler) return options.correctHandler(request, response);
        return request.success({ statusCode: 200, data: JSON.stringify(response) });
      }
      request.success({ statusCode: options.ocrError ? 503 : 200, data: JSON.stringify(options.ocrError ? { ok: false, msg: "识别模型加载失败" } : { ok: true, id: 7, text: "提取成功", boxes: [], model: { id: "rec_head_v1", sha256: "abc" } }) });
    },
    downloadFile: (request) => {
      downloads.push(request);
      if (options.downloadHandler) return options.downloadHandler(request);
      request.success({ statusCode: options.downloadError ? 404 : 200, tempFilePath: options.downloadError ? "/error.html" : "/tmp/processed.png" });
    },
    saveImageToPhotosAlbum: (request) => {
      if (options.albumError) request.fail({ errMsg: "saveImageToPhotosAlbum:fail auth deny" });
      else request.success({});
    },
    createSelectorQuery: () => {
      const query = { in: () => query, select: () => query, selectViewport: () => query,
        boundingClientRect: (callback) => { callback({ left: 10, top: 20, width: 200, height: 400 }); return query; },
        scrollOffset: (callback) => { callback({ scrollLeft: 0, scrollTop: 300 }); return query; },
        exec: (callback) => { if (callback) callback(); } };
      return query;
    }
  });
  h.scanUploads = scanUploads; h.downloads = downloads;
  return h;
}

test("scan correction uploads enhancement mode and original-pixel corner JSON", async () => {
  const h = scannerHarness();
  const quad = [[1,2],[900,2],[900,1900],[1,1900]];
  await h.api.correct("/photo.jpg", { mode: "bw", quad });
  assert.equal(h.scanUploads[0].formData.mode, "bw");
  assert.equal(h.scanUploads[0].formData.quad, JSON.stringify(quad));
  await h.api.correct("/photo.jpg");
  assert.equal(h.scanUploads[1].formData.mode, "clean");
});

test("taking a photo stops at scan preview until the user confirms extraction", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(h.scanUploads.length, 1);
  assert.equal(h.downloads.length, 0);
  assert.equal(h.calls.filter((call) => call.kind === "navigate").length, 0);
  assert.equal(instance.reviewReady, true);
  assert.equal(instance.canRecognize, true);
  await instance.confirmRecognize();
  assert.equal(h.scanUploads[1].filePath, "/tmp/processed.png");
  assert.equal(h.storage.get("last_result").model.id, "rec_head_v1");
});

test("changing effect marks the preview dirty and blocks extraction until reapplied", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(typeof instance.setMode, "function");
  instance.setMode("bw");
  assert.equal(instance.scanDirty, true);
  await instance.confirmRecognize();
  assert.equal(h.scanUploads.length, 1);
  await instance.applyScan();
  assert.equal(h.scanUploads[1].formData.mode, "bw");
  assert.equal(instance.canRecognize, true);
});

test("missing automatic boundaries require a manual apply or explicit whole-image confirmation", async () => {
  const h = scannerHarness({ correction: { has_quad: false, quad: null, warnings: ["未找到纸张边缘"] } });
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(instance.canRecognize, false);
  assert.equal(instance.corners.length, 4);
  await instance.confirmRecognize();
  assert.equal(h.scanUploads.length, 1);
  await instance.keepWhole();
  assert.equal(instance.boundaryChoice, "whole");
  assert.equal(instance.canRecognize, true);
  assert.deepEqual(JSON.parse(h.scanUploads[1].formData.quad), [[0,0],[999,0],[999,1999],[0,1999]]);
});

test("corner dragging maps measured CSS pixels into original image pixels", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(typeof instance.startCorner, "function");
  instance.startCorner(0, { touches: [{ clientX: 50, clientY: 100 }] });
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [200,400]);
  assert.equal(instance.scanDirty, true);
  instance.moveCorner(0, { touches: [{ clientX: -100, clientY: -100 }] });
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [0,0]);
});

test("corner edits reject crossed boundaries and re-taking clears old corners", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(typeof instance.setCorner, "function");
  instance.setCorner(0, 950, 1950);
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [100,100]);
  assert.match(instance.tip, /交叉|边界/);
  instance.setCorner(0, 200, 400);
  await instance.scan("/another.jpg");
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [100,100]);
  assert.equal(instance.sourceFile, "/another.jpg");
});

test("a backend change while scan correction is pending cannot publish a stale preview", async () => {
  let finish;
  const h = scannerHarness({ correctHandler: (request, response) => { finish = () => request.success({ statusCode: 200, data: JSON.stringify(response) }); } });
  const { instance } = page("pages/index/index.vue", h);
  const pending = instance.scan("/photo.jpg");
  h.api.setBase("http://192.168.58.99:8000"); finish(); await pending;
  assert.equal(instance.reviewReady, false);
  assert.match(instance.tip, /后端地址已改变/);
  assert.equal(h.scanUploads.length, 1);
});

test("a backend change during processed-image download blocks OCR and navigation", async () => {
  let finish;
  const h = scannerHarness({ downloadHandler: (request) => { finish = () => request.success({ statusCode: 200, tempFilePath: "/tmp/processed.png" }); } });
  const { instance } = page("pages/index/index.vue", h);
  assert.equal(typeof instance.confirmRecognize, "function");
  await instance.scan("/photo.jpg");
  const pending = instance.confirmRecognize();
  h.api.setBase("http://192.168.58.99:8000"); finish(); await pending;
  assert.equal(h.scanUploads.length, 1);
  assert.equal(h.storage.has("last_result"), false);
  assert.match(instance.tip, /后端地址已改变/);
});

test("saving the processed scan reports album permission failures without running OCR", async () => {
  const h = scannerHarness({ albumError: true });
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  assert.equal(typeof instance.saveScanImage, "function");
  await instance.saveScanImage();
  assert.match(instance.tip, /auth deny|权限/);
  assert.equal(h.scanUploads.length, 1);
  assert.equal(instance.busy, false);
});

test("a successful HTTP response that did not apply manual boundaries cannot enable extraction", async () => {
  const h = scannerHarness({ correctHandler: (request, response) => {
    if (request.formData.quad) { response.has_quad = false; response.quad = null; }
    request.success({ statusCode: 200, data: JSON.stringify(response) });
  } });
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  instance.setMode("bw"); await instance.applyScan();
  assert.equal(instance.scanDirty, true);
  assert.equal(instance.canRecognize, false);
  assert.match(instance.tip, /边界|裁切/);
  await instance.confirmRecognize();
  assert.equal(h.scanUploads.length, 2);
});

test("pending processing disables edits and reuses the original file and captured settings", async () => {
  let finish;
  const h = scannerHarness({ correctHandler: (request, response) => {
    if (request.formData.quad) finish = () => request.success({ statusCode: 200, data: JSON.stringify(response) });
    else request.success({ statusCode: 200, data: JSON.stringify(response) });
  } });
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg"); instance.setMode("bw");
  const original = JSON.stringify(instance.corners);
  const pending = instance.applyScan();
  instance.setMode("color"); instance.setCorner(0,200,400);
  assert.equal(instance.mode, "bw"); assert.equal(JSON.stringify(instance.corners), original);
  finish(); await pending;
  assert.equal(h.scanUploads[1].filePath, "/photo.jpg");
  assert.equal(h.scanUploads[1].formData.mode, "bw");
  assert.equal(instance.canRecognize, true);
});

test("backend switching during reprocessing leaves changes dirty and extraction blocked", async () => {
  let finish;
  const h = scannerHarness({ correctHandler: (request, response) => {
    if (request.formData.quad) finish = () => request.success({ statusCode: 200, data: JSON.stringify(response) });
    else request.success({ statusCode: 200, data: JSON.stringify(response) });
  } });
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg"); instance.setMode("bw");
  const pending = instance.applyScan(); h.api.setBase("http://192.168.58.99:8000"); finish(); await pending;
  assert.equal(instance.scanDirty, true); assert.equal(instance.canRecognize, false);
  assert.match(instance.tip, /后端地址已改变/);
});

test("backend switching while OCR is pending never stores or opens a result from another service", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  const recognize = h.api.recognize;
  let release, notify;
  const started = new Promise((resolve) => { notify = resolve; });
  h.api.recognize = async (...args) => {
    const result = await recognize(...args);
    await new Promise((resolve) => { release = resolve; notify(); });
    return result;
  };
  const pending = instance.confirmRecognize(); await started;
  h.api.setBase("http://192.168.58.99:8000"); release(); await pending;
  assert.equal(h.storage.has("last_result"), false);
  assert.equal(h.calls.filter((call) => call.kind === "navigate").length, 0);
  assert.match(instance.tip, /后端地址已改变/);
});

test("resetting automatic boundaries restores detection without silently applying it", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg"); instance.setCorner(0,200,400); instance.resetBoundary();
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [100,100]);
  assert.equal(instance.scanDirty, true); assert.equal(instance.canRecognize, false);
  assert.equal(h.scanUploads.length, 1);
});

test("page-coordinate-only touch events account for viewport scrolling when dragging corners", async () => {
  const h = scannerHarness();
  const { instance } = page("pages/index/index.vue", h);
  await instance.scan("/photo.jpg");
  instance.startCorner(0, { touches: [{ pageX: 50, pageY: 400 }] });
  assert.deepEqual(JSON.parse(JSON.stringify(instance.corners[0])), [200,400]);
});
