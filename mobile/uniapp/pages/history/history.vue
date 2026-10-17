<template>
  <view class="archive">
    <text class="eyebrow">SNAPSCAN / ARCHIVE</text><text class="title">文档档案</text><text class="subtitle">继续校对，或重新导出已保存的文档。</text>
    <view class="search-row"><input v-model="q" placeholder="搜索文档中的文字" class="search" :disabled="loading" @confirm="load" /><button :loading="loading" :disabled="loading" @click="load">搜索</button></view>
    <text v-if="err" class="error">{{err}}</text>
    <text v-if="loading" class="empty">正在读取文档…</text>
    <view v-if="!loading && !err && !items.length" class="empty"><text class="empty-title">{{q ? '没有找到匹配的文档' : '档案还是空的'}}</text><text>{{q ? '试试较短的关键词。' : '完成一次扫描后，文档会出现在这里。'}}</text></view>
    <view v-for="it in items" :key="it.id" class="item" @click="open(it)">
      <view class="item-top"><text class="record">文档 #{{it.id}}</text><text class="date">{{formatDate(it.created_at)}}</text></view>
      <text class="snippet">{{it.snippet || '未识别到文字'}}</text><view class="item-bottom"><text class="filename">{{it.filename}}</text><text class="open">打开校对 →</text></view>
    </view>
  </view>
</template>
<script>
import { api } from "../../utils/api.js";
export default {
  data: () => ({ q: "", items: [], loading: false, err: "" }),
  methods: {
    async load() {
      if (this.loading) return;
      this.loading = true; this.err = "";
      try { const response = await api.history(this.q); this.items = response.items || []; }
      catch (error) { this.items = []; this.err = "读取失败：" + (error.message || error.errMsg || "请检查后端地址与网络"); }
      finally { this.loading = false; }
    },
    open(item) { if (item.id) uni.navigateTo({ url: "/pages/result/result?id=" + encodeURIComponent(item.id) }); },
    formatDate(value) {
      if (!value) return "";
      const date = new Date(typeof value === "number" ? value * 1000 : value);
      return Number.isNaN(date.getTime()) ? "" : date.getFullYear() + "/" + (date.getMonth() + 1) + "/" + date.getDate();
    }
  },
  onShow() { return this.load(); }
};
</script>
<style>
.archive{min-height:100vh;padding:48rpx 28rpx;background:#f2efe7;color:#253d39;box-sizing:border-box;}.eyebrow{display:block;font-size:19rpx;letter-spacing:5rpx;color:#6c7d6c;}.title{display:block;font-size:54rpx;font-weight:700;font-family:Georgia,'Noto Serif SC','Songti SC',serif;margin:20rpx 0 12rpx;}.subtitle{font-size:25rpx;color:#778073;display:block;margin-bottom:34rpx;}.search-row{display:flex;gap:12rpx;border-bottom:2rpx solid #c7cebf;padding-bottom:28rpx;margin-bottom:26rpx;}.search{background:#fffdf7;border:1rpx solid #d6d9cb;border-radius:7rpx;padding:16rpx 20rpx;flex:1;font-size:27rpx;height:50rpx;}.search-row button{background:#116d5d;color:#fffdf7;font-size:25rpx;line-height:82rpx;padding:0 30rpx;border-radius:7rpx;}.item{padding:28rpx 24rpx;background:#fffdf7;border:1rpx solid #d8dacf;border-radius:8rpx;margin-bottom:20rpx;}.item-top,.item-bottom{display:flex;justify-content:space-between;gap:20rpx;align-items:center;}.record{font-size:23rpx;color:#326c55;font-weight:700;}.date{font-size:21rpx;color:#879080;}.snippet{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-size:29rpx;line-height:1.8;margin:22rpx 0;color:#3e5146;}.item-bottom{border-top:1rpx solid #eceade;padding-top:20rpx;}.filename{font-size:20rpx;color:#929787;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1;}.open{font-size:23rpx;color:#116d5d;flex-shrink:0;}.empty{display:flex;flex-direction:column;gap:12rpx;font-size:25rpx;line-height:1.8;color:#7d8777;padding:38rpx 10rpx;}.empty-title{font-size:30rpx;color:#4c6552;}.error{display:block;padding:22rpx;background:#f7e7dd;color:#9b493a;font-size:25rpx;line-height:1.7;}
</style>
