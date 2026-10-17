"""Safety checks for local resource-only APK repackaging."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from mobile.build_android_candidate import build_unsigned_apk, verify_payload
except ImportError:
    build_unsigned_apk = verify_payload = None
try:
    from mobile.build_android_candidate import compiler_succeeded
except ImportError:
    compiler_succeeded = None


class ResourceOnlyApkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "original.apk"
        self.resources = self.root / "www"
        self.resources.mkdir()
        (self.resources / "manifest.json").write_text('{"id":"__UNI__TEST"}')
        (self.resources / "app-service.js").write_text('const updated = true;')
        with zipfile.ZipFile(self.base, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"native manifest")
            archive.writestr("classes.dex", b"native dex")
            archive.writestr("lib/arm64-v8a/runtime.so", b"native library")
            archive.writestr("META-INF/library.version", b"preserve metadata")
            archive.writestr("META-INF/MANIFEST.MF", b"old signature manifest")
            archive.writestr("META-INF/OLD.SF", b"old signature")
            archive.writestr("META-INF/OLD.RSA", b"old certificate")
            archive.writestr("assets/data/dcloud_properties.xml",
                b'<properties><features><feature name="Gallery" value="io.dcloud.js.gallery.GalleryFeatureImpl"/></features><services/></properties>')
            archive.writestr("assets/apps/__UNI__TEST/www/manifest.json", b"old")
            archive.writestr("assets/apps/__UNI__TEST/www/stale.js", b"stale")

    def test_replaces_all_web_resources_but_preserves_native_bytes(self):
        self.assertIsNotNone(build_unsigned_apk, "APK builder is not implemented")
        out = self.root / "new.apk"
        original = self.base.read_bytes()
        build_unsigned_apk(self.base, self.resources, out, "__UNI__TEST")
        self.assertEqual(self.base.read_bytes(), original)
        with zipfile.ZipFile(out) as archive:
            self.assertEqual(archive.read("classes.dex"), b"native dex")
            self.assertEqual(archive.read("META-INF/library.version"), b"preserve metadata")
            self.assertNotIn("META-INF/OLD.RSA", archive.namelist())
            self.assertNotIn("META-INF/MANIFEST.MF", archive.namelist())
            self.assertNotIn("assets/apps/__UNI__TEST/www/stale.js", archive.namelist())
            self.assertEqual(archive.read("assets/apps/__UNI__TEST/www/app-service.js"), b"const updated = true;")
            features = ET.fromstring(archive.read("assets/data/dcloud_properties.xml")).find("features")
            self.assertEqual([(f.get("name"), f.get("value")) for f in features if f.get("name") == "Camera"],
                             [("Camera", "io.dcloud.js.camera.CameraFeatureImpl")])
            self.assertEqual(features.find("feature[@name='Gallery']").get("value"),
                             "io.dcloud.js.gallery.GalleryFeatureImpl")
        result = verify_payload(self.base, out, self.resources, "__UNI__TEST")
        self.assertTrue(result.get("native_changes_match_expected"))
        self.assertEqual(result["changed_native_entries"], ["assets/data/dcloud_properties.xml"])
        self.assertTrue(result["resource_entries_match"])

    def test_refuses_to_overwrite_original(self):
        self.assertIsNotNone(build_unsigned_apk, "APK builder is not implemented")
        with self.assertRaises(ValueError):
            build_unsigned_apk(self.base, self.resources, self.base, "__UNI__TEST")

    def test_rejects_changed_native_entry_in_verification(self):
        self.assertIsNotNone(build_unsigned_apk, "APK builder is not implemented")
        out = self.root / "changed.apk"
        build_unsigned_apk(self.base, self.resources, out, "__UNI__TEST")
        bad = self.root / "tampered.apk"
        with zipfile.ZipFile(out) as source, zipfile.ZipFile(bad, "w") as target:
            for item in source.infolist():
                target.writestr(item, b"changed dex" if item.filename == "classes.dex" else source.read(item))
        result = verify_payload(self.base, bad, self.resources, "__UNI__TEST")
        self.assertFalse(result["native_entries_unchanged"])
        self.assertEqual(result["changed_native_entries"], ["assets/data/dcloud_properties.xml", "classes.dex"])
        self.assertFalse(result.get("native_changes_match_expected"))

    def test_does_not_duplicate_an_existing_camera_registration(self):
        first, second = self.root / "first.apk", self.root / "second.apk"
        build_unsigned_apk(self.base, self.resources, first, "__UNI__TEST")
        build_unsigned_apk(first, self.resources, second, "__UNI__TEST")
        with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
            self.assertEqual(a.read("assets/data/dcloud_properties.xml"), b.read("assets/data/dcloud_properties.xml"))
            features = ET.fromstring(b.read("assets/data/dcloud_properties.xml")).find("features")
            self.assertEqual(len(features.findall("feature[@name='Camera']")), 1)

    def test_rejects_camera_registration_to_the_wrong_class(self):
        wrong = self.root / "wrong.apk"
        with zipfile.ZipFile(self.base) as source, zipfile.ZipFile(wrong, "w") as target:
            for item in source.infolist():
                payload = source.read(item)
                if item.filename == "assets/data/dcloud_properties.xml":
                    payload = b'<properties><features><feature name="Camera" value="example.WrongClass"/></features></properties>'
                target.writestr(item, payload)
        with self.assertRaises(ValueError):
            build_unsigned_apk(wrong, self.resources, self.root / "out.apk", "__UNI__TEST")

    def test_rejects_incomplete_resource_directory(self):
        self.assertIsNotNone(build_unsigned_apk, "APK builder is not implemented")
        (self.resources / "app-service.js").unlink()
        with self.assertRaises(ValueError):
            build_unsigned_apk(self.base, self.resources, self.root / "new.apk", "__UNI__TEST")

    def test_zero_exit_login_gate_is_not_a_successful_compile(self):
        self.assertIsNotNone(compiler_succeeded, "Compiler result validation is not implemented")
        self.assertFalse(compiler_succeeded(0, "21:56:15 此功能需要先登录"))
        self.assertFalse(compiler_succeeded(0, "no compiler output"))
        self.assertTrue(compiler_succeeded(0, " DONE  Build complete. "))
        self.assertFalse(compiler_succeeded(1, " DONE  Build complete. "))


if __name__ == "__main__":
    unittest.main()
