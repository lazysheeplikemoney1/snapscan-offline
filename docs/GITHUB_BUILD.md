# 从公开源码准备与构建 SnapScan

本说明面向新克隆的公开仓库，当前版本为 `0.2.3-local`。仓库提供源码和关键说明，不包含模型权重、真实图片/标注、生成的 H5 页面、生成字体、SDK、私有签名、历史数据库或 APK。安装后的独立应用不访问网络；构建机器首次获取依赖需要联网。

现有构建工具来自 Windows 开发环境，仍有工具路径、模型哈希与原开发签名的固定核验。以下明确这些前置条件；不能在缺少原权重、签名或测试资产时声称已经复现原发布包。

## 1. 工具与环境

| 用途 | 要求 |
| --- | --- |
| 构建脚本 | Windows、PowerShell、Python 3.11 或更新版本（脚本使用 `hashlib.file_digest`） |
| Android 编译 | JDK 17、Gradle 8.10.2、Android Gradle Plugin 8.8.0 |
| Android SDK | API 35、Build Tools 35.0.0、Platform Tools |
| 共享页面 | HBuilderX 及其 Node / UniApp CLI；原构建记录为 HBuilderX 5.26.2026091802 |
| 本机推理依赖 | ONNX Runtime Android 1.23.2、OpenCV 4.12.0 |
| 资源准备 | `rapidocr-onnxruntime==1.4.4`、`onnx==1.18.0`、`fonttools` |

建议为后端、训练和 Android 资源准备分别建 Python 环境，避免不同版本的 NumPy / ONNX Runtime 互相覆盖。后端依赖见 `backend/requirements.txt`；训练依赖见 `training/requirements.txt`，后者包含 CUDA 12.8 的 PyTorch wheel，普通运行 App 不需要安装 PyTorch。

Android 资源准备环境示例：

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
python -m pip install rapidocr-onnxruntime==1.4.4 onnx==1.18.0 fonttools
```

## 2. 模型来源与放置

`mobile/prepare_local_models.py` **只读取并校验本地文件，不下载模型**。它要求部署文件字节与代码中的 SHA-256 一致：

| 文件 | 来源与用途 |
| --- | --- |
| `backend/models/fp32/det_fp32.onnx` | RapidOCR 1.4.4 包内 `ch_PP-OCRv4_det_infer.onnx` 的副本，用于文字检测 |
| `backend/models/fp32/rec_fp32.onnx` | 同包内 `ch_PP-OCRv4_rec_infer.onnx` 的副本，用于训练输入或后端选择原始模型 |
| `backend/models/rec_head_v1.onnx` | 本项目微调导出的 `training/runs/rec_head_v1/rec_finetuned.onnx`，用于当前部署 |
| RapidOCR 包内 `ch_ppocr_mobile_v2.0_cls_infer.onnx` | 方向分类，资源准备脚本直接读取 |

安装对应版本 RapidOCR 后，可在其包目录的 `models/` 中取得原始 det / rec / cls。查询本机目录：

```powershell
python -c "import importlib.util,pathlib; s=importlib.util.find_spec('rapidocr_onnxruntime'); print(pathlib.Path(s.origin).parent / 'models')"
```

将 det / rec 原始权重复制到表中路径，文件名与路径需保持一致。原始权重来自 RapidOCR / PaddleOCR，不能将原始 rec 改名后冒充 `rec_head_v1`。

**当前仓库未提供 `rec_head_v1` 的公开下载地址。** 要构建与当前版本一致的 OCR，需要由项目作者提供已校验的部署权重。默认识别权重 SHA-256：

```text
d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe
```

也可以基于自己的合规数据运行 `training/` 中的训练代码；这会产生新的模型版本，须同步更新后端模型清单、移动资源准备与发布校验中的模型配置，并重新验证字典、输出类别及识别结果。原训练数据与审核清单未公开，无法仅依赖仓库精确复现同一权重。此说明不把自行训练当成现有版本的替代验收。

模型齐备后，在项目根目录执行：

```powershell
python mobile/prepare_local_models.py
```

该命令生成 Android `assets/models/` 中的模型、CTC 字典与校验清单。缺失、哈希不符或输出类别不匹配时会停止。后端部署选择与权重哈希见 [manifest.json](../backend/models/manifest.json)。

## 3. 配置工具、资源与签名

在运行 `mobile/android-local/build_local.py` 前，按自己的环境配置：

1. 将脚本的 `JDK`、`GRADLE` 常量改为本机工具位置。
2. 将 `prepare_assets()` 中的 HBuilderX `plugins` 位置及版本信息与实际安装匹配。当前工程通过 HBuilderX 自带 CLI 生成 H5 资源，不提供独立 npm 锁文件构建路径。
3. 为自己的构建创建并保管独立签名。`app/build.gradle` 读取 `build/android_candidate/signing/snapscan-development.p12` 和同目录 `password.txt`，别名为 `snapscan-dev`；也可改为自己的 Gradle 签名配置。私钥与口令不可提交到仓库。
4. `build_local.py --publish` 固定核验原开发证书指纹。使用自己的证书时，需同步改为自己的已验证指纹；不同证书不能覆盖安装原签名应用。不要通过移除签名检查来冒充原发布包。

脚本会调用 `mobile/prepare_local_font.py`，从其记录的官方 Noto 来源下载字体并核验哈希，生成本地 PDF 字体与 OFL 文件。字体资源依赖上一步生成的 CTC 字典。若上游内容变动导致哈希不匹配，需要检查来源变化，不能直接忽略校验。

`bootstrap_android.py` 会下载 Android SDK 工具及 ONNX Runtime / OpenCV AAR 至项目 `build/`，URL 和校验方式可直接检查源码。它不会安装 JDK、Gradle 或 HBuilderX。当前脚本使用 Windows 工具包和 junction，Linux / macOS 构建尚未在该流程中验证。

## 4. 构建 Android APK

完成上述配置与资源准备后，在项目根目录运行：

```powershell
python mobile/android-local/build_local.py --bootstrap testDebugUnitTest assembleDebug
```

构建脚本在 ASCII 临时目录中准备工程，通过校验过的 junction 访问项目，以处理中文工作区路径；结果复制到 `mobile/android-local/app/build/outputs/`。`testDebugUnitTest` 运行 JVM 单元测试，`assembleDebug` 构建调试 APK。

若需要构建自己的 ARM64 发布包，先完成自己的签名及发布校验配置，然后执行：

```powershell
python mobile/android-local/build_local.py --publish assembleRelease
```

`--publish` 在这里表示校验并复制**本地文件**到 `releases/`，不表示上传 GitHub Release。该步骤检查模型、无 `INTERNET` 权限、ARM64 运行库、签名、测试图片未被误打包及 APK 对齐。公开仓库不保证存在可直接下载的发布附件。

## 5. 运行电脑开发后端

电脑后端是可选对照实现，独立手机 App 不依赖它。在独立 Python 环境安装 `backend/requirements.txt`，准备识别权重后运行：

```powershell
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/api/health`，同时检查 `ocr.ready`、`ocr.model.id` 与 SHA-256。服务启动不代表模型已经可用。

只有原始识别权重时，可显式选择预训练后端基线：

```powershell
$env:SNAPSCAN_OCR_MODEL = 'pretrained'
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

这条路径需要 `backend/models/fp32/rec_fp32.onnx` 与清单哈希一致；它没有应用本项目识别头微调，不应引用 `rec_head_v1` 的实验指标。

## 6. 测试与复现范围

无需真实照片的逻辑测试可从以下入口开始；先准备对应运行时与依赖：

```powershell
node --test mobile/uniapp/tests/*.test.cjs
python evaluation/test_cer.py
python -m pip install pytest numpy
python -m pytest evaluation/test_independent_scan_metrics.py -q
```

Android JVM 测试通过前述 Gradle 任务运行。真实照片扫描、完整 OCR、导出与 Android 仪器回归，还需要部署模型和原测试图像/标注；公开副本不附带这些数据。`assembleDebugAndroidTest` 仅构建测试包，不能替代在设备执行测试，也不代表缺失的真实图像回归已通过。

历史版本已记录 25 项 Android 15 / API35 x86_64 模拟器断网测试和 2 项 JVM 测试通过。它们是原开发环境结果，并非全新克隆的即时验证。ARM64 目标手机 iQOO Neo8 Pro 的拍照、速度、内存和分享兼容性仍需单独实测。

检查离线链路时应关闭 Wi-Fi 和移动数据，冷启动 App，从拍照/选图到扫描、OCR、历史重开与导出完整走通。只有界面能启动不足以证明本机模型与处理链路正常。
