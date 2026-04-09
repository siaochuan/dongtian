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
CREATE TABLE IF NOT EXISTS layers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chambers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer_id INTEGER NOT NULL REFERENCES layers(id),
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(layer_id, name)
);

CREATE TABLE IF NOT EXISTS strata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chamber_id INTEGER NOT NULL REFERENCES chambers(id),
    content TEXT NOT NULL,
    source TEXT,
    source_ts TEXT,
    embedding BLOB,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strata_chamber ON strata(chamber_id);
CREATE INDEX IF NOT EXISTS idx_strata_source_ts ON strata(source_ts);

CREATE VIRTUAL TABLE IF NOT EXISTS strata_fts USING fts5(
    content, source, content=strata, content_rowid=id
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS strata_ai AFTER INSERT ON strata BEGIN
    INSERT INTO strata_fts(rowid, content, source)
    VALUES (new.id, new.content, new.source);
END;

CREATE TRIGGER IF NOT EXISTS strata_ad AFTER DELETE ON strata BEGIN
    INSERT INTO strata_fts(strata_fts, rowid, content, source)
    VALUES ('delete', old.id, old.content, old.source);
END;

CREATE TRIGGER IF NOT EXISTS strata_au AFTER UPDATE ON strata BEGIN
    INSERT INTO strata_fts(strata_fts, rowid, content, source)
    VALUES ('delete', old.id, old.content, old.source);
    INSERT INTO strata_fts(rowid, content, source)
    VALUES (new.id, new.content, new.source);
END;

CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    deposit_type TEXT NOT NULL CHECK(deposit_type IN ('person','project','concept','tool')),
    aliases TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(name, deposit_type)
);

CREATE INDEX IF NOT EXISTS idx_deposits_type ON deposits(deposit_type);

CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES deposits(id),
    predicate TEXT NOT NULL,
    object_id INTEGER NOT NULL REFERENCES deposits(id),
    confidence REAL DEFAULT 1.0,
    valid_from TEXT,
    valid_to TEXT,
    source_stratum_id INTEGER REFERENCES strata(id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_passages_subject ON passages(subject_id);
CREATE INDEX IF NOT EXISTS idx_passages_object ON passages(object_id);
CREATE INDEX IF NOT EXISTS idx_passages_predicate ON passages(predicate);
"""


# ── Layer CRUD ──

def get_or_create_layer(conn: sqlite3.Connection, name: str, description: str = "") -> int:
    row = conn.execute("SELECT id FROM layers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO layers (name, description, created_at) VALUES (?, ?, ?)",
        (name, description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_layers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT w.id, w.name, w.description, w.created_at,
               COUNT(r.id) AS chamber_count
        FROM layers w LEFT JOIN chambers r ON r.layer_id = w.id
        GROUP BY w.id ORDER BY w.name
    """).fetchall()
    return [dict(r) for r in rows]


# ── Chamber CRUD ──

def get_or_create_chamber(conn: sqlite3.Connection, layer_id: int, name: str, description: str = "") -> int:
    row = conn.execute(
        "SELECT id FROM chambers WHERE layer_id = ? AND name = ?", (layer_id, name)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO chambers (layer_id, name, description, created_at) VALUES (?, ?, ?, ?)",
        (layer_id, name, description, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_chambers(conn: sqlite3.Connection, layer_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT r.id, r.name, r.description, r.created_at,
               COUNT(d.id) AS stratum_count
        FROM chambers r
        JOIN layers w ON w.id = r.layer_id
        LEFT JOIN strata d ON d.chamber_id = r.id
        WHERE w.name = ?
        GROUP BY r.id ORDER BY r.name
    """, (layer_name,)).fetchall()
    return [dict(r) for r in rows]


# ── Stratum CRUD ──

def insert_stratum(
    conn: sqlite3.Connection,
    chamber_id: int,
    content: str,
    source: str = "",
    source_ts: str = "",
    metadata: Optional[dict] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO strata (chamber_id, content, source, source_ts, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (chamber_id, content, source, source_ts, json.dumps(metadata or {}), _now()),
    )
    conn.commit()
    return cur.lastrowid


def browse_strata(
    conn: sqlite3.Connection, layer_name: str, chamber_name: str, limit: int = 20, offset: int = 0
) -> list[dict]:
    rows = conn.execute("""
        SELECT d.id, d.content, d.source, d.source_ts, d.created_at
        FROM strata d
        JOIN chambers r ON r.id = d.chamber_id
        JOIN layers w ON w.id = r.layer_id
        WHERE w.name = ? AND r.name = ?
        ORDER BY d.source_ts DESC, d.id DESC
        LIMIT ? OFFSET ?
    """, (layer_name, chamber_name, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def update_embedding(conn: sqlite3.Connection, stratum_id: int, embedding_blob: bytes) -> None:
    conn.execute("UPDATE strata SET embedding = ? WHERE id = ?", (embedding_blob, stratum_id))
    conn.commit()


# ── Deposit CRUD ──

def get_or_create_deposit(
    conn: sqlite3.Connection, name: str, deposit_type: str, aliases: list[str] | None = None
) -> int:
    row = conn.execute(
        "SELECT id FROM deposits WHERE name = ? AND deposit_type = ?", (name, deposit_type)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO deposits (name, deposit_type, aliases, created_at) VALUES (?, ?, ?, ?)",
        (name, deposit_type, json.dumps(aliases or []), _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_deposits(conn: sqlite3.Connection, deposit_type: Optional[str] = None) -> list[dict]:
    if deposit_type:
        rows = conn.execute(
            "SELECT * FROM deposits WHERE deposit_type = ? ORDER BY name", (deposit_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM deposits ORDER BY deposit_type, name").fetchall()
    return [dict(r) for r in rows]


# ── Passage CRUD ──

def insert_passage(
    conn: sqlite3.Connection,
    subject_id: int,
    predicate: str,
    object_id: int,
    confidence: float = 1.0,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    source_stratum_id: Optional[int] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO passages
           (subject_id, predicate, object_id, confidence, valid_from, valid_to, source_stratum_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (subject_id, predicate, object_id, confidence, valid_from, valid_to, source_stratum_id, _now()),
    )
    conn.commit()
    return cur.lastrowid


def query_passages(
    conn: sqlite3.Connection,
    deposit_name: Optional[str] = None,
    predicate: Optional[str] = None,
    deposit_type: Optional[str] = None,
    active_only: bool = True,
) -> list[dict]:
    clauses = []
    params = []
    if deposit_name:
        clauses.append("(s.name = ? OR o.name = ?)")
        params.extend([deposit_name, deposit_name])
    if predicate:
        clauses.append("t.predicate = ?")
        params.append(predicate)
    if deposit_type:
        clauses.append("(s.deposit_type = ? OR o.deposit_type = ?)")
        params.extend([deposit_type, deposit_type])
    if active_only:
        clauses.append("t.valid_to IS NULL")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(f"""
        SELECT t.id, s.name AS subject, t.predicate, o.name AS object,
               t.confidence, t.valid_from, t.valid_to
        FROM passages t
        JOIN deposits s ON s.id = t.subject_id
        JOIN deposits o ON o.id = t.object_id
        {where}
        ORDER BY t.created_at DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


# ── FTS search ──

def search_fts(
    conn: sqlite3.Connection,
    query: str,
    layer_name: Optional[str] = None,
    chamber_name: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    clauses = ["strata_fts MATCH ?"]
    params: list = [query]
    if layer_name:
        clauses.append("w.name = ?")
        params.append(layer_name)
    if chamber_name:
        clauses.append("r.name = ?")
        params.append(chamber_name)
    params.append(limit)
    where = " AND ".join(clauses)
    rows = conn.execute(f"""
        SELECT d.id, d.content, d.source, d.source_ts, w.name AS layer, r.name AS chamber,
               bm25(strata_fts) AS score
        FROM strata_fts
        JOIN strata d ON d.id = strata_fts.rowid
        JOIN chambers r ON r.id = d.chamber_id
        JOIN layers w ON w.id = r.layer_id
        WHERE {where}
        ORDER BY score
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]


def get_strata_with_embeddings(
    conn: sqlite3.Connection,
    layer_name: Optional[str] = None,
    chamber_name: Optional[str] = None,
) -> list[dict]:
    clauses = ["d.embedding IS NOT NULL"]
    params = []
    if layer_name:
        clauses.append("w.name = ?")
        params.append(layer_name)
    if chamber_name:
        clauses.append("r.name = ?")
        params.append(chamber_name)
    where = " AND ".join(clauses)
    rows = conn.execute(f"""
        SELECT d.id, d.content, d.source, d.source_ts, d.embedding,
               w.name AS layer, r.name AS chamber
        FROM strata d
        JOIN chambers r ON r.id = d.chamber_id
        JOIN layers w ON w.id = r.layer_id
        WHERE {where}
    """, params).fetchall()
    return [dict(r) for r in rows]
