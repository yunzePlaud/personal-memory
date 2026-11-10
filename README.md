# PMI · Personal Memory Infrastructure

**My Memory, Any Agent.** PMI 是跑在你自己电脑上的长期记忆库。把 PRD、会议纪要、Notion 页面、Linear ticket、GitHub PR、Slack 讨论交给它，它会保存原文，并提炼成带出处的知识。Claude Code、Codex、Cursor 等任何 AI Agent 都能通过 MCP（或命令行）读写同一套记忆。

> *English:* PMI is a local-first, portable long-term memory for AI agents. Raw sources and memories live in one SQLite file plus a folder, and agents connect over MCP (stdio) or a CLI. Every memory cites the exact source text. Newer decisions supersede older ones, and conflicts with confirmed facts go to human review. The docs below are in Chinese; the commands are self-explanatory.

- **有出处**：每条记忆都附原文引用。写入时逐字核对，对不上就拒绝。
- **会演化**：新决定自动替代旧决定，旧的成为带有效期的历史。如果新内容和你确认过的结论冲突，PMI 不会静默覆盖，而是送去审批。
- **分得清**：检索结果会标出每条内容属于哪一类：当前有效、已被替代、有争议、未决讨论、证据不足。
- **零服务**：没有数据库服务器、没有 Docker、没有常驻进程。一个 SQLite 文件加一个原文目录就是全部数据，复制文件夹就能搬家。
- **不绑模型**：PMI 自己不调用任何 LLM，理解文本由你正在用的 Agent 完成。可选的本地多语言 embedding 模型只用于语义检索。

---

## 目录

1. [5 分钟上手](#1-5-分钟上手)
2. [安装详解](#2-安装详解)
3. [接入 AI Agent](#3-接入-ai-agent)
4. [增加知识](#4-增加知识)
5. [更新知识](#5-更新知识)
6. [删除知识](#6-删除知识)
7. [查询知识](#7-查询知识)
8. [写入策略与可信度](#8-写入策略与可信度)
9. [数据、备份与迁移](#9-数据备份与迁移)
10. [命令速查](#10-命令速查)
11. [安全边界](#11-安全边界)
12. [故障排查](#12-故障排查)
13. [开发与项目结构](#13-开发与项目结构)

---

## 1. 5 分钟上手

需要：macOS 或 Linux、`git`、[uv](https://docs.astral.sh/uv/)。Python 3.12 由 uv 自动准备。

```bash
# 0. 没有 uv 的话先装
curl -LsSf https://astral.sh/uv/install.sh | sh

# 1. 克隆并安装：装依赖、建数据目录、把 pmi 链接到 ~/.local/bin、装 Claude Code skills
git clone https://github.com/yunzePlaud/personal-memory.git ~/personal-memory
cd ~/personal-memory
./scripts/install.sh            # 加 --with-model 会顺便下载语义检索模型（约 220MB）

# 2. 导入示例数据并提问
pmi import examples/project-a --extract
pmi context "为什么 Project A 不采用方案 B"
pmi context "Project A 现在什么时候上线"
pmi context "Project A 什么时候上线" --as-of 2026-10-15     # 回到过去某天的视角

# 3. 接入 Claude Code（所有项目可用）
claude mcp add --scope user pmi -- ~/personal-memory/pmi mcp
```

重启 Claude Code，然后直接问：「Project A 为什么不用方案 B？」Agent 会先调用 `memory_get_context`，再带着出处回答。

> `examples/` 里的数据是虚构的。想清空重来：删掉 `data/` 目录，再运行 `pmi init`。

---

## 2. 安装详解

### `scripts/install.sh` 做了什么

| 步骤 | 内容 |
|---|---|
| 1 | `uv sync --frozen --extra semantic`：按 `uv.lock` 安装依赖，包括本地语义检索用的 fastembed |
| 2 | `pmi init`：在仓库下创建 `data/` 目录，存放你的记忆数据（已在 `.gitignore` 里） |
| 3 | 把 `pmi` 链接到 `~/.local/bin/pmi`。这个目录不在 PATH 里时，脚本会提示你加上 |
| 4 | 把 `integrations/claude-code/skills/` 下的 3 个 skill 链接到 `~/.claude/skills/` |
| 可选 | `--with-model`：下载本地多语言 embedding 模型 `paraphrase-multilingual-MiniLM-L12-v2`（约 220MB） |

可用选项：

- `--no-skills`：不装 skill。
- `--no-semantic`：只用关键词检索，依赖更少。之后请在 shell 配置里加上 `export PMI_NO_SEMANTIC=1`，接入 Claude Code 时加 `-e PMI_NO_SEMANTIC=1`；否则第一次运行 `pmi` 时会自动补装语义检索依赖。
- `--with-model`：同时下载语义检索模型。
- `PMI_BIN_DIR` / `PMI_SKILLS_DIR`：环境变量，用来改链接的目标目录。

脚本可以重复运行。如果目标位置已经有同名的真实文件或文件夹，脚本不会覆盖它。

### 手动安装

```bash
uv sync --frozen --extra semantic
./pmi init
./pmi doctor          # 检查 Python、SQLite、FTS5、模型、目录权限
./pmi model download  # 可选：下载语义检索模型
```

`./pmi` 是启动脚本：它把 `PMI_HOME` 设为仓库目录，再用 uv 运行。通过符号链接调用（如 `~/.local/bin/pmi`）时，它同样能找到仓库。所有命令都支持 `--json`。

### 外部工具（按需）

| 用途 | 需要 |
|---|---|
| 导入 Notion / Linear / Slack | 在 Claude Code 里连接对应的 connector（claude.ai → Settings → Connectors） |
| 导入 GitHub PR（`/pmi-linear`、`pmi github-pr`） | 安装 [GitHub CLI](https://cli.github.com/)，并运行 `gh auth login` |

### 配置（可选）

把 `config/settings.example.toml` 复制为 `config/settings.toml`。常用项：

```toml
[embedding]
provider = "none"                 # 关闭语义检索；默认是 "fastembed"
[capture]
import_roots = ["~/Documents", "~/Desktop", "~/Downloads"]   # Agent 能让 PMI 读取的目录；隐藏目录一律拒绝
[policy]
inferred_create = "review"        # Agent 提交的、没有原文引用的推断：review / apply / deny
```

**不要在配置文件里放任何密钥**，因为这个文件夹是可以整体复制走的。

---

## 3. 接入 AI Agent

所有 Agent 都用同一条命令启动 PMI 的 MCP 服务器（stdio）：

```
命令：/绝对路径/personal-memory/pmi
参数：["mcp"]
```

### Claude Code

```bash
claude mcp add --scope user pmi -- ~/personal-memory/pmi mcp    # --scope user：所有项目可用
claude mcp list                                                 # 确认已连接
```

`install.sh` 已经装好 3 个 skill：`/pmi-notion`、`/pmi-linear`、`/pmi-slack`，用法见[第 4 节](#4-增加知识)。

### Codex CLI

在 `~/.codex/config.toml` 里加上：

```toml
[mcp_servers.pmi]
command = "/Users/<you>/personal-memory/pmi"
args = ["mcp"]
```

### Cursor / Claude Desktop / Gemini CLI / 其他 MCP 客户端

这些客户端用同一种 JSON 结构，只是配置文件的位置不同：

| 客户端 | 配置文件 |
|---|---|
| Cursor | `~/.cursor/mcp.json`（全局），或项目内的 `.cursor/mcp.json` |
| Claude Desktop | macOS：`~/Library/Application Support/Claude/claude_desktop_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |

```json
{
  "mcpServers": {
    "pmi": { "command": "/Users/<you>/personal-memory/pmi", "args": ["mcp"] }
  }
}
```

### MCP 提供的 6 个工具

服务器的 `instructions` 字段写明了用法，新的 Agent 连上就能直接用：

| 工具 | 作用 |
|---|---|
| `memory_get_context` | **首选**。给一个问题，返回当前有效的记忆（带引用）、历史、冲突和相关原文片段；证据不足时会明说 |
| `memory_search` | 低层检索，范围是记忆或原文片段 |
| `memory_get` | 按 id 读取：记忆（含版本历史）、原文（分页）、实体、待审批变更 |
| `memory_add_source` | 存原文（文本或文件）。传 `url` 会生成稳定的来源身份，便于版本化。默认 `trust="external"` |
| `memory_propose` | **唯一的写入口**：create / supersede / update / link / archive / restore |
| `memory_list` | 浏览记忆、实体、slot、待审批变更、最近操作。传 `source=<来源id>` 可以列出引用某来源的记忆 |

确认、删除、审批**不在 MCP 上**，只能由你本人在命令行里执行。

### 让 Agent 主动使用 PMI

在项目的 `CLAUDE.md` / `AGENTS.md` 里加一段，Agent 会更稳定地先查 PMI：

```markdown
回答与我过去工作相关的问题（需求背景、决策原因、谁负责、某功能现在的状态）之前，先调用 PMI 的 `memory_get_context`，回答时附上它返回的出处；PMI 说证据不足时直接说明，不要编造。
```

### 不支持 MCP 的 Agent 和脚本

能执行命令的 Agent 都可以用命令行：

```bash
pmi --json context "某个问题"
```

在 Python 里直接调用：

```python
from pmi import Memory
with Memory.open("~/personal-memory") as m:
    ctx = m.get_context("为什么 Project A 不采用方案 B")
```

---

## 4. 增加知识

PMI 里的数据分两层：

- **原文**（sources）：证据本身。
- **记忆**（memories）：从原文提炼出的知识，每条都引用原文。

### 4.1 导入文件和目录

```bash
pmi import ~/Documents/prds                 # 批量导入目录：Markdown / TXT / PDF / DOCX，GBK 编码的中文文本也能读
pmi import meeting.md --extract             # 按约定格式写的会议纪要会自动提炼成记忆（格式见下）
pmi import notes/ --trust external          # 别人写的资料可以标为外部来源
```

导入时：

- 隐藏文件和不支持的格式会被跳过，并列出原因。单个坏文件不影响其余文件。
- 内容相同的文件会自动去重。
- **同一个文件改过之后再导入，会存成新版本**，旧版本保留。

**会议纪要约定格式**（用 `--extract` 提炼，不需要任何 LLM）：

```markdown
---
title: Project A 周会
date: 2026-10-01
project: Project A
attendees: [Alice, Bob]
type: meeting
---
# Project A 周会 2026-10-01

## 决策
- [slot: launch-date] Project A 计划于 11 月 15 日上线。

## 事实
- 推送平台由 Bob 的团队维护。

## 待办
- @Bob 在 10 月 10 日前完成接入评估。
```

格式说明：

- 小节名可以用 `决策/决定/Decisions`、`事实/背景/结论/Facts/Context`、`待办/行动项/Action Items/TODO`。
- `[slot: …]` 是主题键，见[第 5 节](#5-更新知识)。
- `@名字` 后面要加一个空格。
- 自由格式的文档（比如 PRD）用这种方式提取不出内容，请交给 Agent 提炼（见 4.3）。

### 4.2 写一段文本

```bash
pmi note "Project A 的发布窗口改到每周四"
pbpaste | pmi note - --kind document --title "某 PRD" --url "<原始链接>" --trust external
```

### 4.3 让 Agent 提炼（推荐）

在 Claude Code 里说：「把这份 PRD 存进 PMI 并提炼知识」。Agent 会这样做：

1. 调用 `memory_add_source` 存原文。
2. 调用 `memory_propose` 提交记忆，每条都附上逐字引用的 `quote`。

PMI 会核对每条引用，并自动处理去重、替代和冲突。

### 4.4 从 Notion / Linear / Slack 导入（Claude Code skills）

| 你说 | 会发生什么 |
|---|---|
| `/pmi-notion <Notion 页面链接>` | 抓取整页，**先给你看标题、位置、字数，你确认后**才存原文，然后自动提炼决策、规则、流程、负责人 |
| `/pmi-linear <ticket 链接或编号>` | 抓取 ticket（描述、评论、状态流转）和它关联的 GitHub PR（描述、人工 review 讨论、改动文件列表，**不含 diff**，由你本机的 `gh` 原样生成）。你确认后存入，再提炼根因、方案、取舍、回归要点 |
| `/pmi-slack <thread 链接>` | 读取整个 thread。确认时会**标出私有频道 / 私信**。存入后按讨论的最终结论提炼，并区分已拍板的决策、**未决讨论**、行动项 |

也可以直接说「把这个 ticket 导入 PMI」。三个 skill 遵循同样的规则：

- **只有你明确要求导入时才执行。** 如果链接夹在别的任务里（「照这个 PRD 实现」「帮我回复这个 thread」），不会被存进 PMI。
- **写入前一定先问你。** 一次确认同时覆盖「存原文」和「自动提炼」。说「只存原文」可以跳过提炼。
- **一律按外部文档保存。** 由它们支撑的记忆，不能自动改掉你确认过的记忆，也不能改掉由你自己文档支撑的记忆。
- **同一对象只有一个身份。** 同一页面、ticket、PR 或 thread 的各种链接写法都对应同一个来源身份，例如 `notion:<页面ID>`、`linear:APP-184`、`github:owner/repo#123`、`slack:<频道>/<thread 时间戳>`。再次导入会生成新版本。
- **不保存个人标识。** 不存邮箱和用户 ID；图片只保留说明文字或文件名。

其他 Agent 可以参考 `integrations/claude-code/skills/*/SKILL.md` 里的流程，用它们自己的 Notion / Linear / Slack 连接器做同样的事。

### 4.5 直接写记忆（JSON）

```bash
pmi propose ops.json                      # 以你本人身份执行，直接生效
pmi propose ops.json --as-agent my-bot    # 以 Agent 身份执行，受写入策略约束，冲突会进审批
```

`ops.json` 示例（完整示例见 `examples/agent-ops.json`）：

```json
[
  {"op": "create", "memory": {
    "kind": "decision",
    "content": "Project A 的推送通知采用方案 A（接入现有推送平台）",
    "slot": "project-a/architecture",
    "entities": [{"name": "Project A", "kind": "project"}],
    "valid_from": "2026-10-01",
    "evidence": [{"source_id": "src_…", "quote": "采用方案 A（接入现有推送平台）"}]
  }}
]
```

字段说明：

- `kind` 可选值：`episodic`（事件）、`semantic`（事实）、`project`、`person`、`decision`、`procedure`（流程）、`preference`。
- `evidence.quote` 必须逐字出自 `source_id` 对应的原文，且至少 4 个字。

---

## 5. 更新知识

### slot：同一主题只有一个当前结论

`slot` 是记忆的主题键，例如 `project-a/launch-date`。同一个 slot 在同一时刻只有一条有效记忆。新主张进来时：

| 新进来的主张 | 结果 |
|---|---|
| 日期比现有结论晚 | **自动替代**旧结论。旧结论变成 superseded，记下有效期和替代者 |
| 日期比现有结论早（补录历史） | 直接插进历史，当前结论不变。所以**乱序导入不影响结果** |
| 和你确认过的结论冲突，或证据的可信度更低 | 新主张标为 `disputed` 并生成待审批变更，原结论不动 |
| 没有日期、分不清先后 | 进待审批 |
| 内容和现有记忆相同 | 合并为一条，只追加引用 |

### 常用更新操作

```bash
pmi import 改过的文件.md              # 存成原文新版本；旧版本里被引用、但新版里消失的句子会提示出来
pmi confirm mem_…                     # 确认一条记忆，之后 Agent 不能自动改它
pmi review list                       # 查看待审批变更，会显示将被替代或激活的记忆全文和引用
pmi review approve prp_…              # 批准；被批准的记忆标为已确认
pmi review reject prp_… --reason "Slack 消息不准确"
pmi show mem_… --history              # 查看一条记忆的完整版本历史和引用
pmi entity alias ent_… "别名"          # 让不同叫法指向同一个人或项目
```

也可以用 JSON 修改，传给 `pmi propose` 或 MCP 的 `memory_propose`：

- 修改内容：`{"op":"update","id":"mem_…","changes":{"content":"…"}}`
- 替代旧记忆：`{"op":"supersede","old_ids":["mem_…"],"memory":{…}}`
- 关联两条记忆：`{"op":"link","from_id":"…","to_id":"…","type":"contradicts"}`

---

## 6. 删除知识

```bash
pmi archive mem_… --reason "不再相关"            # 软删除：检索默认不返回，可以恢复
pmi restore-memory mem_…                         # 恢复归档的记忆

pmi forget mem_… --reason "不需要了"             # 硬删除一条记忆
pmi forget src_… --reason "敏感"                 # 硬删除原文；有记忆依赖它时会拒绝，并列出受影响的记忆
pmi forget src_… --reason "敏感" --cascade        # 连同只依赖这份原文的记忆一起删除
pmi forget src_… --reason "敏感" --keep-memories  # 保留记忆，但降级为「推断」
pmi forget src_… --reason "…" --this-version      # 只删这一个版本（默认删除该文档的所有版本）
```

关于遗忘：

- 遗忘不可恢复，只留下不含内容的墓碑（id、时间、原因）。
- 数据库开启了 `secure_delete`，遗忘后还会压缩索引、截断 WAL，被删内容不会残留在数据库文件里。
- **遗忘之前生成的备份里仍然有这份内容**，需要的话请自己删除旧备份。

---

## 7. 查询知识

```bash
pmi context "Project A 现在什么时候上线"                 # 组装好的上下文：当前结论、冲突、历史、原文片段
pmi context "上线时间" --project "Project A" --history
pmi context "上线时间" --as-of 2026-10-15                 # 按某个时间点的视角回答
pmi search "推送平台" --scope sources                      # 低层检索
pmi show mem_… src_…                                       # 按 id 查看
pmi list slots | entities | proposals | recent | memories  # 浏览
```

`context` 返回结果中的字段：

- `status`：active / superseded / disputed。
- `verification`：user_confirmed / source_backed / inferred。
- `trust`：每条引用的可信度。
- `insufficient_evidence: true`：PMI 没有足够证据回答这个问题。只靠实体关联或弱语义相似召回的内容仍会列出，但不算作证据。

---

## 8. 写入策略与可信度

| 谁 | 能做什么 |
|---|---|
| Agent（通过 MCP，或 CLI 加 `--as-agent`） | 有原文引用的新记忆直接生效（标为未确认）。以下操作会进待审批：没有引用的推断、改动受保护的记忆、恢复你归档的记忆。不能确认、删除或审批 |
| 你（CLI） | 一切操作 |

**受保护的记忆**是指你确认过的记忆，或由更可信来源支撑的记忆。

可信度从高到低：

- `owner`：你自己导入的内容。
- `agent`：Agent 的分析。
- `external`：Slack、网页、别人的文档，以及 Notion / Linear 导入。

一次改动能否自动生效，看的是它**新带来的证据中最低的**可信度。附带一句目标原文里本来就有的话，不能抬高可信度。

---

## 9. 数据、备份与迁移

```
personal-memory/
├── pmi                         启动脚本
├── scripts/install.sh          一键安装
├── integrations/claude-code/   Claude Code skills（pmi-notion / pmi-linear / pmi-slack）
├── src/  tests/  docs/  examples/
├── config/settings.toml        可选配置（不进 git）
└── data/                       你的记忆（不进 git）
    ├── memory/pmi.db           ← 必需：真相源
    ├── sources/                ← 必需：原始文件
    ├── memory/backups/         备份
    ├── index/search.db         可删：检索索引，会自动重建
    └── models/                 可删：embedding 模型，可以重新下载
```

```bash
pmi verify            # 完整性检查：数据库、哈希、每条引用是否仍在原文里、版本链、遗忘是否彻底
pmi verify --fix      # 同时清理没有被任何来源引用的孤儿文件
pmi backup            # 一致性快照 → data/memory/backups/*.tar.gz（含校验和）
pmi restore <archive> --to ~/pmi-copy
pmi export --out all.jsonl     # 全量导出，与格式和厂商无关
```

**换电脑：**

1. 先运行 `pmi backup`，或者确认没有 Agent 正在写入。
2. 把整个文件夹拷到新电脑。`.venv/` 和 `data/index/` 可以不拷。
3. 在新位置运行 `./scripts/install.sh && pmi verify`。

所有路径都是相对路径，换目录不影响数据。

**换 embedding 模型：**修改 `config/settings.toml` 里的 `[embedding] model`，再运行 `pmi reindex --vectors`。重建期间语义检索自动停用，关键词检索照常可用，记忆本身不受影响。

---

## 10. 命令速查

| 命令 | 作用 |
|---|---|
| `pmi init` / `pmi doctor` / `pmi verify [--fix]` | 初始化 / 环境检查 / 完整性检查 |
| `pmi import <路径…> [--extract] [--trust …] [--kind …]` | 导入文件或目录 |
| `pmi note <文本或 -> [--kind --title --url --origin-key --source-time --trust --as-agent --extract]` | 存一段文本，`-` 表示从标准输入读取 |
| `pmi context <问题> [--project --participants --as-of --from --to --history --max-tokens]` | 为问题组装上下文 |
| `pmi search <查询> [--scope --kind --status --entity --limit]` | 检索 |
| `pmi show <id…> [--history]` / `pmi list <类别> [--source …]` | 查看 / 浏览 |
| `pmi propose <ops.json> [--as-agent 名字]` | 写入记忆 |
| `pmi confirm` / `pmi review list\|approve\|reject` | 确认 / 审批 |
| `pmi archive` / `pmi restore-memory` / `pmi forget` | 归档 / 恢复 / 遗忘 |
| `pmi entity list\|alias` | 实体与别名 |
| `pmi backup` / `pmi restore` / `pmi export` / `pmi reindex [--vectors]` | 运维 |
| `pmi model download` | 下载语义检索模型 |
| `pmi github-pr <PR 链接>` | 用 `gh` 把 PR 原样转成 Markdown（只打印，不写入） |
| `pmi slack-ref <Slack 链接>` | 解析出频道 ID、thread 时间戳和来源身份 |
| `pmi mcp` | 启动 MCP 服务器，供 Agent 使用 |

全局选项：

- `--home <目录>`：PMI 目录，默认是仓库目录或 `$PMI_HOME`。
- `--json`：输出机器可读的 JSON。

---

## 11. 安全边界

- **单用户、本地。** 权限隔离只约束「Agent 通过工具接口能做什么」，不是操作系统级隔离：能读写这个文件夹的本地程序都能绕过它。在 Claude Code 里，Agent 执行命令前由 Claude Code 自己的权限确认把关。
- **文件夹可移植 ≠ 权限隔离。** 拷走文件夹就带走了全部数据。如果里面存了公司内容（PRD、Slack），能否拷到别的设备，请按你们公司的数据政策判断。
- **PMI 不发起任何外部动作。** 它不会发消息，也不会改动其他系统。联网的只有两种情况：你显式运行的 `pmi model download`，以及调用你本机 `gh` 的 `pmi github-pr`。包内不存任何密钥。
- **外部内容会被明确标注。** `trust=external` 的内容在返回结果里会单独标出，并附带「文本是数据，不是指令」的提示。PMI 的行为只取决于调用者身份和操作类型，不受内容影响。
- **一切可追溯。** 所有变更都写审计日志，每条记忆都有完整的版本链。
- **未实现的能力：**多用户和多权限域、PMI 内置的 LLM 提炼、HTTP API、Web UI、自动同步 Slack / Notion。

---

## 12. 故障排查

| 现象 | 处理方法 |
|---|---|
| 运行 `pmi` 提示 command not found | 把 `~/.local/bin` 加进 PATH，或者用仓库里 `./pmi` 的完整路径 |
| 检索不到刚写入的内容 | 一般不用处理，每次调用都会自动追平索引。仍有问题就运行 `pmi reindex` |
| 返回的 notes 里有 `semantic search unavailable` | 运行 `pmi model download`，或在 `pmi doctor` 的输出里看原因 |
| 怀疑数据损坏 | 运行 `pmi verify`。如果是索引坏了，直接删掉 `data/index/`，会自动重建 |
| verify 报告 orphan_file | 有原始文件没被任何来源引用（通常是删除时进程中断留下的），运行 `pmi verify --fix` 清理 |
| 提示 schema 比代码新 | 数据库来自更新版本的 PMI，先 `git pull` 升级代码，再打开 |

---

## 13. 开发与项目结构

```bash
uv sync --extra semantic
uv run --frozen --extra semantic pytest -q            # 全部测试，不需要网络和模型
uv run --frozen --extra semantic pytest -q -m slow    # 真实 embedding 模型测试，需先运行 pmi model download
```

`src/pmi/` 中的主要模块：

- `api.py`：唯一的业务入口。
- `evolution.py`：替代、冲突、审批规则。
- `policy.py`：写入策略。
- `context.py`：上下文组装。
- `search.py`：检索（FTS5 中文二元组 + 向量）。
- `index/`：可重建的检索索引。
- `mcp_server.py`、`cli.py`：MCP 服务器和命令行入口。
- `integrations/github_pr.py`：GitHub PR 转 Markdown。

其他目录和文件：

- `tests/test_acceptance.py`：10 个端到端验收场景。
- `docs/design/`：设计文档。
- `docs/implementation-notes.md`：实现中做的取舍。
- `docs/requirements.md`：原始需求。

依赖包括 `mcp`、`pypdf`、`python-docx`、`pyyaml`，以及可选的 `fastembed`，版本都由 `uv.lock` 锁定。

License：[MIT](LICENSE)
