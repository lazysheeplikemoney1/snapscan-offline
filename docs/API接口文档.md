# SnapScan 接口

## Android 独立版（2026-10-16）

独立 APK 不发出下述 HTTP 请求。共享界面使用异步接口 `SnapScanNative.request(id, operation, JSON.stringify(payload))`；原生通过 `window.__snapscanNativeResult(id, {statusCode,data})` 返回。虚拟本地资源域名为 `https://appassets.androidplatform.net`，无网络权限、无 HTTP 服务、无远程回退。

操作包括 `health`、`correct`、`recognize`、`history`、`detail`、`saveDocument`、`toPDF`、`toExcel`；相机/相册及系统文件操作为 `chooseImage`、`localFile`、`saveImage`、`openFile`、`shareFile`。文件仅接受 App 私有目录对应的本地 `/uploads/` 路径。`health` 返回 `runtime:android-local, network_required:false` 并实际加载模型。

图像在解码时应用 EXIF，并对最长边大于4096的输入按2的幂采样，以控制内存。独立版 `source_size` 和四角属于**此规范化输入**，不是相机原始文件尺寸；预览、OCR、存图及 PDF 文字框使用一致坐标。OCR 历史保存实际识别 Mat 的无损 PNG。预览不建历史，确认识别才入库。校对与导出均检查修订号。本机搜索为 SQLite 字面子串匹配，不宣称已经实现 FTS5。

边缘出框的纸面候选返回 `method:clipped_paper_region`、`document_complete:false`、`clipped_edges`；重叠纸张可能返回 `overlap_possible:true, overlap_handling:visible_paper_union`。这些提醒同步显示在扫描确认页，不代表能够恢复画外文字或自动分开每一层纸张。

## 电脑后端 HTTP 接口（旧局域网客户端/开发对照）

BaseURL: `http://<电脑IP>:8000`  手机连电脑热点；断网演示=电脑断WAN+手机飞行模式+WLAN（见 docs/架构与离线声明.md）。

## 1. 健康检查
`GET /api/health` → `{"status":"ok","offline":true,"version":"0.1.0","ocr":{"ready":true,"engine":"rapidocr-onnxruntime","model":{"id":"rec_head_v1","sha256":"..."}}}`

健康检查实际加载并核验模型；加载失败返回 HTTP 503，`status=degraded`，`ocr.ready=false`，`ocr.error` 为原因。

`POST /api/ocr/recognize` 的成功响应保留原有字段，新增 `model: {id, sha256}`。模型加载失败返回 HTTP 503 和 `{ok:false,msg:...}`，不新增历史记录。切换模型需重启后端。

## 2. 扫描成像 POST /api/scan/correct

此接口只生成可确认的扫描效果，不调用 OCR、不新增识别历史。

- multipart form-data：`file=@xxx.jpg`；`mode` 为 `color`（原色）、`clean`（净白，默认）或 `bw`（黑白）。
- 可选 `quad` 为 JSON 字符串 `[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]`，依次为左上、右上、右下、左下，坐标对应原图像素。四点须在原图范围内且不交叉；无效四角或模式返回422。未提供四角时自动检测纸张外沿。
- 返回：`{ok,has_quad,quad,original_image,cropped_image,image,size,source_size,mode,detection,quality,warnings,elapsed_ms}`。图像路径均位于 `/uploads/`。
- `image` 为完整分辨率的处理后 PNG，供下载保存及确认后的 OCR 使用；`size:[height,width]` 为处理图尺寸。`original_image` 是原图预览，`cropped_image` 是拉正后、增强前的预览，预览最长边不超过1600像素。
- `source_size:[height,width]` 保持原图尺寸，`quad` 仍使用原图坐标。客户端拖四角时按实际渲染盒的宽高换算，不能直接把缩小预览的像素当原图坐标。
- `detection` 包含 `confidence`、`method` 等检测说明；手动四角的评分不表示预测置信度。`warnings` 为边界或质量提醒，不是 OCR 精度评价。
- 未检测到可靠边界时，`has_quad:false,quad:null`，当前效果保留整图；客户端提示用户手动调整并应用，或显式选择“保留整图”。不能把此响应宣称为已经去背景。

APP 流程：拍照/选图 → 自动外沿预览 → 必要时拖四角或输入坐标 → 选择 `color/clean/bw` → 应用裁切与效果 → 用户确认后下载 `image` 并调用 OCR。边界或模式修改后标为待应用，成功前禁用提取。处理图下载失败时停止，不回退原图 OCR。客户端也可下载当前已确认的扫描图并调用系统相册保存，不调用 OCR。

## 3. OCR识别 POST /api/ocr/recognize
- form-data: file=@corrected.jpg
- returns: `{ok, id, text, boxes:[{box,text,score}], elapsed_ms, engine, model:{id,sha256}, image, image_width, image_height, revision:0}`
- image 为实际 OCR 输入的无损 PNG，文字框坐标以此图左上角为原点，尺寸单位像素。成功记录同时保留 original_boxes。
- 客户端逐框编辑文字，保存时维持框数量与顺序，不凭文字猜测坐标。
- OCR 只接收用户确认的当前处理图；扫描成像的原图对比预览不自动关联为该识别记录的原照片。

## 4. 历史
- `GET /api/history/list?limit=50`
- `GET /api/history/search?q=发票`
- `GET /api/history/{id}` → `{ok,item,document}`，item 保留旧字段格式；document 的 boxes/original_boxes 为数组、model 为对象，包含 image/尺寸/revision。
- `PATCH /api/history/{id} {texts:["第1行修改后文本",...],revision:0}` → `{ok,document}`。事务更新文本、框文字、搜索索引，revision 加1；original_boxes 保留初始值。行数不符422、版本冲突409、记录缺失404。
- 旧记录没有 image；不会伪造或关联不确定的图片。FTS 与字面子串查询合并去重，支持中文片段查找。

## 5. 导出
- `POST /api/export/pdf {scan_id,revision,mode:"searchable"}` → `{ok,file,mode,revision}`。扫描图做底图、OCR框对应不可见文字层；保持原比例，最长边842pt。没有扫描图返回409。
- `mode:"text"` 导出重排的纯文本 PDF；旧 `{text,boxes}` 请求默认仍采用此模式。
- `POST /api/export/excel {scan_id,revision}` → `{ok,file,kind:"ocr_rows",revision}`，从同一已保存文档导出校对后的 OCR 明细；不是表格单元格恢复。
- 有 scan_id 时导出使用数据库内容，忽略客户端 text/boxes；修订号缺失422、过期409。客户端先保存修改，再用返回的 revision 导出。
- 兼容无 scan_id 的 `{text,boxes}` Excel 请求，但二者文字不一致时返回422；仅 text 时按行导出。文字作为字符串写入，保留前导零及等号。
- APP可复制链接或使用系统浏览器打开文件；自动系统分享尚未接入。

## 状态码
业务响应使用 `ok:true/false`；模型未就绪时 health/recognize 返回 HTTP 503，参数校验可能返回 HTTP 422。客户端需同时处理 HTTP 状态与业务错误，失败时显示具体提示，不跳转成功结果页。
