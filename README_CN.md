# 洞天 (Dongtian)

**一个 SQLite 文件装下你所有的 AI 对话记忆。不需要 ChromaDB，不需要大模型，只有 ~1,200 行 Python。**

> "洞天"源自道家概念——壶中别有天地，一方小小的洞府容纳了整个世界。你和 AI 的对话也值得这样一座记忆宫殿：紧凑、本地、随时可检索。

---

## 为什么做洞天？

现有的 AI 记忆方案要么太重（ChromaDB + 本地 embedding 模型 + 数千行代码），要么太贵（每次检索都调 LLM）。

**洞天把它精简到极致：** 同样的宫殿隐喻、同样的知识图谱，但只用 SQLite + FTS5 全文检索，可选的 embedding 走任何 OpenAI 兼容 API，代码量一次性读完。

| | 传统方案 | 洞天 |
|---|----------|------|
| **向量存储** | ChromaDB（独立进程） | SQLite BLOB（零运维） |
| **Embedding** | 本地 GPU/CPU 模型 | 任意 OpenAI 兼容 API（SiliconFlow 免费） |
| **依赖** | ChromaDB + transformers + Llama | `httpx` + `mcp`（2 个包） |
| **代码量** | 5,000+ 行 | **~1,200 行** |
| **数据源** | Claude, ChatGPT | Claude, ChatGPT, Slack, **Codex/OpenCode(DeepSeek)**, 纯文本 |
| **中文支持** | 仅 embedding | FTS5 关键词 + embedding 双路径 |
| **存储** | 多个文件/进程 | **单个 SQLite 文件** |

---

## 架构

洞天用"宫殿"隐喻组织记忆，映射到简洁的关系模型：

```
  宫殿 (SQLite 数据库)
    │
    ├── 翼 (Wing): "claude-176"         # 顶层分组（按来源/机器）
    │     ├── 室 (Room): "2026-04-01"   # 会话/主题
    │     │     ├── 抽屉 (Drawer): "用户问了因子流水线架构..."
    │     │     └── 抽屉 (Drawer): "助手解释了回测框架..."
    │     └── 室 (Room): "2026-04-07"
    │           └── 抽屉 (Drawer): ...
    │
    ├── 翼 (Wing): "deepseek-176"       # DeepSeek/OpenCode 会话
    │     └── 室 (Room): "MST构建讨论"
    │           └── 抽屉 (Drawer): ...
    │
    ├── 翼 (Wing): "remote-212"         # SSH 远程同步的数据
    │     └── ...
    │
    └── 知识图谱
          ├── 实体: "Docker" (工具)
          ├── 实体: "Ray Cluster" (概念)
          └── 三元组: "交易系统" --部署在--> "生产服务器"
```

**6 张表，3 个索引，1 个 FTS5 虚拟表，就这些。**

---

## 核心特性

### 多模型记忆共享

不同 AI 工具产生的会话存入同一个数据库，跨模型检索：

- **Claude Code** 的深度分析 → 用 MiniMax 也能搜到
- **DeepSeek/OpenCode** 的调试记录 → 用 Claude 也能检索
- **ChatGPT** 的导出 → 统一入库

便宜的模型可以复用贵模型的推理结果，用存储成本换计算成本。

### SSH 远程同步

自动从其他机器拉取会话数据：

```python
# 配置远程主机
"remote_hosts": [
    {"host": "user@10.0.1.50", "wing": "remote-server-a"},
    {"host": "dev-machine", "wing": "remote-dev"}
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

### 知识图谱

自动从对话中提取实体和关系：

- **实体类型：** 人物、项目、概念、工具
- **关系类型：** 使用(uses)、部署于(deployed_on)、依赖(depends_on)、维护(maintains)、连接(connects_to)、替代(replaced)、属于(is_a)

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
    {"host": "user@10.0.1.50", "wing": "remote-server-a"},
    {"host": "dev-machine", "wing": "remote-dev"}
  ]
}
```

### 作为 MCP 服务器使用

添加到 Claude Code 或 OpenHarness 配置：

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

然后在对话中：

```
> 搜索记忆中关于"因子回测"的内容
> 把 ChatGPT 导出导入到宫殿
> 同步远程服务器的会话数据
```

---

## MCP 工具列表

### 浏览

| 工具 | 功能 |
|------|------|
| `list_wings` | 列出所有翼（顶层分组） |
| `list_rooms` | 列出翼下的所有室 |
| `browse_room` | 分页浏览抽屉内容 |

### 搜索

| 工具 | 功能 |
|------|------|
| `search` | 混合搜索：FTS5 关键词 + embedding 语义 |
| `search_graph` | 查询知识图谱三元组 |

### 导入

| 工具 | 功能 |
|------|------|
| `ingest_source` | 导入文件（claude / chatgpt / slack / codex / opencode / text） |
| `ingest_claude_project` | 批量导入 Claude Code 会话 |
| `ingest_codex_sessions` | 批量导入 Codex/OpenCode 会话 |
| `ingest_opencode` | 导入 OpenCode (DeepSeek) SQLite 数据库 |

### 远程同步

| 工具 | 功能 |
|------|------|
| `sync_remote` | SSH 拉取并导入单台远程机器的会话 |
| `sync_all_remotes` | 批量同步所有配置的远程主机 |
| `discover_remote` | 探测远程主机有哪些会话数据（不拉取） |

### 知识图谱

| 工具 | 功能 |
|------|------|
| `add_entity` | 添加知识图谱实体 |
| `add_triple` | 添加关系三元组 |
| `extract_knowledge` | 从抽屉内容自动提取实体和关系 |

---

## 支持的数据源

| 来源 | 格式 | 解析内容 |
|------|------|----------|
| **Claude Code** | JSONL | `~/.claude/projects/` 会话历史 |
| **Codex / OpenCode** | JSONL | `~/.codex/sessions/` rollout 文件 |
| **OpenCode (DeepSeek)** | SQLite | `~/.local/share/opencode/opencode.db` |
| **ChatGPT** | JSON | OpenAI 导出文件 (`conversations.json`) |
| **Slack** | JSON | 频道导出（目录或单个文件） |
| **纯文本** | .txt / .md | 按标题或段落分割 |

所有来源都会被切分为对话轮次对（用户 + 助手），长内容在句子边界处分割。

---

## 与 OpenHarness 集成

洞天可以作为 [OpenHarness](https://github.com/HKUDS/OpenHarness) 的记忆层：

1. **MCP 工具** — 15 个工具直接可用，agent 可以搜索、导入、同步
2. **System Prompt 注入** — 每次对话自动检索相关历史记忆注入上下文（[PR #72](https://github.com/HKUDS/OpenHarness/pull/72)）
3. **本地个性化** — 配合自动规则提取，从历史中学习用户的本地环境（[PR #65](https://github.com/HKUDS/OpenHarness/pull/65)）

---

## 实测数据

| 指标 | 数值 |
|------|------|
| 导入会话数 | 91+ sessions |
| 总记忆片段 | **16,589 drawers** |
| 数据库大小 | ~80 MB（含 embedding） |
| Embedding 速度 | 174 chunks/sec |
| 翼 (Wings) | 5（本机 Claude + Codex + DeepSeek + 远程 212） |
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
  graph.py             # 实体提取 + 知识图谱（~130 行）
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

*以道家洞天福地之名——一方小天地，容纳无限可能。你的 AI 对话值得一座宫殿，而不是一个垃圾场。*
