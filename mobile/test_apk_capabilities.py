"""Regression tests for the native Camera registration missed by repackaging."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib

try:
    from mobile.apk_capabilities import inspect_apk_capabilities
except ImportError:
    inspect_apk_capabilities = None

ROOT = Path(__file__).resolve().parents[1]
APPID = "__UNI__751ED2B"
CAMERA = "io.dcloud.js.camera.CameraFeatureImpl"
GALLERY = "io.dcloud.js.gallery.GalleryFeatureImpl"
PROVIDER = "io.dcloud.common.util.DCloud_FileProvider"
ANDROID = "http://schemas.android.com/apk/res/android"
FLAGS = ("camera_feature_registered", "gallery_feature_registered", "camera_class_defined",
         "gallery_class_defined", "android_camera_permission", "file_provider_present",
         "runtime_camera_permission", "runtime_gallery_permission")


def dex_fixture(defined=(CAMERA, GALLERY), references=()):
    """Serialize a checksummed minimal DEX with separate type/class_def tables."""
    descriptor = lambda name: "L" + name.replace(".", "/") + ";"
    types = sorted({descriptor(name) for name in (*defined, *references)} | {"Ljava/lang/Object;"})
    strings = types
    string_ids = 112
    type_ids = string_ids + len(strings) * 4
    class_defs = type_ids + len(types) * 4
    data_offset = class_defs + len(defined) * 32
    data = bytearray()
    offsets = []
    for value in strings:
        offsets.append(data_offset + len(data))
        encoded = value.encode("utf-8")
        data.extend(bytes([len(value)]) + encoded + b"\0")
    while (data_offset + len(data)) % 4:
        data.append(0)
    map_offset = data_offset + len(data)
    entries = [(0x0000, 1, 0), (0x0001, len(strings), string_ids),
               (0x0002, len(types), type_ids)]
    if defined:
        entries.append((0x0006, len(defined), class_defs))
    entries.extend([(0x2002, len(strings), data_offset), (0x1000, 1, map_offset)])
    data.extend(struct.pack("<I", len(entries)))
    for kind, count, offset in entries:
        data.extend(struct.pack("<HHII", kind, 0, count, offset))
    result = bytearray(112)
    result[:8] = b"dex\n035\0"
    header = [data_offset + len(data), 112, 0x12345678, 0, 0, map_offset,
              len(strings), string_ids, len(types), type_ids, 0, 0, 0, 0,
              0, 0, len(defined), class_defs if defined else 0, len(data), data_offset]
    struct.pack_into("<20I", result, 32, *header)
    for offset in offsets:
        result.extend(struct.pack("<I", offset))
    for index in range(len(types)):
        result.extend(struct.pack("<I", index))
    for name in sorted(defined):
        result.extend(struct.pack("<8I", types.index(descriptor(name)), 1,
                                  types.index("Ljava/lang/Object;"), 0, 0xFFFFFFFF, 0, 0, 0))
    result.extend(data)
    result[12:32] = hashlib.sha1(result[32:]).digest()
    struct.pack_into("<I", result, 8, zlib.adler32(result[12:]) & 0xFFFFFFFF)
    return bytes(result)


def axml_fixture(permission=True, provider_authority="com.snapscan.fixture.dc.fileprovider",
                 provider_in_application=True):
    """Binary Android XML with string-pool decoys and real hierarchy."""
    events = [("start", "manifest", {"package": "com.snapscan.fixture"}),
              ("start", "uses-permission", {"name": "android.permission.CAMERA" if permission else "android.permission.INTERNET"}),
              ("end", "uses-permission", {}), ("start", "application", {}),
              ("start", "meta-data", {"name": "decoy", "value": "android.permission.CAMERA"}),
              ("end", "meta-data", {})]
    if not provider_in_application:
        events.append(("end", "application", {}))
    events.extend([("start", "provider", {"name": PROVIDER, "authorities": provider_authority}),
                   ("end", "provider", {})])
    if provider_in_application:
        events.append(("end", "application", {}))
    events.append(("end", "manifest", {}))
    strings = list(dict.fromkeys([ANDROID, "android"] + [s for _, name, attrs in events
                                                        for s in [name, *attrs.keys(), *attrs.values()]]))
    string_data = bytearray()
    offsets = []
    for value in strings:
        encoded = value.encode("utf-8")
        assert len(encoded) < 128
        offsets.append(len(string_data))
        string_data.extend(bytes([len(value), len(encoded)]) + encoded + b"\0")
    while len(string_data) % 4:
        string_data.append(0)
    start = 28 + len(strings) * 4
    pool = (struct.pack("<HHI5I", 1, 28, start + len(string_data), len(strings), 0, 256, start, 0)
            + struct.pack("<" + "I" * len(strings), *offsets) + string_data)
    body = bytearray(pool)
    for kind, name, attrs in events:
        if kind == "end":
            body.extend(struct.pack("<HHI4I", 0x0103, 16, 24, 1, 0xFFFFFFFF,
                                    0xFFFFFFFF, strings.index(name)))
            continue
        body.extend(struct.pack("<HHI4I6H", 0x0102, 16, 36 + len(attrs) * 20,
                                1, 0xFFFFFFFF, 0xFFFFFFFF, strings.index(name), 20, 20,
                                len(attrs), 0, 0, 0))
        for key, value in attrs.items():
            namespace = 0xFFFFFFFF if key == "package" else strings.index(ANDROID)
            body.extend(struct.pack("<3IHBBI", namespace, strings.index(key), strings.index(value),
                                    8, 0, 3, strings.index(value)))
    return struct.pack("<HHI", 3, 8, len(body) + 8) + body


def properties(camera=True, camera_class=CAMERA):
    root = ET.Element("properties")
    features = ET.SubElement(root, "features")
    ET.SubElement(features, "feature", name="Gallery", value=GALLERY)
    if camera:
        ET.SubElement(features, "feature", name="Camera", value=camera_class)
    return ET.tostring(root, encoding="utf-8")


class ApkCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(inspect_apk_capabilities, "Native APK capabilities inspector is not implemented")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def apk(self, **changes):
        entries = {"AndroidManifest.xml": axml_fixture(), "classes.dex": dex_fixture(),
                   "assets/data/dcloud_properties.xml": properties(),
                   f"assets/apps/{APPID}/www/manifest.json": json.dumps({
                       "id": APPID, "permissions": {"Camera": {}, "Gallery": {}}}).encode()}
        for key, value in changes.items():
            if value is None:
                entries.pop(key, None)
            else:
                entries[key] = value
        path = self.root / "fixture.apk"
        with zipfile.ZipFile(path, "w") as archive:
            for key, value in entries.items():
                archive.writestr(key, value)
        return path

    def actual_dev_fixture(self, camera):
        source = ROOT / "releases/SnapScan-rec-head-v1-dev.apk"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        path = self.root / ("patched.apk" if camera else "legacy.apk")
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(path, "w") as new:
            for info in old.infolist():
                value = old.read(info)
                if info.filename == "assets/data/dcloud_properties.xml":
                    tree = ET.fromstring(value)
                    features = tree.find("features")
                    for node in list(features):
                        if node.get("name") == "Camera":
                            features.remove(node)
                    if camera:
                        ET.SubElement(features, "feature", name="Camera", value=CAMERA)
                    value = ET.tostring(tree, encoding="utf-8")
                new.writestr(info, value)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        return path

    def test_legacy_dev_apk_fails_only_missing_camera_registration(self):
        result = inspect_apk_capabilities(self.actual_dev_fixture(False), APPID)
        self.assertFalse(result["camera_feature_registered"])
        self.assertFalse(result["camera_static_ready"])
        self.assertTrue(all(result[key] for key in FLAGS if key != "camera_feature_registered"))
        self.assertEqual(result["errors"], [])

    def test_only_patching_features_xml_makes_actual_dev_apk_ready(self):
        result = inspect_apk_capabilities(self.actual_dev_fixture(True), APPID)
        self.assertTrue(result["camera_static_ready"], result)
        self.assertTrue(all(result[key] for key in FLAGS))
        self.assertEqual(result["evidence"]["dex"]["required_classes"]["camera"], CAMERA)
        self.assertEqual(result["evidence"]["android_manifest"]["package"], "com.snapscan.offline")

    def test_binary_axml_and_real_dex_definitions_are_ready(self):
        result = inspect_apk_capabilities(self.apk(), APPID)
        self.assertTrue(result["camera_static_ready"], result)
        self.assertEqual(result["evidence"]["dex"]["root_dex_files"], ["classes.dex"])

    def test_class_names_in_reference_tables_are_not_definitions(self):
        result = inspect_apk_capabilities(self.apk(**{
            "classes.dex": dex_fixture(defined=(), references=(CAMERA, GALLERY))}), APPID)
        self.assertFalse(result["camera_class_defined"])
        self.assertFalse(result["gallery_class_defined"])
        self.assertFalse(result["camera_static_ready"])

    def test_nonstandard_or_asset_dex_does_not_supply_classes(self):
        for name in ("assets/classes.dex", "classes1.dex", "classes02.dex", "extra.dex"):
            with self.subTest(name=name):
                result = inspect_apk_capabilities(self.apk(**{"classes.dex": None, name: dex_fixture()}), APPID)
                self.assertFalse(result["camera_class_defined"])
                self.assertEqual(result["evidence"]["dex"]["root_dex_files"], [])

    def test_permission_string_in_metadata_does_not_grant_permission(self):
        result = inspect_apk_capabilities(self.apk(**{"AndroidManifest.xml": axml_fixture(permission=False)}), APPID)
        self.assertFalse(result["android_camera_permission"])
        self.assertTrue(result["file_provider_present"])
        self.assertFalse(result["camera_static_ready"])

    def test_provider_must_be_inside_application_with_matching_authority(self):
        for changes in ({"provider_authority": "other.package.dc.fileprovider"},
                        {"provider_in_application": False}):
            with self.subTest(changes=changes):
                result = inspect_apk_capabilities(self.apk(**{"AndroidManifest.xml": axml_fixture(**changes)}), APPID)
                self.assertFalse(result["file_provider_present"])
                self.assertFalse(result["camera_static_ready"])

    def test_camera_feature_requires_exact_implementation(self):
        result = inspect_apk_capabilities(self.apk(**{
            "assets/data/dcloud_properties.xml": properties(camera_class="fake." + CAMERA)}), APPID)
        self.assertFalse(result["camera_feature_registered"])

    def test_runtime_permissions_must_be_in_requested_app_manifest(self):
        result = inspect_apk_capabilities(self.apk(**{
            f"assets/apps/{APPID}/www/manifest.json": json.dumps({"id": APPID, "permissions": {"Gallery": {}}}).encode(),
            "assets/apps/OTHER/www/manifest.json": json.dumps({"permissions": {"Camera": {}}}).encode()}), APPID)
        self.assertFalse(result["runtime_camera_permission"])
        self.assertTrue(result["runtime_gallery_permission"])

    def test_corrupt_root_dex_fails_closed(self):
        damaged = bytearray(dex_fixture())
        damaged[-1] ^= 1
        result = inspect_apk_capabilities(self.apk(**{"classes.dex": bytes(damaged)}), APPID)
        self.assertFalse(result["camera_class_defined"])
        self.assertFalse(result["camera_static_ready"])
        self.assertTrue(result["errors"])

    def test_truncated_binary_xml_fails_closed(self):
        result = inspect_apk_capabilities(self.apk(**{"AndroidManifest.xml": axml_fixture()[:-5]}), APPID)
        self.assertFalse(result["android_camera_permission"])
        self.assertFalse(result["camera_static_ready"])
        self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
