"""The acceptance gate must reject a partial receipt crop."""
import unittest

try:
    from evaluation.accept_scan_imaging import quad_iou
except ImportError:
    quad_iou = None


class ImagingAcceptanceGeometryTests(unittest.TestCase):
    def test_full_narrow_document_has_complete_overlap(self):
        self.assertIsNotNone(quad_iou, "Independent crop-overlap check is missing")
        quad = [[100, 20], [250, 30], [270, 900], [90, 890]]
        self.assertAlmostEqual(quad_iou(quad, quad), 1.0, places=6)

    def test_only_the_top_half_is_not_a_complete_receipt(self):
        self.assertIsNotNone(quad_iou, "Independent crop-overlap check is missing")
        full = [[100, 20], [250, 30], [270, 900], [90, 890]]
        half = [[100, 20], [250, 30], [260, 465], [95, 455]]
        self.assertLess(quad_iou(half, full), .6)


if __name__ == "__main__":
    unittest.main()
