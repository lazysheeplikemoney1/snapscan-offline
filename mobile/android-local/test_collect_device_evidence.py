"""Evidence collection must read only the named test files in this app's cache."""
import unittest
import copy
import collect_device_evidence as collector


class ReadingOrderReferenceTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(collector, "reading_order_reference", None)))

    def test_private_cache_artifacts_keep_their_exact_filename(self):
        for filename in ("reading-order-proof-text.pdf", "reading-order-proof-searchable.pdf",
                         "reading-order-proof-source.png", "reading-order-proof-layout.json",
                         "reading-order-proof-ocr-details.xlsx", "reading-order-proof-text.txt"):
            self.assertEqual(collector.reading_order_reference(
                "/data/user/0/com.snapscan.offline/cache/" + filename), ("cache/" + filename, filename))

    def test_foreign_package_traversal_and_arbitrary_private_files_are_rejected(self):
        for path in ("/data/user/0/com.other/cache/reading-order-proof-text.pdf",
                     "/data/user/0/com.snapscan.offline/cache/../reading-order-proof-text.pdf",
                     "/data/user/0/com.snapscan.offline/files/reading-order-proof-text.pdf",
                     "/data/user/0/com.snapscan.offline/cache/private.txt",
                     "https://example.invalid/reading-order-proof-text.pdf"):
            with self.assertRaises(RuntimeError):
                collector.reading_order_reference(path)


class BookOcrEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(collector, "book_ocr_references", None)))
        names = {"source_image": "source.jpg", "prediction_json": "prediction.json", "text": "text.txt", "overlay": "overlay.jpg"}
        self.proof = {"version_code": 203, "source_split": "dev", "holdout_read": False,
                      "artifacts": {key: {"path": "/data/user/0/com.snapscan.offline/cache/book-ocr-proof-" + name}
                                    for key, name in names.items()}}

    def test_all_four_old_dev_artifacts_keep_their_exact_cache_names(self):
        refs = collector.book_ocr_references(self.proof, 203)
        self.assertEqual(set(refs), {"source_image", "prediction_json", "text", "overlay"})
        self.assertEqual(refs["text"], ("cache/book-ocr-proof-text.txt", "book-ocr-proof-text.txt"))

    def test_stale_version_or_wrong_dataset_scope_is_rejected(self):
        for field, value in (("version_code", 202), ("source_split", "test"), ("holdout_read", True)):
            proof = copy.deepcopy(self.proof); proof[field] = value
            with self.assertRaises(RuntimeError):
                collector.book_ocr_references(proof, 203)

    def test_missing_or_foreign_or_renamed_artifact_is_rejected(self):
        proof = copy.deepcopy(self.proof); del proof["artifacts"]["overlay"]
        with self.assertRaises(RuntimeError):
            collector.book_ocr_references(proof, 203)
        for path in ("/data/user/0/com.other/cache/book-ocr-proof-text.txt",
                     "/data/user/0/com.snapscan.offline/cache/../book-ocr-proof-text.txt",
                     "/data/user/0/com.snapscan.offline/files/book-ocr-proof-text.txt",
                     "/data/user/0/com.snapscan.offline/cache/book-ocr-proof-source.jpg"):
            proof = copy.deepcopy(self.proof); proof["artifacts"]["text"]["path"] = path
            with self.assertRaises(RuntimeError):
                collector.book_ocr_references(proof, 203)


if __name__ == "__main__":
    unittest.main()
