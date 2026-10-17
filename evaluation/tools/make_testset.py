"""生成50张合成中文测试图 + gt.json（无隐私，答辩可用，离线）"""
import os, json, random
from PIL import Image, ImageDraw, ImageFont

out_dir = "data/synthetic"
gt_path = "data/synthetic/gt_synthetic.json"
os.makedirs(out_dir, exist_ok=True)
os.makedirs(os.path.dirname(gt_path), exist_ok=True)

texts = [
    "离线智能扫描仪 课程大作业",
    "发票金额 128.50元 日期2026-09-11",
    "黑板报：今日作业 数学P32",
    "快递单号 SF1234567890 收件人张三",
    "成绩单 语文92 数学88 英语95",
    "会议纪要：下周三前提交报告",
    "借条 今借到人民币伍仟元整",
    "说明书：长按3秒开机",
]
# 尝试找中文字体，找不到就用默认
font = None
for fp in ["C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
    if os.path.exists(fp):
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(fp, 36)
            break
        except Exception:
            pass

gt = {}
for i in range(50):
    t = random.choice(texts) + f" No.{i:03d}"
    img = Image.new("RGB", (800, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((30, 70), t, fill="black", font=font)
    # 加噪声模拟拍照
    name = f"syn_{i:03d}.jpg"
    img.save(os.path.join(out_dir, name))
    gt[name] = t
json.dump(gt, open(gt_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"生成50张 -> {out_dir}, gt -> {gt_path}")

# 注：老目录 data/samples 保留50张兼容；新规范以 data/synthetic 为准，仅回归用。
