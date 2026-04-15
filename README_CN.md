# 洞天 (Dongtian)

**本地统一记忆层：把多种 AI 工具的对话历史存进一个 SQLite 文件，并以 MCP Server 形式对外提供检索与导入能力。**

洞天用于跨工具保存与检索你的对话记录（例如 Claude Code、Codex、ChatGPT 导出、OpenCode、Slack）。

它会把对话摄入到一套简洁的 SQLite 模型中，并提供：
- 关键词检索（SQLite FTS5，离线可用）
- 可选的 embedding 语义检索（需要配置 embedding API）
- 混合排序（关键词 + 语义）
- 可选的 SSH 远程同步
- 可选的“洞穴勘测”知识图谱（沉积 + 通道）

---

## 数据模型（术语）

洞天使用“洞穴”隐喻，让概念更直观，也方便在不同数据源之间保持一致：

- **洞府 (Cavern)**：SQLite 数据库文件
- **层 (Layer)**：顶层分组（常用作机器名或来源名，例如 `codex-laptop`）
- **洞室 (Chamber)**：某层下的会话/主题（常用日期或会话 id）
- **地层 (Stratum)**：洞室内的一段文本内容（摄入后的文本分块）

可选的洞穴勘测（知识图谱）：
- **沉积 (Deposit)**：实体（`person`/`project`/`concept`/`tool`）
- **通道 (Passage)**：实体之间的关系（例如 `uses`、`depends_on`）

---

## 快速开始

### 安装

```bash
pip install dongtian
```

或从源码安装（本地开发/迭代更推荐）：

```bash
git clone https://github.com/siaochuan/dongtian.git
cd dongtian
pip install -e .
```

### 配置（可选）

创建 `~/.dongtian/config.json`：

```json
{
  "db_path": "~/.dongtian/cavern.db",
  "embedding_api_key": "YOUR_KEY",
  "embedding_base_url": "https://api.siliconflow.cn/v1",
  "embedding_model": "BAAI/bge-m3",
  "hook_candidate_auto_update": true,
  "hook_candidate_auto_ingest": true,
  "hook_candidate_since_days": 14,
  "hook_candidate_output_dir": "~/.dongtian/hook_candidates",
  "hook_candidate_hook_file": "~/.openharness-w8/hooks/strategy_route_env_guard.py",
  "hook_candidate_layer": "dongtian-system",
  "hook_candidate_chamber_prefix": "hook_candidates",
  "remote_hosts": [
    {"host": "dev-machine", "layer": "remote-dev"}
  ]
}
```

- Embedding 是可选项；不配置时默认使用 FTS5 关键词检索。
- `remote_hosts` 中 `layer` 与 `wing` 两个字段都支持（作用相同：指定同步进来的 layer 名称）。
- hook 候选规则配置是可选项；开启后可自动挖掘 OpenHarness 的拦截历史并落盘/入库。

### 作为 MCP Server 使用

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

---

## MCP 工具

| 工具 | 作用 |
|------|------|
| `list_layers` | 列出所有层 |
| `list_chambers` | 列出层下所有洞室 |
| `browse_chamber` | 分页浏览地层内容 |
| `search` | 关键词 / 语义 / 混合检索 |
| `ingest_source` | 导入单个文件/目录（按 source 类型解析） |
| `ingest_claude_project` | 批量导入 Claude Code 会话 |
| `ingest_codex_sessions` | 批量导入 Codex 会话 |
| `ingest_opencode` | 导入 OpenCode (DeepSeek) 数据库 |
| `sync_remote` | SSH 同步单台远程主机并导入 |
| `sync_all_remotes` | 同步所有配置的远程主机 |
| `discover_remote` | 探测远程主机可用数据（不拉取） |
| `add_deposit` | 添加/获取沉积（实体） |
| `add_passage` | 添加通道（关系） |
| `survey` | 查询通道（知识图谱） |
| `extract_survey` | 从某条地层内容中提取沉积/通道 |
| `mine_hook_candidates` | 从 OpenHarness 会话挖掘 hook 候选规则 |
| `hook_update_status` | 查看/触发每日异步 hook 更新状态 |

---

## 支持的数据源

| 来源 | 格式 | 解析内容 |
|------|------|----------|
| **Claude Code** | JSONL | `~/.claude/projects/` 会话历史 |
| **Codex** | JSONL | `~/.codex/sessions/` rollout 文件（按轮次解析，含工具摘要） |
| **OpenCode (DeepSeek)** | SQLite | `~/.local/share/opencode/opencode.db` |
| **ChatGPT** | JSON | OpenAI 导出文件（`conversations.json`） |
| **Slack** | JSON | 频道导出（目录或单个文件） |
| **纯文本** | .txt / .md | 按标题或段落分割 |

所有来源会被切分为对话轮次，再进一步切成地层分块存储。

---

## 每日异步 Hook 更新

当 `hook_candidate_auto_update=true` 时，洞天会在**每天第一次 MCP 工具调用**时触发后台异步更新。

行为：
- 非阻塞异步执行（工具调用立即返回，不等待更新完成）
- 产物写入 `hook_candidate_output_dir/latest`（可选额外时间戳快照）
- `hook_candidate_auto_ingest=true` 时自动将报告产物写回洞天

手动控制：
- 调用 `hook_update_status(force_trigger=true)` 可强制触发一次异步刷新
- 调用 `mine_hook_candidates(...)` 可前台手动执行并按需入库

---

## 许可证

MIT
