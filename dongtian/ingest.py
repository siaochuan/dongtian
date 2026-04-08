"""Multi-source conversation ingestion for Dongtian."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from . import db as dbmod
from .embeddings import get_client, pack_embedding


Chunk = tuple[str, str, str]  # (content, source_label, timestamp)


# ── Chunking helpers ──

def _split_long(text: str, max_len: int = 1500) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # find sentence boundary before max_len
        cut = text.rfind("。", 0, max_len)
        if cut == -1:
            cut = text.rfind(". ", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1 or cut < 200:
            cut = max_len
        else:
            cut += 1
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return [c for c in chunks if len(c) >= 50]


# ── Claude Code JSONL parser ──

def parse_claude_jsonl(path: str) -> Generator[Chunk, None, None]:
    session_id = Path(path).stem[:8]
    exchanges: list[tuple[str, str, str]] = []  # (role, content, ts)

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = entry.get("type", "")
            ts = entry.get("timestamp", "")
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()

            if role == "human":
                content = _extract_human_content(entry)
                if content:
                    exchanges.append(("user", content, ts))
            elif role == "assistant":
                content = _extract_assistant_content(entry)
                if content:
                    exchanges.append(("assistant", content, ts))

    # pair user+assistant turns
    i = 0
    while i < len(exchanges):
        parts = []
        ts = exchanges[i][2]
        if exchanges[i][0] == "user":
            parts.append(f"User: {exchanges[i][1]}")
            i += 1
            if i < len(exchanges) and exchanges[i][0] == "assistant":
                parts.append(f"Assistant: {exchanges[i][1]}")
                i += 1
        else:
            parts.append(f"Assistant: {exchanges[i][1]}")
            i += 1
        combined = "\n\n".join(parts)
        for chunk in _split_long(combined):
            yield (chunk, f"claude:{session_id}", ts)


def _extract_human_content(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts).strip()
    return ""


def _extract_assistant_content(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                # skip thinking, tool_use, tool_result
        return "\n".join(texts).strip()
    return ""


# ── ChatGPT JSON parser ──

def parse_chatgpt_json(path: str) -> Generator[Chunk, None, None]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conversations = data if isinstance(data, list) else [data]

    for convo in conversations:
        title = convo.get("title", "untitled")
        mapping = convo.get("mapping", {})
        messages = _flatten_chatgpt_mapping(mapping)

        i = 0
        while i < len(messages):
            parts = []
            ts = messages[i].get("ts", "")
            role = messages[i].get("role", "")
            text = messages[i].get("text", "")
            if role == "user":
                parts.append(f"User: {text}")
                i += 1
                if i < len(messages) and messages[i]["role"] == "assistant":
                    parts.append(f"Assistant: {messages[i]['text']}")
                    ts = ts or messages[i].get("ts", "")
                    i += 1
            else:
                parts.append(f"Assistant: {text}")
                i += 1
            combined = "\n\n".join(parts)
            for chunk in _split_long(combined):
                yield (chunk, f"chatgpt:{title[:30]}", ts)


def _flatten_chatgpt_mapping(mapping: dict) -> list[dict]:
    messages = []
    # find root node
    root_id = None
    for nid, node in mapping.items():
        if node.get("parent") is None:
            root_id = nid
            break
    if not root_id:
        return messages
    # BFS traversal
    queue = [root_id]
    while queue:
        nid = queue.pop(0)
        node = mapping.get(nid, {})
        msg = node.get("message")
        if msg and msg.get("content", {}).get("parts"):
            role = msg.get("author", {}).get("role", "")
            if role in ("user", "assistant"):
                text = " ".join(str(p) for p in msg["content"]["parts"] if isinstance(p, str))
                ts = ""
                if ct := msg.get("create_time"):
                    ts = datetime.fromtimestamp(ct, tz=timezone.utc).isoformat()
                if text.strip():
                    messages.append({"role": role, "text": text.strip(), "ts": ts})
        for child_id in node.get("children", []):
            queue.append(child_id)
    return messages


# ── Slack JSON parser ──

def parse_slack_json(path: str) -> Generator[Chunk, None, None]:
    p = Path(path)
    if p.is_dir():
        json_files = sorted(p.glob("*.json"))
    else:
        json_files = [p]

    channel_name = p.stem if p.is_file() else p.name

    for jf in json_files:
        with open(jf, "r", encoding="utf-8") as f:
            messages = json.load(f)
        if not isinstance(messages, list):
            continue
        # group by 5-minute windows
        windows: list[list[dict]] = []
        current_window: list[dict] = []
        window_start = 0.0

        for msg in messages:
            ts_val = float(msg.get("ts", "0"))
            if not current_window or (ts_val - window_start) < 300:
                if not current_window:
                    window_start = ts_val
                current_window.append(msg)
            else:
                windows.append(current_window)
                current_window = [msg]
                window_start = ts_val
        if current_window:
            windows.append(current_window)

        for window in windows:
            lines = []
            first_ts = ""
            for msg in window:
                user = msg.get("user", msg.get("username", "unknown"))
                text = msg.get("text", "")
                if text.strip():
                    lines.append(f"{user}: {text}")
                if not first_ts and msg.get("ts"):
                    first_ts = datetime.fromtimestamp(
                        float(msg["ts"]), tz=timezone.utc
                    ).isoformat()
            combined = "\n".join(lines)
            for chunk in _split_long(combined):
                yield (chunk, f"slack:{channel_name}", first_ts)


# ── Generic text/markdown parser ──

def parse_text(path: str) -> Generator[Chunk, None, None]:
    filename = Path(path).stem
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # split on markdown headings or double newlines
    import re
    sections = re.split(r"\n(?=## )", content)
    if len(sections) <= 1:
        sections = re.split(r"\n\n+", content)

    for section in sections:
        section = section.strip()
        if len(section) < 50:
            continue
        for chunk in _split_long(section):
            yield (chunk, f"text:{filename}", "")


# ── Codex rollout JSONL parser ──

def parse_codex_rollout(path: str) -> Generator[Chunk, None, None]:
    """Parse Codex/OpenCode rollout JSONL files.

    Format: {timestamp, type, payload} where type=response_item has
    payload.role in (user, assistant, developer).
    """
    session_id = Path(path).stem.split("-")[-1][:8] if "-" in Path(path).stem else Path(path).stem[:8]
    exchanges: list[tuple[str, str, str]] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "response_item":
                continue

            payload = entry.get("payload", {})
            role = payload.get("role", "")
            if role not in ("user", "assistant"):
                continue

            ts = entry.get("timestamp", "")
            content_blocks = payload.get("content", [])
            texts = []
            for block in content_blocks:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype in ("input_text", "output_text", "text"):
                        texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            text = "\n".join(texts).strip()
            if text and len(text) > 20:
                exchanges.append((role, text, ts))

    # pair user+assistant turns
    i = 0
    while i < len(exchanges):
        parts = []
        ts = exchanges[i][2]
        if exchanges[i][0] == "user":
            parts.append(f"User: {exchanges[i][1]}")
            i += 1
            if i < len(exchanges) and exchanges[i][0] == "assistant":
                parts.append(f"Assistant: {exchanges[i][1]}")
                i += 1
        else:
            parts.append(f"Assistant: {exchanges[i][1]}")
            i += 1
        combined = "\n\n".join(parts)
        for chunk in _split_long(combined):
            yield (chunk, f"codex:{session_id}", ts)


def ingest_codex_sessions(
    conn: sqlite3.Connection,
    config: dict,
    codex_sessions_dir: str,
    wing_name: str,
) -> dict:
    """Bulk-ingest all Codex rollout JSONL files from ~/.codex/sessions/."""
    sessions_path = Path(codex_sessions_dir)
    jsonl_files = sorted(sessions_path.rglob("rollout-*.jsonl"))
    total = 0
    sessions = 0
    for jf in jsonl_files:
        # room name from date: rollout-2026-03-19T16-39-18-... -> 2026-03-19
        name_parts = jf.stem.replace("rollout-", "")
        room_name = name_parts[:10]  # "2026-03-19"
        count = ingest_source(conn, config, str(jf), "codex", wing_name, room_name)
        total += count
        if count > 0:
            sessions += 1
    return {"sessions": sessions, "drawers": total}


# ── OpenCode (DeepSeek) SQLite parser ──

def parse_opencode_db(path: str) -> Generator[Chunk, None, None]:
    """Parse an OpenCode SQLite database (opencode.db) into chunks.

    OpenCode stores sessions in SQLite with tables: session, message, part.
    Messages have role (user/assistant), parts have type (text/tool/step-*).
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(path)
    conn.row_factory = _sqlite3.Row

    sessions = conn.execute(
        "SELECT id, title, time_created FROM session ORDER BY time_created"
    ).fetchall()

    for sess in sessions:
        sid = sess["id"]
        title = sess["title"] or sid[:12]
        sess_ts = ""
        if sess["time_created"]:
            try:
                sess_ts = datetime.fromtimestamp(
                    sess["time_created"] / 1000, tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                pass

        messages = conn.execute(
            "SELECT id, data, time_created FROM message WHERE session_id=? ORDER BY time_created",
            (sid,),
        ).fetchall()

        exchanges: list[tuple[str, str, str]] = []
        for msg in messages:
            try:
                mdata = json.loads(msg["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            role = mdata.get("role", "")
            msg_ts = sess_ts
            if msg["time_created"]:
                try:
                    msg_ts = datetime.fromtimestamp(
                        msg["time_created"] / 1000, tz=timezone.utc
                    ).isoformat()
                except (ValueError, OSError):
                    pass

            # Collect text parts for this message
            parts = conn.execute(
                "SELECT data FROM part WHERE message_id=? ORDER BY time_created",
                (msg["id"],),
            ).fetchall()

            text_parts = []
            for p in parts:
                try:
                    pdata = json.loads(p["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if pdata.get("type") == "text" and pdata.get("text"):
                    text_parts.append(pdata["text"])

            content = "\n".join(text_parts).strip()
            if content and role in ("user", "assistant"):
                exchanges.append((role, content, msg_ts))

        # Pair user+assistant turns
        i = 0
        while i < len(exchanges):
            turn_parts = []
            ts = exchanges[i][2]
            if exchanges[i][0] == "user":
                turn_parts.append(f"User: {exchanges[i][1]}")
                i += 1
                if i < len(exchanges) and exchanges[i][0] == "assistant":
                    turn_parts.append(f"Assistant: {exchanges[i][1]}")
                    i += 1
            else:
                turn_parts.append(f"Assistant: {exchanges[i][1]}")
                i += 1

            combined = "\n\n".join(turn_parts)
            for chunk in _split_long(combined):
                yield chunk, f"opencode:{title[:30]}", ts

    conn.close()


def ingest_opencode_db(
    conn: sqlite3.Connection,
    config: dict,
    db_path: str,
    wing_name: str,
) -> dict:
    """Ingest all sessions from an OpenCode SQLite database.

    Args:
        conn: Dongtian palace connection
        config: Dongtian config
        db_path: Path to opencode.db
        wing_name: Wing name for ingested data
    """
    import sqlite3 as _sqlite3

    oc_conn = _sqlite3.connect(db_path)
    oc_conn.row_factory = _sqlite3.Row

    sessions = oc_conn.execute(
        "SELECT id, title, time_created FROM session ORDER BY time_created"
    ).fetchall()

    total = 0
    sess_count = 0
    for sess in sessions:
        title = (sess["title"] or sess["id"])[:40]
        # Use title slug as room name
        room_name = title.replace(" ", "-").replace("/", "-")[:30]

        wing_id = dbmod.get_or_create_wing(conn, wing_name)
        room_id = dbmod.get_or_create_room(conn, wing_id, room_name)

        drawer_ids = []
        for content, source_label, ts in _opencode_session_chunks(oc_conn, sess["id"], title):
            did = dbmod.insert_drawer(conn, room_id, content, source_label, ts)
            drawer_ids.append(did)

        _embed_drawers(conn, config, drawer_ids)
        total += len(drawer_ids)
        if drawer_ids:
            sess_count += 1

    oc_conn.close()
    return {"sessions": sess_count, "drawers": total}


def _opencode_session_chunks(
    oc_conn, session_id: str, title: str
) -> Generator[Chunk, None, None]:
    """Yield chunks from a single OpenCode session."""
    messages = oc_conn.execute(
        "SELECT id, data, time_created FROM message WHERE session_id=? ORDER BY time_created",
        (session_id,),
    ).fetchall()

    exchanges: list[tuple[str, str, str]] = []
    for msg in messages:
        try:
            mdata = json.loads(msg["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        role = mdata.get("role", "")
        msg_ts = ""
        if msg["time_created"]:
            try:
                msg_ts = datetime.fromtimestamp(
                    msg["time_created"] / 1000, tz=timezone.utc
                ).isoformat()
            except (ValueError, OSError):
                pass

        parts_rows = oc_conn.execute(
            "SELECT data FROM part WHERE message_id=? ORDER BY time_created",
            (msg["id"],),
        ).fetchall()

        text_parts = []
        for p in parts_rows:
            try:
                pdata = json.loads(p["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if pdata.get("type") == "text" and pdata.get("text"):
                text_parts.append(pdata["text"])

        content = "\n".join(text_parts).strip()
        if content and role in ("user", "assistant"):
            exchanges.append((role, content, msg_ts))

    i = 0
    while i < len(exchanges):
        turn_parts = []
        ts = exchanges[i][2]
        if exchanges[i][0] == "user":
            turn_parts.append(f"User: {exchanges[i][1]}")
            i += 1
            if i < len(exchanges) and exchanges[i][0] == "assistant":
                turn_parts.append(f"Assistant: {exchanges[i][1]}")
                i += 1
        else:
            turn_parts.append(f"Assistant: {exchanges[i][1]}")
            i += 1

        combined = "\n\n".join(turn_parts)
        for chunk in _split_long(combined):
            yield chunk, f"opencode:{title[:30]}", ts


# ── Ingest orchestrator ──

PARSERS = {
    "claude": parse_claude_jsonl,
    "chatgpt": parse_chatgpt_json,
    "slack": parse_slack_json,
    "text": parse_text,
    "codex": parse_codex_rollout,
    "opencode": parse_opencode_db,
}


def ingest_source(
    conn: sqlite3.Connection,
    config: dict,
    path: str,
    source_type: str,
    wing_name: str,
    room_name: str,
) -> int:
    parser = PARSERS.get(source_type)
    if parser is None:
        raise ValueError(f"Unknown source_type: {source_type}. Use: {list(PARSERS.keys())}")

    wing_id = dbmod.get_or_create_wing(conn, wing_name)
    room_id = dbmod.get_or_create_room(conn, wing_id, room_name)

    drawer_ids = []
    for content, source_label, ts in parser(path):
        did = dbmod.insert_drawer(conn, room_id, content, source_label, ts)
        drawer_ids.append(did)

    # generate embeddings if available
    _embed_drawers(conn, config, drawer_ids)
    return len(drawer_ids)


def ingest_claude_project(
    conn: sqlite3.Connection,
    config: dict,
    project_path: str,
    wing_name: str,
) -> dict:
    p = Path(project_path)
    jsonl_files = sorted(p.glob("*.jsonl"))
    total = 0
    sessions = 0
    for jf in jsonl_files:
        room_name = jf.stem[:12]
        count = ingest_source(conn, config, str(jf), "claude", wing_name, room_name)
        total += count
        sessions += 1
    return {"sessions": sessions, "drawers": total}


def _embed_drawers(conn: sqlite3.Connection, config: dict, drawer_ids: list[int]) -> int:
    client = get_client(config)
    if client is None or not drawer_ids:
        return 0

    batch_size = 20
    embedded = 0
    for i in range(0, len(drawer_ids), batch_size):
        batch_ids = drawer_ids[i:i + batch_size]
        rows = conn.execute(
            f"SELECT id, content FROM drawers WHERE id IN ({','.join('?' * len(batch_ids))})",
            batch_ids,
        ).fetchall()
        if not rows:
            continue
        texts = [r["content"] for r in rows]
        try:
            vectors = client.embed(texts)
        except Exception:
            continue
        for row, vec in zip(rows, vectors):
            dbmod.update_embedding(conn, row["id"], pack_embedding(vec))
            embedded += 1
    return embedded
