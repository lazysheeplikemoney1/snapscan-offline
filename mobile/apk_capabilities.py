"""Inspect Camera/Gallery capability from real APK nodes and class definitions.

This uses only the standard library and never extracts or changes an APK.
``camera_static_ready`` is a structural check, not a real-device camera test.
Malformed mandatory inputs fail closed and are described in ``errors``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

CAMERA_CLASS = "io.dcloud.js.camera.CameraFeatureImpl"
GALLERY_CLASS = "io.dcloud.js.gallery.GalleryFeatureImpl"
FILE_PROVIDER_CLASS = "io.dcloud.common.util.DCloud_FileProvider"
ANDROID_NS = "http://schemas.android.com/apk/res/android"
_FLAGS = ("camera_feature_registered", "gallery_feature_registered", "camera_class_defined",
          "gallery_class_defined", "android_camera_permission", "file_provider_present",
          "runtime_camera_permission", "runtime_gallery_permission")
_ROOT_DEX = re.compile(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex")


def _span(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError("truncated or out-of-range binary data")
    return data[offset:offset + size]


def _u32(data, offset):
    return struct.unpack("<I", _span(data, offset, 4))[0]


def _uleb(data, offset):
    value = 0
    for index in range(5):
        byte = _span(data, offset + index, 1)[0]
        value |= (byte & 127) << (index * 7)
        if byte < 128:
            if value > 0xFFFFFFFF:
                raise ValueError("invalid DEX string length")
            return value, offset + index + 1
    raise ValueError("invalid DEX string length")


def _dex_class_definitions(data):
    """Read descriptor indices only through the DEX class_defs table."""
    if len(data) < 112 or data[:8] not in {b"dex\n" + v + b"\0" for v in (b"035", b"037", b"038", b"039", b"040")}:
        raise ValueError("unsupported or invalid standard DEX header")
    if _u32(data, 32) != len(data) or _u32(data, 36) != 112 or _u32(data, 40) != 0x12345678:
        raise ValueError("invalid DEX size, header size or endian tag")
    if hashlib.sha1(data[32:]).digest() != data[12:32] or (zlib.adler32(data[12:]) & 0xFFFFFFFF) != _u32(data, 8):
        raise ValueError("DEX signature/checksum mismatch")
    string_count, strings = _u32(data, 56), _u32(data, 60)
    type_count, types = _u32(data, 64), _u32(data, 68)
    class_count, classes = _u32(data, 96), _u32(data, 100)
    for count, offset, width in ((string_count, strings, 4), (type_count, types, 4), (class_count, classes, 32)):
        if count and (offset < 112 or offset % 4):
            raise ValueError("invalid DEX table offset")
        _span(data, offset, count * width)
    map_offset = _u32(data, 52)
    if map_offset < 112 or map_offset % 4:
        raise ValueError("invalid DEX map offset")
    _span(data, map_offset + 4, _u32(data, map_offset) * 12)
    definitions = set()
    seen_types = set()
    for index in range(class_count):
        class_type = _u32(data, classes + index * 32)
        if class_type >= type_count or class_type in seen_types:
            raise ValueError("invalid or duplicate DEX class_def index")
        seen_types.add(class_type)
        descriptor_index = _u32(data, types + class_type * 4)
        if descriptor_index >= string_count:
            raise ValueError("invalid DEX descriptor index")
        string_offset = _u32(data, strings + descriptor_index * 4)
        if string_offset < 112:
            raise ValueError("invalid DEX string offset")
        _, begin = _uleb(data, string_offset)
        end = data.find(b"\0", begin)
        if end < 0:
            raise ValueError("unterminated DEX class descriptor")
        descriptor = data[begin:end].decode("utf-8", errors="surrogatepass")
        if not descriptor.startswith("L") or not descriptor.endswith(";"):
            raise ValueError("invalid DEX class descriptor")
        definitions.add(descriptor[1:-1].replace("/", "."))
    return definitions


def _string_pool(chunk):
    header_size = struct.unpack_from("<H", chunk, 2)[0]
    if header_size < 28:
        raise ValueError("invalid XML string pool header")
    count, styles, flags, start = struct.unpack("<4I", _span(chunk, 8, 16))
    if start < header_size + (count + styles) * 4:
        raise ValueError("invalid XML string pool offset")
    _span(chunk, header_size, (count + styles) * 4)
    strings = []
    for index in range(count):
        offset = start + _u32(chunk, header_size + index * 4)
        if flags & 256:
            def length8(at):
                first = _span(chunk, at, 1)[0]
                if first & 128:
                    return ((first & 127) << 8) | _span(chunk, at + 1, 1)[0], at + 2
                return first, at + 1
            _, offset = length8(offset)
            length, offset = length8(offset)
            value = _span(chunk, offset, length).decode("utf-8")
            if _span(chunk, offset + length, 1) != b"\0":
                raise ValueError("unterminated XML UTF-8 string")
        else:
            length = struct.unpack("<H", _span(chunk, offset, 2))[0]
            offset += 2
            if length & 0x8000:
                length = ((length & 0x7FFF) << 16) | struct.unpack("<H", _span(chunk, offset, 2))[0]
                offset += 2
            value = _span(chunk, offset, length * 2).decode("utf-16le")
            if _span(chunk, offset + length * 2, 2) != b"\0\0":
                raise ValueError("unterminated XML UTF-16 string")
        strings.append(value)
    return strings


def _xml_nodes(data):
    """Return (actual element path, namespace-qualified attributes) pairs."""
    if data.lstrip().startswith(b"<"):
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("XML declarations with entities are not supported")
        root = ET.fromstring(data)
        result = []
        def visit(node, parent):
            path = (*parent, node.tag)
            result.append((path, dict(node.attrib)))
            for child in node:
                visit(child, path)
        visit(root, ())
        return result
    if len(data) < 8 or struct.unpack_from("<HHI", data) != (3, 8, len(data)):
        raise ValueError("invalid binary XML header or size")
    strings = None
    nodes, stack = [], []
    root_seen = False
    offset = 8
    while offset < len(data):
        kind, header, size = struct.unpack("<HHI", _span(data, offset, 8))
        if header < 8 or size < header:
            raise ValueError("invalid binary XML chunk size")
        chunk = _span(data, offset, size)
        if kind == 1:
            if strings is not None:
                raise ValueError("duplicate binary XML string pool")
            strings = _string_pool(chunk)
        elif kind in (0x0102, 0x0103):
            if strings is None or header != 16:
                raise ValueError("missing XML string pool or invalid node header")
            def get_string(index):
                if index >= len(strings):
                    raise ValueError("invalid XML string index")
                return strings[index]
            name = get_string(_u32(chunk, 20))
            if kind == 0x0103:
                if not stack or stack.pop() != name:
                    raise ValueError("unbalanced binary XML element")
            else:
                attribute_start, attribute_size, attribute_count = struct.unpack("<3H", _span(chunk, 24, 6))
                if attribute_start < 20 or attribute_size < 20:
                    raise ValueError("invalid XML attribute layout")
                _span(chunk, 16 + attribute_start, attribute_count * attribute_size)
                if not stack:
                    if root_seen:
                        raise ValueError("multiple XML roots")
                    root_seen = True
                stack.append(name)
                attrs = {}
                for index in range(attribute_count):
                    start = 16 + attribute_start + index * attribute_size
                    namespace, attr_name, raw = struct.unpack("<3I", _span(chunk, start, 12))
                    key = get_string(attr_name)
                    if namespace != 0xFFFFFFFF:
                        key = "{" + get_string(namespace) + "}" + key
                    value_type = chunk[start + 15]
                    value = _u32(chunk, start + 16)
                    if raw != 0xFFFFFFFF:
                        value = get_string(raw)
                    elif value_type == 3:
                        value = get_string(value)
                    if key in attrs:
                        raise ValueError("duplicate XML attribute")
                    attrs[key] = value
                nodes.append((tuple(stack), attrs))
        offset += size
    if stack or not root_seen:
        raise ValueError("incomplete binary XML document")
    return nodes


def inspect_apk_capabilities(apk_path, appid):
    """Return eight capability flags, camera_static_ready, evidence and errors.

    ``appid`` is the UniApp resource ID, e.g. ``__UNI__751ED2B``. FileProvider
    authority is checked against the Android manifest's *package*, not this ID.
    Every failed parser contributes to ``errors`` and prevents a ready verdict.
    Only root ``classes.dex`` / ``classes2.dex`` ... are considered executable.
    """
    if not isinstance(appid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", appid) or appid in (".", ".."):
        raise ValueError("appid must be a single valid resource directory name")
    runtime_path = f"assets/apps/{appid}/www/manifest.json"
    result = {key: False for key in _FLAGS}
    result.update({"camera_static_ready": False, "errors": [], "evidence": {
        "features_xml": {"path": "assets/data/dcloud_properties.xml", "entries": []},
        "dex": {"root_dex_files": [], "required_classes": {"camera": CAMERA_CLASS, "gallery": GALLERY_CLASS},
                "class_definitions": {CAMERA_CLASS: [], GALLERY_CLASS: []}, "class_definition_counts": {}},
        "android_manifest": {"path": "AndroidManifest.xml", "package": None,
                             "permissions": [], "providers": [], "expected_file_provider": FILE_PROVIDER_CLASS},
        "runtime_manifest": {"path": runtime_path, "appid": None, "permissions": []},
    }})
    evidence = result["evidence"]

    def failed(entry, exc):
        result["errors"].append({"entry": entry, "reason": str(exc)})

    try:
        with zipfile.ZipFile(Path(apk_path)) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("duplicate APK ZIP entries")
            feature_path = evidence["features_xml"]["path"]
            try:
                features = [attrs for path, attrs in _xml_nodes(archive.read(feature_path))
                            if path == ("properties", "features", "feature")]
                entries = [{"name": node.get("name"), "value": node.get("value")} for node in features]
                evidence["features_xml"]["entries"] = entries
                for label, expected in (("camera", CAMERA_CLASS), ("gallery", GALLERY_CLASS)):
                    matches = [node for node in entries if node["name"] == label.title()]
                    result[label + "_feature_registered"] = len(matches) == 1 and matches[0]["value"] == expected
            except Exception as exc:
                failed(feature_path, exc)

            dex_evidence = evidence["dex"]
            dex_evidence["root_dex_files"] = sorted(name for name in names if _ROOT_DEX.fullmatch(name))
            for name in dex_evidence["root_dex_files"]:
                try:
                    definitions = _dex_class_definitions(archive.read(name))
                    dex_evidence["class_definition_counts"][name] = len(definitions)
                    for expected in (CAMERA_CLASS, GALLERY_CLASS):
                        if expected in definitions:
                            dex_evidence["class_definitions"][expected].append(name)
                except Exception as exc:
                    failed(name, exc)
            result["camera_class_defined"] = bool(dex_evidence["class_definitions"][CAMERA_CLASS])
            result["gallery_class_defined"] = bool(dex_evidence["class_definitions"][GALLERY_CLASS])

            try:
                nodes = _xml_nodes(archive.read("AndroidManifest.xml"))
                manifests = [attrs for path, attrs in nodes if path == ("manifest",)]
                if len(manifests) != 1 or not isinstance(manifests[0].get("package"), str):
                    raise ValueError("Android manifest must have one root and a package")
                android = evidence["android_manifest"]
                android["package"] = manifests[0]["package"]
                name_attr = "{" + ANDROID_NS + "}name"
                authority_attr = "{" + ANDROID_NS + "}authorities"
                android["permissions"] = sorted({attrs[name_attr] for path, attrs in nodes
                                                  if path == ("manifest", "uses-permission")
                                                  and isinstance(attrs.get(name_attr), str)})
                android["providers"] = [{"name": attrs.get(name_attr), "authorities": attrs.get(authority_attr)}
                                        for path, attrs in nodes if path == ("manifest", "application", "provider")]
                expected_authority = android["package"] + ".dc.fileprovider"
                android["expected_file_provider_authority"] = expected_authority
                result["android_camera_permission"] = "android.permission.CAMERA" in android["permissions"]
                result["file_provider_present"] = any(node == {"name": FILE_PROVIDER_CLASS, "authorities": expected_authority}
                                                       for node in android["providers"])
            except Exception as exc:
                failed("AndroidManifest.xml", exc)

            try:
                manifest = json.loads(archive.read(runtime_path).decode("utf-8-sig"))
                if manifest.get("id") != appid or not isinstance(manifest.get("permissions"), dict):
                    raise ValueError("runtime manifest ID or permissions is invalid")
                runtime = evidence["runtime_manifest"]
                runtime["appid"] = manifest["id"]
                runtime["permissions"] = sorted(manifest["permissions"])
                result["runtime_camera_permission"] = isinstance(manifest["permissions"].get("Camera"), dict)
                result["runtime_gallery_permission"] = isinstance(manifest["permissions"].get("Gallery"), dict)
            except Exception as exc:
                failed(runtime_path, exc)
    except Exception as exc:
        failed("APK", exc)
    result["camera_static_ready"] = all(result[key] for key in _FLAGS) and not result["errors"]
    return result
