"""Canonical persisted documents shared by history and exports."""
import copy
import json
import re

from ..core.db import get_conn
from .layout import layout_fields

_IMAGE = re.compile(r"/uploads/scan_[0-9a-f]{32}\.png")


def _json_value(value, kind):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        parsed = None
    return copy.deepcopy(parsed) if isinstance(parsed, kind) else kind()


def document_from_row(row):
    """Normalize JSON fields without inventing missing legacy images or boxes."""
    if row is None:
        return None
    document = dict(row)
    document["boxes"] = _json_value(document.get("boxes"), list)
    originals = document.get("original_boxes")
    document["original_boxes"] = _json_value(originals, list) if originals is not None else copy.deepcopy(document["boxes"])
    document["model"] = _json_value(document.get("model"), dict)
    document["text"] = document.get("text") or ""
    # Derived on read: old database rows and optimistic-lock revisions are not
    # migrated or rewritten merely to expose the corrected reading order.
    document.update(layout_fields(document["boxes"], document["text"]))
    document["revision"] = document.get("revision") or 0
    image = document.get("image")
    document["image"] = image if isinstance(image, str) and _IMAGE.fullmatch(image) else None
    for key in ("image_width", "image_height"):
        document[key] = document.get(key) if document["image"] else None
    return document


def load_document(sid):
    conn = get_conn()
    try:
        return document_from_row(conn.execute("SELECT * FROM scans WHERE id=?", (sid,)).fetchone())
    finally:
        conn.close()
