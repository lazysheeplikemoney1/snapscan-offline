<template>
  <view class="workbench">
    <view class="masthead">
      <text class="eyebrow">SNAPSCAN / DOCUMENT</text>
      <view class="heading-row"><text class="title">文档校对</text><text class="record">{{id ? '#' + id : '本次扫描'}}</text></view>
      <text class="subtitle">对照扫描图校对文字，汇总按原图同行组合。</text>
    </view>
    <view v-if="loading" class="notice">正在读取文档…</view>
    <view v-if="err" class="notice error"><text>{{err}}</text><button v-if="!id" size="mini" @click="retryLoad">重新读取</button></view>
    <view v-if="loaded && !loading && (id || text || image)" class="document">
      <view class="paper scan-panel">
        <view class="section-head"><text class="section-no">01</text><text class="section-title">扫描图</text><text class="section-note">点击放大</text></view>
        <view v-if="imageUrl" class="scan-frame" :style="imageFrameStyle" @click="previewScan">
          <image class="scan-image" :src="imageUrl" mode="scaleToFill" @error="imageFailed = true" />
          <view v-if="highlightStyle" class="highlight" :style="highlightStyle" />
        </view>
        <text v-if="imageFailed" class="notice error">扫描图加载失败，请检查网络；文字仍可校对。</text>
        <view v-if="!imageUrl" class="missing-image"><text class="missing-title">旧记录没有保存扫描图</text><text>可继续校对文字、导出纯文本 PDF。</text></view>
        <text v-if="imageUrl" class="caption">{{selectedIndex >= 0 ? '已定位第 ' + (selectedIndex + 1) + ' 项' : '选择下方文字，在扫描图中定位'}} · 原扫描图保持不变</text>
      </view>
      <view class="paper edit-panel">
        <view class="section-head"><text class="section-no">02</text><text class="section-title">逐项校对</text><text class="section-note">{{boxes.length}} 项</text></view>
        <view class="status-row"><text :class="dirty ? 'status unsaved' : 'status'">{{dirty ? '有未保存的校对' : '文字已保存'}}</text><text class="revision">版本 {{revision}}</text></view>
        <text class="edit-hint">改字只更新文字层，不会改变扫描图、行框或原表格外观。</text>
        <text v-if="lowScoreCount" class="quality">{{lowScoreCount}} 行识别分数低于 80%，建议对照扫描图核对。</text>
        <text v-if="warn" class="quality">{{warn}}</text>
        <text v-if="draftNotice" class="draft-note">{{draftNotice}}</text>
        <view v-if="!boxes.length" class="empty"><text class="missing-title">未识别到可校对的文字行</text><text>请检查是否模糊、反光或文字过小，再返回首页重拍。</text></view>
        <view v-for="entry in readingItems" :key="entry.index" :class="['line', {active: selectedIndex === entry.index}]" @click="selectLine(entry.index)">
          <view class="line-meta"><text class="line-number">{{String(entry.index + 1).padStart(2,'0')}}</text><text :class="['score', {low: isLowScore(entry.box)}]">{{scoreLabel(entry.box)}}</text></view>
          <textarea class="line-input" :value="entry.box.text" :disabled="busy || !id" :maxlength="-1" auto-height @focus="selectLine(entry.index)" @input="editLine(entry.index,$event)" />
        </view>
        <button class="save-button" :disabled="busy || !dirty || !id" :loading="busy" @click="saveChanges">{{dirty ? '保存校对' : '校对已保存'}}</button>
        <text v-if="!id" class="edit-hint">旧会话没有记录编号；重新扫描后可保存逐行校对。</text>
      </view>
      <view class="paper export-panel">
        <view class="section-head"><text class="section-no">03</text><text class="section-title">导出与复制</text></view>
        <button class="primary-button" :disabled="busy || !text" :loading="busy" @click="expPDF">{{imageUrl ? '保留版式 PDF' : '纯文本 PDF'}}</button>
        <view class="action-row"><button v-if="imageUrl" :disabled="busy || !text" @click="expTextPDF">纯文本 PDF</button><button :disabled="busy || !boxes.length" @click="expExcel">OCR 明细 Excel</button><button :disabled="!text" @click="copy">复制文字</button></view>
        <text class="edit-hint">保留版式 PDF 保持扫描图外观；纯文本 PDF 和复制文字按同行组合。Excel 是识别明细，不恢复表格单元格。</text>
        <view class="transcript"><text class="transcript-title">文字汇总 · {{textLayout.mode === 'spatial' ? '按原图同行组合' : '原识别顺序'}} · 只读</text><text class="transcript-text" selectable>{{text || '暂无文字'}}</text></view>
        <view v-if="fullUrl" class="download"><text class="download-title">文件已生成</text><text class="file">{{localEngine ? '文件已保存在本机，可打开或分享' : fullUrl}}</text><view class="action-row"><button v-if="localEngine" @click="shareFile">分享文件</button><button v-else @click="copyLink">复制链接</button><button @click="openLink">{{localEngine ? '打开文件' : '浏览器打开'}}</button></view></view>
      </view>
      <view class="provenance"><text v-if="model">模型 {{model.id || '版本未标识'}}<text v-if="model.sha256"> · {{model.sha256.slice(0,12)}}</text></text><text>{{engine}}<text v-if="elapsed"> · {{elapsed}} ms</text></text></view>
    </view>
    <view v-if="loaded && !loading && !id && !text && !image && !err" class="empty"><text class="missing-title">还没有扫描文档</text><text>返回首页拍照或从相册选择一张图片。</text></view>
  </view>
</template>
<script>
import { api } from "../../utils/api.js";
import { buildTextLayout } from "../../utils/text_layout.js";

function message(error) { return error && (error.message || error.errMsg) || "请求失败，请检查后端地址与网络"; }
function asArray(value) {
  if (Array.isArray(value)) return value;
  try { const parsed = JSON.parse(value || "[]"); return Array.isArray(parsed) ? parsed : []; } catch (e) { return []; }
}

export default {
  data: () => ({ localEngine: api.isNative, id: null, revision: 0, legacyText: "", boxes: [], savedTexts: [], image: "", imageWidth: 0, imageHeight: 0,
    documentBase: "", routeId: null, loaded: false, loading: false, dirty: false, selectedIndex: -1, imageFailed: false,
    elapsed: 0, engine: "", model: null, warn: "", draftNotice: "", file: "", fullUrl: "", err: "", busy: false }),
  computed: {
    textLayout() { return buildTextLayout(this.boxes); },
    readingItems() { return [].concat(...this.textLayout.rows).map(index => ({ index, box: this.boxes[index] })); },
    text() { return this.boxes.length ? this.textLayout.text : this.legacyText; },
    imageUrl() { return this.image ? this.documentBase + this.image : ""; },
    imageFrameStyle() { return "padding-top:" + (this.imageWidth > 0 && this.imageHeight > 0 ? this.imageHeight / this.imageWidth * 100 : 140) + "%;"; },
    lowScoreCount() { return this.boxes.filter((box) => this.isLowScore(box)).length; },
    highlightStyle() {
      const selected = this.boxes[this.selectedIndex];
      if (!selected || !Array.isArray(selected.box) || !selected.box.length || !this.imageWidth || !this.imageHeight) return "";
      const xs = selected.box.map((point) => Number(point[0])), ys = selected.box.map((point) => Number(point[1]));
      if (!xs.concat(ys).every((value) => Number.isFinite(value))) return "";
      const left = Math.max(0, Math.min(...xs)) / this.imageWidth * 100;
      const top = Math.max(0, Math.min(...ys)) / this.imageHeight * 100;
      return "left:" + left + "%;top:" + top + "%;width:" + (Math.max(...xs) - Math.min(...xs)) / this.imageWidth * 100 + "%;height:" + (Math.max(...ys) - Math.min(...ys)) / this.imageHeight * 100 + "%;";
    }
  },
  onLoad(options) { return this.loadDocument(options || {}); },
  onShow() { if (!this.loaded && !this.loading) return this.loadDocument({}); },
  onHide() { this.persistDraft(); },
  onUnload() { this.persistDraft(); },
  methods: {
    async loadDocument(options) {
      if (this.loading || this.busy) return;
      this.loading = true; this.err = ""; this.documentBase = api.BASE;
      this.routeId = options.id !== undefined ? options.id : null;
      try {
        let document;
        if (this.routeId !== null) {
          if (!Number.isSafeInteger(Number(this.routeId)) || Number(this.routeId) <= 0) throw new Error("文档编号无效，请从历史记录重新打开");
          this.routeId = Number(this.routeId);
          const response = await api.detail(this.routeId);
          document = response.document || response.item;
          if (!document) throw new Error("文档详情缺失");
        } else {
          document = uni.getStorageSync("last_result") || {};
          if (document.backend_base && document.backend_base !== this.documentBase) throw new Error("后端地址已改变，请从当前服务的历史记录重新打开文档");
        }
        this.applyDocument(document, true);
      } catch (error) { this.err = "读取失败：" + message(error); }
      finally { this.loading = false; this.loaded = true; }
    },
    retryLoad() { return this.loadDocument(this.routeId ? { id: this.routeId } : {}); },
    applyDocument(document, restoreDraft) {
      this.id = document.id || null; this.revision = Number(document.revision) || 0;
      this.boxes = asArray(document.boxes).map((box) => Object.assign({}, box, { text: String(box.text || "") }));
      this.savedTexts = this.boxes.map((box) => box.text);
      this.legacyText = document.text || "";
      this.image = document.image || ""; this.imageWidth = Number(document.image_width) || 0; this.imageHeight = Number(document.image_height) || 0;
      this.elapsed = document.elapsed_ms || 0; this.engine = document.engine || ""; this.model = document.model || null; this.warn = document.warn || "";
      this.dirty = false; this.imageFailed = false; this.draftNotice = "";
      if (restoreDraft && this.draftKey()) {
        try {
          const draft = uni.getStorageSync(this.draftKey());
          if (draft && Array.isArray(draft.texts)) {
            if (draft.revision === this.revision && draft.texts.length === this.boxes.length) {
              this.boxes = this.boxes.map((box, i) => Object.assign({}, box, { text: String(draft.texts[i]) }));
              this.refreshText(); this.draftNotice = "已恢复这份文档的本地校对草稿。";
            } else { this.draftNotice = "本地有旧版本草稿，已保留但未覆盖当前文档，请重新核对。"; }
          }
        } catch (error) { this.draftNotice = "本地草稿读取失败，请完成校对后及时保存。"; }
      }
    },
    draftKey() { return this.id ? "snapscan_draft:" + encodeURIComponent(this.documentBase) + ":" + this.id : ""; },
    persistDraft() {
      if (!this.dirty || !this.draftKey()) return;
      try { uni.setStorageSync(this.draftKey(), { revision: this.revision, texts: this.boxes.map((box) => box.text) }); }
      catch (error) { this.draftNotice = "本地草稿未能保存，请勿退出，连接后端后点保存校对。"; }
    },
    refreshText() {
      this.dirty = this.boxes.some((box, i) => box.text !== this.savedTexts[i]);
    },
    editLine(index, event) {
      if (this.busy || !this.boxes[index]) return;
      this.boxes[index].text = String(event.detail.value); this.refreshText();
      this.file = ""; this.fullUrl = "";
      if (this.dirty) this.persistDraft();
      else if (this.draftKey()) {
        try { uni.removeStorageSync(this.draftKey()); }
        catch (error) { this.draftNotice = "本地旧草稿未能清除，请在退出前保存校对。"; }
      }
    },
    selectLine(index) { this.selectedIndex = index; },
    isLowScore(box) { return typeof box.score === "number" && box.score < 0.8; },
    scoreLabel(box) { return typeof box.score === "number" ? Math.round(box.score * 100) + "%" : "旧记录"; },
    previewScan() { if (this.imageUrl) uni.previewImage({ current: this.imageUrl, urls: [this.imageUrl] }); },
    assertOrigin() { if (api.BASE !== this.documentBase) throw new Error("后端地址已改变，请返回历史记录重新打开文档；当前草稿已保留"); },
    async ensureSaved() {
      this.assertOrigin();
      if (!this.dirty) return;
      if (!this.id) throw new Error("旧会话没有文档编号，请重新扫描后保存校对");
      const response = await api.saveDocument(this.id, this.boxes.map((box) => box.text), this.revision);
      const document = response.document;
      if (!document || document.id !== this.id || !Array.isArray(document.boxes) || document.boxes.length !== this.boxes.length || Number(document.revision) <= this.revision) throw new Error("保存响应异常，草稿已保留，请重新打开文档核对");
      const key = this.draftKey();
      this.applyDocument(document, false);
      try {
        uni.removeStorageSync(key);
        const last = uni.getStorageSync("last_result");
        if (last && last.id === this.id && (!last.backend_base || last.backend_base === this.documentBase)) uni.setStorageSync("last_result", Object.assign({}, last, document, { backend_base: this.documentBase }));
      } catch (error) { this.draftNotice = "后端已保存，本地缓存未更新；重新进入时请从历史打开。"; }
    },
    async saveChanges() {
      if (this.busy) return;
      this.busy = true; this.err = "";
      try { await this.ensureSaved(); uni.showToast({ title: "校对已保存", icon: "success" }); }
      catch (error) { this.err = "保存失败：" + message(error); }
      finally { this.busy = false; }
    },
    expPDF() { return this.exportDocument("pdf", this.imageUrl ? "searchable" : "text"); },
    expTextPDF() { return this.exportDocument("pdf", "text"); },
    expExcel() { return this.exportDocument("excel"); },
    async exportDocument(kind, mode) {
      if (this.busy || this.loading) return;
      this.busy = true; this.err = ""; this.file = ""; this.fullUrl = "";
      uni.showLoading({ title: this.dirty ? "保存校对并导出" : "正在生成文件" });
      try {
        await this.ensureSaved();
        this.assertOrigin();
        const options = this.id ? { scan_id: this.id, revision: this.revision } : {};
        if (kind === "pdf") options.mode = mode;
        const response = kind === "pdf" ? await api.toPDF(this.text, this.boxes, options) : await api.toExcel(this.text, this.boxes, options);
        if (!response || !response.file) throw new Error("后端未返回导出文件");
        this.file = response.file; this.fullUrl = this.documentBase + response.file;
        uni.showToast({ title: "文件已生成", icon: "success" });
      } catch (error) { this.err = "导出失败：" + message(error); }
      finally { uni.hideLoading(); this.busy = false; }
    },
    copy() { uni.setClipboardData({ data: this.text }); },
    copyLink() { if (this.fullUrl) uni.setClipboardData({ data: this.fullUrl }); },
    async shareFile() {
      if (!this.fullUrl) return;
      try { await api.shareFile(this.fullUrl); } catch(error) { this.err="分享失败："+message(error); }
    },
    async openLink() {
      if (!this.fullUrl) return;
      if (api.isNative) { try { await api.openFile(this.fullUrl); } catch(error) { this.err="打开失败："+message(error); } return; }
      try {
        // #ifdef APP-PLUS
        plus.runtime.openURL(this.fullUrl);
        // #endif
        // #ifndef APP-PLUS
        uni.setClipboardData({ data: this.fullUrl });
        uni.showToast({ title: "链接已复制，用浏览器打开", icon: "none" });
        // #endif
      } catch (error) { uni.setClipboardData({ data: this.fullUrl }); }
    }
  }
};
</script>
<style>
.workbench{min-height:100vh;padding:38rpx 26rpx 64rpx;background:#f2efe7;color:#253d39;box-sizing:border-box;}
.masthead{padding:8rpx 4rpx 32rpx;border-bottom:2rpx solid #cad0c4;margin-bottom:26rpx;}
.eyebrow{font-size:19rpx;letter-spacing:5rpx;color:#63786b;display:block;}.heading-row{display:flex;justify-content:space-between;align-items:center;margin:18rpx 0 10rpx;}
.title{font-size:54rpx;font-weight:700;font-family:Georgia,'Noto Serif SC','Songti SC',serif;}.record{font-size:23rpx;color:#68796d;border:1rpx solid #b9c5b8;padding:7rpx 15rpx;border-radius:30rpx;}
.subtitle{font-size:25rpx;color:#6b776e;}.paper{background:#fffdf7;border:1rpx solid #d8dacf;border-radius:8rpx;margin-bottom:24rpx;padding:24rpx;box-shadow:0 5rpx 18rpx rgba(44,61,47,.035);}
.section-head{display:flex;align-items:center;gap:14rpx;margin-bottom:22rpx;}.section-no{font-size:22rpx;color:#7d8f7f;font-family:Georgia,serif;}.section-title{font-size:31rpx;font-weight:700;}.section-note{margin-left:auto;font-size:22rpx;color:#788379;}
.scan-frame{position:relative;width:100%;height:0;background:#e6e5dc;overflow:hidden;}.scan-image{position:absolute;top:0;left:0;width:100%;height:100%;}.highlight{position:absolute;box-sizing:border-box;border:3rpx solid #087866;background:rgba(8,120,102,.14);pointer-events:none;}
.caption{font-size:21rpx;color:#6c7b70;margin-top:16rpx;display:block;}.missing-image,.empty{display:flex;flex-direction:column;gap:12rpx;padding:38rpx 16rpx;color:#798276;font-size:24rpx;line-height:1.7;}.missing-title{font-size:29rpx;font-weight:700;color:#526552;}
.status-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:14rpx;}.status{font-size:22rpx;color:#217568;background:#e7f0e7;border-radius:5rpx;padding:6rpx 12rpx;}.unsaved{background:#f8e9ce;color:#966419;}.revision{font-size:21rpx;color:#819080;}
.edit-hint{font-size:22rpx;color:#7c8377;line-height:1.7;display:block;margin:12rpx 0;}.quality,.draft-note{display:block;font-size:23rpx;line-height:1.6;padding:15rpx 18rpx;margin:18rpx 0;background:#f6edd8;color:#866128;border-left:4rpx solid #bb913e;}.draft-note{background:#eaf1e7;border-color:#658a68;color:#4e7354;}
.line{display:flex;gap:18rpx;border-top:1rpx solid #e8e7dd;padding:19rpx 8rpx;}.line.active{background:#edf4eb;border-left:4rpx solid #087866;padding-left:12rpx;}.line-meta{width:65rpx;flex-shrink:0;display:flex;flex-direction:column;gap:10rpx;padding-top:5rpx;}.line-number{font-family:Georgia,serif;font-size:24rpx;color:#5c7162;}.score{font-size:19rpx;color:#788979;}.score.low{color:#9c6f26;}.line-input{flex:1;width:0;min-height:52rpx;font-size:29rpx;line-height:1.7;color:#253d39;padding:0;box-sizing:border-box;}
.save-button,.primary-button{background:#116d5d;color:#fffdf7;font-size:27rpx;border-radius:7rpx;margin-top:20rpx;}.save-button[disabled],.primary-button[disabled]{background:#dce2d7;color:#8a978a;}.action-row{display:flex;gap:10rpx;margin-top:14rpx;flex-wrap:wrap;}.action-row button{font-size:23rpx;background:#edf0e6;color:#335d4f;flex:1;min-width:135rpx;padding:0 12rpx;border-radius:6rpx;}.action-row button[disabled]{color:#a1aaa0;}
.transcript{border-top:1rpx solid #dddfd1;margin-top:26rpx;padding-top:22rpx;}.transcript-title{font-size:21rpx;letter-spacing:2rpx;color:#7a8978;display:block;margin-bottom:16rpx;}.transcript-text{white-space:pre-wrap;font-size:25rpx;line-height:1.9;color:#647265;display:block;}.download{background:#e8f1e7;padding:22rpx;margin-top:24rpx;}.download-title{font-size:27rpx;color:#216f56;font-weight:700;display:block;}.file{font-size:21rpx;word-break:break-all;display:block;margin-top:12rpx;}.notice{display:block;font-size:25rpx;line-height:1.7;padding:22rpx;background:#e3eadd;margin-bottom:22rpx;}.error{background:#f7e7dd;color:#9b493a;}.provenance{font-size:20rpx;line-height:1.9;color:#849080;display:flex;flex-direction:column;padding:8rpx 6rpx;}
@media (min-width:850px){.workbench{padding:48rpx 48rpx 80rpx;}.document{display:flex;flex-wrap:wrap;align-items:flex-start;gap:26rpx;}.scan-panel{width:calc(47% - 39rpx);}.edit-panel{width:calc(53% - 87rpx);}.export-panel{width:100%;}.provenance{width:100%;}.masthead{max-width:1500px;}.scan-panel{position:sticky;top:24rpx;}}
</style>
