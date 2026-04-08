"""SQLite database schema and query functions for Dongtian."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS wings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wing_id INTEGER NOT NULL REFERENCES wings(id),
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(wing_id, name)
);

CREATE TABLE IF NOT EXISTS drawers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    content TEXT NOT NULL,
    source TEXT,
    source_ts TEXT,
    embedding BLOB,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drawers_room ON drawers(room_id);
CREATE INDEX IF NOT EXISTS idx_drawers_source_ts ON drawers(source_ts);

CREATE VIRTUAL TABLE IF NOT EXISTS drawers_fts USING fts5(
    content, source, content=drawers, content_rowid=id
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS drawers_ai AFTER INSERT ON drawers BEGIN
    INSERT INTO drawers_fts(rowid, content, source)
    VALUES (new.id, new.content, new.source);
END;

CREATE TRIGGER IF NOT EXISTS drawers_ad AFTER DELETE ON drawers BEGIN
    INSERT INTO drawers_fts(drawers_fts, rowid, content, source)
    VALUES ('delete', old.id, old.content, old.source);
END;

CREATE TRIGGER IF NOT EXISTS drawers_au AFTER UPDATE ON drawers BEGIN
    INSERT INTO drawers_fts(drawers_fts, rowid, content, source)
    VALUES ('delete', old.id, old.content, old.source);
    INSERT INTO drawers_fts(rowid, content, source)
    VALUES (new.id, new.content, new.source);
END;

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('person','project','concept','tool')),
    aliases TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(name, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);

CREATE TABLE IF NOT EXISTS triples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object_id INTEGER NOT NULL REFERENCES entities(id),
    confidence REAL DEFAULT 1.0,
    valid_from TEXT,
    valid_to TEXT,
    source_drawer_id INTEGER REFERENCES drawers(id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject_id);
CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object_id);
CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
"""


# ── Wing CRUD ──

def get_or_create_wing(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    row = conn.execute("SELECT id FROM wings WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO wings (name, description, created_at) VALUES (?, ?, ?)",
        (name, description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_wings(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT w.id, w.name, w.description, w.created_at,
               COUNT(r.id) AS room_count
        FROM wings w LEFT JOIN rooms r ON r.wing_id = w.id
        GROUP BY w.id ORDER BY w.name
    """).fetchall()
    return [dict(r) for r in rows]


# ── Room CRUD ──

def get_or_create_room(conn: sqlite3.Connection, wing_id: int, name: str, description: str = "") -> int:
    row = conn.execute(
        "SELECT id FROM rooms WHERE wing_id = ? AND name = ?", (wing_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO rooms (wing_id, name, description, created_at) VALUES (?, ?, ?, ?)",
        (wing_id, name, description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_rooms(conn: sqlite3.Connection, wing_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT r.id, r.name, r.description, r.created_at,
               COUNT(d.id) AS drawer_count
        FROM rooms r
        JOIN wings w ON w.id = r.wing_id
        LEFT JOIN drawers d ON d.room_id = r.id
        WHERE w.name = ?
        GROUP BY r.id ORDER BY r.name
    """, (wing_name,)).fetchall()
    return [dict(r) for r in rows]


# ── Drawer CRUD ──

def insert_drawer(
    conn: sqlite3.Connection,
    room_id: int,
    content: str,
    source: str = "",
    source_ts: str = "",
    metadata: Optional[dict] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO drawers (room_id, content, source, source_ts, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (room_id, content, source, source_ts, json.dumps(metadata or {}), _now()),
    )
    conn.commit()
    return cur.lastrowid


def browse_drawers(
    conn: sqlite3.Connection, wing_name: str, room_name: str, limit: int = 20, offset: int = 0
) -> list[dict]:
    rows = conn.execute("""
        SELECT d.id, d.content, d.source, d.source_ts, d.created_at
        FROM drawers d
        JOIN rooms r ON r.id = d.room_id
        JOIN wings w ON w.id = r.wing_id
        WHERE w.name = ? AND r.name = ?
        ORDER BY d.source_ts DESC, d.id DESC
        LIMIT ? OFFSET ?
    """, (wing_name, room_name, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def update_embedding(conn: sqlite3.Connection, drawer_id: int, embedding_blob: bytes) -> None:
    conn.execute("UPDATE drawers SET embedding = ? WHERE id = ?", (embedding_blob, drawer_id))
    conn.commit()


# ── Entity CRUD ──

def get_or_create_entity(
    conn: sqlite3.Connection, name: str, entity_type: str, aliases: list[str] | None = None
) -> int:
    row = conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = ?", (name, entity_type)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO entities (name, entity_type, aliases, created_at) VALUES (?, ?, ?, ?)",
        (name, entity_type, json.dumps(aliases or []), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_entities(conn: sqlite3.Connection, entity_type: Optional[str] = None) -> list[dict]:
    if entity_type:
        rows = conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY name", (entity_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM entities ORDER BY entity_type, name").fetchall()
    return [dict(r) for r in rows]


# ── Triple CRUD ──

def insert_triple(
    conn: sqlite3.Connection,
    subject_id: int,
    predicate: str,
    object_id: int,
    confidence: float = 1.0,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    source_drawer_id: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO triples
           (subject_id, predicate, object_id, confidence, valid_from, valid_to, source_drawer_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (subject_id, predicate, object_id, confidence, valid_from, valid_to, source_drawer_id, _now()),
    )
    conn.commit()
    return cur.lastrowid


def query_triples(
    conn: sqlite3.Connection,
    entity_name: Optional[str] = None,
    predicate: Optional[str] = None,
    entity_type: Optional[str] = None,
    active_only: bool = True,
) -> list[dict]:
    clauses = []
    params = []
    if entity_name:
        clauses.append("(s.name = ? OR o.name = ?)")
        params.extend([entity_name, entity_name])
    if predicate:
        clauses.append("t.predicate = ?")
        params.append(predicate)
    if entity_type:
        clauses.append("(s.entity_type = ? OR o.entity_type = ?)")
        params.extend([entity_type, entity_type])
    if active_only:
        clauses.append("t.valid_to IS NULL")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(f"""
        SELECT t.id, s.name AS subject, t.predicate, o.name AS object,
               t.confidence, t.valid_from, t.valid_to
        FROM triples t
        JOIN entities s ON s.id = t.subject_id
        JOIN entities o ON o.id = t.object_id
        {where}
        ORDER BY t.created_at DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


# ── FTS search ──

def search_fts(
    conn: sqlite3.Connection,
    query: str,
    wing_name: Optional[str] = None,
    room_name: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    clauses = ["drawers_fts MATCH ?"]
    params: list = [query]
    if wing_name:
        clauses.append("w.name = ?")
        params.append(wing_name)
    if room_name:
        clauses.append("r.name = ?")
        params.append(room_name)
    params.append(limit)
    where = " AND ".join(clauses)
    rows = conn.execute(f"""
        SELECT d.id, d.content, d.source, d.source_ts, w.name AS wing, r.name AS room,
               bm25(drawers_fts) AS score
        FROM drawers_fts
        JOIN drawers d ON d.id = drawers_fts.rowid
        JOIN rooms r ON r.id = d.room_id
        JOIN wings w ON w.id = r.wing_id
        WHERE {where}
        ORDER BY score
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]


def get_drawers_with_embeddings(
    conn: sqlite3.Connection,
    wing_name: Optional[str] = None,
    room_name: Optional[str] = None,
) -> list[dict]:
    clauses = ["d.embedding IS NOT NULL"]
    params = []
    if wing_name:
        clauses.append("w.name = ?")
        params.append(wing_name)
    if room_name:
        clauses.append("r.name = ?")
        params.append(room_name)
    where = " AND ".join(clauses)
    rows = conn.execute(f"""
        SELECT d.id, d.content, d.source, d.source_ts, d.embedding,
               w.name AS wing, r.name AS room
        FROM drawers d
        JOIN rooms r ON r.id = d.room_id
        JOIN wings w ON w.id = r.wing_id
        WHERE {where}
    """, params).fetchall()
    return [dict(r) for r in rows]
