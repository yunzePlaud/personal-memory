# PMI Web UI 设计

- 日期：2026-11-11
- 状态：草案，待用户确认
- 标注约定：**【用户决定】** 是用户在讨论中拍板的；**【Claude 的决定】** 是我替用户做的默认选择，用户审阅时可以推翻；**【留给实现者】** 是实现时自行决定、不影响产出的细节。

## 1. 目的与背景

PMI 目前只能通过命令行和 MCP 使用。用户想要一个**只在本机打开的网页**，用来：

1. **浏览记忆并直接管理**：看现在存了哪些记忆，在页面上确认、归档、恢复、永久删除记忆，审批 agent 提交的待审改动。
2. **看磁盘占用**：PMI 各部分各占多少空间。
3. **监控自己的 agent 在做什么**：用户在 opencode 里跑着一个常驻 bot（slack-dm-responder，每 5 分钟处理一轮私信），它把每轮处理过程写成人能读的日志，例如 `~/.slack-dm-responder/logs/session-2026-11-11.log`：

   ```text
   [DM] 收到 @某人 的消息：「……」，开始尝试处理
   [DM] @某人：已定位相关 App 代码，尝试自动回复
   [DM] @某人：自动回复完成
   [DM] 回复内容：……
   [Workflow] 本轮完成：扫描 8 条，自动回复 3 条，跳过 5 条
   ```

   现在只能在终端 `tail`。用户想在网页上看这份日志的原文，并一眼看出它是否还在运行。

PMI 是一个公开仓库，面向其他开发者；slack-dm-responder 是用户个人的工具。所以**日志查看是 PMI 的通用能力**（配置要看哪些日志文件），slack-dm-responder 只是第一个配置项。

## 2. 目标、非目标、反目标

**目标**

- `pmi ui` 一条命令启动，浏览器打开就能用。
- 第 1 节列出的三件事全部在网页上完成。

**非目标（第一版不做）**

- 编辑记忆的文字内容（继续用命令行 / agent 的 `update` 操作）。【Claude 的决定】
- 管理原文（浏览、删除 source）。原文只在记忆详情里以引用出现。【Claude 的决定】
- 启动 / 停止被监控的 agent。【Claude 的决定】
- 把日志解析成结构化数据；只显示原文。【用户决定】
- 远程访问、多用户。【Claude 的决定】
- 实时推送；用轮询。【Claude 的决定】

**反目标（即使能用，出现这些也算失败）**

- PMI 变成常驻服务或后台进程（违背 PMI "没有常驻服务"的原则）。
- 引入新的运行时依赖，或需要 npm 之类的前端构建链。
- 浏览器里的**其他网页**能读取或修改记忆（跨站请求、DNS rebinding）。
- 网页让 agent 获得比现在更大的权限。现在 agent 通过 MCP 不能确认、删除、审批；网页不能成为绕过这条规则的新通道（见 5.3）。
- 页面能读取配置以外的文件（路径穿越）。
- 把 slack-dm-responder 写死在 PMI 代码里。

## 3. 用户已经做的决定

- **布局 B：顶部标签 + 概览首页。**【用户决定】标签依次为 `Overview` / `Memories` / `Review` / `Logs` / `Storage`。`Memories` 标签采用"左边列表、右边详情"（讨论中的布局 A 的样子）。
- **界面文字用英文**；记忆内容、日志原文照原样显示（会有中文）。【用户决定】
- **记忆操作**：确认、归档、恢复、永久删除；待审改动：批准、拒绝。【用户决定】
- **日志显示配置文件的原文**，不要求 slack-dm-responder 做任何改动。【用户决定】
- **日志查看做成通用能力**，靠配置指定日志文件。【用户决定（认可了这个方向）】

## 4. 页面规格

### 4.1 顶栏

- 左边是标签，`Review` 标签上显示待审数量（例如 `Review 3`），为 0 时不显示数字。
- 每个标签对应一个地址（例如 `#/memories`、`#/memories/mem_…`），刷新页面停留在原处，可以收藏。【Claude 的决定】

### 4.2 Overview（首页）

一行四张卡片，下面两块列表：

| 区域 | 内容 |
|---|---|
| 卡片 Memories | 生效（active）记忆数；小字显示已归档、已替代、有争议的数量 |
| 卡片 Review | 待审改动数；点击跳到 `Review` |
| 卡片 Disk | PMI 数据总占用；小字显示最大的两项（例如 `model 240 MB · memory 1.2 MB`）；点击跳到 `Storage` |
| 卡片（每个配置的日志一张） | 日志名 + 状态灯（见 4.5 的状态规则），例如 `slack-dm-responder · Active · last output 2 min ago`；没有配置日志时这张卡片不出现 |
| Recent memories | 最近更新的 5 条记忆（类型标签 + 标题），点击进入详情 |
| Latest log lines | 第一个配置日志的最后 5 行；没有配置日志时显示"如何配置日志"的一句提示 |

首页每 10 秒自动刷新一次。【Claude 的决定】

### 4.3 Memories

**左栏：列表**

- 顶部搜索框：有输入时用 PMI 现有检索（`Memory.search`，scope=memories），按相关度排序，中英文都可以搜；没有输入时按更新时间倒序列出。
- 筛选：状态（默认 `active`，可选 `active` / `superseded` / `disputed` / `archived` / 全部）、类型（`decision` / `semantic` / `person` / `procedure` / …）、槽位（slot）。
- 每行：类型标签、标题、状态（非 active 时显示）、更新时间。
- 分页：每页 50 条，底部 "Load more"。

**右栏：详情**（选中一条后显示）

- 标题、类型、状态、核实程度（`user_confirmed` / `source_backed` / `inferred`）、槽位、有效期（`valid_from` / `valid_until`）、写入者（`memories.created_by`，例如 `agent:claude-code`）、创建 / 更新时间。
- 完整内容（双语记忆的 `EN:` 段照原样换行显示）。
- 引用：每条引用的原句、出处标题、出处链接（Notion / Linear / Slack / PR 的 URL，可点击，在新标签打开）、原文版本、该版本是否最新。
- 关联实体（项目、人、系统）。
- 历史：被哪条记忆替代 / 替代了哪些记忆，可点击跳转。
- 操作按钮（按状态显示）：

| 按钮 | 何时显示 | 行为 |
|---|---|---|
| `Confirm` | 状态为 active 或 superseded，且尚未 user_confirmed（与 PMI 现有的确认规则一致） | 标为用户已确认（`Memory.confirm`） |
| `Resolve in Review` | 状态为 disputed | 跳到 `Review` 里与它相关的待审改动。PMI 规定有争议的记忆只能通过批准 / 拒绝待审改动来解决，不能直接确认【Claude 的决定】 |
| `Archive` | 状态不是 archived | 归档（`archive` 操作）；弹出一句确认 |
| `Restore` | 状态为 archived | 恢复到归档前的状态（`restore` 操作，即 `pmi restore-memory`）；若该槽位已被另一条生效记忆占用，PMI 会拒绝，页面显示这条错误 |
| `Delete permanently` | 任何状态 | 弹窗说明"不可恢复"，要求输入原因（预填 `deleted in web UI`）并输入 `DELETE` 才能提交（`Memory.forget_memory`） |

操作成功后刷新详情和列表；失败时在页面上显示 PMI 返回的错误信息（例如策略拒绝）。

### 4.4 Review

- 列出所有 pending 的待审改动，最早的在前。
- 每条显示：提交者（例如 `agent:claude-code`）、提交时间、提交理由、为什么需要审批（PMI 记录的原因，例如与已确认的结论冲突）、以及**批准后会发生什么**——直接用 PMI 现有的 `proposal_preview`：新内容全文、会被替代 / 修改的现有记忆全文、引用。新旧内容并排或上下对照显示。【留给实现者：并排还是上下】
- 按钮 `Approve`、`Reject`（拒绝要求填写原因，预填 `rejected in web UI`）。
- 处理完的条目从列表消失；顶栏数字随之更新。

### 4.5 Logs

- 每个配置的日志一个子标签（只有一个时不显示子标签）。
- 文件选择：配置的路径支持通配（例如 `session-*.log`），匹配到的文件按文件名倒序排列，默认显示最新的一个，下拉框可切换到其他日期。
- 显示文件末尾 1000 行原文，等宽字体，自动滚到最底部；用户向上滚动时暂停自动滚动，回到底部后恢复。【Claude 的决定】
- 每 3 秒增量刷新，只取新增内容。文件变短（被截断或轮转）时整份重读。
- 正在看最新的文件时出现了更新的文件（例如过了午夜，bot 开始写新一天的日志），自动切到新文件；在看历史文件时不切。【Claude 的决定】
- **"Hide idle lines" 开关**：配置了 `hide_pattern` 时显示，打开后隐藏匹配该文本的行（对 slack-dm-responder 配置为 `扫描 0 条`，用来隐藏空转的轮次汇总）。没配置就不显示这个开关。
- **状态规则**（Overview 卡片和 Logs 页共用）：看日志文件的最后修改时间。
  - 距今不超过 `stale_minutes`（默认 10）：绿色 `Active`，并显示 `last output N min ago`。
  - 超过：黄色 `No output for N min`。
  - 没有任何匹配文件：灰色 `No log file`。
  - 只看文件修改时间，不检查进程。这对任何会定期写日志的 agent 都适用。【Claude 的决定】
- 文件按 UTF-8 读取，无法解码的字节替换显示，不报错。

### 4.6 Storage

表格列出 PMI 数据各部分的大小和路径，最后一行是总计：

| 项目 | 路径 |
|---|---|
| Memory database | `data/memory/pmi.db`（连同 `-wal`、`-shm`） |
| Sources | `data/sources/` |
| Search index | `data/index/` |
| Embedding model | `data/models/` |
| Backups | `data/memory/backups/` |

外加一行 PMI home 的完整路径。只读，不提供清理操作。【Claude 的决定】

## 5. 技术方案

### 5.1 启动方式【Claude 的决定】

```bash
pmi ui                 # 前台运行，打开浏览器；Ctrl-C 停止
pmi ui --port 9000     # 指定端口（默认 8765）
pmi ui --no-open       # 不自动打开浏览器，只在终端打印链接
```

- 只监听 `127.0.0.1`，不提供改成其他地址的选项。
- 端口被占用时直接报错退出，提示用 `--port` 换一个；不自动换端口（否则收藏的地址会失效）。
- 启动后在终端打印带口令的完整链接（见 5.3），并用系统默认浏览器打开。
- 与 `pmi mcp`、命令行、其他 agent 同时运行没有问题：PMI 的 SQLite 已经是 WAL 模式，有并发测试覆盖。

### 5.2 实现形态【Claude 的决定】

- 服务端只用 Python 标准库：`http.server.ThreadingHTTPServer` + JSON 接口，挂在 `/api/` 下。
- 前端是静态文件（一个 HTML、一个 JS、一个 CSS），放在 `src/pmi/ui/static/`，随 PMI 一起分发；原生 JS，不用框架，不从 CDN 加载任何东西（离线可用）。
- 所有读写都通过现有的 `pmi.api.Memory`，操作者身份为 `Actor.owner("ui")`，因此操作记录里显示为 `owner:ui`，能和命令行（`owner:cli`）区分。
- 浅色 / 深色跟随系统设置。

### 5.3 安全【Claude 的决定】

页面会显示同事的私信原文和你的全部记忆，并且能永久删除数据，所以：

1. **口令**：第一次运行 `pmi ui` 时生成一个随机口令（`secrets.token_urlsafe(32)`），保存到 PMI home 下的 `data/ui-token`（文件权限 600），以后每次启动复用；`pmi ui --new-token` 重新生成，旧口令立即失效。这样收藏的页面在新标签页里、重启 `pmi ui` 之后都还能直接打开（4.1 的承诺）。
   - 打开的链接是 `http://127.0.0.1:8765/#token=<口令>`，口令放在 `#` 后面，不会出现在服务器请求日志或 Referer 里。前端把它存进本站点的 `localStorage`（别的网站读不到），之后每个 `/api/` 请求都带 `X-PMI-Token` 请求头。缺失或不匹配返回 401。
   - 静态页面本身不含数据，可以不带口令加载。口令缺失或失效时，页面提示"请用 `pmi ui` 打印的链接重新打开"，并记下当时的地址；用新链接打开后回到这个地址，而不是首页。
   - `data/ui-token` 是本机的密钥，`pmi backup` / `pmi export` 不包含它，迁移到别的机器后会重新生成。
   - 口令文件放在磁盘上不扩大风险：能读这个文件的程序，本来就能直接运行 `pmi` 命令（见第 4 点）。要防的是浏览器里的其他网页，它们读不到本机文件，也读不到本站点的 `localStorage`。【Claude 的决定】
2. **Host 检查**：请求的 `Host` 必须是 `127.0.0.1:<端口>` 或 `localhost:<端口>`，否则返回 403。这是为了防 DNS rebinding。
3. **不返回任何 CORS 响应头**。写操作只接受 `POST` 且 `Content-Type: application/json`；加上自定义请求头，其他网站的页面无法发起这些请求。
4. **与 PMI 信任模型的关系**：网页上的操作等同于用户在命令行执行，身份是 owner。agent 通过 MCP 仍然不能确认、删除、审批。能运行 shell 的 agent 本来就能执行 `pmi` 命令，网页没有扩大这条边界；口令只在启动它的那个终端里出现。
5. **日志文件只能读配置里的**：接口只接受"日志名 + 文件名"，文件名必须在该日志通配符当前的匹配结果里，否则返回 404。不接受任何路径参数，所以不存在路径穿越。

### 5.4 日志配置

在 `config/settings.toml` 里新增 `[ui]` 下的日志列表：

```toml
[[ui.logs]]
name = "slack-dm-responder"                          # 显示名，同一份配置里唯一
path = "~/.slack-dm-responder/logs/session-*.log"    # 支持 ~ 和通配符 * ?
stale_minutes = 10                                   # 可选，默认 10
hide_pattern = "扫描 0 条"                            # 可选；配置后显示 "Hide idle lines" 开关
```

- **`[ui]` 段由 `pmi ui` 自己读取和校验，写错了只影响 `pmi ui`。**【Claude 的决定】现有的 `load_settings` 在每个命令（包括给 agent 用的 `pmi mcp`）启动时都会运行，遇到未知的段会报错退出。所以它只需要改成跳过 `[ui]` 段，不去解析它。这样日志配置写错不会让 agent 的 MCP 服务器起不来。
- `pmi ui` 逐项校验：`name` 必填且唯一、`path` 必填、`stale_minutes` 为正整数、`hide_pattern` 为字符串。出错时启动失败，并给出清楚的报错，风格和现有配置错误一致。`pmi doctor` 也报告 `[ui]` 配置的问题。
- `settings.example.toml` 增加一个注释掉的示例。
- 没有配置任何日志时，Logs 标签显示配置方法，而不是报错。

### 5.5 接口（服务端）

【留给实现者：路径命名可以调整，但能力要齐全】

| 方法和路径 | 作用 | 基于 |
|---|---|---|
| `GET /api/overview` | 首页所需的全部数据 | 计数查询、`list("memories")`、日志状态、存储统计 |
| `GET /api/memories?q=&status=&kind=&slot=&limit=&offset=` | 列表 / 搜索 | `Memory.list` / `Memory.search` |
| `GET /api/memories/<id>` | 详情（含历史） | `Memory.get([id], include_history=True)` |
| `POST /api/memories/<id>/confirm` | 确认 | `Memory.confirm` |
| `POST /api/memories/<id>/archive` | 归档 | `propose([{"op":"archive"}])` |
| `POST /api/memories/<id>/restore` | 恢复 | 与 `pmi restore-memory` 相同的操作 |
| `POST /api/memories/<id>/forget`，body `{"reason": "..."}` | 永久删除 | `Memory.forget_memory` |
| `GET /api/proposals` | 待审列表（含预览） | `Memory.list("proposals")` |
| `POST /api/proposals/<id>/approve` | 批准 | `Memory.approve` |
| `POST /api/proposals/<id>/reject`，body `{"reason": "..."}` | 拒绝 | `Memory.reject` |
| `GET /api/logs` | 配置的日志、各自匹配的文件和状态 | 配置 + 文件系统 |
| `GET /api/logs/<name>/<file>?since=<字节偏移>` | 读日志：不带 `since` 返回末尾 1000 行；带 `since` 返回新增内容；同时返回新的偏移和修改时间 | 文件系统 |
| `GET /api/storage` | 各部分大小 | 文件系统 |

需要对现有 API 做的小扩展：

- `Memory.list("memories")` 增加 `offset`（分页）和 `slot` 筛选。
- 首页的各状态计数，如果现有 API 没有现成的，就新增一个只读的计数方法。

错误统一返回 `{"error": {"code": ..., "message": ...}}`：PMI 的校验错误 → 400，策略拒绝 → 403，找不到 → 404，口令错误 → 401。

## 6. 测试

- 用 pytest 测服务端（在测试里启动服务器、发真实 HTTP 请求），覆盖：
  - 口令：缺失、错误 → 401；正确 → 200。错误的 `Host` → 403。口令在重启后复用；`--new-token` 之后旧口令 → 401；`data/ui-token` 权限为 600，且不出现在 `pmi backup` / `pmi export` 的结果里。
  - `[ui]` 配置写错时 `pmi ui` 报错，而 `pmi mcp`、`pmi context` 等其他命令照常运行。
  - 按钮规则：disputed 的记忆不能确认，详情返回的数据里带有指向其待审改动的信息。
  - 各读接口返回正确数据。
  - 各写操作的效果：确认后变 `user_confirmed`；归档后变 `archived`；恢复后回到原状态；永久删除后查不到；批准 / 拒绝后待审列表变化；操作记录里的操作者是 `owner:ui`。
  - 日志：通配匹配和倒序；不在匹配结果里的文件名 → 404；`../` 之类的文件名被拒绝；`since` 增量读取；文件变短后整份重读；状态规则（Active / No output / No log file）；非 UTF-8 字节不报错。
  - 存储统计数值正确。
  - `[[ui.logs]]` 配置的解析和各种错误情况。
- 前端不做浏览器自动化测试，按一份手测清单验收（每个标签、每个按钮、深浅色、口令丢失提示）。【Claude 的决定】
- 现有测试全部保持通过。

## 7. 文档

- README 新增一节 "Web UI"：启动命令、日志配置示例、安全说明（只监听本机、口令、网页操作等同命令行）。
- README 的命令参考加上 `pmi ui`。
- `settings.example.toml` 加 `[[ui.logs]]` 的注释示例。

## 8. 留给实现者

- 数据库连接是每个请求新开还是按线程复用。
- 视觉样式细节（颜色、间距、字体）；整体按讨论中选定的布局 B。
- 待审改动新旧内容并排还是上下对照。
- 接口路径的具体命名。

## 9. Claude 替用户做的决定（请审阅）

1. 第一版不做：编辑记忆文字、管理原文、启动 / 停止 agent、远程访问。
2. 永久删除要求输入 `DELETE` 并填写原因；归档只需一次确认。
3. 只监听 `127.0.0.1`；默认端口 8765；端口被占用就报错，不自动换端口。
4. 口令第一次启动时生成，保存在 `data/ui-token`（权限 600）并一直复用，收藏的页面在重启后照样能打开；`pmi ui --new-token` 可以换新口令；备份和导出不包含它。
5. 运行状态只看日志文件的最后修改时间，默认超过 10 分钟没输出就显示黄色。
6. 日志显示末尾 1000 行，每 3 秒增量刷新；首页每 10 秒刷新。
7. 网页操作在操作记录里记为 `owner:ui`。
8. 只用 Python 标准库和原生 JS，不加任何依赖，不用 CDN。
9. 前端不做浏览器自动化测试，用手测清单验收。
10. `Confirm` 按钮沿用 PMI 现有规则：active 和 superseded 的记忆可以确认；disputed 的记忆不能直接确认，改为显示 `Resolve in Review`，跳到对应的待审改动。
11. `[ui]` 配置由 `pmi ui` 单独读取和校验，写错了不影响 `pmi mcp` 和其他命令。
12. 看最新日志时，过了午夜出现新一天的文件会自动切过去。
