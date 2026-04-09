import json
import tempfile
import unittest
from pathlib import Path

from dongtian import db as dbmod
from dongtian.ingest import ingest_claude_project, parse_claude_jsonl


def _entry(role_type: str, role: str, text: str, timestamp: str) -> dict:
    return {
        "type": role_type,
        "message": {
            "role": role,
            "content": text,
        },
        "timestamp": timestamp,
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry))
            handle.write("\n")


class IngestTests(unittest.TestCase):
    def test_parse_claude_jsonl_supports_current_user_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "session-12345678.jsonl"
            _write_jsonl(
                session_path,
                [
                    _entry(
                        "user",
                        "user",
                        "This is a current Claude user turn with enough detail to survive chunk filtering.",
                        "2026-04-09T12:00:00.000Z",
                    ),
                    _entry(
                        "assistant",
                        "assistant",
                        "This is the assistant reply that should be paired with the user turn in storage.",
                        "2026-04-09T12:00:05.000Z",
                    ),
                ],
            )

            chunks = list(parse_claude_jsonl(str(session_path)))

        self.assertEqual(
            chunks,
            [
                (
                    "User: This is a current Claude user turn with enough detail to survive chunk filtering.\n\n"
                    "Assistant: This is the assistant reply that should be paired with the user turn in storage.",
                    "claude:session-",
                    "2026-04-09T12:00:00.000Z",
                )
            ],
        )

    def test_ingest_claude_project_recursively_imports_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_path = root / "project"
            db_path = root / "cavern.db"

            _write_jsonl(
                project_path / "main-session-123456.jsonl",
                [
                    _entry(
                        "user",
                        "user",
                        "Main session user message with enough detail to survive chunk filtering.",
                        "2026-04-09T12:00:00.000Z",
                    ),
                    _entry(
                        "assistant",
                        "assistant",
                        "Main session assistant reply with enough detail to survive chunk filtering.",
                        "2026-04-09T12:00:05.000Z",
                    ),
                ],
            )
            _write_jsonl(
                project_path / "conversation-a" / "subagents" / "agent-1.jsonl",
                [
                    _entry(
                        "user",
                        "user",
                        "Nested subagent user message with enough detail to survive chunk filtering.",
                        "2026-04-09T13:00:00.000Z",
                    ),
                    _entry(
                        "assistant",
                        "assistant",
                        "Nested subagent assistant reply with enough detail to survive chunk filtering.",
                        "2026-04-09T13:00:05.000Z",
                    ),
                ],
            )

            conn = dbmod.init_db(str(db_path))
            result = ingest_claude_project(
                conn,
                {"embedding_api_key": None},
                str(project_path),
                "claude-test",
            )
            chamber_names = [
                row["name"]
                for row in conn.execute("SELECT name FROM chambers ORDER BY name").fetchall()
            ]
            strata_count = conn.execute("SELECT COUNT(*) FROM strata").fetchone()[0]
            conn.close()

        self.assertEqual(result, {"sessions": 2, "strata": 2})
        self.assertEqual(chamber_names, ["conversation-a__subagents__agent-1", "main-session"])
        self.assertEqual(strata_count, 2)


if __name__ == "__main__":
    unittest.main()
