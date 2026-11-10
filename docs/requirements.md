# Project: Personal Memory Infrastructure (PMI)
## 构建一个可移植、Agent-Native、持续进化的个人记忆基础设施

> 原始需求，2026-11-10。公开版把一处公司内部系统的名称泛化成了「团队记忆系统（Team Memory）」，其余原样保存。设计见 `docs/design/2026-11-10-pmi-design.md`。

### 1. 项目愿景（Vision）

我希望构建一个 **Personal Memory Infrastructure（PMI）**，作为我所有 AI Agent 共享的长期记忆基础设施。

它不只是一个 RAG 知识库，也不只是一个文档搜索工具，而是一个可以持续积累、组织、检索、更新并复用我个人知识与工作经验的长期记忆系统。

核心理念：

**My Memory, Any Agent.**

无论未来我使用 Claude Code、Codex、Cursor、自定义 Slack Agent，还是其他 AI Agent，它们都应该能够访问同一套个人记忆，而不需要我重复提供背景信息。

这个系统应该成为我长期拥有、独立管理、可以迁移的个人数字记忆资产。

### 2. 核心设计原则

请以以下原则作为所有架构和技术决策的优先级依据。

**1. Memory First, RAG Second**

RAG 是检索实现方式之一，而不是系统本身。

系统应围绕记忆的生命周期设计：

Capture → Understand → Organize → Store → Retrieve → Update → Forget

**2. Agent-Native**

记忆系统独立于任何 AI Agent，所有 Agent 通过统一协议进行访问。

优先支持 MCP 和 CLI，未来可以扩展 API 或 SDK。

**3. Local First, Portable First**

系统优先在本地运行，所有核心数据尽可能存储在单个文件夹中。

能够通过复制文件夹完成迁移和备份，不强制依赖云数据库、Docker 或复杂后台服务。

**4. Model Agnostic**

不绑定某个 LLM、Embedding Model 或 Agent Framework。

模型服务应可插拔；更换模型时允许重建派生索引，但不应破坏原始知识和长期记忆。

**5. Source of Truth**

所有提取的记忆都应该尽可能追溯到原始来源。

AI 生成的结论、推断和用户确认的事实必须明确区分，避免未经验证的推断变成永久事实。

**6. Evolving Memory**

系统不仅能够添加新记忆，还需要支持修正、合并、关联、版本更新、失效和遗忘。

不能简单地无限追加 Memory，最终使记忆库成为不可维护的信息垃圾场。

**7. Progressive Complexity**

优先实现最小但完整的记忆系统。

不要在 MVP 阶段引入不必要的微服务、知识图数据库或复杂的多 Agent 工作流。

---

### 3. 系统核心能力

整个系统需要包含五个核心模块。

#### Module A：Memory Capture — 记忆采集

系统应支持从多个来源采集信息。

**主动输入：**

- Markdown、TXT、PDF、DOCX 等文件
- 纯文本、工作笔记和个人说明
- 会议纪要、项目方案和决策记录
- 用户主动添加的重要事实、规则和偏好
- 批量导入目录中的文件

**Agent 输入：**

- AI Agent 在执行任务过程中处理的文档
- Agent 生成的重要分析与结论
- 用户确认过的决策
- Slack 对话及消息线程
- 已完成任务的背景和结果

Agent 应当能够提出需要记住的内容，但默认不能将所有对话和推断无条件写入长期记忆。

**采集机制需要支持：**

- 自动解析和文本规范化
- 内容去重
- 增量更新
- 来源标记
- 时间信息
- 文件版本和内容哈希
- 可控的自动写入策略

#### Module B：Memory Understanding — 记忆理解

原始内容不等于长期记忆。

系统需要将文档和对话中的信息提炼成具有长期价值、可维护的记忆单元。

建议支持以下记忆类型：

| Memory Type | 描述 |
|---|---|
| Episodic Memory | 过去发生的事件、讨论、任务和处理过程 |
| Semantic Memory | 稳定事实、概念、业务知识和背景信息 |
| Project Memory | 项目目标、历史、状态、决策和相关人员 |
| People Memory | 人员角色、协作关系及相关工作上下文 |
| Decision Memory | 决策内容、理由、时间和依据 |
| Procedural Memory | 常见问题的处理方法、SOP、工作流程 |
| Preference Memory | 个人偏好、沟通方式、工作习惯 |

这些类型可以互相关联，不要求每条记忆只能属于一个类别。

每条记忆建议具有：

```json
{
  "memory_id": "uuid",
  "type": "decision",
  "content": "Decision content",
  "entities": ["Project A", "Person B"],
  "source_ids": ["source_001"],
  "created_at": "...",
  "updated_at": "...",
  "valid_from": "...",
  "valid_until": null,
  "status": "active",
  "confidence": 0.95,
  "verification_status": "user_confirmed"
}
```

这是概念性数据结构，具体 Schema 请根据实现需要优化。

**必须区分：**

- Source Content：原始证据
- Extracted Memory：AI 提炼出的记忆
- Verified Memory：经过用户或可信来源确认的记忆
- Inferred Memory：基于信息进行的推断

未经验证的推断不能自动覆盖已确认的事实。

#### Module C：Memory Storage — 记忆存储

系统应同时保留三个逻辑层次：

**Layer 1：Source Store**

保存原始文档、文本、来源、导入时间及版本。

这是最终可以追溯的原始证据。

**Layer 2：Memory Store**

保存结构化长期记忆及其关系。

例如：

- Project A contains Decision B
- Decision B was discussed with Person C
- Decision B was superseded by Decision D
- Task E belongs to Project A

MVP 可以使用 SQLite 关系表实现，不要求引入专门的 Graph Database。

**Layer 3：Retrieval Index**

维护支持检索的派生数据，包括：

- 全文索引
- 向量索引
- 元数据索引
- 实体关联索引（如有必要）

索引应当可以从原始数据和 Memory Store 重新生成。

三个层次逻辑上分离，但可以基于相同的嵌入式数据库或少量本地文件实现。

#### Module D：Memory Retrieval — 记忆检索

系统不能只返回与问题相似的文本片段，还需要针对 Agent 的任务组装相关上下文。

典型检索流程：

```text
Agent Request
    ↓
Query Understanding
    ↓
Entity / Project Identification
    ↓
Hybrid Retrieval
    ├── Keyword Search
    ├── Semantic Search
    ├── Metadata Search
    └── Memory Relationship Lookup
    ↓
Ranking / Filtering
    ↓
Temporal & Conflict Resolution
    ↓
Context Assembly
    ↓
Memory Context + Citations
    ↓
AI Agent
```

返回结果应该包括：

- 相关的长期记忆
- 相关的原始文档片段
- 项目与人员背景
- 决策历史
- 时间有效性
- 来源引用
- 信息可信度或验证状态
- 存在冲突时的说明

**特别重要：**

系统应能区分：

- 当前仍然有效的决策
- 曾经有效但已经被替代的决策
- 尚未确认的讨论
- 没有足够证据的推断

当用户询问"最新状态"时，应优先寻找近期、有效且有来源的信息，而不是简单返回语义相似度最高的历史内容。

#### Module E：Memory Evolution — 记忆进化

这是 PMI 区别于普通 RAG 的关键模块。

当新的信息进入系统时，应该能够判断：

1. 是否属于全新记忆？
2. 是否与已有记忆重复？
3. 是否补充已有事实？
4. 是否修正已有信息？
5. 是否推翻或替代了旧决策？
6. 是否应该关联到已有项目或人员？
7. 是否需要用户确认？
8. 是否不值得长期保存？

系统需要支持：

- Memory Merge
- Memory Update
- Memory Supersession
- Memory Conflict Detection
- Memory Versioning
- Memory Archiving
- Explicit Forgetting

例如：

过去的记忆：

> Project A 计划于 11 月上线。

新的会议记录：

> Project A 上线时间调整至 12 月。

正确行为不是保留两个互不关联的事实，而应该：

- 保留历史计划和其来源
- 将当前目标时间更新为 12 月
- 标记旧计划已被替代
- 记录变化发生的依据和时间
- 后续回答默认使用最新有效信息

MVP 不要求实现完全自动化的复杂 Memory Consolidation Engine，但必须在数据模型和接口层面支持上述操作。

---

### 4. 核心应用：Slack AI Agent

第一个主要应用场景是让 AI Agent 帮助我处理 Slack 消息。

PMI 本身不应该与 Slack 强绑定。

Slack 应该只是连接 PMI 的一个外部应用。

推荐架构：

```text
Slack
  ↓
Slack Connector / Agent
  ↓
Context Extraction
  ↓
Personal Memory Infrastructure
  ├── Project Memory
  ├── People Memory
  ├── Decision Memory
  ├── Episodic Memory
  └── Source Documents
  ↓
Relevant Context + Citations
  ↓
LLM / Agent Reasoning
  ↓
Suggested Reply / Action
  ↓
User Review
```

#### 示例工作流程

假设 Slack 中有人问：

"关于 Project A，我们之前为什么决定不采用方案 B？"

Agent 应当：

1. 理解当前 Slack Thread。
2. 识别 Project A 和方案 B。
3. 通过 PMI 检索相关历史讨论。
4. 找到此前的决策及其依据。
5. 找到相关的会议纪要和文档。
6. 判断是否存在后续变更。
7. 生成基于事实的回答。
8. 附上可追溯的来源。
9. 在用户授权后发送回复。

对于缺少证据的问题，应明确指出信息不足，而不是编造历史背景。

#### Slack 数据回写

当 Slack 中出现新的重要信息时，Agent 可以调用 PMI 提出记忆写入建议。

例如：

- 确认了新的项目决策。
- 更改了某项计划。
- 讨论出新的 SOP。
- 明确了某个项目的负责人。

对于有较高影响的记忆变更，应支持用户确认。

系统也应防止 Slack 消息中的恶意指令通过检索或记忆提取影响 Agent 的工具调用行为。

---

### 5. Agent Integration Layer

PMI 需要提供统一、稳定、尽量与框架无关的访问方式。

优先支持：

1. MCP Server：主要 Agent 接口
2. CLI：脚本、调试及其他 Agent 访问
3. Python API：程序化集成
4. HTTP API：后续按需扩展

建议 MCP 暴露以下工具：

```text
memory_search
memory_get
memory_get_context
memory_add_source
memory_propose
memory_commit
memory_update
memory_link
memory_history
memory_list
memory_archive
```

请重新评估这些接口的颗粒度，避免过多重复或难以理解的工具。

#### Context API

重点设计一个高层接口：

```python
memory.get_context(
    query="Why did we reject solution B?",
    project="Project A",
    participants=["Person C"],
    time_range=None,
    max_tokens=4000
)
```

该接口不只是搜索文档，而是负责组织适合 Agent 使用的上下文。

返回：

```json
{
  "query": "Why did we reject solution B?",
  "summary": "Relevant historical context",
  "memories": [],
  "sources": [],
  "related_decisions": [],
  "conflicts": [],
  "as_of": "...",
  "insufficient_evidence": false
}
```

具体字段由实现者设计。

对于一个新的 AI Agent，我希望它只需要连接 MCP Server，就能够快速理解如何搜索和使用我的个人记忆。

不要要求不同 Agent 分别实现一套复杂的知识处理逻辑。

---

### 6. Portable Memory Package

这是本项目的关键非功能需求。

**整个 PMI 应当尽可能成为一个 Self-Contained Memory Package。**

建议目录：

```text
personal-memory/
│
├── sources/
│   ├── documents/
│   ├── notes/
│   └── conversations/
│
├── memory/
│   ├── memory.db
│   └── backups/
│
├── indexes/
│   ├── fulltext/
│   └── vector/
│
├── runtime/
│   ├── mcp_server/
│   └── cli/
│
├── config/
│   └── settings.yaml
│
├── scripts/
│   ├── import.py
│   ├── rebuild.py
│   └── migrate.py
│
├── tests/
│
└── README.md
```

目录仅作为参考，可以优化。

核心要求：

**A. Copy and Run**

复制整个文件夹后，应该能够通过简单命令启动或访问 PMI。

**B. No External Infrastructure**

默认不依赖 Redis、PostgreSQL、独立向量数据库服务器或云托管基础设施。

**C. Minimal Configuration**

只需要极少量配置，路径尽可能使用相对路径。

**D. Self-Contained Data**

原始数据、记忆数据和索引都位于文件夹内。

模型文件和密钥可根据体积与安全需求独立存储，但必须明确依赖。

**E. Rebuildable Index**

即使向量索引损坏或更换 Embedding 模型，也能够重建索引，而不丢失记忆。

**F. Portable Across Agents**

不同 Agent 通过 MCP 或 CLI 访问同一套记忆。

**G. Predictable Migration**

需要提供迁移检查与验证机制，说明哪些文件是必需的、哪些是可以重建的。

**H. Controlled Concurrency**

多个 Agent 同时使用时，应支持可靠的并发读取和安全写入，避免数据损坏。

---

### 7. 团队记忆系统（Team Memory） 作为参考架构

该项目可以借鉴 团队记忆系统（Team Memory） 的设计思想。

请重点研究：

- 从原始信息生成长期记忆的机制
- Knowledge 与 Memory 的分离
- 项目、人员、事件和决策之间的关联
- 记忆合并与更新
- 时间维度及知识有效性
- Context Retrieval
- Memory Consolidation
- Agent 使用记忆的工作流

如果可以访问 团队记忆系统（Team Memory） 的代码、内部文档或设计资料，请先进行真实调研。

如果无法访问其内部实现，则只参考已知或公开的信息，并清晰标注假设。

不需要复刻其完整架构。

本项目的方向是：

**将 Team Memory 的核心理念转化为轻量化、便携式、个人拥有的 Memory Infrastructure。**

---

### 8. 安全、隐私与记忆治理

PMI 可能包含敏感工作资料，因此需考虑：

- 默认本地存储
- 只读与写入权限分离
- 记忆修改操作留痕
- 原始数据与 AI 推断区分
- 不可信内容隔离
- 数据删除与清理
- 备份和恢复
- 凭据不能以明文保存在可共享的知识包内
- 来自 Slack 的内容遵守组织权限和数据保留政策

需要特别说明：单个文件夹的可移植性不等于权限隔离。

如果多个 Agent 或用户拥有不同的数据访问权限，系统必须能够限制其检索范围，或者明确说明单用户本地 MVP 的安全边界。

任何自动处理 Slack 的 Agent 默认只能生成草稿。发送消息、修改工作系统或写入高影响记忆，需要额外授权。

---

### 9. 技术选型

请重点比较轻量级实现方案。

候选技术：

- Python
- SQLite
- SQLite FTS5
- sqlite-vec / LanceDB
- MCP Python SDK
- 本地 Embedding 模型
- 可选 LLM API
- 本地文件系统

不要为了满足清单而强制使用所有技术。

请分析：

1. 是否可以将结构化 Memory 和元数据放在同一个 SQLite 数据库？
2. 是否需要独立向量数据库？
3. 是否可以减少依赖，仅通过 SQLite + FTS5 完成第一版？
4. 本地 Embedding 与云端 Embedding 如何切换？
5. 更换 Embedding 模型后如何安全迁移？
6. 是否需要一个持续运行的后台服务？
7. 如何支持多 Agent 同时读取？
8. 如何保障增量更新的原子性？
9. 如何在无需 LLM 的情况下完成基本查询？
10. 如何降低未来五年的维护成本？

请明确给出推荐方案，并解释为什么它比其他方案更适合个人长期使用。

---

### 10. MVP 定义

第一阶段的目标不是打造完整的个人 AI 操作系统，而是建立一个可靠的个人长期记忆核心。

必须实现：

**Memory Storage**
- 原始文档持久化
- SQLite 或等效的轻量存储
- 结构化记忆存储
- 来源关联
- 时间与版本信息

**Memory Capture**
- Markdown、TXT、PDF、DOCX 导入
- 纯文本写入
- 批量导入
- 去重与增量更新

**Memory Retrieval**
- 关键词检索
- 语义检索（在依赖可用时）
- Metadata Filtering
- 结果引用
- 基础 Context Assembly

**Memory Evolution**
- 新增记忆
- 更新记忆
- 历史版本
- Supersession
- 冲突标记
- 基本归档能力

**Agent Integration**
- MCP Server
- CLI
- 标准化的结构化返回格式

**Portability**
- 自包含文件夹
- 简单启动
- 索引重建
- 备份恢复
- 无复杂服务依赖

**Quality**
- 自动化测试
- 使用示例
- 迁移测试
- 数据完整性验证
- 清晰的 README

MVP 暂时不要求：

- 完整知识图谱引擎
- 多用户协作
- 独立 Web UI
- 复杂的主动 Agent 调度
- 全自动 Slack 同步
- 自动执行 Slack 回复
- 复杂的 Memory Importance Scoring
- 大规模分布式检索

这些可以作为后续扩展。

---

### 11. 验收场景

请设计可重复运行的测试，证明系统满足以下要求。

**Test 1：记忆生成**

导入一个项目会议纪要，能够保存原文，并创建与项目、人员及决策相关的记忆。

**Test 2：上下文检索**

询问该项目为什么做出某个决策，能够返回相关依据及来源。

**Test 3：记忆更新**

导入新的决策记录，能够识别并处理旧决策被替代的情况。

**Test 4：历史追溯**

查询旧决策时，仍然能够找到历史内容及其有效时间。

**Test 5：Agent Access**

一个新的 MCP Client 能够发现 PMI 的工具并读取记忆。

**Test 6：Portability**

将整个文件夹复制到另一个目录，更新必要的运行环境配置后，能够正常读取和查询全部数据。

**Test 7：Index Recovery**

删除可重建的检索索引后，能够依据源数据恢复检索能力。

**Test 8：Safe Write**

未经授权的 Agent 无法直接删除或覆盖关键记忆。

**Test 9：Conflicting Evidence**

当两个来源存在冲突时，系统不会静默覆盖已确认信息，而是保留来源并返回冲突状态。

**Test 10：Slack Context Simulation**

输入一条模拟 Slack 消息，PMI 能够返回相关的项目历史、决策与文档证据，供外部 Agent 生成回复。

---

### 12. 预期交付方式

请按照以下阶段推进。

#### Phase 1：Architecture Research

首先分析需求，并检查当前工作区是否存在 团队记忆系统（Team Memory） 的参考实现。

输出：

- 推荐系统架构
- 核心组件及职责
- Memory Schema
- Source 与 Memory 的关系
- Memory Lifecycle
- Retrieval Pipeline
- MCP API 设计
- 可移植文件结构
- 技术选型及取舍
- 关键风险与限制

#### Phase 2：Implementation Plan

输出可执行的开发计划：

- MVP 模块划分
- 代码结构
- 依赖管理
- 数据库 Schema
- API Contract
- 测试策略
- 里程碑
- 验收标准

#### Phase 3：MVP Implementation

实现实际可运行的软件，包括：

- 完整源代码
- 数据库初始化与迁移机制
- 文件导入及解析
- 记忆存储与更新
- 检索引擎
- MCP Server
- CLI
- 测试用例
- 示例数据
- README 与使用教程

不要只生成架构设计或伪代码。

#### Phase 4：Validation

运行自动化测试，并验证：

- 数据导入
- 记忆查询
- 历史更新
- MCP 调用
- 文件夹迁移
- 索引恢复
- 权限边界
- 数据完整性

对于未实现的功能，必须明确说明，不要将规划能力描述成已实现能力。

---

### 13. 最终成功标准

最终我希望获得一个可以长期使用的个人记忆基础设施。

我可以：

1. 将看过的文档和重要信息交给 PMI。
2. 将个人经验、决策和工作知识沉淀成长期记忆。
3. 让不同 AI Agent 读取同一套知识。
4. 让 AI Agent 在处理 Slack 时理解我的历史工作上下文。
5. 持续添加、更新和修正记忆。
6. 随时追溯一个结论来自哪里。
7. 在更换 AI 模型、Agent 或电脑后继续使用原有记忆。
8. 通过复制文件夹保留和迁移自己的知识资产。

**最终目标不是构建一个更好的文档搜索系统，而是建立一个真正属于我自己、能够持续进化，并服务于所有 AI Agent 的长期记忆基础设施。**

请以技术架构师和资深软件工程师的身份执行这个项目。

设计时优先保证正确性、数据完整性、可移植性和长期维护性，而不是追求功能数量。

**开始执行时，先完成架构与技术方案设计，再根据方案开发可运行的 MVP。**
