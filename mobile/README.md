# SnapScan 手机端

当前手机独立版位于 `android-local/`，版本 `0.2.3-local` / code203，最低 Android 8（API 26），面向 Android 15 构建。目标手机为 iQOO Neo8 Pro。模型、图像处理、历史和导出均在 APK 内运行，无需电脑服务。此版修复课本正文的漏行、文字行裁剪混入邻行及错误同行合并；自动候选不足整图 10% 时保留整图并提示手调，显式手动四角不受此限制。保留原框索引和坐标、屏幕翻拍检测修复，沿用原 OCR 权重与相机接口。

- `android-local/`：原生 Android Java 容器、OpenCV、ONNX Runtime、SQLite、PDF/XLSX、相机和文件接口。
- `uniapp/`：共享扫描预览、四角调整、校对和历史页面。编译为 H5 资源并内置于原生容器。
- `prepare_local_models.py`：校验并准备本地检测、方向分类、`rec_head_v1` 识别模型及字典。
- `prepare_local_font.py`：准备本地 PDF 字体和许可证。
- `build_android_candidate.py`：旧版局域网客户端的构建脚本，不能用于生成手机独立版。

安装与断网验收见 `docs/手机安装APK指引.md`；构建入口为 `mobile/android-local/build_local.py`。SDK/JDK 不采用机器上已经失效的全局环境变量。

## 独立版构建

在项目根目录执行（首次需要联网取得构建工具与依赖，安装后的 App 不联网）：

```powershell
& 'C:/pycharm/python.exe' mobile/prepare_local_models.py
& 'C:/pycharm/python.exe' mobile/android-local/build_local.py --bootstrap testDebugUnitTest assembleDebug assembleDebugAndroidTest
& 'C:/pycharm/python.exe' mobile/android-local/build_local.py --publish assembleRelease
```

构建脚本在 ASCII 临时目录中复制新工程，使用经核验的 junction 引用工作区，以绕过 Android 构建工具对 Windows 中文路径的限制。不会移动原工程或修改全局 SDK 设置。产物复制回 `mobile/android-local/app/build/outputs/`，发布文件为 `releases/SnapScan-offline-local.apk`，同时保留带版本的 `releases/SnapScan-offline-local-0.2.3.apk`。两者应具有相同 SHA-256。

本次发布为 ARM64 包，82,177,906 字节，SHA-256 为 `25b6217d35e3f10895b04e036b58e1428952fbe630b2c2c2bf760ad88b9fc0c3`。25 项 Android 15 x86_64 模拟器断网测试和 2 项 JVM 单元测试通过；尚未完成目标 ARM64 手机的实机验收。证据见 `reports/android_local_device_verification.json` 和 `reports/android_local_build_verification.json`。测试照片和评测 GT 不随手机应用包分发。

覆盖升级保留历史；已有文字框可在重开后采用新阅读顺序，但漏字记录必须用原图重新扫描才能补全。已导出的 PDF 不会自动改写。原框顺序与坐标不变，Excel 仍为 OCR 明细，不恢复表格单元格。三类新图与旧课本结果见 [评测报告](../reports/课本漏字修复与三类新数据评测.md)；原包和证据见 0.2.2 归档清单（本地资料，未随仓库公开）。

模型与字体、SDK、编译资源和私有签名文件不加入 Git。必须保留模型原始文件及开发签名，缺失时构建或加载应失败，不从网络临时补模型。

## 两种运行方式

独立 APK 中 `api.js` 通过 `SnapScanNative` 调用本机计算。`https://appassets.androidplatform.net` 只用于 WebView 拦截本地资源，不是远程服务器；App 不声明 INTERNET 权限，也不监听 HTTP 端口。启动页面可检查本机模型，不能设置电脑地址。

直接在浏览器或旧 UniApp 原生壳运行时，仍使用电脑 FastAPI 接口。这条兼容路径保留用于开发对照，不能将其当成独立离线 APK。旧电脑历史与新手机 SQLite 历史相互独立，不会自动迁移。
