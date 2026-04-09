"""Cave survey: deposit extraction and passage management."""

import re
import sqlite3
from typing import Optional

from . import db as dbmod

# Common tech tools for entity detection
KNOWN_TOOLS = {
    "python", "javascript", "typescript", "rust", "golang", "go", "java", "kotlin", "swift",
    "react", "vue", "angular", "nextjs", "flask", "django", "fastapi", "express",
    "docker", "kubernetes", "nginx", "redis", "postgresql", "mysql", "sqlite", "mongodb",
    "git", "github", "gitlab", "linux", "windows", "macos",
    "aws", "gcp", "azure", "cloudflare", "vercel",
    "ray", "numpy", "pandas", "pytorch", "tensorflow", "cupy",
    "claude", "chatgpt", "openai", "deepseek", "anthropic",
    "ssh", "tmux", "vim", "vscode", "cursor",
}

# Relationship extraction patterns
RELATION_PATTERNS = [
    (r"(\w[\w\s]{1,30})\s+(?:uses?|using)\s+(\w[\w\s]{1,30})", "uses"),
    (r"(\w[\w\s]{1,30})\s+(?:deployed?(?:\s+on)?|running\s+on)\s+(\w[\w\s]{1,30})", "deployed_on"),
    (r"(\w[\w\s]{1,30})\s+(?:maintains?|owns?)\s+(\w[\w\s]{1,30})", "maintains"),
    (r"(\w[\w\s]{1,30})\s+(?:depends?\s+on|requires?)\s+(\w[\w\s]{1,30})", "depends_on"),
    (r"(\w[\w\s]{1,30})\s+is\s+(?:a|an)\s+(\w[\w\s]{1,30})", "is_a"),
    (r"(\w[\w\s]{1,30})\s+(?:connects?\s+to|talks?\s+to)\s+(\w[\w\s]{1,30})", "connects_to"),
    (r"(\w[\w\s]{1,30})\s+(?:replaced?|switched?\s+(?:to|from))\s+(\w[\w\s]{1,30})", "replaced"),
]

# Stopwords for person name detection
_NAME_STOPS = {
    "the", "this", "that", "with", "from", "into", "user", "assistant",
    "when", "then", "also", "just", "here", "there", "would", "could",
    "should", "about", "after", "before", "between", "through", "during",
    "each", "every", "some", "many", "most", "other", "first", "last",
    "next", "new", "old", "good", "bad", "best", "worst", "well",
}


def extract_entities(text: str) -> list[dict]:
    """Extract entities from text using heuristics."""
    found = []

    # Tools: match known tool names (case-insensitive)
    words = set(re.findall(r"\b\w+\b", text.lower()))
    for tool in KNOWN_TOOLS & words:
        found.append({"name": tool, "type": "tool", "confidence": 0.9})

    # Persons: capitalized two-word sequences
    cap_pairs = re.findall(r"\b([A-Z][a-z]{1,15})\s([A-Z][a-z]{1,15})\b", text)
    seen_names = set()
    for first, last in cap_pairs:
        name = f"{first} {last}"
        if first.lower() in _NAME_STOPS or last.lower() in _NAME_STOPS:
            continue
        if name not in seen_names:
            seen_names.add(name)
            found.append({"name": name, "type": "person", "confidence": 0.7})

    # Projects: match "project X" pattern
    project_matches = re.findall(r"\bproject\s+[\"']?(\w[\w\s-]{1,30})[\"']?", text, re.IGNORECASE)
    for proj in project_matches:
        found.append({"name": proj.strip(), "type": "project", "confidence": 0.6})

    return found


def extract_passages(text: str) -> list[dict]:
    """Extract relationship passages from text using regex patterns."""
    found = []
    for pattern, predicate in RELATION_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for subj, obj in matches:
            subj = subj.strip()
            obj = obj.strip()
            if len(subj) > 2 and len(obj) > 2:
                found.append({
                    "subject": subj,
                    "predicate": predicate,
                    "object": obj,
                    "confidence": 0.7,
                })
    return found


def extract_and_store(
    conn: sqlite3.Connection,
    stratum_id: int,
    source_ts: Optional[str] = None,
) -> dict:
    """Run extraction on a stratum and store results."""
    row = conn.execute("SELECT content, source_ts FROM strata WHERE id = ?", (stratum_id,)).fetchone()
    if not row:
        return {"error": "Stratum not found"}

    text = row["content"]
    ts = source_ts or row["source_ts"]

    deposits = extract_entities(text)
    passages = extract_passages(text)

    deposit_count = 0
    for e in deposits:
        dbmod.get_or_create_deposit(conn, e["name"], e["type"])
        deposit_count += 1

    passage_count = 0
    for p in passages:
        subj_type = _guess_type(p["subject"])
        obj_type = _guess_type(p["object"])
        subj_id = dbmod.get_or_create_deposit(conn, p["subject"], subj_type)
        obj_id = dbmod.get_or_create_deposit(conn, p["object"], obj_type)
        dbmod.insert_passage(
            conn, subj_id, p["predicate"], obj_id,
            confidence=p["confidence"],
            valid_from=ts,
            source_stratum_id=stratum_id,
        )
        passage_count += 1

    return {"deposits_found": deposit_count, "passages_found": passage_count}


def _guess_type(name: str) -> str:
    if name.lower() in KNOWN_TOOLS:
        return "tool"
    if " " in name and name[0].isupper():
        return "person"
    return "concept"
