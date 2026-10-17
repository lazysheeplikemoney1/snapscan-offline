// 后端地址可在首页修改；换 WiFi 后无需再次打包。
const DEFAULT_BASE = "http://192.168.58.120:8000";
const BASE_STORAGE_KEY = "snapscan_backend_base";
const LOCAL_BASE = "https://appassets.androidplatform.net";
const nativePending = new Map();
let nativeSequence = 0;
function nativeAvailable() { return typeof window !== "undefined" && window.SnapScanNative && typeof window.SnapScanNative.request === "function"; }
function isNative() { return !!(nativeAvailable() || (typeof window !== "undefined" && window.location && window.location.origin === LOCAL_BASE)); }
function local(operation, payload = {}) {
  if (!nativeAvailable()) return Promise.reject(new Error("手机本地引擎不可用，请重新打开离线版 APK"));
  window.__snapscanNativeResult = (id, response) => {
    const pending = nativePending.get(String(id));
    if (!pending) return;
    nativePending.delete(String(id)); clearTimeout(pending.timer);
    try { pending.resolve(responseData(response)); } catch (error) { pending.reject(error); }
  };
  return new Promise((resolve, reject) => {
    const id = String(++nativeSequence);
    const timer = setTimeout(() => { nativePending.delete(id); reject(new Error("本地操作超时，请重试；不会转发到电脑或云端")); }, operation === "chooseImage" ? 600000 : 180000);
    nativePending.set(id, {resolve,reject,timer});
    try { window.SnapScanNative.request(id, operation, JSON.stringify(payload)); }
    catch (error) { clearTimeout(timer); nativePending.delete(id); reject(error); }
  });
}

function normalizeBase(value) {
  const match = /^(https?):\/\/(\[[0-9a-f:]+\]|[^/?#:\s@]+)(?::([0-9]{1,5}))?\/?$/i.exec(String(value || "").trim());
  if (!match) throw new Error("请输入有效的 HTTP/HTTPS 后端地址，例如 http://192.168.58.120:8000");
  const host = match[2];
  if (host[0] === "[") {
    if (host.indexOf(":") < 0) throw new Error("后端地址无效");
  } else {
    const labels = host.split(".");
    if (labels.some((label) => !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label) || label.length > 63)) {
      throw new Error("后端地址的主机名无效");
    }
    if (/^[0-9.]+$/.test(host) && (labels.length !== 4 || labels.some((part) => Number(part) > 255))) {
      throw new Error("后端地址的 IPv4 地址无效");
    }
  }
  if (match[3] && (Number(match[3]) < 1 || Number(match[3]) > 65535)) throw new Error("后端端口应为 1 到 65535");
  return match[1].toLowerCase() + "://" + host + (match[3] ? ":" + Number(match[3]) : "");
}

function getBase() {
  if (isNative()) return LOCAL_BASE;
  try {
    const saved = uni.getStorageSync(BASE_STORAGE_KEY);
    return saved ? normalizeBase(saved) : DEFAULT_BASE;
  } catch (e) {
    return DEFAULT_BASE;
  }
}

function responseData(res) {
  let data = res.data;
  try {
    if (typeof data === "string") data = JSON.parse(data);
  } catch (e) {
    throw new Error("后端返回无效 JSON，请检查后端地址或服务状态");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("后端返回无效 JSON");
  if (res.statusCode < 200 || res.statusCode >= 300 || data.ok === false) {
    const detail = data.msg || (data.ocr && data.ocr.error) || data.detail || "后端请求失败（HTTP " + res.statusCode + "）";
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.statusCode = res.statusCode;
    error.data = data;
    throw error;
  }
  return data;
}

function req(url, opt = {}) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: getBase() + url,
      method: opt.method || "GET",
      data: opt.data,
      header: Object.assign({ "Content-Type": "application/json" }, opt.header || {}),
      timeout: 30000,
      success: (res) => {
        try {
          res.data = responseData(res);
          resolve(res);
        } catch (e) {
          reject(e);
        }
      },
      fail: (e) => reject(e)
    });
  });
}

function upload(url, filePath, formData = {}) {
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: getBase() + url, filePath, formData, name: "file", timeout: 60000,
      success: (res) => {
        try { resolve(responseData(res)); }
        catch (e) { reject(e); }
      },
      fail: reject
    });
  });
}

export const api = {
  get isNative() { return !!isNative(); },
  get BASE() { return getBase(); },
  DEFAULT_BASE,
  setBase(value) {
    if (isNative()) throw new Error("离线版固定使用手机本地引擎，无需设置电脑地址");
    const base = normalizeBase(value);
    uni.setStorageSync(BASE_STORAGE_KEY, base);
    return base;
  },
  resetBase() {
    if (isNative()) return LOCAL_BASE;
    uni.removeStorageSync(BASE_STORAGE_KEY);
    return DEFAULT_BASE;
  },
  async health() {
    if (isNative()) return local("health");
    const r = await req("/api/health");
    return r.data;
  },
  correct(filePath, options = {}) {
    if (isNative()) return local("correct", {filePath, mode:options.mode || "clean", quad:options.quad || null});
    const formData = { mode: options.mode || "clean" };
    if (options.quad) formData.quad = typeof options.quad === "string" ? options.quad : JSON.stringify(options.quad);
    return upload("/api/scan/correct", filePath, formData);
  },
  recognize(filePath) {
    if (isNative()) return local("recognize", {filePath});
    return upload("/api/ocr/recognize", filePath);
  },
  async history(q = "") {
    if (isNative()) return local("history", {q});
    const url = q ? ("/api/history/search?q=" + encodeURIComponent(q)) : "/api/history/list";
    const r = await req(url);
    return r.data;
  },
  async detail(id) {
    if (isNative()) return local("detail", {id});
    const r = await req("/api/history/" + encodeURIComponent(id));
    return r.data;
  },
  async saveDocument(id, texts, revision) {
    if (isNative()) return local("saveDocument", {id,texts,revision});
    const r = await req("/api/history/" + encodeURIComponent(id), { method: "PATCH", data: { texts, revision } });
    return r.data;
  },
  async toPDF(text, boxes = [], options = {}) {
    if (isNative()) return local("toPDF", Object.assign({text,boxes},options));
    const r = await req("/api/export/pdf", { method: "POST", data: Object.assign({ text, boxes }, options) });
    return r.data;
  },
  async toExcel(text, boxes = [], options = {}) {
    if (isNative()) return local("toExcel", Object.assign({text,boxes},options));
    const r = await req("/api/export/excel", { method: "POST", data: Object.assign({ text, boxes }, options) });
    return r.data;
  },
  chooseImage(source) { return local("chooseImage", {source}); },
  async localFile(filePath) { await local("localFile", {filePath}); return filePath; },
  saveImage(filePath) { return local("saveImage", {filePath}); },
  openFile(filePath) { return local("openFile", {filePath}); },
  shareFile(filePath) { return local("shareFile", {filePath}); }
};
