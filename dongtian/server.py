"""MCP Server for Dongtian - exposes tools for cave system integration."""

import sqlite3

from mcp.server.fastmcp import FastMCP

from .config import load_config
from . import db as dbmod
from .search import search as do_search
from .ingest import ingest_source as do_ingest, ingest_claude_project as do_ingest_project, ingest_codex_sessions as do_ingest_codex, ingest_opencode_db as do_ingest_opencode
from .graph import extract_and_store
from .remote import sync_remote_host, sync_all_hosts, discover_remote_sessions

mcp = FastMCP("dongtian", instructions="Dongtian: cave system memory for AI conversations")

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
def list_layers() -> list[dict]:
    """List all layers (top-level groupings) with chamber counts."""
    return dbmod.list_layers(_get_conn())


@mcp.tool()
def list_chambers(layer: str) -> list[dict]:
    """List chambers in a layer with stratum counts.

    Args:
        layer: Name of the layer to list chambers for
    """
    return dbmod.list_chambers(_get_conn(), layer)


@mcp.tool()
def browse_chamber(layer: str, chamber: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """Browse stratum contents in a specific chamber with pagination.

    Args:
        layer: Layer name
        chamber: Chamber name
        limit: Max results (default 20)
        offset: Skip first N results
    """
    return dbmod.browse_strata(_get_conn(), layer, chamber, limit, offset)


# ── Search tools ──

@mcp.tool()
def search(query: str, layer: str = "", chamber: str = "", mode: str = "hybrid", limit: int = 10) -> list[dict]:
    """Search stored memories using keyword and/or semantic search.

    Args:
        query: Search query text
        layer: Optional layer filter
        chamber: Optional chamber filter
        mode: Search mode - "hybrid" (default), "keyword", or "embedding"
        limit: Max results (default 10)
    """
    return do_search(
        _get_conn(), query, _get_config(),
        layer=layer or None, chamber=chamber or None,
        mode=mode, limit=limit,
    )


@mcp.tool()
def survey(
    deposit: str = "", predicate: str = "", deposit_type: str = "", active_only: bool = True
) -> list[dict]:
    """Query cave survey (knowledge graph) passages by deposit, predicate, or type.

    Args:
        deposit: Deposit name to search for (as subject or object)
        predicate: Relationship type filter (e.g. "uses", "deployed_on")
        deposit_type: Deposit type filter ("person", "project", "concept", "tool")
        active_only: Only return currently valid passages (default True)
    """
    return dbmod.query_passages(
        _get_conn(),
        deposit_name=deposit or None,
        predicate=predicate or None,
        deposit_type=deposit_type or None,
        active_only=active_only,
    )


# ── Ingestion tools ──

@mcp.tool()
def ingest_source(path: str, source_type: str, layer: str, chamber: str) -> dict:
    """Ingest a file into the cavern.

    Args:
        path: File path to ingest
        source_type: Source format - "claude", "chatgpt", "slack", or "text"
        layer: Layer name (auto-created if needed)
        chamber: Chamber name (auto-created if needed)
    """
    count = do_ingest(_get_conn(), _get_config(), path, source_type, layer, chamber)
    return {"strata_created": count, "layer": layer, "chamber": chamber}


@mcp.tool()
def ingest_claude_project(project_path: str, layer: str) -> dict:
    """Bulk-ingest all JSONL session files from a Claude Code project directory.

    Args:
        project_path: Path to Claude project dir (e.g. ~/.claude/projects/D--codex-prj)
        layer: Layer name for all ingested sessions
    """
    return do_ingest_project(_get_conn(), _get_config(), project_path, layer)


@mcp.tool()
def ingest_codex_sessions(sessions_dir: str, layer: str) -> dict:
    """Bulk-ingest all Codex/OpenCode rollout JSONL files.

    Args:
        sessions_dir: Path to Codex sessions dir (e.g. ~/.codex/sessions)
        layer: Layer name for all ingested sessions
    """
    return do_ingest_codex(_get_conn(), _get_config(), sessions_dir, layer)


@mcp.tool()
def ingest_opencode(db_path: str, layer: str) -> dict:
    """Ingest all sessions from an OpenCode (DeepSeek) SQLite database.

    Args:
        db_path: Path to opencode.db (e.g. ~/.local/share/opencode/opencode.db)
        layer: Layer name for all ingested sessions
    """
    return do_ingest_opencode(_get_conn(), _get_config(), db_path, layer)


# ── Cave survey tools ──

@mcp.tool()
def add_deposit(name: str, deposit_type: str, aliases: list[str] | None = None) -> dict:
    """Add or get a deposit in the cave survey.

    Args:
        name: Canonical deposit name
        deposit_type: One of "person", "project", "concept", "tool"
        aliases: Optional list of alternate names
    """
    did = dbmod.get_or_create_deposit(_get_conn(), name, deposit_type, aliases)
    return {"deposit_id": did, "name": name, "type": deposit_type}


@mcp.tool()
def add_passage(
    subject: str, predicate: str, object: str,
    confidence: float = 1.0, valid_from: str = "", valid_to: str = ""
) -> dict:
    """Add a passage (relationship) to the cave survey. Creates deposits if they don't exist.

    Args:
        subject: Subject deposit name
        predicate: Relationship type (e.g. "uses", "deployed_on", "maintains")
        object: Object deposit name
        confidence: Confidence score 0.0-1.0 (default 1.0)
        valid_from: ISO8601 start date (optional)
        valid_to: ISO8601 end date (optional, NULL = still valid)
    """
    conn = _get_conn()
    subj_id = dbmod.get_or_create_deposit(conn, subject, "concept")
    obj_id = dbmod.get_or_create_deposit(conn, object, "concept")
    pid = dbmod.insert_passage(
        conn, subj_id, predicate, obj_id,
        confidence=confidence,
        valid_from=valid_from or None,
        valid_to=valid_to or None,
    )
    return {"passage_id": pid, "subject": subject, "predicate": predicate, "object": object}


@mcp.tool()
def extract_survey(stratum_id: int) -> dict:
    """Run deposit and passage extraction on a specific stratum's content.

    Args:
        stratum_id: ID of the stratum to analyze
    """
    return extract_and_store(_get_conn(), stratum_id)


# ── Remote sync tools ──

@mcp.tool()
def sync_remote(host: str, layer: str = "") -> dict:
    """Pull session data from a remote machine via SSH and ingest into the cavern.

    Automatically discovers Claude Code and Codex sessions on the remote host,
    rsync-pulls them, and ingests into a layer named after the host.

    Args:
        host: SSH host string (e.g. "user@192.168.1.50", "dev-machine")
        layer: Optional layer name override (default: auto from hostname)
    """
    return sync_remote_host(
        _get_conn(), _get_config(), host,
        layer_name=layer or None,
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
