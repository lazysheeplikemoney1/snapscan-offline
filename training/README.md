# 本地 OCR 识别头微调

此目录训练现有 PP-OCRv4 模型的 CTC 文字识别头，共 801,625 个参数。主干网络、文字检测与方向分类模型保持预训练权重。训练会真实执行梯度计算和参数更新，并导出 RapidOCR 可读取的 ONNX 副本；这不是从零训练，也不是全网络微调。

## 数据与验证口径

- 输入为 `data/real/metadata.jsonl` 开头的 105 张本地 dev 照片。代码检查这些记录全部属于 dev，只处理其原图。
- 图片先按相同哈希、来源、同页及高文字重叠关系并组，再以固定种子 17 划分训练/内部验证。
- 从原照片裁出文字行，标签取自人工转录。检测结果只用于定位。非精确匹配必须经过图片视觉审核；不接受 `[UNK]`、不支持字符、歧义匹配或未审候选。
- `data/real/test/` 的 40 张锁定图片不用于训练、验证或模型选择。
- 指标为保守筛选的文本行上的内部 dev 验证 CER，不等于整页 OCR 性能、独立测试集准确率或四类文档的全面表现。相同书籍/课程来源仍可能共享风格。
- 原始图片与总标注文件保留不变。审核发现的截字、错配或标注错误通过排除候选处理，排除原因保存在审查记录中。

## 环境

本次运行解释器为项目内 `.venv-training/Scripts/python.exe`，CUDA 训练在 RTX 4060 Laptop 8GB 上执行。训练环境安装了 PyTorch，原应用运行环境未安装/替换训练框架。新环境可按 `training/requirements.txt` 安装依赖；其中 GPU wheel 来自 PyTorch 官方仓库。

```powershell
python -m venv .venv-training
.\.venv-training\Scripts\python.exe -m pip install -r training/requirements.txt
```

## 重现步骤（项目根目录）

```powershell
# 检查代码契约，包括文档隔离、CTC梯度、原模型/导出模型一致性
.\.venv-training\Scripts\python.exe -m unittest discover -s training -p 'test_*.py' -v

# 已审核候选按固定ID批准；未审候选继续排除
.\.venv-training\Scripts\python.exe -m training.prepare_data --approved-review training/data/approved_review.json --rejected-candidates training/data/rejected_candidates.json

# 每次完整实验使用新的输出目录，避免覆盖已完成结果
.\.venv-training\Scripts\python.exe -m training.train_recognition --out training/runs/rec_head_v1 --epochs 20 --lr 0.00005 --batch-size 16 --patience 5
```

数据中含本地票据内容；裁图、标签、特征和权重均为本地研究产物，已从默认 Git 跟踪中排除。数据和审核记录须与实验目录一起保留才能重现。

## 产物

2026-10-15 首轮 `rec_head_v1` 已完成 20 轮训练：实际训练 1,272 行、内部验证 307 行；验证 CER 从 1.0973% 降至 0.5949%，整行完全正确率从 84.69% 提升至 89.58%。ONNX/RapidOCR 验证及 31 项自动化测试通过。详见 训练报告（本地资料，未随仓库公开）。后续已按用户要求将模型副本接入 APP 后端，部署及回滚说明见 [模型部署说明](../backend/models/README.md)，接入验收见 验收报告（本地资料，未随仓库公开）。

- `training/data/data_report.json`：样本数量、排除理由、分组与坐标转换来源。
- `training/data/review_decisions_*.json`、`approved_review.json`：逐行视觉审核记录及批准清单。
- `training/runs/rec_head_v1/rec_finetuned.onnx`：实际训练后的模型副本。
- `best.pt`、`last.pt`、`history.json`：最佳/最后训练权重与每轮记录。
- `training_report.json`：训练前后验证 CER、参数变化、文件哈希、导出一致性验证及适用限制。
- `baseline_predictions.json`、`best_predictions.json`：同一验证集的完整前后预测。

训练成功不代表一定优于原模型。脚本始终保存最佳的“已训练”checkpoint，并单独判断是否超过原模型；不会自动替换 APP 模型。上线前仍需要整页代表性评测与 APP 联调。

## 方法来源

- [PaddleOCR 微调说明](https://www.paddleocr.ai/v3.0.3/en/version2.x/ppocr/model_train/finetune.html)
- [PyTorch CTC loss](https://docs.pytorch.org/docs/stable/generated/torch.nn.CTCLoss.html)
- [ONNX graph API](https://onnx.ai/onnx/api/helper.html)
- [PyTorch 官方版本安装命令](https://pytorch.org/get-started/previous-versions/)
