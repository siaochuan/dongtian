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
            ts = _normalize_timestamp(entry.get("timestamp", ""))

            if role in ("human", "user"):
                content = _extract_human_content(entry)
                if content:
                    exchanges.append(("user", content, ts))
            elif role == "assistant":
                content = _extract_assistant_content(entry)
                if content:
                    exchanges.append(("assistant", content, ts))
            else:
                message = entry.get("message", {})
                message_role = message.get("role", "")
                if message_role == "user":
                    content = _extract_human_content(entry)
                    if content:
                        exchanges.append(("user", content, ts))
                elif message_role == "assistant":
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


def _normalize_timestamp(ts: str | int | float) -> str:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
    return ts


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

def _load_codex_session_index() -> dict[str, str]:
    """Load session_index.jsonl to map session IDs to thread names."""
    idx_path = Path("~/.codex/session_index.jsonl").expanduser()
    mapping: dict[str, str] = {}
    if not idx_path.exists():
        return mapping
    with open(idx_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                sid = entry.get("id", "")
                name = entry.get("thread_name", "")
                if sid and name:
                    mapping[sid] = name
            except json.JSONDecodeError:
                continue
    return mapping


def _extract_codex_session_id(path: str) -> str:
    """Extract UUID session ID from rollout filename.

    Filename format: rollout-2026-04-07T14-26-47-019d669f-44b2-7912-a4a1-b0121b869633.jsonl
    The UUID is the last 5 hyphen-separated groups (36 chars).
    """
    stem = Path(path).stem
    # Remove "rollout-" prefix, then extract trailing UUID
    rest = stem.replace("rollout-", "")
    # UUID is 36 chars: 8-4-4-4-12
    if len(rest) >= 36:
        candidate = rest[-36:]
        if candidate.count("-") == 4:
            return candidate
    return rest[:8]


def parse_codex_rollout(path: str) -> Generator[Chunk, None, None]:
    """Parse Codex CLI rollout JSONL files with turn-based grouping.

    Processes the full Codex session format (v0.105+):
    - session_meta: session metadata (model, cwd, version)
    - event_msg/user_message: actual user input
    - event_msg/task_complete: turn summary with last_agent_message
    - response_item/message: user/assistant messages
    - response_item/function_call: tool invocations (summarized)
    """
    session_id = _extract_codex_session_id(path)
    short_id = session_id[:8]

    entries: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return

    # Extract session metadata for source label
    meta_cwd = ""
    meta_model = ""
    for e in entries:
        if e.get("type") == "session_meta":
            p = e.get("payload", {})
            meta_cwd = p.get("cwd", "")
            meta_model = p.get("model_provider", "")
            break

    # Group entries into turns: task_started -> ... -> task_complete
    turns: list[dict] = []
    current_turn: dict | None = None

    for e in entries:
        etype = e.get("type", "")
        payload = e.get("payload", {})
        ts = e.get("timestamp", "")
        ptype = payload.get("type", "") if isinstance(payload, dict) else ""

        if etype == "event_msg" and ptype == "task_started":
            current_turn = {
                "ts": ts,
                "user_msg": "",
                "assistant_msg": "",
                "tool_calls": [],
                "model": "",
            }
            continue

        if current_turn is None:
            # Messages outside turns: handle response_item/message directly
            if etype == "response_item" and ptype == "message":
                role = payload.get("role", "")
                text = _extract_codex_message_text(payload)
                if role == "user" and text and not _is_codex_system_context(text):
                    current_turn = {
                        "ts": ts, "user_msg": text,
                        "assistant_msg": "", "tool_calls": [], "model": "",
                    }
                elif role == "assistant" and text:
                    if turns and not turns[-1].get("assistant_msg"):
                        turns[-1]["assistant_msg"] = text
                    else:
                        turns.append({
                            "ts": ts, "user_msg": "",
                            "assistant_msg": text, "tool_calls": [], "model": "",
                        })
            continue

        # Inside a turn: collect content
        if etype == "event_msg":
            if ptype == "user_message":
                msg = payload.get("message", "")
                if msg and not msg.startswith("Tip:") and not msg.startswith("⚠"):
                    current_turn["user_msg"] = msg
            elif ptype == "task_complete":
                lam = payload.get("last_agent_message", "")
                if lam:
                    current_turn["assistant_msg"] = lam
                turns.append(current_turn)
                current_turn = None
            elif ptype == "turn_context":
                current_turn["model"] = payload.get("model", "")

        elif etype == "response_item":
            if ptype == "message":
                role = payload.get("role", "")
                text = _extract_codex_message_text(payload)
                if role == "user" and text and not _is_codex_system_context(text):
                    if not current_turn["user_msg"]:
                        current_turn["user_msg"] = text
                elif role == "assistant" and text:
                    current_turn["assistant_msg"] = text
            elif ptype == "function_call":
                name = payload.get("name", "")
                args = payload.get("arguments", "")
                summary = _summarize_tool_call(name, args)
                if summary:
                    current_turn["tool_calls"].append(summary)
            elif ptype == "custom_tool_call":
                name = payload.get("name", "")
                if name:
                    current_turn["tool_calls"].append(name)

    # Flush any incomplete turn
    if current_turn and (current_turn["user_msg"] or current_turn["assistant_msg"]):
        turns.append(current_turn)

    # Build source label
    source = f"codex:{short_id}"
    if meta_model:
        source = f"codex:{meta_model}:{short_id}"

    # Yield chunks from turns
    for turn in turns:
        if not turn["user_msg"] and not turn["assistant_msg"]:
            continue
        parts = []
        if turn["user_msg"]:
            parts.append(f"User: {turn['user_msg']}")
        if turn["tool_calls"]:
            # Deduplicate consecutive identical tool calls
            deduped = _dedup_tool_calls(turn["tool_calls"])
            tools_summary = ", ".join(deduped[:10])
            if len(deduped) > 10:
                tools_summary += f" (+{len(deduped) - 10} more)"
            parts.append(f"Tools: [{tools_summary}]")
        if turn["assistant_msg"]:
            parts.append(f"Assistant: {turn['assistant_msg']}")
        combined = "\n\n".join(parts)
        for chunk in _split_long(combined):
            yield (chunk, source, turn["ts"])


def _extract_codex_message_text(payload: dict) -> str:
    """Extract text content from a Codex response_item/message payload."""
    content = payload.get("content", [])
    if isinstance(content, str):
        return content.strip()
    texts = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type", "")
            if btype in ("input_text", "output_text", "text"):
                texts.append(block.get("text", ""))
        elif isinstance(block, str):
            texts.append(block)
    return "\n".join(texts).strip()


def _is_codex_system_context(text: str) -> bool:
    """Check if a user message is system/environment context, not real user input."""
    prefixes = (
        "<environment_context>", "<permissions", "<collaboration_mode>",
        "# Collaboration Mode:", "Filesystem sandboxing",
    )
    return any(text.lstrip().startswith(p) for p in prefixes)


def _summarize_tool_call(name: str, args_json: str) -> str:
    """Produce a compact summary of a tool call."""
    if not name:
        return ""
    if name == "exec_command":
        try:
            args = json.loads(args_json)
            cmd = args.get("command", "")
            if cmd:
                # Truncate long commands
                cmd_short = cmd.split("\n")[0][:80]
                return f"exec({cmd_short})"
        except (json.JSONDecodeError, TypeError):
            pass
        return "exec_command"
    if name == "apply_patch":
        return "apply_patch"
    if name == "update_plan":
        return ""  # Noise, skip
    return name


def _dedup_tool_calls(calls: list[str]) -> list[str]:
    """Collapse consecutive identical tool call summaries."""
    if not calls:
        return []
    result = []
    prev = None
    count = 0
    for c in calls:
        if c == prev:
            count += 1
        else:
            if prev is not None:
                result.append(f"{prev} x{count}" if count > 1 else prev)
            prev = c
            count = 1
    if prev is not None:
        result.append(f"{prev} x{count}" if count > 1 else prev)
    return result


def ingest_codex_sessions(
    conn: sqlite3.Connection,
    config: dict,
    codex_sessions_dir: str,
    layer_name: str,
) -> dict:
    """Bulk-ingest all Codex rollout JSONL files from ~/.codex/sessions/.

    Uses session_index.jsonl for human-readable chamber names when available.
    """
    sessions_path = Path(codex_sessions_dir)
    jsonl_files = sorted(sessions_path.rglob("rollout-*.jsonl"))
    session_index = _load_codex_session_index()

    total = 0
    sessions = 0
    for jf in jsonl_files:
        sid = _extract_codex_session_id(str(jf))
        thread_name = session_index.get(sid, "")
        if thread_name:
            # Use date + thread name slug as chamber
            date_part = jf.stem.replace("rollout-", "")[:10]
            name_slug = thread_name.replace(" ", "-").replace("/", "-")[:30]
            chamber_name = f"{date_part}_{name_slug}"
        else:
            name_parts = jf.stem.replace("rollout-", "")
            chamber_name = name_parts[:10]
        count = ingest_source(conn, config, str(jf), "codex", layer_name, chamber_name)
        total += count
        if count > 0:
            sessions += 1
    return {"sessions": sessions, "strata": total}


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
    layer_name: str,
) -> dict:
    """Ingest all sessions from an OpenCode SQLite database.

    Args:
        conn: Dongtian cavern connection
        config: Dongtian config
        db_path: Path to opencode.db
        layer_name: Layer name for ingested data
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
        # Use title slug as chamber name
        chamber_name = title.replace(" ", "-").replace("/", "-")[:30]

        layer_id = dbmod.get_or_create_layer(conn, layer_name)
        chamber_id = dbmod.get_or_create_chamber(conn, layer_id, chamber_name)

        stratum_ids = []
        for content, source_label, ts in _opencode_session_chunks(oc_conn, sess["id"], title):
            did = dbmod.insert_stratum(conn, chamber_id, content, source_label, ts)
            stratum_ids.append(did)

        _embed_strata(conn, config, stratum_ids)
        total += len(stratum_ids)
        if stratum_ids:
            sess_count += 1

    oc_conn.close()
    return {"sessions": sess_count, "strata": total}


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
    layer_name: str,
    chamber_name: str,
) -> int:
    parser = PARSERS.get(source_type)
    if parser is None:
        raise ValueError(f"Unknown source_type: {source_type}. Use: {list(PARSERS.keys())}")

    layer_id = dbmod.get_or_create_layer(conn, layer_name)
    chamber_id = dbmod.get_or_create_chamber(conn, layer_id, chamber_name)

    stratum_ids = []
    for content, source_label, ts in parser(path):
        did = dbmod.insert_stratum(conn, chamber_id, content, source_label, ts)
        stratum_ids.append(did)

    # generate embeddings if available
    _embed_strata(conn, config, stratum_ids)
    return len(stratum_ids)


def ingest_claude_project(
    conn: sqlite3.Connection,
    config: dict,
    project_path: str,
    layer_name: str,
) -> dict:
    p = Path(project_path)
    jsonl_files = sorted(p.rglob("*.jsonl"))
    total = 0
    sessions = 0
    for jf in jsonl_files:
        rel_stem = jf.relative_to(p).with_suffix("")
        if len(rel_stem.parts) == 1:
            chamber_name = jf.stem[:12]
        else:
            chamber_name = "__".join(part[:24] for part in rel_stem.parts)[:120]
        count = ingest_source(conn, config, str(jf), "claude", layer_name, chamber_name)
        total += count
        if count > 0:
            sessions += 1
    return {"sessions": sessions, "strata": total}


def _embed_strata(conn: sqlite3.Connection, config: dict, stratum_ids: list[int]) -> int:
    client = get_client(config)
    if client is None or not stratum_ids:
        return 0

    batch_size = 20
    embedded = 0
    for i in range(0, len(stratum_ids), batch_size):
        batch_ids = stratum_ids[i:i + batch_size]
        rows = conn.execute(
            f"SELECT id, content FROM strata WHERE id IN ({','.join('?' * len(batch_ids))})",
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
