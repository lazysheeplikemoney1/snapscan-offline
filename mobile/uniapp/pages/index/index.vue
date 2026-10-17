<template>
  <view class="scanner">
    <view class="masthead"><text class="eyebrow">SNAPSCAN / PAPER STUDIO</text><view class="title-row"><text class="title">纸面扫描</text><button class="archive-link" @click="goHistory">文档档案 →</button></view><text class="subtitle">先把纸张留下，再把文字提取出来。</text></view>
    <view class="capture-row"><button class="primary" :disabled="busy || checking" @click="shoot">{{sourceFile ? '重新拍照' : '拍照扫描'}}</button><button class="secondary" :disabled="busy || checking" @click="pick">从相册选</button></view>
    <view v-if="!sourceFile" class="welcome"><view class="paper-mark"><view/><view/><view/></view><text class="welcome-title">让小票回到纸张的样子</text><text>拍下完整纸张与四周背景，边缘清晰时裁切更准确。</text><text class="small">{{localEngine ? '模型和扫描引擎已内置手机，飞行模式也可使用。' : '手机与电脑在同一局域网即可使用，无需连接互联网。'}}</text></view>
    <view v-if="sourceFile && !reviewReady" class="paper"><image :src="sourceFile" class="effect-image" mode="widthFix"/><text class="hint">{{busy ? '正在寻找纸张边缘并生成扫描效果…' : '扫描效果尚未生成，请重选照片重试。'}}</text></view>
    <view v-if="reviewReady" class="review">
      <view class="paper image-panel">
        <view class="section-head"><text class="section-no">01</text><text class="section-title">查看扫描</text><text class="state">{{scanDirty ? '待应用修改' : boundaryLabel}}</text></view>
        <view class="tabs"><button :class="{selected: previewTab === 'original'}" :disabled="busy" @click="showOriginal">原图 / 调整边界</button><button :class="{selected: previewTab === 'effect'}" :disabled="busy" @click="previewTab = 'effect'">扫描效果</button></view>
        <view v-if="previewTab === 'original'" id="source-canvas" class="source-frame" :style="sourceFrameStyle">
          <image class="source-image" :src="sourceUrl" mode="scaleToFill" @load="measureSource"/>
          <view v-for="(edge,i) in edgeStyles" :key="'edge'+i" class="crop-edge" :style="edge"/>
          <view v-for="(point,i) in corners" :key="i" :class="['corner', {dragging: activeCorner === i}]" :style="cornerStyle(point)" @touchstart.stop="startCorner(i,$event)" @touchmove.stop.prevent="moveCorner(i,$event)" @touchend.stop="endCorner" @touchcancel.stop="endCorner"><text>{{i+1}}</text></view>
        </view>
        <image v-else :src="processedUrl" class="effect-image" mode="widthFix" @click="previewProcessed"/>
        <text class="caption">{{previewTab === 'original' ? '拖动四角贴合纸张；1 左上 · 2 右上 · 3 右下 · 4 左下' : '点击扫描效果可放大查看。确认边缘与文字完整后再提取。'}}</text>
        <text v-if="!boundaryConfirmed" class="notice">尚未确认纸张边缘。当前预览仍包含背景，请手动调四角并应用，或明确选择保留整图。</text>
        <text v-for="(warning,i) in warnings" :key="'warning'+i" class="warning">{{warning}}</text>
        <text v-if="scanDirty" class="notice">边界或效果已修改，当前扫描效果尚未更新。请先“应用裁切与效果”。</text>
      </view>
      <view class="paper controls">
        <view class="section-head"><text class="section-no">02</text><text class="section-title">裁切与纸色</text></view>
        <view class="mode-row"><button v-for="item in modes" :key="item.value" :class="['mode', {selected: mode === item.value}]" :disabled="busy" @click="setMode(item.value)"><text class="mode-title">{{item.label}}</text><text class="mode-note">{{item.note}}</text></button></view>
        <view class="boundary-actions"><button :disabled="busy" @click="resetBoundary">重置自动边界</button><button :disabled="busy" @click="keepWhole">保留整图</button></view>
        <view class="coordinate-head" @click="coordinatesOpen = !coordinatesOpen"><text>精确调整四角坐标</text><text>{{coordinatesOpen ? '收起 −' : '展开 + '}}</text></view>
        <view v-if="coordinatesOpen" class="coordinate-grid"><view v-for="(point,i) in corners" :key="'coord'+i" class="coordinate"><text class="corner-name">{{cornerNames[i]}}</text><view class="coordinate-inputs"><text>X</text><input type="number" :value="Math.round(point[0])" :disabled="busy" @blur="inputCorner(i,0,$event)"/><text>Y</text><input type="number" :value="Math.round(point[1])" :disabled="busy" @blur="inputCorner(i,1,$event)"/></view></view></view>
        <button class="apply-button" :loading="busy" :disabled="busy || !scanDirty" @click="applyScan">应用裁切与效果</button>
        <text class="hint">裁切会拉正纸张；边界没有包住的文字将被裁掉。请保留完整四角。</text>
      </view>
      <view class="paper confirm-panel"><view class="section-head"><text class="section-no">03</text><text class="section-title">确认扫描结果</text></view><button class="primary" :loading="busy" :disabled="!canRecognize" @click="confirmRecognize">确认并提取文字</button><button class="save-image" :disabled="!canRecognize" @click="saveScanImage">保存扫描图到相册</button><text class="hint">只识别当前已应用的扫描效果。扫描图与提取的文字都会保留在校对流程中。</text></view>
    </view>
    <text v-if="tip" class="tip">{{tip}}</text>
    <view class="connection"><view class="connection-head" @click="settingsOpen = !settingsOpen"><view><text :class="['dot', {ready: netOk}]">●</text><text>{{localEngine ? '手机本地引擎' : '局域网后端'}} · {{netOk ? '识别已就绪' : '待检测'}}</text></view><text v-if="!localEngine">{{settingsOpen ? '收起 −' : '设置 +'}}</text><text v-else>完全离线</text></view><view v-if="settingsOpen && !localEngine" class="connection-body"><text class="base">{{base}}</text><input v-model="baseInput" class="address" :disabled="busy || checking" placeholder="http://电脑IP:8000" :maxlength="200" @confirm="saveBase"/><view class="connection-actions"><button :disabled="busy || checking" @click="saveBase">保存地址</button><button :disabled="busy || checking" @click="resetBase">恢复默认</button><button :loading="checking" :disabled="busy || checking" @click="check">检测连接</button></view></view></view>
  </view>
</template>
<script>
import { api } from "../../utils/api.js";
function errorMessage(error) { return error && (error.message || error.errMsg || (error.data && error.data.msg)) || "请求失败，检查后端地址和网络"; }
function copyQuad(quad) { return quad.map((point) => [Number(point[0]), Number(point[1])]); }
function validQuad(quad) {
  if (!Array.isArray(quad) || quad.length !== 4 || quad.some((p) => !Array.isArray(p) || p.length !== 2 || !p.every(Number.isFinite))) return false;
  for (let i=0;i<4;i++) {
    const a=quad[i], b=quad[(i+1)%4], c=quad[(i+2)%4];
    if ((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]) <= 1) return false;
  }
  return true;
}
export default {
  data: () => ({ localEngine: api.isNative, tip: "", netOk: false, base: api.BASE, baseInput: api.BASE, busy: false, checking: false, settingsOpen: false,
    sourceFile: "", scanBase: "", originalImage: "", processedImage: "", sourceWidth: 0, sourceHeight: 0, sourceRect: null, sourceScroll: {left:0,top:0},
    reviewReady: false, scanDirty: false, boundaryConfirmed: false, boundaryChoice: "manual", autoQuad: [], corners: [], activeCorner: -1,
    mode: "clean", previewTab: "effect", warnings: [], detection: null, coordinatesOpen: false,
    cornerNames: ["1 · 左上", "2 · 右上", "3 · 右下", "4 · 左下"],
    modes: [{value:"color",label:"原色",note:"保留颜色"},{value:"clean",label:"净白",note:"清理纸色"},{value:"bw",label:"黑白",note:"突出文字"}] }),
  computed: {
    sourceUrl() { return this.originalImage ? this.scanBase + this.originalImage : this.sourceFile; },
    processedUrl() { return this.processedImage ? this.scanBase + this.processedImage : ""; },
    sourceFrameStyle() { return "padding-top:" + (this.sourceWidth ? this.sourceHeight/this.sourceWidth*100 : 140) + "%;"; },
    canRecognize() { return !!(this.reviewReady && this.boundaryConfirmed && !this.scanDirty && this.processedImage && !this.busy && !this.checking); },
    boundaryLabel() { return this.boundaryChoice === "whole" ? "保留整图" : this.boundaryConfirmed ? (this.boundaryChoice === "auto" ? "自动裁切" : "手动裁切") : "边界待确认"; },
    edgeStyles() {
      if (!this.sourceWidth || !this.sourceHeight) return [];
      return this.corners.map((a,i) => {
        const b=this.corners[(i+1)%4], dx=b[0]-a[0], dy=b[1]-a[1];
        return "left:"+a[0]/this.sourceWidth*100+"%;top:"+a[1]/this.sourceHeight*100+"%;width:"+Math.sqrt(dx*dx+dy*dy)/this.sourceWidth*100+"%;transform:rotate("+Math.atan2(dy,dx)*180/Math.PI+"deg);";
      });
    }
  },
  methods: {
    async check() {
      if (this.busy || this.checking) return;
      this.base=api.BASE; this.netOk=false; this.checking=true; this.tip=this.localEngine?"正在加载手机内置模型…":"检测后端及识别模型…";
      try {
        const r=await api.health();
        if (r.status!=="ok") throw new Error(r.msg||"后端状态异常");
        if (!r.ocr || r.ocr.ready!==true) throw new Error(r.ocr && (r.ocr.error||r.ocr.msg)||"识别模型未就绪");
        this.netOk=true; this.tip=(this.localEngine?"手机本地模型已就绪（":"后端及识别模型已就绪（")+((r.ocr.model||{}).id||"版本未标识")+"）";
      } catch(error) { this.netOk=false; this.tip=(this.localEngine?"本地引擎未就绪：":"后端未就绪：")+errorMessage(error); }
      finally { this.checking=false; }
    },
    async saveBase() {
      if (this.busy || this.checking) return;
      try { this.base=api.setBase(this.baseInput); this.baseInput=this.base; await this.check(); if (this.reviewReady && this.scanBase!==api.BASE) this.tip="后端地址已改变，请重新选择照片生成扫描效果"; }
      catch(error) { this.tip=errorMessage(error); }
    },
    async resetBase() {
      if (this.busy || this.checking) return;
      try { this.base=api.resetBase(); this.baseInput=this.base; await this.check(); if (this.reviewReady && this.scanBase!==api.BASE) this.tip="后端地址已改变，请重新选择照片生成扫描效果"; }
      catch(error) { this.tip=errorMessage(error); }
    },
    shoot() { this.choose("camera"); },
    pick() { this.choose("album"); },
    choose(sourceType) {
      if (this.busy || this.checking) return;
      const fail=(error) => { const text=errorMessage(error); if (!/cancel|取消/i.test(text)) this.tip=(sourceType==="camera"?"拍照失败：":"选择图片失败：")+text; };
      if (api.isNative) return api.chooseImage(sourceType).then((res)=>{ if (res.filePath) return this.scan(res.filePath); throw new Error("没有取得照片，请重试"); }).catch(fail);
      try { uni.chooseImage({ count:1, sourceType:[sourceType], success:(res) => { const fp=res.tempFilePaths && res.tempFilePaths[0]; if (fp) this.scan(fp); else fail(new Error("没有取得照片，请重试")); }, fail }); }
      catch(error) { fail(error); }
    },
    assertScanOrigin() { if (api.BASE!==this.scanBase) throw new Error("后端地址已改变，请重新选择照片；本次操作已停止"); },
    fullQuad(inset=0) {
      const x=Math.round((this.sourceWidth-1)*inset), y=Math.round((this.sourceHeight-1)*inset);
      return [[x,y],[this.sourceWidth-1-x,y],[this.sourceWidth-1-x,this.sourceHeight-1-y],[x,this.sourceHeight-1-y]];
    },
    acceptResponse(response) {
      if (!response || response.ok!==true || !response.image || !response.original_image || !Array.isArray(response.source_size) || response.source_size.length!==2 || !response.source_size.every((n) => Number.isFinite(n) && n>1)) throw new Error(response && response.msg || "扫描服务未返回完整图像和原图尺寸，请更新后端后重试");
      this.originalImage=response.original_image; this.processedImage=response.image;
      this.sourceHeight=response.source_size[0]; this.sourceWidth=response.source_size[1];
      this.warnings=Array.isArray(response.warnings)?response.warnings:[]; this.detection=response.detection||null;
      uni.setStorageSync("last_correct", Object.assign({},response,{backend_base:this.scanBase}));
    },
    async scan(fp) {
      if (this.busy) return;
      this.busy=true; this.tip="正在寻找纸张边缘并生成扫描效果…";
      this.sourceFile=fp; this.scanBase=api.BASE; this.reviewReady=false; this.scanDirty=false; this.boundaryConfirmed=false;
      this.originalImage=""; this.processedImage=""; this.sourceWidth=0; this.sourceHeight=0; this.sourceRect=null; this.autoQuad=[]; this.corners=[]; this.activeCorner=-1; this.mode="clean"; this.warnings=[]; this.coordinatesOpen=false;
      try {
        this.assertScanOrigin(); const response=await api.correct(fp,{mode:this.mode}); this.assertScanOrigin();
        this.acceptResponse(response);
        const detected=response.has_quad===true && validQuad(response.quad);
        this.autoQuad=detected?copyQuad(response.quad):[]; this.corners=detected?copyQuad(response.quad):this.fullQuad(0.03);
        this.boundaryConfirmed=detected; this.boundaryChoice=detected?"auto":"manual"; this.scanDirty=!detected;
        this.reviewReady=true; this.previewTab=detected?"effect":"original";
        this.tip=detected?"扫描效果已生成。请检查边缘和文字，再确认提取。":"未检测到可靠纸张边缘，请手动调四角并应用，或选择保留整图。";
      } catch(error) { this.tip="扫描失败："+errorMessage(error); }
      finally { this.busy=false; }
    },
    setMode(mode) { if (this.busy || !["color","clean","bw"].includes(mode) || mode===this.mode) return; this.mode=mode; this.scanDirty=true; },
    showOriginal() { this.previewTab="original"; if (this.$nextTick) this.$nextTick(()=>this.measureSource()); else this.measureSource(); },
    cornerStyle(point) { return "left:"+point[0]/this.sourceWidth*100+"%;top:"+point[1]/this.sourceHeight*100+"%;"; },
    measureSource(callback) {
      if (typeof uni.createSelectorQuery!=="function") return;
      const query=uni.createSelectorQuery().in(this);
      let rect=null, scroll=null;
      query.select("#source-canvas").boundingClientRect((value)=>{rect=value;});
      if (typeof query.selectViewport==="function") query.selectViewport().scrollOffset((value)=>{scroll=value;});
      query.exec(()=>{
        if (rect && rect.width>0 && rect.height>0) {
          this.sourceRect=rect; this.sourceScroll={left:scroll && scroll.scrollLeft || 0,top:scroll && scroll.scrollTop || 0};
          if (typeof callback==="function") callback();
        }
      });
    },
    startCorner(index,event) { if (this.busy) return; this.activeCorner=index; this.measureSource(()=>this.moveCorner(index,event)); },
    moveCorner(index,event) {
      if (this.busy || this.activeCorner!==index || !this.sourceRect) return;
      const touch=(event.touches && event.touches[0]) || (event.changedTouches && event.changedTouches[0]);
      if (!touch) return;
      const rect=this.sourceRect;
      const x=Number.isFinite(touch.clientX)?touch.clientX:touch.pageX-this.sourceScroll.left;
      const y=Number.isFinite(touch.clientY)?touch.clientY:touch.pageY-this.sourceScroll.top;
      this.setCorner(index,(x-rect.left)/rect.width*this.sourceWidth,(y-rect.top)/rect.height*this.sourceHeight);
    },
    endCorner() { this.activeCorner=-1; },
    setCorner(index,x,y) {
      if (this.busy || !this.corners[index] || !Number.isFinite(x) || !Number.isFinite(y)) return;
      const next=copyQuad(this.corners);
      next[index]=[Math.round(Math.max(0,Math.min(this.sourceWidth-1,x))),Math.round(Math.max(0,Math.min(this.sourceHeight-1,y)))];
      if (!validQuad(next)) { this.tip="边界不能交叉或颠倒，请按左上、右上、右下、左下顺序调整。"; return; }
      this.corners=next; this.boundaryChoice="manual"; this.scanDirty=true;
    },
    inputCorner(index,axis,event) { const value=String(event.detail.value).trim(); if (!value) return; const point=this.corners[index].slice(); point[axis]=Number(value); this.setCorner(index,point[0],point[1]); },
    resetBoundary() { if (this.busy) return; this.corners=this.autoQuad.length?copyQuad(this.autoQuad):this.fullQuad(0.03); this.boundaryChoice=this.autoQuad.length?"auto":"manual"; this.scanDirty=true; this.showOriginal(); },
    async keepWhole() { if (this.busy) return; this.corners=this.fullQuad(); this.boundaryChoice="whole"; this.scanDirty=true; return this.applyScan(); },
    async applyScan() {
      if (this.busy || !this.reviewReady) return;
      this.busy=true; this.tip="正在应用裁切与效果…";
      try {
        this.assertScanOrigin(); if (!validQuad(this.corners)) throw new Error("边界无效，请重新调整四角");
        const response=await api.correct(this.sourceFile,{mode:this.mode,quad:copyQuad(this.corners)}); this.assertScanOrigin();
        if (!response || response.has_quad!==true || !validQuad(response.quad)) throw new Error("服务未确认裁切边界，请重新调整并应用；尚未提取文字");
        this.acceptResponse(response); this.boundaryConfirmed=true; this.scanDirty=false; this.previewTab="effect";
        this.tip=this.boundaryChoice==="whole"?"已保留整张照片，请检查效果后提取文字。":"裁切与效果已应用，请检查纸张和文字是否完整。";
      } catch(error) { this.scanDirty=true; this.tip="应用失败："+errorMessage(error); }
      finally { this.busy=false; }
    },
    downloadCorrected() {
      this.assertScanOrigin();
      if (api.isNative) return api.localFile(this.processedUrl);
      return new Promise((resolve,reject)=>uni.downloadFile({ url:this.processedUrl,
        success:(res)=>{ if (res.statusCode>=200 && res.statusCode<300 && res.tempFilePath) resolve(res.tempFilePath); else reject(new Error("扫描图下载失败，请重试；已停止本次操作")); }, fail:reject }));
    },
    async confirmRecognize() {
      if (this.busy || this.checking) return;
      if (!this.canRecognize) { this.tip="请先确认边界并应用最新裁切与效果，再提取文字。"; return; }
      this.busy=true; this.tip=this.localEngine?"正在读取确认后的扫描图…":"正在下载确认后的扫描图…";
      try {
        const fp=await this.downloadCorrected(); this.assertScanOrigin(); this.tip="正在提取文字…";
        const result=await api.recognize(fp); this.assertScanOrigin();
        if (!result || result.ok!==true) throw new Error(result && result.msg || "识别失败");
        result.backend_base=this.scanBase;
        if (this.warnings.length) result.warn=[result.warn].concat(this.warnings).filter(Boolean).join("；");
        uni.setStorageSync("last_result",result); this.tip="文字提取完成"; uni.navigateTo({url:"/pages/result/result"});
      } catch(error) { this.tip="提取失败："+errorMessage(error); }
      finally { this.busy=false; }
    },
    previewProcessed() { if (this.processedUrl) uni.previewImage({current:this.processedUrl,urls:[this.processedUrl]}); },
    async saveScanImage() {
      if (!this.canRecognize) return;
      this.busy=true; this.tip="正在保存扫描图…";
      try {
        const filePath=await this.downloadCorrected(); this.assertScanOrigin();
        if (api.isNative) await api.saveImage(filePath);
        else await new Promise((resolve,reject)=>uni.saveImageToPhotosAlbum({filePath,success:resolve,fail:reject}));
        this.assertScanOrigin();
        this.tip="扫描图已保存到相册。"; uni.showToast({title:"扫描图已保存",icon:"success"});
      } catch(error) { const text=errorMessage(error); this.tip="保存扫描图失败："+text+(/auth|deny|permission|权限/i.test(text)?"。请在系统设置中允许相册/照片权限后重试。":""); }
      finally { this.busy=false; }
    },
    goHistory() { uni.navigateTo({url:"/pages/history/history"}); }
  },
  onShow() { this.base=api.BASE; this.baseInput=this.base; if (this.reviewReady) { if (api.BASE!==this.scanBase) this.tip="后端地址已改变，请重新选择照片生成扫描效果"; } else if (!this.busy && !this.checking) this.check(); },
  onResize() { if (this.previewTab==="original") this.measureSource(); }
};
</script>
<style>
.scanner{min-height:100vh;padding:40rpx 26rpx 60rpx;background:#f2efe7;color:#253d39;box-sizing:border-box;}.masthead{padding:8rpx 4rpx 30rpx;border-bottom:2rpx solid #ccd1c3;}.eyebrow{font-size:19rpx;letter-spacing:4rpx;color:#738272;display:block;}.title-row{display:flex;align-items:center;justify-content:space-between;margin:18rpx 0 10rpx;}.title{font-family:Georgia,'Noto Serif SC','Songti SC',serif;font-size:56rpx;font-weight:700;}.subtitle{display:block;font-size:25rpx;color:#738071;}.archive-link{margin:0;padding:0;background:transparent;font-size:23rpx;color:#116d5d;}.archive-link:after{border:0;}.capture-row{display:flex;gap:14rpx;padding:28rpx 0;}.capture-row button{flex:1;margin:0;}.primary{background:#116d5d;color:#fffdf7;font-size:28rpx;border-radius:7rpx;}.secondary{background:#e3e9dc;color:#315c4d;font-size:28rpx;border-radius:7rpx;}.primary[disabled]{background:#d3ddd0;color:#83947f;}.welcome{padding:60rpx 32rpx;display:flex;flex-direction:column;align-items:center;gap:18rpx;text-align:center;font-size:26rpx;line-height:1.8;color:#7b8777;}.welcome-title{font-size:33rpx;color:#3c5a48;font-weight:700;}.small{font-size:22rpx;}.paper-mark{width:130rpx;height:168rpx;transform:rotate(-7deg);border:2rpx solid #a8b89d;background:#fffdf7;padding:26rpx 20rpx;box-sizing:border-box;margin-bottom:24rpx;}.paper-mark view{height:2rpx;background:#bdc7b4;margin:20rpx 0;}.paper{background:#fffdf7;border:1rpx solid #d8dacf;border-radius:8rpx;padding:24rpx;margin-bottom:24rpx;}.section-head{display:flex;gap:14rpx;align-items:center;margin-bottom:22rpx;}.section-no{font-family:Georgia,serif;color:#80907c;font-size:22rpx;}.section-title{font-size:31rpx;font-weight:700;}.state{margin-left:auto;font-size:21rpx;color:#4b7c62;padding:5rpx 10rpx;background:#e8efe1;}.tabs{display:flex;gap:8rpx;margin-bottom:24rpx;background:#eef0e6;padding:6rpx;border-radius:6rpx;}.tabs button{flex:1;background:transparent;font-size:24rpx;line-height:68rpx;padding:0 8rpx;margin:0;color:#778473;border-radius:4rpx;}.tabs .selected{background:#fffdf7;color:#215d49;box-shadow:0 2rpx 5rpx rgba(36,56,36,.08);}.tabs button:after{border:0;}.source-frame{position:relative;height:0;width:100%;background:#e0e2d5;margin:26rpx 0;}.source-image{position:absolute;left:0;top:0;width:100%;height:100%;}.effect-image{width:100%;display:block;height:auto;background:#e0e2d5;}.crop-edge{position:absolute;height:3rpx;background:#00b791;transform-origin:0 50%;pointer-events:none;box-shadow:0 1rpx 3rpx #203d32;}.corner{position:absolute;width:74rpx;height:74rpx;transform:translate(-50%,-50%);display:flex;justify-content:center;align-items:center;z-index:2;}.corner text{display:flex;align-items:center;justify-content:center;width:38rpx;height:38rpx;border:3rpx solid #fffdf7;border-radius:50%;background:#087b64;color:#fff;font-size:23rpx;box-shadow:0 1rpx 7rpx rgba(0,0,0,.28);}.corner.dragging text{background:#df9d33;transform:scale(1.2);}.caption,.hint{display:block;font-size:22rpx;line-height:1.7;color:#7a8574;margin-top:18rpx;}.notice,.warning{display:block;margin-top:20rpx;padding:18rpx;font-size:24rpx;line-height:1.7;background:#f6ebd5;color:#86652d;border-left:4rpx solid #c19a49;}.warning{background:#f6efe0;}.mode-row{display:flex;gap:12rpx;}.mode-row button{flex:1;margin:0;padding:20rpx 8rpx;background:#eef0e6;border:2rpx solid transparent;border-radius:7rpx;line-height:1.6;}.mode-row .selected{background:#e5f0e1;border-color:#457e59;}.mode-title{font-size:29rpx;font-weight:700;display:block;color:#315d48;}.mode-note{font-size:20rpx;display:block;color:#88917f;}.boundary-actions{display:flex;gap:12rpx;margin-top:22rpx;}.boundary-actions button{flex:1;margin:0;background:#f3f1e7;color:#617359;font-size:23rpx;padding:0 14rpx;}.coordinate-head{display:flex;justify-content:space-between;margin-top:26rpx;padding:18rpx 0;border-top:1rpx solid #e5e5d9;color:#7a8773;font-size:23rpx;}.coordinate-grid{display:flex;flex-wrap:wrap;gap:14rpx;}.coordinate{width:calc(50% - 7rpx);padding:12rpx;box-sizing:border-box;background:#f2f3e9;}.corner-name{font-size:22rpx;color:#45654c;display:block;margin-bottom:8rpx;}.coordinate-inputs{display:flex;gap:7rpx;align-items:center;font-size:20rpx;color:#8b9581;}.coordinate-inputs input{width:0;flex:1;background:#fffdf7;padding:7rpx;font-size:23rpx;height:42rpx;color:#30543f;}.apply-button{background:#e4eddf;color:#2b6149;font-size:27rpx;margin-top:24rpx;border-radius:7rpx;}.apply-button[disabled]{background:#eceee3;color:#99a28f;}.confirm-panel .primary{margin:0;}.save-image{margin-top:18rpx;background:transparent;font-size:25rpx;color:#3d7456;}.save-image:after{border-color:#cad7c1;}.tip{display:block;padding:22rpx;margin:24rpx 0;background:#e5ebdd;border-left:4rpx solid #668765;color:#486444;font-size:25rpx;line-height:1.7;}.connection{border-top:2rpx solid #cfd5c5;margin-top:30rpx;padding:20rpx 4rpx;}.connection-head{display:flex;justify-content:space-between;align-items:center;font-size:23rpx;color:#6e7e68;}.dot{color:#b7a16d;margin-right:12rpx;}.dot.ready{color:#397e5a;}.connection-body{padding-top:20rpx;}.base{display:block;font-size:20rpx;color:#8b9682;word-break:break-all;}.address{margin-top:16rpx;background:#fffdf7;border:1rpx solid #d2dac6;border-radius:5rpx;padding:16rpx;font-size:26rpx;height:44rpx;}.connection-actions{display:flex;gap:10rpx;margin-top:14rpx;}.connection-actions button{flex:1;font-size:22rpx;background:#e2e8d8;color:#57734d;margin:0;padding:0 9rpx;}@media(min-width:850px){.scanner{max-width:1100px;margin:0 auto;}.review{display:flex;gap:24rpx;flex-wrap:wrap;align-items:flex-start;}.image-panel{width:calc(58% - 74rpx);}.controls{width:calc(42% - 50rpx);}.confirm-panel{width:100%;}.image-panel{position:sticky;top:20rpx;}}
</style>
