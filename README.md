# Dongtian

[中文文档](README_CN.md)

**A local, unified memory layer for AI conversations — stored in one SQLite file and exposed as an MCP server.**

Dongtian helps you **keep** and **search** conversation history across multiple AI tools (e.g. Claude Code, Codex, ChatGPT exports, OpenCode, Slack).

It ingests conversations into a simple SQLite schema and provides:
- keyword search (SQLite FTS5, offline)
- optional embedding search (via an embedding API)
- hybrid ranking (keyword + embedding)
- optional remote sync over SSH
- an optional “cave survey” knowledge graph (deposits + passages)

---

## What it stores (terminology)

Dongtian uses a cave metaphor to keep the data model small and consistent:

- **Cavern**: the SQLite database file
- **Layer**: a top-level grouping (often a machine name or source, e.g. `codex-laptop`)
- **Chamber**: a session/topic inside a layer (often a date or session id)
- **Stratum**: a chunk of text inside a chamber (ingested content)

Optional cave survey (knowledge graph):
- **Deposit**: an entity (`person`, `project`, `concept`, `tool`)
- **Passage**: a relationship between deposits (e.g. `uses`, `depends_on`)

---

## Quick Start

### Install

```bash
pip install dongtian
```

Or from source (recommended while iterating):

```bash
git clone https://github.com/siaochuan/dongtian.git
cd dongtian
pip install -e .
```

### Configure (optional)

Create `~/.dongtian/config.json`:

```json
{
  "db_path": "~/.dongtian/cavern.db",
  "embedding_api_key": "YOUR_KEY",
  "embedding_base_url": "https://api.siliconflow.cn/v1",
  "embedding_model": "BAAI/bge-m3",
  "remote_hosts": [
    {"host": "dev-machine", "layer": "remote-dev"}
  ]
}
```

- Embeddings are optional. If not configured, Dongtian uses keyword search (FTS5).
- For `remote_hosts`, `layer` and `wing` are both accepted as the layer name override.

### Use as MCP Server

**Claude Code** — add to `.mcp.json` or `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "dongtian": {
      "command": "python",
      "args": ["-m", "dongtian"]
    }
  }
}
```

**Codex CLI** — register via command:

```bash
codex mcp add dongtian -- python -m dongtian
```

---

## MCP Tools

| Tool | Purpose |
|------|---------|
| `list_layers` | List layers |
| `list_chambers` | List chambers in a layer |
| `browse_chamber` | Browse strata (paged) |
| `search` | Keyword / embedding / hybrid search |
| `ingest_source` | Ingest a single file or directory (source-specific) |
| `ingest_claude_project` | Bulk ingest Claude Code sessions |
| `ingest_codex_sessions` | Bulk ingest Codex sessions |
| `ingest_opencode` | Ingest OpenCode (DeepSeek) database |
| `sync_remote` | Sync from one remote host via SSH |
| `sync_all_remotes` | Sync all configured remote hosts |
| `discover_remote` | Discover remote availability (no pull) |
| `add_deposit` | Add/get a deposit (entity) |
| `add_passage` | Add a passage (relationship) |
| `survey` | Query passages (knowledge graph) |
| `extract_survey` | Extract deposits/passages from a stratum |

---

## Supported Sources

| Source | Format | What it parses |
|--------|--------|----------------|
| **Claude Code** | JSONL | `~/.claude/projects/` session histories |
| **Codex** | JSONL | `~/.codex/sessions/` rollouts (turn-based parsing with tool summaries) |
| **OpenCode (DeepSeek)** | SQLite | `~/.local/share/opencode/opencode.db` |
| **ChatGPT** | JSON | OpenAI conversation export (`conversations.json`) |
| **Slack** | JSON | Channel export (directory or single file) |
| **Plain text** | .txt / .md | Split on headings or paragraphs |

All sources are chunked into conversation turns and then into strata.

---

## License

MIT
