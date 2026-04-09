# 洞天 (Dongtian)

**跨 AI 平台的统一记忆层。一个 SQLite 文件索引 Claude Code、Codex、ChatGPT、OpenCode、Slack 的所有对话——支持 BM25 + 语义混合搜索。**

> "洞天"源自道家概念——壶中别有天地，一方小小的洞府容纳了整个世界。你和 AI 的对话也值得这样一座溶洞：紧凑、本地、随时可检索。

---

## 为什么做洞天？

你同时使用多种 AI 工具——Claude Code 做架构、Codex 做原型、ChatGPT 做调研、DeepSeek 做调试。每个工具产生的有价值上下文在会话结束后就消失了。**目前没有工具能让你跨平台搜索所有对话。**

- **mem0**（52K stars）是强大的记忆平台，但依赖云端（Qdrant/pgvector），且不摄入对话历史——它存储 LLM 提取的事实
- **claude-mem**（46K stars）自动捕获 Claude Code 会话，但仅支持 Claude，且需要 ChromaDB
- **Engram**（2.3K stars）是最接近的轻量竞品（SQLite + FTS5 + MCP），但没有 embedding 搜索和多源摄入
- **MCP 官方 memory server** 用 JSON 文件存知识图谱三元组——没有全文搜索，没有语义搜索

**洞天填补了这个空白**：零依赖的 MCP 服务器，从 6 种来源摄入对话到单个 SQLite 文件，支持 FTS5 + embedding 混合搜索。~1,800 行 Python，2 个 pip 依赖。

### 竞品对比

| | 洞天 | mem0 | claude-mem | Engram | MCP 官方 |
|---|------|------|------------|--------|----------|
| **存储** | SQLite（单文件） | 云端 / Qdrant / pgvector | SQLite + ChromaDB | SQLite | JSON 文件 |
| **搜索** | FTS5 BM25 + embedding 混合 | 语义 + 图谱 | RAG | 仅 FTS5 | 仅关键词 |
| **多源摄入** | Claude, Codex, ChatGPT, OpenCode, Slack, 文本 | 无（API 驱动） | 仅 Claude | 无 | 无 |
| **洞穴勘测（KG）** | 有 | 无 | 无 | 无 | 有 |
| **SSH 远程同步** | 有 | 无 | 无 | 无 | 无 |
| **依赖** | `httpx` + `mcp` | Qdrant + LLM API | ChromaDB + transformers | 无（Go 二进制） | 无 |
| **MCP 服务器** | 原生 | 需 wrapper | 无（hooks） | 原生 | 原生 |
| **中文支持** | FTS5 + embedding 双路径 | 仅 embedding | 仅 embedding | 无 | 无 |
| **费用** | $0（SiliconFlow 免费） | 免费→$249/月 | 免费 | 免费 | 免费 |

---

## 架构

洞天用**洞穴系统**隐喻组织记忆，映射到简洁的关系模型：

```
  溶洞 (Cavern, SQLite 数据库)
    │
    ├── 层 (Layer): "claude-local"         # 顶层分组（按来源/机器）
    │     ├── 洞室 (Chamber): "2026-04-01" # 会话/主题
    │     │     ├── 地层 (Stratum): "用户问了部署配置..."
    │     │     └── 地层 (Stratum): "助手解释了架构设计..."
    │     └── 洞室 (Chamber): "2026-04-07"
    │           └── 地层 (Stratum): ...
    │
    ├── 层 (Layer): "codex-local"          # Codex 会话
    │     └── 洞室 (Chamber): "API重构讨论"
    │           └── 地层 (Stratum): ...
    │
    ├── 层 (Layer): "remote-dev"           # SSH 远程同步的数据
    │     └── ...
    │
    └── 洞穴勘测 (Cave Survey)
          ├── 沉积 (Deposit): "Docker" (工具)
          ├── 沉积 (Deposit): "PostgreSQL" (工具)
          └── 通道 (Passage): "Web 服务" --使用--> "PostgreSQL"
```

**6 张表，3 个索引，1 个 FTS5 虚拟表，就这些。**

---

## 核心特性

### 多模型记忆共享

不同 AI 工具产生的会话存入同一个溶洞，跨模型检索：

- **Claude Code** 的深度分析 → 用其他模型也能搜到
- **DeepSeek/OpenCode** 的调试记录 → 用 Claude 也能检索
- **ChatGPT** 的导出 → 统一入库

便宜的模型可以复用贵模型的推理结果，用存储成本换计算成本。

### SSH 远程同步

自动从其他机器拉取会话数据：

```json
"remote_hosts": [
    {"host": "user@10.0.1.50", "layer": "remote-server-a"},
    {"host": "dev-machine", "layer": "remote-dev"}
]
```

一条命令同步所有机器的 Claude/Codex/OpenCode 会话。

### OpenCode (DeepSeek) 支持

直接读取 OpenCode 的 SQLite 数据库（`~/.local/share/opencode/opencode.db`），解析 session → message → part 三级结构，提取文本内容。

### 混合搜索

三种搜索模式：

1. **关键词** — SQLite FTS5 + BM25 排序，离线可用
2. **语义** — 向量余弦相似度，需要 embedding API
3. **混合**（默认） — 40% BM25 + 60% 余弦相似度加权

中文搜索原生支持，FTS5 + embedding 双路径。

### 洞穴勘测（知识图谱）

从对话中提取沉积（实体）和通道（关系）：

- **沉积类型：** 人物、项目、概念、工具
- **通道类型：** 使用(uses)、部署于(deployed_on)、依赖(depends_on)、维护(maintains)、连接(connects_to)、替代(replaced)、属于(is_a)

---

## 快速开始

### 安装

```bash
pip install dongtian
```

或从源码：

```bash
git clone https://github.com/siaochuan/dongtian.git
cd dongtian
pip install -e .
```

### 配置（可选）

创建 `~/.dongtian/config.json` 启用 embedding 搜索：

```json
{
  "embedding_api_key": "your-siliconflow-key",
  "embedding_base_url": "https://api.siliconflow.cn/v1",
  "embedding_model": "BAAI/bge-m3"
}
```

> Embedding 是可选的。不配置时洞天使用 FTS5 全文检索，关键词查询效果也很好。

> 在 [cloud.siliconflow.cn](https://cloud.siliconflow.cn) 免费注册即可获得 API Key，bge-m3 模型在免费额度内。

配置远程同步（可选）：

```json
{
  "remote_hosts": [
    {"host": "user@10.0.1.50", "layer": "remote-server-a"},
    {"host": "dev-machine", "layer": "remote-dev"}
  ]
}
```

### 作为 MCP 服务器使用

**Claude Code** — 添加到 `.mcp.json` 或 `~/.claude/settings.json`：

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

**Codex CLI** — 命令行注册：

```bash
codex mcp add dongtian -- python -m dongtian
```

然后在任意 MCP 兼容客户端中：

```
> 搜索记忆中关于"部署配置"的内容
> 把 ChatGPT 导出导入到溶洞
> 同步远程服务器的会话数据
```

---

## MCP 工具列表

### 浏览

| 工具 | 功能 |
|------|------|
| `list_layers` | 列出所有层（顶层分组） |
| `list_chambers` | 列出层下的所有洞室 |
| `browse_chamber` | 分页浏览地层内容 |

### 搜索

| 工具 | 功能 |
|------|------|
| `search` | 混合搜索：FTS5 关键词 + embedding 语义 |
| `survey` | 查询洞穴勘测通道（知识图谱） |

### 导入

| 工具 | 功能 |
|------|------|
| `ingest_source` | 导入文件（claude / chatgpt / slack / codex / opencode / text） |
| `ingest_claude_project` | 批量导入 Claude Code 会话 |
| `ingest_codex_sessions` | 批量导入 Codex 会话（按轮次解析，含工具调用摘要） |
| `ingest_opencode` | 导入 OpenCode (DeepSeek) SQLite 数据库 |

### 远程同步

| 工具 | 功能 |
|------|------|
| `sync_remote` | SSH 拉取并导入单台远程机器的会话 |
| `sync_all_remotes` | 批量同步所有配置的远程主机 |
| `discover_remote` | 探测远程主机有哪些会话数据（不拉取） |

### 洞穴勘测

| 工具 | 功能 |
|------|------|
| `add_deposit` | 添加沉积（实体） |
| `add_passage` | 添加通道（关系） |
| `extract_survey` | 从地层内容自动提取沉积和通道 |

---

## 支持的数据源

| 来源 | 格式 | 解析内容 |
|------|------|----------|
| **Claude Code** | JSONL | `~/.claude/projects/` 会话历史 |
| **Codex** | JSONL | `~/.codex/sessions/` rollout 文件（按轮次解析，含工具摘要） |
| **OpenCode (DeepSeek)** | SQLite | `~/.local/share/opencode/opencode.db` |
| **ChatGPT** | JSON | OpenAI 导出文件 (`conversations.json`) |
| **Slack** | JSON | 频道导出（目录或单个文件） |
| **纯文本** | .txt / .md | 按标题或段落分割 |

所有来源都会被切分为对话轮次对（用户 + 助手），长内容在句子边界处分割。

---

## 实测数据

| 指标 | 数值 |
|------|------|
| 摄入来源 | Claude Code + Codex + OpenCode |
| 总会话数 | 117+（13 Claude, 61 Codex, 44 OpenCode） |
| 总地层 | **5,833 strata** |
| Embedding 覆盖率 | 100%（5,833 / 5,833） |
| 层 (Layers) | 3（claude-176, codex-176, opencode-176） |
| 数据库大小 | 33 MB（含 embedding） |
| Embedding 模型 | BAAI/bge-m3（1024 维，SiliconFlow 免费） |
| Embedding 成本 | **$0**（SiliconFlow 免费额度） |

---

## 项目结构

```
dongtian/
  __init__.py          # 包标识
  __main__.py          # 入口：python -m dongtian
  config.py            # 配置加载（~40 行）
  db.py                # SQLite 模式 + 查询（~340 行）
  embeddings.py        # OpenAI 兼容客户端（~70 行）
  graph.py             # 沉积提取 + 洞穴勘测（~130 行）
  ingest.py            # 6 种源解析器（~620 行）
  remote.py            # SSH 远程同步（~200 行）
  search.py            # 混合搜索（~120 行）
  server.py            # MCP 服务器，15 个工具（~250 行）
```

**总计 ~1,800 行 Python，2 个依赖，1 个 SQLite 文件。**

---

## 费用

| 组件 | 费用 |
|------|------|
| 洞天软件 | 免费 (MIT) |
| 存储 | 免费（本地 SQLite） |
| 关键词搜索 (FTS5) | 免费 |
| Embedding (SiliconFlow bge-m3) | 免费（免费额度） |
| **合计** | **¥0/年** |

---

## 许可证

MIT

---

*以道家洞天福地之名——一方小天地，容纳无限可能。你的 AI 对话值得一座溶洞，而不是一个垃圾场。*
