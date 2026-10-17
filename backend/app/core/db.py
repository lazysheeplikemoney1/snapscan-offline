import sqlite3
from .config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def has_search_index(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scans_fts'").fetchone() is not None


def init_db():
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS scans(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at INTEGER,
          filename TEXT,
          text TEXT,
          boxes TEXT,
          elapsed_ms INTEGER
        )""")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(scans)")}
        additions = {
            "image": "TEXT", "image_width": "INTEGER", "image_height": "INTEGER",
            "model": "TEXT DEFAULT '{}'", "revision": "INTEGER NOT NULL DEFAULT 0",
            "original_boxes": "TEXT",
        }
        migrated = False
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE scans ADD COLUMN {name} {definition}")
                migrated = True
        conn.execute("UPDATE scans SET original_boxes=boxes WHERE original_boxes IS NULL")
        index_existed = has_search_index(conn)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS scans_fts USING fts5(text, content='scans', content_rowid='id')"
            )
            if migrated or not index_existed:
                conn.execute("INSERT INTO scans_fts(scans_fts) VALUES('rebuild')")
        except sqlite3.OperationalError as exc:
            if "no such module: fts5" not in str(exc).lower():
                raise
            print("[SnapScan] FTS5不可用，回退LIKE")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

init_db()
