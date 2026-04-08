# Dongtian

[中文文档](README_CN.md)

**A single SQLite file holds your entire AI memory. No ChromaDB. No heavy models. ~1,200 lines of Python.**

> *Dongtian* is a Daoist concept meaning "grotto-heaven" -- a small sacred space that contains an entire world within. Your conversations deserve the same: a compact local database that holds everything you've ever discussed with AI, structured and instantly searchable.

---

## Why Dongtian?

We loved the idea behind [MemPalace](https://github.com/milla-jovovich/mempalace) -- turning AI conversations into a structured, searchable memory system. But we found it too heavy: ChromaDB as a separate process, sentence-transformers for local embeddings, complex AAAK compression, and thousands of lines of code.

**Dongtian strips it down to the essentials.** Same palace metaphor. Same knowledge graph. But built on pure SQLite with FTS5 full-text search, optional embeddings via any OpenAI-compatible API, and a codebase small enough to read in one sitting.

| | MemPalace | Dongtian |
|---|-----------|----------|
| **Vector store** | ChromaDB (separate process) | SQLite BLOB (zero ops) |
| **Embedding** | sentence-transformers (local GPU/CPU) | Any OpenAI-compatible API (SiliconFlow free tier) |
| **Dependencies** | ChromaDB + sentence-transformers + Llama | `httpx` + `mcp` (2 packages) |
| **Codebase** | ~5,000+ lines | **~1,200 lines** |
| **Install** | pip install + ChromaDB setup + model download | `pip install dongtian` |
| **Knowledge graph** | Yes | Yes |
| **Data sources** | Claude, ChatGPT, Slack | Claude, ChatGPT, Slack, **Codex/OpenCode**, text |
| **Chinese support** | Embedding only | FTS5 keyword + embedding dual-path |
| **MCP tools** | 19 | 11 (focused) |
| **Storage** | ChromaDB collection + SQLite KG | **Single SQLite file** |

---

## Architecture

Dongtian organizes memory using the palace metaphor, mapped to a simple relational schema:

```
  Palace (SQLite DB)
    |
    +-- Wing: "codex-project"        # top-level domain
    |     +-- Room: "2026-03-19"     # session / topic
    |     |     +-- Drawer: "User asked about factor pipeline..."
    |     |     +-- Drawer: "Assistant explained the backtest..."
    |     +-- Room: "2026-04-01"
    |           +-- Drawer: ...
    |
    +-- Wing: "chatgpt-export"
    |     +-- Room: "project-planning"
    |           +-- Drawer: ...
    |
    +-- Knowledge Graph
          +-- Entity: "Docker" (tool)
          +-- Entity: "Server 176" (concept)
          +-- Triple: "trading-system" --deployed_on--> "Server 176"
```

**6 tables. 3 indexes. 1 FTS5 virtual table. That's it.**

---

## Real-World Numbers

Tested on a live setup across two machines with actual AI conversation histories:

| Metric | Value |
|--------|-------|
| Total conversations ingested | 91 sessions |
| Total memory chunks (drawers) | **16,456** |
| Database size | 82.6 MB (with embeddings) |
| Embedding generation speed | 174 chunks/sec |
| 3,623 chunks embedded in | 21 seconds |
| Embedding model | BAAI/bge-m3 (1024-dim, free on SiliconFlow) |
| Embedding cost | **$0** (free tier) |

### Search Quality (Hybrid Mode)

| Query | Top Hit | Embedding Score |
|-------|---------|-----------------|
| "labubu price monitor" | Labubu price tracking dashboard | 0.65 |
| "Iran USDT exchange rate" | USDT premium analysis report | 0.77 |
| "backtest factor strategy" | Factor pipeline architecture doc | 0.62 |
| "SSH connection config" | SSH troubleshooting session | 0.67 |
| "state management bottleneck" | Architecture review session | 0.64 |

Chinese queries work natively through the embedding path -- no special tokenizer needed.

---

## Quick Start

### Install

```bash
pip install dongtian
```

Or from source:

```bash
git clone https://github.com/yourname/dongtian.git
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

Add to your Claude Code config (`.mcp.json` or `~/.claude/settings.json`):

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

Then in Claude Code:

```
> Search my memory for "factor pipeline architecture"
> Ingest my ChatGPT export into the palace
> What entities are connected to "Server 176"?
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
| `ingest_codex_sessions` | Bulk import Codex/OpenCode rollouts |
| `add_entity` | Add knowledge graph entity |
| `add_triple` | Add relationship triple |
| `extract_knowledge` | Auto-extract entities from a drawer |

---

## Supported Sources

| Source | Format | What it parses |
|--------|--------|----------------|
| **Claude Code** | JSONL | `~/.claude/projects/` session histories |
| **Codex / OpenCode** | JSONL | `~/.codex/sessions/` rollout files |
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
- `deployed_on` -- "app deployed on Server 176"
- `depends_on` -- "project requires Redis"
- `maintains` -- "Alice maintains the pipeline"
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

Compare: MemPalace estimates ~$10/year with Haiku reranking, or $507/year for LLM-summary approaches.

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
  ingest.py            # 5 source parsers (~420 lines)
  search.py            # hybrid search (~120 lines)
  server.py            # MCP server, 11 tools (~200 lines)
```

**Total: ~1,200 lines of Python. 2 dependencies. 1 SQLite file.**

---

## License

MIT

---

*Named after the Daoist concept of Dongtian -- a grotto-heaven where a small space contains an entire world. Your AI conversations deserve a palace, not a landfill.*
