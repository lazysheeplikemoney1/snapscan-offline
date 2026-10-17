"""Prepare renamed OFL fonts with complete deployed-CTC coverage.

Only five missing outlines are imported from pinned official Noto sources, so
the Android bundle does not need another full CJK font. Runtime recovery of
other Unicode uses a small square/space seed and PDF ToUnicode mappings.
"""
from pathlib import Path
import copy
import hashlib
import json
import urllib.request
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools import subset

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "build/android-local/font-source"
DEST = ROOT / "mobile/android-local/app/src/main/assets/fonts"
BUILD_VERSION = 3
PINNED = {
    "NotoSansSC-wght.ttf": ("https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf", "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"),
    "OFL.txt": ("https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt", "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"),
    "NotoSans-Regular.ttf": ("https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf", "b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5"),
    "NotoSansSymbols2-Regular.ttf": ("https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSansSymbols2/NotoSansSymbols2-Regular.ttf", "630846d528dbe4c4981370a4d0a9475a1fd1491a129bb411f8e157cdb5de13c6"),
    "NotoSansCJKsc-VF.ttf": ("https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/TTF/NotoSansCJKsc-VF.ttf", "990c807e79c25662a5a9ecf7f971baeb2bf2eab9a559e5ecf15cdfdb8561d21f"),
    "OFL-noto-fonts.txt": ("https://raw.githubusercontent.com/notofonts/noto-fonts/main/LICENSE", "0dab92d0544f7b233403f14b84a663bdbfa746982eda629e7f4f9ffe1b036feb"),
    "OFL-noto-cjk.txt": ("https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE", "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"),
}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rename(font, family, postscript):
    names = {1: family, 2: "Regular", 3: postscript + "-v2", 4: family + " Regular",
             6: postscript, 16: family, 17: "Regular"}
    for record in font["name"].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())


def _add_outline(target, source, codepoint):
    location = {"wght": 400} if "fvar" in source else None
    glyphs = source.getGlyphSet(location=location)
    old_name = source.getBestCmap()[codepoint]
    recording = DecomposingRecordingPen(glyphs)
    glyphs[old_name].draw(recording)
    pen = TTGlyphPen(None)
    scale = target["head"].unitsPerEm / source["head"].unitsPerEm
    recording.replay(TransformPen(pen, (scale, 0, 0, scale, 0, 0)))
    glyph = pen.glyph()
    glyph.recalcBounds(target["glyf"])
    name = "snapscan.uni" + format(codepoint, "X")
    order = target.getGlyphOrder() + [name]
    target.setGlyphOrder(order)
    target["glyf"][name] = glyph
    target["hmtx"].metrics[name] = (round(glyphs[old_name].width * scale), getattr(glyph, "xMin", 0))
    if "vmtx" in target:
        advance, bearing = source["vmtx"].metrics[old_name] if "vmtx" in source else (source["head"].unitsPerEm, 0)
        target["vmtx"].metrics[name] = (round(advance * scale), round(bearing * scale))
    for table in target["cmap"].tables:
        if table.isUnicode() and (codepoint <= 0xFFFF or table.format in (12, 13)):
            table.cmap[codepoint] = name
    target["maxp"].numGlyphs = len(order)


def _separate_cmap_aliases(font):
    """PDFBox generates ToUnicode by reverse GID lookup; aliases are ambiguous.

    Keep outlines/metrics identical but give every Unicode scalar its own GID,
    including Kangxi radicals and compatibility ideographs. No glyph is drawn
    differently, and all Unicode cmap formats are updated consistently.
    """
    cmap=font.getBestCmap()
    used=set();order=list(font.getGlyphOrder());count=0
    horizontal=font['hmtx'].metrics
    vertical=font['vmtx'].metrics if 'vmtx' in font else None
    for codepoint,old_name in sorted(cmap.items()):
        if old_name not in used:
            used.add(old_name);continue
        name='snapscan.alias'+format(codepoint,'X')
        font['glyf'][name]=copy.deepcopy(font['glyf'][old_name])
        horizontal[name]=horizontal[old_name]
        if vertical is not None:vertical[name]=vertical[old_name]
        order.append(name);count+=1
        for table in font['cmap'].tables:
            if table.isUnicode() and codepoint in table.cmap:table.cmap[codepoint]=name
    if len(order)>=65535:raise ValueError('Unicode-safe font exceeds TrueType glyph count')
    font.setGlyphOrder(order);font['maxp'].numGlyphs=len(order)
    return count


def prepare_fonts():
    SOURCE.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST / "font_manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("build_version") == BUILD_VERSION and all(
                (DEST / name).is_file() and _sha(DEST / name) == entry["sha256"]
                for name, entry in previous.get("files", {}).items()) and len(previous.get("files", {})) == 5:
            return previous
    for filename, (url, expected) in PINNED.items():
        path = SOURCE / filename
        if not path.exists():
            with urllib.request.urlopen(url, timeout=180) as response:
                path.write_bytes(response.read())
        if _sha(path) != expected:
            raise ValueError("Pinned official font source changed: " + filename)
    base = TTFont(SOURCE / "NotoSansSC-wght.ttf", recalcTimestamp=False)
    base = instantiateVariableFont(base, {"wght": 400}, inplace=True)
    additions = [("NotoSansSymbols2-Regular.ttf", "❤❋"), ("NotoSans-Regular.ttf", "ɔʌ"),
                 ("NotoSansCJKsc-VF.ttf", "𣇉")]
    copyright_names = set()
    for filename, text in additions:
        source = TTFont(SOURCE / filename, recalcTimestamp=False)
        for item in source["name"].names:
            if item.nameID == 0:
                copyright_names.add(item.toUnicode())
        for character in text:
            _add_outline(base, source, ord(character))
        source.close()
    for item in base["name"].names:
        if item.nameID == 0:
            item.string = (item.toUnicode() + "\n" + "\n".join(sorted(copyright_names))).encode(item.getEncoding(), errors="replace")
    _rename(base, "SnapScan Sans SC", "SnapScanSansSC-Regular")
    aliases_separated=_separate_cmap_aliases(base)
    base.save(DEST / "SnapScanSansSC.ttf")
    seed = TTFont(DEST / "SnapScanSansSC.ttf", recalcTimestamp=False)
    options = subset.Options()
    options.recalc_timestamp = False
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=[0x20, 0x25A1])
    subsetter.subset(seed)
    _rename(seed, "SnapScan Unicode Seed", "SnapScanUnicodeSeed-Regular")
    seed.save(DEST / "SnapScanUnicodeSeed.ttf")
    for filename in ("OFL.txt", "OFL-noto-fonts.txt", "OFL-noto-cjk.txt"):
        (DEST / filename).write_bytes((SOURCE / filename).read_bytes())
    dictionary = json.loads((ROOT / "mobile/android-local/app/src/main/assets/models/characters.json").read_text(encoding="utf-8"))
    cmap = TTFont(DEST / "SnapScanSansSC.ttf").getBestCmap()
    missing = [label for label in dictionary[1:] if any(ord(character) not in cmap for character in label)]
    if missing:
        raise ValueError("Refusing incomplete deployed CTC font coverage: " + repr(missing))
    manifest = {"build_version": BUILD_VERSION, "license": "SIL Open Font License 1.1",
                "modification": "Static weight 400; five missing CTC outlines imported from OFL Noto fonts; shared Unicode GIDs separated without visual changes; renamed SnapScan; square/space seed subset",
                "unicode_aliases_separated": aliases_separated,
                "sources": [{"filename": name, "url": url, "sha256": sha} for name, (url, sha) in PINNED.items()],
                "model_ctc_labels": len(dictionary) - 1, "model_missing_labels": missing,
                "imported_codepoints": ["U+2764", "U+274B", "U+0254", "U+028C", "U+231C9"], "files": {}}
    for name in ("SnapScanSansSC.ttf", "SnapScanUnicodeSeed.ttf", "OFL.txt", "OFL-noto-fonts.txt", "OFL-noto-cjk.txt"):
        path = DEST / name
        manifest["files"][name] = {"sha256": _sha(path), "bytes": path.stat().st_size}
    manifest_path.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return manifest


if __name__ == "__main__":
    print(json.dumps(prepare_fonts(), ensure_ascii=False, indent=2))
