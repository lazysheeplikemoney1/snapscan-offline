# 模型部署与离线条件

后端默认加载 `rec_head_v1.onnx`，它是 2026-10-15 训练的 PP-OCRv4 CTC 识别头微调模型。手机 APP 通过局域网请求电脑后端，手机本身不运行 ONNX 或 PyTorch。

## 部署文件

- `rec_head_v1.onnx`：默认识别模型，来自 `training/runs/rec_head_v1/rec_finetuned.onnx`，10,857,958 字节。
- `fp32/rec_fp32.onnx`：保留的原始识别模型，用于显式回滚。
- `manifest.json`：两个版本的文件路径和 SHA-256。引擎加载前核对哈希，缺失或损坏时返回 503，不静默切换模型或保存占位识别结果。
- det/cls：使用已安装的 `rapidocr-onnxruntime==1.4.4` 包内本地权重。

默认识别模型 SHA-256：`d3b7f29d89e43c24ad812e2f3302d5f55a9b8f2da23db8163116cef929e3cdbe`。

ONNX 权重不默认进入 Git；复制到另一台电脑时，必须同时带上上述识别权重和清单，并安装运行依赖。训练框架不是后端推理依赖。

## 启动与确认

项目根目录运行 `run_backend.bat`，或在已装依赖的 Python 环境中执行：

```powershell
cd backend
py -3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

访问 `http://127.0.0.1:8000/api/health`，应返回 `status=ok`、`ocr.ready=true`、`ocr.model.id=rec_head_v1` 及上述 SHA-256。识别接口也返回同一 `model` 信息，避免把服务器连通误认为新模型已经就绪。手机填写电脑当前 WLAN 地址及端口 8000，手机和电脑须能通过局域网互通。

## 回滚

关闭现有后端，在新终端中显式选择原模型并重新启动：

```powershell
$env:SNAPSCAN_OCR_MODEL = 'pretrained'
cd backend
py -3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

恢复新模型：设置 `$env:SNAPSCAN_OCR_MODEL = 'rec_head_v1'` 后重启，或删除该变量后重启。模型实例在进程内缓存，运行中改文件/环境变量不会自动切换。

## 结果口径

本轮内部文本行验证 CER 为 1.0973% → 0.5949%，不是整页手机照片的最终测试成绩。训练方法、数据隔离和限制见 `training/runs/rec_head_v1/训练报告.md`。接入验证报告另见 `reports/model_integration_acceptance.json`。

模型与依赖完整存在本地时，推理不需要公网。准确架构说明见 `docs/架构与离线声明.md`。Excel 仍为 OCR 结果导出，不包含表格结构恢复。
