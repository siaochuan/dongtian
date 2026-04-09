# Dongtian

[中文文档](README_CN.md)

**Unified memory layer for AI agents. One SQLite file indexes conversations across Claude Code, Codex, ChatGPT, OpenCode, and Slack — with hybrid BM25 + semantic search.**

> *Dongtian* (洞天) is a Daoist concept meaning "grotto-heaven" -- a small sacred space that contains an entire world within. Your conversations deserve the same: a compact local database that holds everything you've ever discussed with AI, structured and instantly searchable.

---

## Why Dongtian?

You use multiple AI tools — Claude Code for architecture, Codex for prototyping, ChatGPT for research, DeepSeek for debugging. Each generates valuable context that dies when the session ends. **No existing tool lets you search across all of them.**

- **mem0** (52K stars) is a powerful memory platform, but it's cloud-heavy (Qdrant/pgvector) and doesn't ingest conversation histories — it stores facts extracted by LLMs
- **claude-mem** (46K stars) auto-captures Claude Code sessions, but it's Claude-only and uses ChromaDB
- **Engram** (2.3K stars) is the closest lightweight competitor (SQLite + FTS5 + MCP), but it has no embedding search and no multi-source ingestion
- **Official MCP memory server** stores knowledge graph triples in a JSON file — no full-text search, no semantic search

**Dongtian fills the gap**: a zero-dependency MCP server that ingests conversations from 6 sources into a single SQLite file, with hybrid FTS5 + embedding search. ~1,800 lines of Python, 2 pip dependencies.

### Competitive Comparison

| | Dongtian | mem0 | claude-mem | Engram | MCP official |
|---|----------|------|------------|--------|--------------|
| **Storage** | SQLite (single file) | Cloud / Qdrant / pgvector | SQLite + ChromaDB | SQLite | JSON file |
| **Search** | FTS5 BM25 + embedding hybrid | Semantic + graph | RAG | FTS5 only | Keyword only |
| **Multi-source ingestion** | Claude, Codex, ChatGPT, OpenCode, Slack, text | No (API-driven) | Claude only | No | No |
| **Knowledge graph** | Yes | No | No | No | Yes |
| **SSH remote sync** | Yes | No | No | No | No |
| **Dependencies** | `httpx` + `mcp` | Qdrant + LLM API | ChromaDB + transformers | None (Go binary) | None |
| **MCP server** | Native | Via wrapper | No (hooks) | Native | Native |
| **Chinese support** | FTS5 + embedding | Embedding only | Embedding only | No | No |
| **Cost** | $0 (SiliconFlow free tier) | Free→$249/mo | Free | Free | Free |

---

## Architecture

Dongtian organizes memory using the palace metaphor, mapped to a simple relational schema:

```
  Palace (SQLite DB)
    |
    +-- Wing: "claude-local"           # top-level domain
    |     +-- Room: "2026-03-19"       # session / topic
    |     |     +-- Drawer: "User asked about deployment config..."
    |     |     +-- Drawer: "Assistant explained the architecture..."
    |     +-- Room: "2026-04-01"
    |           +-- Drawer: ...
    |
    +-- Wing: "chatgpt-export"
    |     +-- Room: "project-planning"
    |           +-- Drawer: ...
    |
    +-- Knowledge Graph
          +-- Entity: "Docker" (tool)
          +-- Entity: "PostgreSQL" (tool)
          +-- Triple: "web-service" --uses--> "PostgreSQL"
```

**6 tables. 3 indexes. 1 FTS5 virtual table. That's it.**

---

## Real-World Numbers

Tested on a live multi-machine setup with real AI conversation histories:

| Metric | Value |
|--------|-------|
| Sources ingested | Claude Code + Codex + OpenCode + 3 remote hosts |
| Total sessions | 117+ (12 Claude, 61 Codex, 44 OpenCode) |
| Total memory chunks (drawers) | **37,936** |
| Embedding coverage | 73% (27,584 / 37,936) |
| Wings (top-level groups) | 10 (local + remote machines) |
| Database size | ~80 MB (with embeddings) |
| Embedding model | BAAI/bge-m3 (1024-dim, free on SiliconFlow) |
| Embedding cost | **$0** (free tier) |

### Search Quality (Hybrid Mode)

| Query | Top Hit | Score |
|-------|---------|-------|
| "database migration rollback" | PostgreSQL migration debugging session | 0.72 |
| "部署配置 Nginx" | Nginx reverse proxy setup discussion | 0.69 |
| "SSH connection config" | SSH troubleshooting session | 0.67 |
| "React state management" | Architecture review session | 0.62 |

Chinese queries work natively through the embedding path -- no special tokenizer needed.

---

## Quick Start

### Install

```bash
pip install dongtian
```

Or from source:

```bash
git clone https://github.com/siaochuan/dongtian.git
cd dongtian
pip install -e .
```

### Configure (optional)

Create `~/.dongtian/config.json` for embedding support:

```json
{
  "embedding_api_key": "your-siliconflow-key",
  "embedding_base_url": "https://api.siliconflow.cn/v1",
  "embedding_model": "BAAI/bge-m3"
}
```

> Embedding is optional. Without it, Dongtian uses SQLite FTS5 full-text search which works great for keyword queries.

> Get a free SiliconFlow API key at [cloud.siliconflow.cn](https://cloud.siliconflow.cn) -- bge-m3 is free tier.

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

Then in any MCP-compatible client:

```
> Search my memory for "deployment configuration"
> Ingest my ChatGPT export into the palace
> What entities are connected to "PostgreSQL"?
```

---

## MCP Tools

| Tool | Purpose |
|------|---------|
| `list_wings` | Browse top-level groupings |
| `list_rooms` | Browse rooms in a wing |
| `browse_room` | Paginated drawer contents |
| `search` | Hybrid search: FTS5 keyword + embedding semantic |
| `search_graph` | Query knowledge graph triples |
| `ingest_source` | Import a file (claude / chatgpt / slack / codex / text) |
| `ingest_claude_project` | Bulk import Claude Code sessions |
| `ingest_codex_sessions` | Bulk import Codex rollouts (turn-based, with tool call summaries) |
| `ingest_opencode` | Import OpenCode (DeepSeek) SQLite database |
| `add_entity` | Add knowledge graph entity |
| `add_triple` | Add relationship triple |
| `extract_knowledge` | Auto-extract entities from a drawer |

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

All sources are chunked into conversation-turn pairs (user + assistant), with sentence-boundary splitting for long content.

---

## Knowledge Graph

Dongtian automatically extracts entities and relationships from your conversations:

**Entity types:** `person`, `project`, `concept`, `tool`

**Relationship predicates:**
- `uses` -- "Flask uses SQLite"
- `deployed_on` -- "app deployed on production"
- `depends_on` -- "project requires Redis"
- `maintains` -- "team maintains the pipeline"
- `connects_to` -- "service connects to database"
- `replaced` -- "switched from MySQL to PostgreSQL"
- `is_a` -- "React is a framework"

Entities and triples can also be added manually via MCP tools for high-confidence facts.

---

## How Search Works

Dongtian offers three search modes:

1. **`keyword`** -- SQLite FTS5 with BM25 ranking. Fast, works offline, no API needed.
2. **`embedding`** -- Cosine similarity against stored vectors. Requires an embedding API.
3. **`hybrid`** (default) -- Weighted combination: 40% BM25 + 60% cosine similarity.

All modes support `wing` and `room` filters to narrow scope.

If no embedding API is configured, hybrid mode automatically falls back to keyword-only.

---

## Cost

| Component | Cost |
|-----------|------|
| Dongtian software | Free (MIT) |
| Storage | Free (local SQLite) |
| Keyword search (FTS5) | Free |
| Embedding (SiliconFlow bge-m3) | Free (free tier) |
| **Total** | **$0/year** |

Compare: mem0 Pro costs $249/month. MemPalace estimates ~$10/year with Haiku reranking.

---

## Project Structure

```
dongtian/
  __init__.py          # package marker
  __main__.py          # entry point: python -m dongtian
  config.py            # configuration (~40 lines)
  db.py                # SQLite schema + queries (~340 lines)
  embeddings.py        # OpenAI-compatible client (~70 lines)
  graph.py             # entity extraction + KG (~130 lines)
  ingest.py            # 6 source parsers (~620 lines)
  remote.py            # SSH remote sync (~200 lines)
  search.py            # hybrid search (~120 lines)
  server.py            # MCP server, 15 tools (~250 lines)
```

**Total: ~1,800 lines of Python. 2 dependencies. 1 SQLite file.**

---

## License

MIT

---

*Named after the Daoist concept of Dongtian -- a grotto-heaven where a small space contains an entire world. Your AI conversations deserve a palace, not a landfill.*
