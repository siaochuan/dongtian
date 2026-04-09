"""Remote SSH sync — pull session data from other machines into the cavern."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import db as dbmod
from .ingest import ingest_source, ingest_claude_project, ingest_opencode_db, _embed_strata
from .embeddings import get_client

log = logging.getLogger(__name__)

# Default remote session paths to scan
_REMOTE_PATHS = {
    "claude": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "opencode": "~/.local/share/opencode/opencode.db",
}


def _ssh_run(host: str, cmd: str, *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command on a remote host via SSH."""
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def _ssh_check(host: str) -> bool:
    """Check if SSH to host is reachable."""
    try:
        r = _ssh_run(host, "echo ok", timeout=15)
        return r.returncode == 0 and "ok" in r.stdout
    except (subprocess.TimeoutExpired, Exception):
        return False


def _rsync_pull(host: str, remote_path: str, local_dir: str, *, timeout: int = 120) -> bool:
    """Pull remote directory to local via rsync over SSH."""
    try:
        r = subprocess.run(
            [
                "rsync", "-az", "--timeout=30",
                "-e", "ssh -o ConnectTimeout=10 -o BatchMode=yes",
                f"{host}:{remote_path}/",
                f"{local_dir}/",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("rsync from %s failed: %s", host, e)
        return False


def _scp_pull(host: str, remote_path: str, local_path: str, *, timeout: int = 60) -> bool:
    """Pull a single file from remote via SCP."""
    try:
        r = subprocess.run(
            ["scp", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{host}:{remote_path}", local_path],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("scp from %s failed: %s", host, e)
        return False


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _get_synced_hashes(conn, layer_name: str) -> set[str]:
    """Get set of content hashes already in a layer to skip duplicates."""
    rows = conn.execute("""
        SELECT d.metadata FROM strata d
        JOIN chambers r ON d.chamber_id = r.id
        JOIN layers w ON r.layer_id = w.id
        WHERE w.name = ? AND d.metadata LIKE '%sync_hash%'
    """, (layer_name,)).fetchall()
    hashes = set()
    for row in rows:
        try:
            m = json.loads(row[0])
            if h := m.get("sync_hash"):
                hashes.add(h)
        except (json.JSONDecodeError, TypeError):
            pass
    return hashes


def discover_remote_sessions(host: str) -> dict:
    """Discover what session data exists on a remote host.

    Returns dict with counts per source type.
    """
    result = {}
    for src_type, rpath in _REMOTE_PATHS.items():
        try:
            if rpath.endswith(".db"):
                # Single file (e.g. opencode.db) — check existence and session count
                r = _ssh_run(host, f"test -f {rpath} && sqlite3 {rpath} 'SELECT count(*) FROM session' 2>/dev/null || echo 0")
                count = int(r.stdout.strip()) if r.returncode == 0 else 0
            else:
                r = _ssh_run(host, f"find {rpath} -name '*.jsonl' 2>/dev/null | wc -l")
                count = int(r.stdout.strip()) if r.returncode == 0 else 0
            result[src_type] = count
        except Exception:
            result[src_type] = 0
    return result


def sync_remote_host(
    conn,
    config: dict,
    host: str,
    layer_name: Optional[str] = None,
    source_types: Optional[list[str]] = None,
) -> dict:
    """Sync session data from a remote host into the local cavern.

    Args:
        conn: SQLite connection
        config: Dongtian config dict
        host: SSH host (e.g. "user@192.168.1.50" or SSH alias)
        layer_name: Layer name override (default: host short name)
        source_types: Which types to sync (default: all available)

    Returns dict with sync stats.
    """
    if not _ssh_check(host):
        return {"error": f"Cannot reach {host} via SSH", "host": host}

    # Derive layer name from host
    if not layer_name:
        # "konghm@192.168.91.212" → "212", "renchuan-01" → "renchuan-01"
        h = host.split("@")[-1]
        if h.replace(".", "").isdigit():
            layer_name = h.split(".")[-1]
        else:
            layer_name = h
        layer_name = f"remote-{layer_name}"

    types_to_sync = source_types or list(_REMOTE_PATHS.keys())
    stats = {"host": host, "layer": layer_name, "synced": {}}

    with tempfile.TemporaryDirectory(prefix="dongtian-sync-") as tmpdir:
        for src_type in types_to_sync:
            rpath = _REMOTE_PATHS.get(src_type)
            if not rpath:
                continue

            # Handle single-file sources (opencode.db) vs directory sources
            is_single_file = rpath.endswith(".db")

            # Check if remote path exists
            try:
                check_cmd = f"test -f {rpath}" if is_single_file else f"test -d {rpath}"
                r = _ssh_run(host, f"{check_cmd} && echo yes || echo no")
                if "yes" not in r.stdout:
                    stats["synced"][src_type] = {"skipped": "path not found"}
                    continue
            except Exception:
                stats["synced"][src_type] = {"skipped": "check failed"}
                continue

            local_pull = Path(tmpdir) / src_type
            local_pull.mkdir(exist_ok=True)

            if is_single_file:
                # SCP single file
                local_file = local_pull / Path(rpath).name
                log.info("Pulling %s from %s:%s ...", src_type, host, rpath)
                ok = _scp_pull(host, rpath, str(local_file))
            else:
                log.info("Pulling %s from %s:%s ...", src_type, host, rpath)
                ok = _rsync_pull(host, rpath, str(local_pull))

            if not ok:
                stats["synced"][src_type] = {"error": "pull failed"}
                continue

            # Ingest pulled data
            if src_type == "claude":
                count = _ingest_pulled_claude(conn, config, local_pull, layer_name)
            elif src_type == "codex":
                count = _ingest_pulled_codex(conn, config, local_pull, layer_name)
            elif src_type == "opencode":
                db_file = local_pull / "opencode.db"
                if db_file.exists():
                    result = ingest_opencode_db(conn, config, str(db_file), layer_name)
                    count = result.get("strata", 0)
                else:
                    count = 0
            else:
                count = 0

            stats["synced"][src_type] = {"strata": count}

    return stats


def _ingest_pulled_claude(conn, config: dict, local_dir: Path, layer_name: str) -> int:
    """Ingest Claude project dirs pulled from a remote host."""
    total = 0
    # Claude projects dir contains subdirs per project, each with .jsonl files
    for project_dir in sorted(local_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        jsonl_files = sorted(project_dir.glob("*.jsonl"))
        for jf in jsonl_files:
            chamber_name = jf.stem[:12]
            try:
                count = ingest_source(conn, config, str(jf), "claude", layer_name, chamber_name)
                total += count
            except Exception as e:
                log.warning("Failed to ingest %s: %s", jf, e)
    return total


def _ingest_pulled_codex(conn, config: dict, local_dir: Path, layer_name: str) -> int:
    """Ingest Codex session files pulled from a remote host."""
    total = 0
    # Codex sessions may be nested: sessions/2026/03/24/rollout-*.jsonl
    for jf in sorted(local_dir.rglob("rollout-*.jsonl")):
        stem = jf.stem
        chamber_name = stem[8:18] if stem.startswith("rollout-") and len(stem) > 18 else stem[:12]
        try:
            count = ingest_source(conn, config, str(jf), "codex", layer_name, chamber_name)
            total += count
        except Exception as e:
            log.warning("Failed to ingest %s: %s", jf, e)
    return total


def sync_all_hosts(conn, config: dict) -> dict:
    """Sync from all configured remote hosts.

    Reads host list from config["remote_hosts"].
    """
    hosts = config.get("remote_hosts", [])
    if not hosts:
        return {"error": "No remote_hosts configured in ~/.dongtian/config.json"}

    results = {}
    for entry in hosts:
        if isinstance(entry, str):
            host, layer = entry, None
        elif isinstance(entry, dict):
            host = entry["host"]
            layer = entry.get("wing") or entry.get("layer")
        else:
            continue

        log.info("Syncing from %s ...", host)
        results[host] = sync_remote_host(conn, config, host, layer_name=layer)

    return results
