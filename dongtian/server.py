"""MCP Server for Dongtian - exposes 10 tools for Claude Code integration."""

import sqlite3

from mcp.server.fastmcp import FastMCP

from .config import load_config
from . import db as dbmod
from .search import search as do_search
from .ingest import ingest_source as do_ingest, ingest_claude_project as do_ingest_project, ingest_codex_sessions as do_ingest_codex, ingest_opencode_db as do_ingest_opencode
from .graph import extract_and_store
from .remote import sync_remote_host, sync_all_hosts, discover_remote_sessions

mcp = FastMCP("dongtian", instructions="Dongtian: structured memory system for AI conversations")

_conn: sqlite3.Connection | None = None
_config: dict | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn, _config
    if _conn is None:
        _config = load_config()
        _conn = dbmod.init_db(_config["db_path"])
    return _conn


def _get_config() -> dict:
    if _config is None:
        _get_conn()
    return _config


# ── Browse tools ──

@mcp.tool()
def list_wings() -> list[dict]:
    """List all wings (top-level groupings) with room counts."""
    return dbmod.list_wings(_get_conn())


@mcp.tool()
def list_rooms(wing: str) -> list[dict]:
    """List rooms in a wing with drawer counts.

    Args:
        wing: Name of the wing to list rooms for
    """
    return dbmod.list_rooms(_get_conn(), wing)


@mcp.tool()
def browse_room(wing: str, room: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """Browse drawer contents in a specific room with pagination.

    Args:
        wing: Wing name
        room: Room name
        limit: Max results (default 20)
        offset: Skip first N results
    """
    return dbmod.browse_drawers(_get_conn(), wing, room, limit, offset)


# ── Search tools ──

@mcp.tool()
def search(query: str, wing: str = "", room: str = "", mode: str = "hybrid", limit: int = 10) -> list[dict]:
    """Search stored memories using keyword and/or semantic search.

    Args:
        query: Search query text
        wing: Optional wing filter
        room: Optional room filter
        mode: Search mode - "hybrid" (default), "keyword", or "embedding"
        limit: Max results (default 10)
    """
    return do_search(
        _get_conn(), query, _get_config(),
        wing=wing or None, room=room or None,
        mode=mode, limit=limit,
    )


@mcp.tool()
def search_graph(
    entity: str = "", predicate: str = "", entity_type: str = "", active_only: bool = True
) -> list[dict]:
    """Query knowledge graph triples by entity, predicate, or type.

    Args:
        entity: Entity name to search for (as subject or object)
        predicate: Relationship type filter (e.g. "uses", "deployed_on")
        entity_type: Entity type filter ("person", "project", "concept", "tool")
        active_only: Only return currently valid triples (default True)
    """
    return dbmod.query_triples(
        _get_conn(),
        entity_name=entity or None,
        predicate=predicate or None,
        entity_type=entity_type or None,
        active_only=active_only,
    )


# ── Ingestion tools ──

@mcp.tool()
def ingest_source(path: str, source_type: str, wing: str, room: str) -> dict:
    """Ingest a file into the memory palace.

    Args:
        path: File path to ingest
        source_type: Source format - "claude", "chatgpt", "slack", or "text"
        wing: Wing name (auto-created if needed)
        room: Room name (auto-created if needed)
    """
    count = do_ingest(_get_conn(), _get_config(), path, source_type, wing, room)
    return {"drawers_created": count, "wing": wing, "room": room}


@mcp.tool()
def ingest_claude_project(project_path: str, wing: str) -> dict:
    """Bulk-ingest all JSONL session files from a Claude Code project directory.

    Args:
        project_path: Path to Claude project dir (e.g. ~/.claude/projects/D--codex-prj)
        wing: Wing name for all ingested sessions
    """
    return do_ingest_project(_get_conn(), _get_config(), project_path, wing)


@mcp.tool()
def ingest_codex_sessions(sessions_dir: str, wing: str) -> dict:
    """Bulk-ingest all Codex/OpenCode rollout JSONL files.

    Args:
        sessions_dir: Path to Codex sessions dir (e.g. ~/.codex/sessions)
        wing: Wing name for all ingested sessions
    """
    return do_ingest_codex(_get_conn(), _get_config(), sessions_dir, wing)


@mcp.tool()
def ingest_opencode(db_path: str, wing: str) -> dict:
    """Ingest all sessions from an OpenCode (DeepSeek) SQLite database.

    Args:
        db_path: Path to opencode.db (e.g. ~/.local/share/opencode/opencode.db)
        wing: Wing name for all ingested sessions
    """
    return do_ingest_opencode(_get_conn(), _get_config(), db_path, wing)


# ── Knowledge graph tools ──

@mcp.tool()
def add_entity(name: str, entity_type: str, aliases: list[str] | None = None) -> dict:
    """Add or get an entity in the knowledge graph.

    Args:
        name: Canonical entity name
        entity_type: One of "person", "project", "concept", "tool"
        aliases: Optional list of alternate names
    """
    eid = dbmod.get_or_create_entity(_get_conn(), name, entity_type, aliases)
    return {"entity_id": eid, "name": name, "type": entity_type}


@mcp.tool()
def add_triple(
    subject: str, predicate: str, object: str,
    confidence: float = 1.0, valid_from: str = "", valid_to: str = ""
) -> dict:
    """Add a relationship triple to the knowledge graph. Creates entities if they don't exist.

    Args:
        subject: Subject entity name
        predicate: Relationship type (e.g. "uses", "deployed_on", "maintains")
        object: Object entity name
        confidence: Confidence score 0.0-1.0 (default 1.0)
        valid_from: ISO8601 start date (optional)
        valid_to: ISO8601 end date (optional, NULL = still valid)
    """
    conn = _get_conn()
    subj_id = dbmod.get_or_create_entity(conn, subject, "concept")
    obj_id = dbmod.get_or_create_entity(conn, object, "concept")
    tid = dbmod.insert_triple(
        conn, subj_id, predicate, obj_id,
        confidence=confidence,
        valid_from=valid_from or None,
        valid_to=valid_to or None,
    )
    return {"triple_id": tid, "subject": subject, "predicate": predicate, "object": object}


@mcp.tool()
def extract_knowledge(drawer_id: int) -> dict:
    """Run entity and relationship extraction on a specific drawer's content.

    Args:
        drawer_id: ID of the drawer to analyze
    """
    return extract_and_store(_get_conn(), drawer_id)


# ── Remote sync tools ──

@mcp.tool()
def sync_remote(host: str, wing: str = "") -> dict:
    """Pull session data from a remote machine via SSH and ingest into the palace.

    Automatically discovers Claude Code and Codex sessions on the remote host,
    rsync-pulls them, and ingests into a wing named after the host.

    Args:
        host: SSH host string (e.g. "konghm@192.168.91.212", "renchuan-01")
        wing: Optional wing name override (default: auto from hostname)
    """
    return sync_remote_host(
        _get_conn(), _get_config(), host,
        wing_name=wing or None,
    )


@mcp.tool()
def sync_all_remotes() -> dict:
    """Sync session data from all configured remote hosts.

    Reads host list from remote_hosts in ~/.dongtian/config.json.
    """
    return sync_all_hosts(_get_conn(), _get_config())


@mcp.tool()
def discover_remote(host: str) -> dict:
    """Check what session data exists on a remote host without pulling.

    Args:
        host: SSH host string
    """
    return discover_remote_sessions(host)
