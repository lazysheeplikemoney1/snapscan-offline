"""Independent checks must detect layout/text regressions, not merely file existence."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

try:
    from evaluation.accept_document_pdf import inspect_document_pdf
except ImportError:
    inspect_document_pdf = None


class DocumentPdfAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "input.png"
        Image.new("RGB", (200, 100), "#e4edfe").save(self.image)
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    def make_pdf(self, name="good.pdf", *, hidden=True, text="中文识别测试ABC", size=(842, 421), image=True):
        path = self.root / name
        pdf = canvas.Canvas(str(path), pagesize=size)
        if image:
            pdf.drawImage(str(self.image), 0, 0, width=size[0], height=size[1])
        txt = pdf.beginText(20, 390)
        txt.setFont("STSong-Light", 12)
        if hidden:
            txt.setTextRenderMode(3)
        txt.textOut(text)
        pdf.drawText(txt)
        pdf.save()
        return path

    def inspect(self, path):
        self.assertIsNotNone(inspect_document_pdf, "Independent PDF inspector is not implemented")
        return inspect_document_pdf(path, self.image, [{"text": "中文识别测试ABC", "box": [[1, 1], [180, 1], [180, 20], [1, 20]]}])

    def test_accepts_proportional_background_and_complete_hidden_chinese(self):
        result = self.inspect(self.make_pdf())
        self.assertTrue(result["pass_all"])
        self.assertTrue(result["checks"]["complete_text_extraction"])
        self.assertTrue(result["checks"]["all_text_invisible"])
        self.assertTrue(result["checks"]["embedded_image_pixels_match"])

    def test_rejects_visible_ocr_overlay(self):
        result = self.inspect(self.make_pdf(hidden=False))
        self.assertFalse(result["checks"]["all_text_invisible"])
        self.assertFalse(result["pass_all"])

    def test_rejects_missing_chinese_characters(self):
        result = self.inspect(self.make_pdf(text="中文识别ABC"))
        self.assertFalse(result["checks"]["complete_text_extraction"])
        self.assertFalse(result["pass_all"])

    def test_rejects_a4_reflow_even_if_text_is_searchable(self):
        result = self.inspect(self.make_pdf(size=(595, 842), image=False))
        self.assertFalse(result["checks"]["page_aspect_ratio_matches"])
        self.assertFalse(result["checks"]["one_full_resolution_image"])
        self.assertFalse(result["pass_all"])


if __name__ == "__main__":
    unittest.main()
