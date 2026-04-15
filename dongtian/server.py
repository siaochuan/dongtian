"""MCP Server for Dongtian - exposes tools for cave system integration."""

import sqlite3
import threading
import traceback
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import load_config
from . import db as dbmod
from .search import search as do_search
from .ingest import ingest_source as do_ingest, ingest_claude_project as do_ingest_project, ingest_codex_sessions as do_ingest_codex, ingest_opencode_db as do_ingest_opencode
from .graph import extract_and_store
from .remote import sync_remote_host, sync_all_hosts, discover_remote_sessions
from .hook_candidates import run_hook_candidate_update, default_chamber_for_today

mcp = FastMCP("dongtian", instructions="Dongtian: cave system memory for AI conversations")

_conn: sqlite3.Connection | None = None
_config: dict | None = None
_daily_update_lock = threading.Lock()
_daily_update_state: dict = {
    "last_trigger_date": "",
    "running": False,
    "last_started_at": "",
    "last_finished_at": "",
    "last_status": "never",
    "last_error": "",
    "last_output_dir": "",
    "last_result": {},
}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _state_snapshot() -> dict:
    with _daily_update_lock:
        return dict(_daily_update_state)


def _ingest_hook_artifacts(
    conn: sqlite3.Connection,
    config: dict,
    update_result: dict,
    *,
    layer: str = "",
    chamber: str = "",
) -> dict:
    layer_name = layer.strip() if layer else str(config.get("hook_candidate_layer", "dongtian-system"))
    chamber_name = chamber.strip() if chamber else default_chamber_for_today(config)
    files = [
        update_result.get("report_path", ""),
        update_result.get("candidate_rules_path", ""),
        update_result.get("meta_path", ""),
    ]
    strata_created = 0
    ingested_files: list[str] = []
    for file_path in files:
        if not file_path:
            continue
        path = Path(file_path).expanduser()
        if not path.exists():
            continue
        count = do_ingest(conn, config, str(path), "text", layer_name, chamber_name)
        strata_created += int(count)
        ingested_files.append(str(path))
    return {
        "layer": layer_name,
        "chamber": chamber_name,
        "strata_created": strata_created,
        "files": ingested_files,
    }


def _run_daily_hook_update_worker(day_key: str) -> None:
    try:
        config = load_config()
        result = run_hook_candidate_update(config, since_days=None, emit_timestamped=True)
        ingest_result: dict | None = None
        if bool(config.get("hook_candidate_auto_ingest", True)):
            conn = dbmod.init_db(config["db_path"])
            try:
                chamber_prefix = str(config.get("hook_candidate_chamber_prefix", "hook_candidates")).strip() or "hook_candidates"
                ingest_result = _ingest_hook_artifacts(
                    conn,
                    config,
                    result,
                    layer=str(config.get("hook_candidate_layer", "dongtian-system")),
                    chamber=f"{day_key}_{chamber_prefix}",
                )
            finally:
                conn.close()
        with _daily_update_lock:
            _daily_update_state["running"] = False
            _daily_update_state["last_finished_at"] = _now_str()
            _daily_update_state["last_status"] = "ok"
            _daily_update_state["last_error"] = ""
            _daily_update_state["last_output_dir"] = str(result.get("latest_dir", ""))
            _daily_update_state["last_result"] = {
                "update": result,
                "ingest": ingest_result or {},
            }
    except Exception:
        with _daily_update_lock:
            _daily_update_state["running"] = False
            _daily_update_state["last_finished_at"] = _now_str()
            _daily_update_state["last_status"] = "error"
            _daily_update_state["last_error"] = traceback.format_exc(limit=3)


def _maybe_trigger_daily_async_update(config: dict | None = None, *, force: bool = False) -> dict:
    cfg = config or load_config()
    if not bool(cfg.get("hook_candidate_auto_update", True)):
        return {"triggered": False, "reason": "disabled", "state": _state_snapshot()}

    day_key = _today_str()
    with _daily_update_lock:
        if not force:
            if _daily_update_state.get("running"):
                return {"triggered": False, "reason": "running", "state": dict(_daily_update_state)}
            if _daily_update_state.get("last_trigger_date") == day_key:
                return {"triggered": False, "reason": "already_triggered_today", "state": dict(_daily_update_state)}

        _daily_update_state["last_trigger_date"] = day_key
        _daily_update_state["running"] = True
        _daily_update_state["last_started_at"] = _now_str()
        _daily_update_state["last_status"] = "running"
        _daily_update_state["last_error"] = ""

        worker = threading.Thread(
            target=_run_daily_hook_update_worker,
            args=(day_key,),
            daemon=True,
            name="dongtian-hook-daily-update",
        )
        worker.start()
        return {"triggered": True, "reason": "started", "state": dict(_daily_update_state)}


def _get_conn() -> sqlite3.Connection:
    global _conn, _config
    if _config is None:
        _config = load_config()
    _maybe_trigger_daily_async_update(_config)
    if _conn is None:
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
        source_type: Source format - "claude", "codex", "chatgpt", "opencode", "slack", or "text"
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
    _maybe_trigger_daily_async_update(_get_config())
    return discover_remote_sessions(host)


# ── Hook candidate tools ──

@mcp.tool()
def mine_hook_candidates(
    since_days: float = 0.0,
    layer: str = "",
    chamber: str = "",
    auto_ingest: bool = True,
    emit_timestamped: bool = True,
) -> dict:
    """Mine hook candidate rules from OpenHarness sessions and optionally ingest report artifacts.

    Args:
        since_days: Lookback window in days (<=0 means use config default)
        layer: Optional ingest layer override
        chamber: Optional ingest chamber override
        auto_ingest: Ingest generated report/candidate/meta files into Dongtian
        emit_timestamped: Also write a timestamped snapshot directory
    """
    config = _get_config()
    lookback = since_days if since_days > 0 else None
    result = run_hook_candidate_update(config, since_days=lookback, emit_timestamped=emit_timestamped)
    ingest_result = {}
    if auto_ingest:
        ingest_result = _ingest_hook_artifacts(
            _get_conn(),
            config,
            result,
            layer=layer,
            chamber=chamber,
        )
    return {
        "update": result,
        "ingest": ingest_result,
    }


@mcp.tool()
def hook_update_status(force_trigger: bool = False) -> dict:
    """Get async hook-update status and optionally force-trigger background update.

    Args:
        force_trigger: When true, trigger async update immediately regardless of day key
    """
    trigger = _maybe_trigger_daily_async_update(_get_config(), force=force_trigger)
    return {
        "trigger": trigger,
        "status": _state_snapshot(),
    }
