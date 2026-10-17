# -*- coding: utf-8 -*-
"""导出：保版式扫描PDF、重排文本PDF与OCR明细XLSX。
命名纠正（评审意见6）：当前Excel是"OCR结果导出XLSX"，不是"表格结构恢复"。
如需真表格恢复，需另做 表格区域->单元格->Box匹配->行列关系，本期不宣称。
"""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from openpyxl import Workbook
from PIL import Image
from reportlab.lib.utils import ImageReader
import math
import os
from .layout import build_layout, layout_fields

def _cn_font():
    # STSong-Light 为 reportlab 内置CID字体，无需外带字体文件即可显示中文（答辩机可用）
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception:
        return "Helvetica"

def export_txt_to_pdf(text, boxes, out_path, title="SnapScan OCR"):
    """文本型PDF：文字可选/可搜索（区别于扫描图片型PDF）。验收：Ctrl+F能搜到文本。"""
    text = layout_fields(boxes, text)["text"]
    font = _cn_font()
    c = canvas.Canvas(out_path, pagesize=A4)
    c.setTitle(title)
    W_, H = A4
    c.setFont(font, 11)
    y = H - 40
    for line in (text or "").split("\n"):
        # 按宽度折行（粗略按字符数，中文占宽）
        while len(line) > 48:
            c.drawString(40, y, line[:48])
            line = line[48:]
            y -= 15
            if y < 40:
                c.showPage(); c.setFont(font, 11); y = H - 40
        c.drawString(40, y, line)
        y -= 15
        if y < 40:
            c.showPage(); c.setFont(font, 11); y = H - 40
    c.save()
    return out_path

def _positioned_lines(boxes, width, height):
    """Validate positions before writing a file; preserve edited multiline text."""
    lines = []
    boxes = boxes or []
    order = [index for row in build_layout(boxes)["rows"] for index in row]
    for index in order:
        item = boxes[index]
        text = item.get("text", "")
        if not text:
            continue
        if not isinstance(text, str):
            raise ValueError("文字层文本必须为字符串")
        try:
            points = item["box"]
            if len(points) != 4 or any(len(p) != 2 for p in points):
                raise ValueError()
            points = [(float(p[0]), float(p[1])) for p in points]
            if any(not math.isfinite(v) for p in points for v in p):
                raise ValueError()
            if any(x < 0 or y < 0 or x > width or y > height for x, y in points):
                raise ValueError()
            tl, tr, br, bl = points
            if math.dist(tl, tr) < .1 or math.dist(tl, bl) < .1:
                raise ValueError()
            cross = (tr[0]-tl[0]) * (bl[1]-tl[1]) - (tr[1]-tl[1]) * (bl[0]-tl[0])
            if cross <= .01:
                raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("文字坐标无效或超出扫描图，请重新识别后导出") from exc
        parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, part in enumerate(parts):
            if not part:
                continue
            top, bottom = index / len(parts), (index + 1) / len(parts)
            def lerp(a, b, t):
                return (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)
            lines.append((part, lerp(tl, bl, top), lerp(tr, br, top), lerp(tl, bl, bottom)))
    return lines


def export_searchable_pdf(image_path, boxes, out_path, title="SnapScan"):
    """Keep the scan intact and place invisible searchable text in its coordinates.

    This preserves the visible page, not editable paragraph/table structure.
    Boxes must refer to this exact image in top-left pixel coordinates.
    """
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    width, height = image.size
    lines = _positioned_lines(boxes, width, height)
    font = _cn_font()
    if font == "Helvetica":
        raise ValueError("中文 PDF 字体不可用")
    scale = 842.0 / max(width, height)
    page_width, page_height = width * scale, height * scale
    c = canvas.Canvas(str(out_path), pagesize=(page_width, page_height), pageCompression=1)
    c.setTitle(title)
    c.drawImage(ImageReader(image), 0, 0, width=page_width, height=page_height)
    ascent, descent = pdfmetrics.getAscentDescent(font)
    for text, tl, tr, bl in lines:
        # PDF y increases upward; the affine transform retains tilted baselines.
        top = (tl[0]*scale, page_height-tl[1]*scale)
        right = (tr[0]*scale, page_height-tr[1]*scale)
        bottom = (bl[0]*scale, page_height-bl[1]*scale)
        line_width = math.dist(top, right)
        line_height = math.dist(top, bottom)
        ux, uy = (right[0]-top[0])/line_width, (right[1]-top[1])/line_width
        vx, vy = (top[0]-bottom[0])/line_height, (top[1]-bottom[1])/line_height
        size = line_height * 1000 / (ascent - descent)
        baseline = -descent * size / 1000
        layer = c.beginText()
        layer.setTextTransform(ux, uy, vx, vy, bottom[0]+vx*baseline, bottom[1]+vy*baseline)
        layer.setFont(font, size)
        natural_width = pdfmetrics.stringWidth(text, font, size)
        if natural_width > 0:
            layer.setHorizScale(100 * line_width / natural_width)
        layer.setTextRenderMode(3)
        layer.textOut(text)
        c.drawText(layer)
    c.showPage()
    c.save()
    return out_path


def export_boxes_to_excel(boxes, out_path):
    """OCR结果导出XLSX（一行一条含置信度/坐标），非表格结构恢复。_REPORT_里禁止写"表格识别"。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "OCR结果"
    ws.append(["序号", "文本", "置信度", "坐标"])
    for i, b in enumerate(boxes or [], 1):
        ws.append([i, b.get("text", ""), b.get("score", ""), str(b.get("box", ""))])
        # OCR text is literal data, even when it starts with '=' or zeroes.
        ws.cell(row=i + 1, column=2).data_type = "s"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.column_dimensions["B"].width = 60
    wb.save(out_path)
    return out_path
