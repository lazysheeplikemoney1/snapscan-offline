"""Lightweight publication metadata regressions; no SDK/device is invoked."""
import unittest
import build_local


class PackageMetadataTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(build_local, "package_metadata", None)),
                        "Publication must read version metadata from the actual APK")

    def test_report_uses_the_actual_apk_version_without_a_fixed_release_number(self):
        self.assertEqual(build_local.package_metadata(
            "package: name='com.snapscan.offline' versionCode='201' versionName='0.2.1-local' compileSdkVersion='35'\n"),
            {"version_code": 201, "version_name": "0.2.1-local"})

    def test_a_different_package_cannot_be_published_under_the_snapscan_name(self):
        with self.assertRaises(RuntimeError):
            build_local.package_metadata("package: name='com.example.other' versionCode='201' versionName='0.2.1-local'\n")

    def test_missing_or_malformed_versions_are_rejected(self):
        for bad in ("", "package: name='com.snapscan.offline' versionName='0.2.1-local'\n",
                    "package: name='com.snapscan.offline' versionCode='wrong' versionName='0.2.1-local'\n"):
            with self.assertRaises(RuntimeError):
                build_local.package_metadata(bad)


class TestFixturePublicationTest(unittest.TestCase):
    def test_old_and_new_test_assets_cannot_be_published(self):
        self.assertTrue(callable(getattr(build_local, "reject_test_fixtures", None)))
        for name in ("assets/book_ocr/real-010-old-dev.jpg", "assets/book_ocr/real-010-gt.json",
                     "assets/independent-20261016/inputs.json", "assets/receipt-feedback.png"):
            with self.assertRaises(RuntimeError):
                build_local.reject_test_fixtures({"AndroidManifest.xml", name})

    def test_models_fonts_and_web_assets_are_allowed(self):
        self.assertTrue(callable(getattr(build_local, "reject_test_fixtures", None)))
        build_local.reject_test_fixtures({"assets/models/rec_head_v1.onnx", "assets/fonts/SnapScanSansSC.ttf", "assets/www/index.html"})


if __name__ == "__main__":
    unittest.main()
