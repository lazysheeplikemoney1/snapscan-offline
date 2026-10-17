"""The shipped font must cover every deployed OCR label, not just common Chinese."""
import json
from pathlib import Path
import unittest
from fontTools.ttLib import TTFont

ROOT=Path(__file__).resolve().parents[1]


class FontCoverageTests(unittest.TestCase):
    def test_actual_font_covers_model_and_rare_regressions(self):
        assets=ROOT/'mobile/android-local/app/src/main/assets'
        font=TTFont(assets/'fonts/SnapScanSansSC.ttf')
        cmap=font.getBestCmap()
        labels=json.loads((assets/'models/characters.json').read_text(encoding='utf-8'))
        missing=[c for c in labels[1:] if any(ord(cp) not in cmap for cp in c)]
        self.assertEqual(missing,[],"Model labels missing embedded glyphs")
        self.assertTrue(all(ord(c) in cmap for c in '❤❋ɔʌ𣇉'))

    def test_recovery_seed_font_has_blank_and_visible_missing_glyph(self):
        path=ROOT/'mobile/android-local/app/src/main/assets/fonts/SnapScanUnicodeSeed.ttf'
        self.assertTrue(path.exists(),'Missing-glyph recovery seed font is not prepared')
        cmap=TTFont(path).getBestCmap()
        self.assertIn(ord(' '),cmap)
        self.assertIn(ord('□'),cmap)

    def test_distinct_unicode_values_do_not_share_a_gid(self):
        font=TTFont(ROOT/'mobile/android-local/app/src/main/assets/fonts/SnapScanSansSC.ttf')
        cmap=font.getBestCmap()
        self.assertEqual(len(cmap),len(set(cmap.values())), 'Shared GIDs make PDFBox reverse-cmap change Unicode (文 -> ⽂)')


if __name__=='__main__':unittest.main()
