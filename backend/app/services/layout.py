"""Conservative reading rows that reference, but never reorder, OCR boxes.

This groups line fragments; it does not reconstruct paragraphs or table cells.
All three runtimes share evaluation/fixtures/text_layout_cases.json.
"""
import math
from numbers import Real
from statistics import median


def _text(item):
    value = item.get("text", "") if isinstance(item, dict) else ""
    return value if isinstance(value, str) else ""


def _joined_text(boxes, rows):
    lines = []
    for row in rows:
        values = [_text(boxes[index]) for index in row if _text(boxes[index]) != ""]
        if values:
            lines.append("  ".join(values))
    return "\n".join(lines)


def _geometry(item):
    points = item.get("box") if isinstance(item, dict) else None
    if not isinstance(points, (list, tuple)) or len(points) != 4:
        return None
    output = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in point):
            return None
        output.append((float(point[0]), float(point[1])))
    for index in range(4):
        a, b, c = output[index], output[(index+1) % 4], output[(index+2) % 4]
        cross = (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])
        if not math.isfinite(cross) or cross <= 1e-6 or math.dist(a, b) <= 1e-6:
            return None
    tl, tr, br, bl = output
    theta = math.atan2((tr[1]-tl[1])+(br[1]-bl[1]), (tr[0]-tl[0])+(br[0]-bl[0]))
    if abs(theta) > math.pi/4:
        return None
    return output, theta


def build_layout(boxes):
    """Return rows of original indices, generated text and spatial/fallback mode.

    Any unusable quadrilateral makes the whole document fall back to its input
    order. Row seeds never drift as fragments join; the smaller height controls
    matching, so a tall label cannot absorb several nearby receipt rows.
    """
    boxes = boxes if isinstance(boxes, (list, tuple)) else []
    fallback_rows = [[index] for index in range(len(boxes))]
    fallback = {"rows": fallback_rows, "text": _joined_text(boxes, fallback_rows), "mode": "original_order"}
    if not boxes:
        return fallback
    geometry = [_geometry(item) for item in boxes]
    if any(item is None for item in geometry):
        return fallback
    theta = median(item[1] for item in geometry)
    cosine, sine = math.cos(theta), math.sin(theta)
    fragments = []
    for index, (points, _) in enumerate(geometry):
        us = [x*cosine+y*sine for x, y in points]
        vs = [-x*sine+y*cosine for x, y in points]
        low, high = min(vs), max(vs)
        height, center = high-low, (low+high)/2
        if not all(math.isfinite(value) for value in [*us, *vs, height, center]) or height <= 1e-6:
            return fallback
        fragments.append({"index": index, "u": min(us), "right": max(us), "low": low, "high": high, "height": height, "center": center})
    # Taller body fragments establish row seeds before tiny marginal marks;
    # otherwise a tiny note can split two legitimate same-row field boxes.
    fragments.sort(key=lambda value: (-value["height"], value["center"], value["u"], value["index"]))
    rows = []
    for fragment in fragments:
        choices = []
        for index, row in enumerate(rows):
            seed = row["seed"]
            short = min(seed["height"], fragment["height"])
            distance = abs(seed["center"]-fragment["center"])
            intersection = max(0., min(seed["high"], fragment["high"])-max(seed["low"], fragment["low"]))
            if distance <= .6*short and intersection/short >= .3:
                # Curved full text lines have tall enclosing rectangles. Two
                # such lines may overlap vertically but cannot occupy one row.
                cross_line = any(
                    max(0., min(other["right"], fragment["right"])-max(other["u"], fragment["u"]))
                    > .5*min(other["right"]-other["u"], fragment["right"]-fragment["u"])
                    and abs(other["center"]-fragment["center"]) > .2*min(other["height"], fragment["height"])
                    for other in row["fragments"]
                )
                if not cross_line:
                    choices.append((distance/short, index))
        if choices:
            rows[min(choices)[1]]["fragments"].append(fragment)
        else:
            rows.append({"seed": fragment, "fragments": [fragment]})
    rows.sort(key=lambda row: (row["seed"]["center"], row["seed"]["u"], row["seed"]["index"]))
    indices = [[fragment["index"] for fragment in sorted(row["fragments"], key=lambda value: (value["u"], value["index"]))] for row in rows]
    return {"rows": indices, "text": _joined_text(boxes, indices), "mode": "spatial"}


def layout_fields(boxes, text=None):
    """Boxes' current texts are authoritative; no-box legacy text stays intact."""
    layout = build_layout(boxes)
    generated = layout["text"] if boxes or text is None else text
    return {"text": generated, "text_rows": layout["rows"], "text_layout": layout["mode"]}
