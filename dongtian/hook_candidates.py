"""Hook candidate mining for OpenHarness session histories."""

from __future__ import annotations

import ast
import json
import math
import re
import shlex
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

GUARD_LINE_RE = re.compile(r"\[(?P<guard>[a-zA-Z0-9_-]+)\]\s*(?P<line>[^\n]+)")
SCRIPT_TOKEN_RE = re.compile(r"(?P<name>[\w./-]+\.(?:py|sh))", flags=re.IGNORECASE)


@dataclass
class BlockEvent:
    ts: float
    pane: str
    session_file: str
    guard: str
    reason_line: str
    suggestion_line: str
    command_raw: str
    command_norm: str
    reason_class: str
    script_hint: str
    script_path: str
    primary_exec: str


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ts_to_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _split_tokens(line: str) -> list[str]:
    try:
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return list(lexer)
    except Exception:
        return line.split("#", 1)[0].split()


def _token_list(command_norm: str) -> list[str]:
    tokens: list[str] = []
    for line in command_norm.splitlines():
        tokens.extend(_split_tokens(line))
    return tokens


def _first_exec_token(tokens: list[str]) -> str:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if "=" in tok and tok.find("=") > 0 and not tok.startswith(("/", "./", "../")):
            i += 1
            continue
        return tok
    return ""


def normalize_command(command: str) -> str:
    lines: list[str] = []
    for raw in command.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = _split_tokens(line)
        if not tokens:
            continue
        lines.append(" ".join(tokens))
    return "\n".join(lines)


def _script_hint_from_command(command_norm: str) -> tuple[str, str]:
    if not command_norm:
        return "", ""
    for tok in _token_list(command_norm):
        mt = SCRIPT_TOKEN_RE.search(tok)
        if mt:
            full = mt.group("name")
            return Path(full).name, full
    return "", ""


def _classify_reason(reason_line: str) -> str:
    line = reason_line.lower()
    if "historical rebuild/backfill" in line:
        return "history_route"
    if "incremental production" in line:
        return "incremental_route"
    if "local heavy compute" in line:
        return "local_dev_env"
    if "list(set" in line:
        return "nondeterministic_set"
    return "other"


def _hook_bucket_for_reason(reason_class: str) -> str:
    if reason_class == "history_route":
        return "HISTORY_PATTERNS"
    if reason_class == "incremental_route":
        return "INCREMENTAL_PATTERNS"
    if reason_class == "local_dev_env":
        return "LOCAL_HEAVY_PATTERNS"
    return ""


def _is_temporary_script(script_path: str, script_hint: str) -> bool:
    path_lc = script_path.lower()
    hint_lc = script_hint.lower()
    if "/tmp/" in path_lc or path_lc.startswith("tmp/"):
        return True
    if hint_lc.endswith("_tmp.py") or hint_lc.startswith("tmp_"):
        return True
    if hint_lc in {"check_times.py", "check_times_tmp.py"}:
        return True
    return False


def _candidate_regex_from_event(event: BlockEvent) -> str:
    if event.reason_class == "nondeterministic_set":
        return r"list\(set\("
    if event.script_hint:
        if _is_temporary_script(event.script_path, event.script_hint):
            return ""
        return re.escape(event.script_hint).replace(r"\.", r"\.")
    return ""


def _discover_default_roots() -> list[Path]:
    roots: list[Path] = []
    for pane_dir in sorted(Path.home().glob(".openharness-w*/data/sessions/OpenHarness-*")):
        if pane_dir.is_dir():
            roots.append(pane_dir)
    return roots


def _pane_name_from_root(root: Path) -> str:
    for part in root.parts:
        if part.startswith(".openharness-w"):
            return part.replace(".openharness-", "")
    return root.name


def _parse_existing_patterns(hook_file: Path) -> dict[str, list[str]]:
    out = {
        "HISTORY_PATTERNS": [],
        "INCREMENTAL_PATTERNS": [],
        "LOCAL_HEAVY_PATTERNS": [],
    }
    if not hook_file.exists():
        return out
    src = hook_file.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name not in out:
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        vals: list[str] = []
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                vals.append(elt.value)
        out[name] = vals
    return out


def _extract_events_from_session(path: Path, pane: str, min_ts: float) -> list[BlockEvent]:
    events: list[BlockEvent] = []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return events

    created = float(obj.get("created_at", 0) or 0)
    if not created:
        created = path.stat().st_mtime
    if created < min_ts:
        return events

    tool_uses: dict[str, str] = {}
    for msg in obj.get("messages", []):
        for block in msg.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "bash":
                tid = block.get("id")
                payload = block.get("input") if isinstance(block.get("input"), dict) else {}
                cmd = str(payload.get("command", ""))
                if tid:
                    tool_uses[str(tid)] = cmd

    for msg in obj.get("messages", []):
        if msg.get("role") != "user":
            continue
        for block in msg.get("content", []):
            if block.get("type") != "tool_result" or not block.get("is_error"):
                continue
            content = str(block.get("content", ""))
            matches = list(GUARD_LINE_RE.finditer(content))
            if not matches:
                continue

            reasons: list[tuple[str, str]] = []
            suggestions: list[tuple[str, str]] = []
            for mt in matches:
                guard = mt.group("guard").strip()
                line = mt.group("line").strip()
                if line.startswith("BLOCKED"):
                    reasons.append((guard, line))
                elif line.startswith("Use "):
                    suggestions.append((guard, line))
            if not reasons:
                continue

            tid = str(block.get("tool_use_id", ""))
            command_raw = tool_uses.get(tid, "")
            command_norm = normalize_command(command_raw)
            script_hint, script_path = _script_hint_from_command(command_norm)
            primary_exec = _first_exec_token(_token_list(command_norm))
            suggestion_by_guard = {g: s for g, s in suggestions}

            for guard, reason_line in reasons:
                events.append(
                    BlockEvent(
                        ts=created,
                        pane=pane,
                        session_file=path.name,
                        guard=guard,
                        reason_line=reason_line,
                        suggestion_line=suggestion_by_guard.get(guard, ""),
                        command_raw=command_raw,
                        command_norm=command_norm,
                        reason_class=_classify_reason(reason_line),
                        script_hint=script_hint,
                        script_path=script_path,
                        primary_exec=primary_exec,
                    )
                )
    return events


def _confidence_score(hit_count: int, session_count: int, pane_count: int) -> float:
    score = 0.30 + 0.18 * math.log1p(hit_count) + 0.10 * max(session_count - 1, 0) + 0.08 * max(pane_count - 1, 0)
    return round(min(0.98, score), 3)


def _build_candidates(
    events: list[BlockEvent],
    existing: dict[str, list[str]],
    promote_hit_threshold: int,
    promote_session_threshold: int,
    max_command_examples: int,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    grouped: dict[tuple[str, str, str, str], list[BlockEvent]] = defaultdict(list)
    ignored_counter: Counter[str] = Counter()
    for event in events:
        if event.guard not in {"route-env-guard", "strategy-guard"}:
            ignored_counter["unsupported_guard"] += 1
            continue
        regex = _candidate_regex_from_event(event)
        if not regex and event.reason_class != "nondeterministic_set":
            ignored_counter[f"no_stable_candidate:{event.reason_class}"] += 1
            continue
        bucket = _hook_bucket_for_reason(event.reason_class)
        grouped[(event.guard, event.reason_class, bucket, regex)].append(event)

    candidates: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    for (guard, reason_class, bucket, regex), items in grouped.items():
        sessions = sorted({it.session_file for it in items})
        panes = sorted({it.pane for it in items})
        seen_cmd: set[str] = set()
        commands: list[str] = []
        for it in items:
            cmd = it.command_norm.strip()
            if not cmd or cmd in seen_cmd:
                continue
            seen_cmd.add(cmd)
            commands.append(cmd)
            if len(commands) >= max_command_examples:
                break

        already_exists = regex in existing.get(bucket, [])
        if reason_class == "nondeterministic_set":
            status = "discarded"
            rationale = "User requested dropping list(set) hook."
        elif already_exists:
            status = "covered"
            rationale = "Pattern already exists in hook."
        elif len(items) >= promote_hit_threshold and len(sessions) >= promote_session_threshold:
            status = "promote"
            rationale = "Repeated true-positive style block across sessions."
        else:
            status = "observe"
            rationale = "Insufficient recurrence, keep observing."

        candidate = {
            "guard": guard,
            "reason_class": reason_class,
            "hook_bucket": bucket,
            "regex": regex,
            "status": status,
            "rationale": rationale,
            "already_exists": already_exists,
            "hit_count": len(items),
            "session_count": len(sessions),
            "pane_count": len(panes),
            "first_seen": _ts_to_str(min(it.ts for it in items)),
            "last_seen": _ts_to_str(max(it.ts for it in items)),
            "reason_lines": sorted({it.reason_line for it in items}),
            "suggestions": sorted({it.suggestion_line for it in items if it.suggestion_line}),
            "script_hints": sorted({it.script_hint for it in items if it.script_hint}),
            "sample_commands": commands,
            "confidence": _confidence_score(len(items), len(sessions), len(panes)),
        }
        status_counter[status] += 1
        candidates.append(candidate)

    candidates.sort(
        key=lambda x: (
            {"promote": 0, "observe": 1, "covered": 2, "discarded": 3}.get(x["status"], 9),
            -x["hit_count"],
            x["reason_class"],
            x["regex"],
        )
    )
    return candidates, dict(status_counter), dict(ignored_counter)


def _detect_stale_existing_patterns(events: list[BlockEvent], existing: dict[str, list[str]]) -> dict[str, list[str]]:
    stale: dict[str, list[str]] = {}
    for bucket, patterns in existing.items():
        missed: list[str] = []
        for pat in patterns:
            try:
                compiled = re.compile(pat, flags=re.IGNORECASE)
            except re.error:
                missed.append(pat)
                continue
            hit = any(compiled.search(ev.command_norm.lower()) for ev in events if ev.command_norm)
            if not hit:
                missed.append(pat)
        stale[bucket] = missed
    return stale


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _render_report(
    out_path: Path,
    roots: list[Path],
    events: list[BlockEvent],
    candidates: list[dict[str, Any]],
    stale_patterns: dict[str, list[str]],
    hook_file: Path,
    status_counter: dict[str, int],
    ignored_counter: dict[str, int],
) -> None:
    lines: list[str] = []
    lines.append("# Hook Candidate Miner Report")
    lines.append("")
    lines.append(f"- generated_at: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"- hook_file: `{hook_file}`")
    lines.append(f"- session_roots: `{len(roots)}`")
    for root in roots:
        lines.append(f"- root: `{root}`")
    lines.append(f"- blocked_events: `{len(events)}`")
    lines.append(f"- candidates_total: `{len(candidates)}`")
    lines.append(f"- promote: `{status_counter.get('promote', 0)}`")
    lines.append(f"- observe: `{status_counter.get('observe', 0)}`")
    lines.append(f"- covered: `{status_counter.get('covered', 0)}`")
    lines.append(f"- discarded: `{status_counter.get('discarded', 0)}`")
    lines.append(f"- ignored_events: `{sum(ignored_counter.values())}`")
    lines.append("")
    lines.append("## Promote Candidates")
    promotes = [c for c in candidates if c["status"] == "promote"]
    if not promotes:
        lines.append("- none")
    for c in promotes:
        lines.append(
            f"- `{c['hook_bucket']}` regex=`{c['regex']}` hits={c['hit_count']} "
            f"sessions={c['session_count']} panes={c['pane_count']} confidence={c['confidence']}"
        )
        lines.append(f"  - reason_class={c['reason_class']}")
        if c["sample_commands"]:
            lines.append(f"  - sample={c['sample_commands'][0]}")
    lines.append("")
    lines.append("## Observe Candidates")
    observes = [c for c in candidates if c["status"] == "observe"]
    if not observes:
        lines.append("- none")
    for c in observes:
        lines.append(
            f"- `{c['hook_bucket']}` regex=`{c['regex']}` hits={c['hit_count']} "
            f"sessions={c['session_count']} panes={c['pane_count']}"
        )
    lines.append("")
    lines.append("## Covered Or Discarded")
    covered = [c for c in candidates if c["status"] in {"covered", "discarded"}]
    if not covered:
        lines.append("- none")
    for c in covered:
        lines.append(
            f"- status={c['status']} bucket={c['hook_bucket']} regex=`{c['regex']}` "
            f"hits={c['hit_count']} reason={c['reason_class']}"
        )
    lines.append("")
    lines.append("## Stale Existing Patterns (No Hit In Window)")
    for bucket in ("HISTORY_PATTERNS", "INCREMENTAL_PATTERNS", "LOCAL_HEAVY_PATTERNS"):
        missed = stale_patterns.get(bucket, [])
        lines.append(f"- `{bucket}` stale_count={len(missed)}")
        for pat in missed[:10]:
            lines.append(f"  - `{pat}`")
    lines.append("")
    lines.append("## Ignored Events (No Stable Candidate)")
    if not ignored_counter:
        lines.append("- none")
    for key, value in sorted(ignored_counter.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{key}`: {value}")
    lines.append("")
    lines.append("## Recent Blocked Events")
    recent = sorted(events, key=lambda x: x.ts)[-20:]
    if not recent:
        lines.append("- none")
    for event in recent:
        lines.append(
            f"- `{_ts_to_str(event.ts)}` pane={event.pane} guard={event.guard} "
            f"reason={event.reason_class} file={event.session_file}"
        )
        lines.append(f"  - line={event.reason_line}")
        if event.command_norm:
            lines.append(f"  - cmd={event.command_norm[:220]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_hook_candidate_update(
    config: dict,
    *,
    since_days: float | None = None,
    emit_timestamped: bool = True,
) -> dict[str, Any]:
    """Run hook-candidate mining and write artifacts.

    Returns a summary with generated paths and counters.
    """
    since_days = float(since_days if since_days is not None else config.get("hook_candidate_since_days", 14))
    roots_cfg = config.get("hook_candidate_session_roots") or []
    roots = [Path(p).expanduser().resolve() for p in roots_cfg if Path(p).expanduser().is_dir()]
    if not roots:
        roots = _discover_default_roots()
    roots = sorted(set(roots))

    hook_file = Path(config.get("hook_candidate_hook_file", "~/.openharness-w8/hooks/strategy_route_env_guard.py")).expanduser()
    output_root = Path(config.get("hook_candidate_output_dir", "~/.dongtian/hook_candidates")).expanduser()
    latest_dir = output_root / "latest"

    min_ts = datetime.now().timestamp() - since_days * 86400.0
    events: list[BlockEvent] = []
    session_files_scanned = 0
    for root in roots:
        pane = _pane_name_from_root(root)
        for session_file in sorted(root.glob("session-*.json")):
            session_files_scanned += 1
            events.extend(_extract_events_from_session(session_file, pane, min_ts=min_ts))

    events.sort(key=lambda x: x.ts)
    existing = _parse_existing_patterns(hook_file)
    candidates, status_counter, ignored_counter = _build_candidates(
        events=events,
        existing=existing,
        promote_hit_threshold=int(config.get("hook_candidate_promote_hit_threshold", 3)),
        promote_session_threshold=int(config.get("hook_candidate_promote_session_threshold", 2)),
        max_command_examples=int(config.get("hook_candidate_max_command_examples", 3)),
    )
    stale_patterns = _detect_stale_existing_patterns(events, existing)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "generated_at": generated_at,
        "session_roots": [str(root) for root in roots],
        "session_files_scanned": session_files_scanned,
        "events_count": len(events),
        "since_days": since_days,
        "hook_file": str(hook_file),
        "status_counter": status_counter,
        "ignored_counter": ignored_counter,
    }

    latest_dir.mkdir(parents=True, exist_ok=True)
    _write_json(latest_dir / "meta.json", meta)
    _write_json(latest_dir / "events.json", [asdict(event) for event in events])
    _write_json(latest_dir / "candidate_rules.json", candidates)
    _write_json(latest_dir / "stale_existing_patterns.json", stale_patterns)
    _render_report(
        out_path=latest_dir / "report.md",
        roots=roots,
        events=events,
        candidates=candidates,
        stale_patterns=stale_patterns,
        hook_file=hook_file,
        status_counter=status_counter,
        ignored_counter=ignored_counter,
    )

    snapshot_dir: Path | None = None
    if emit_timestamped:
        snapshot_dir = output_root / f"hook_candidates_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        _write_json(snapshot_dir / "meta.json", meta)
        _write_json(snapshot_dir / "events.json", [asdict(event) for event in events])
        _write_json(snapshot_dir / "candidate_rules.json", candidates)
        _write_json(snapshot_dir / "stale_existing_patterns.json", stale_patterns)
        _render_report(
            out_path=snapshot_dir / "report.md",
            roots=roots,
            events=events,
            candidates=candidates,
            stale_patterns=stale_patterns,
            hook_file=hook_file,
            status_counter=status_counter,
            ignored_counter=ignored_counter,
        )

    return {
        "generated_at": generated_at,
        "session_roots": [str(root) for root in roots],
        "session_files_scanned": session_files_scanned,
        "events_count": len(events),
        "candidates_total": len(candidates),
        "promote": status_counter.get("promote", 0),
        "observe": status_counter.get("observe", 0),
        "covered": status_counter.get("covered", 0),
        "discarded": status_counter.get("discarded", 0),
        "ignored_events": sum(ignored_counter.values()),
        "latest_dir": str(latest_dir),
        "snapshot_dir": str(snapshot_dir) if snapshot_dir else "",
        "report_path": str(latest_dir / "report.md"),
        "candidate_rules_path": str(latest_dir / "candidate_rules.json"),
        "meta_path": str(latest_dir / "meta.json"),
        "status_counter": status_counter,
        "ignored_counter": ignored_counter,
    }


def default_chamber_for_today(config: dict) -> str:
    prefix = str(config.get("hook_candidate_chamber_prefix", "hook_candidates")).strip() or "hook_candidates"
    return f"{_today_str()}_{prefix}"
