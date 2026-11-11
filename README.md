# PMI · Personal Memory Infrastructure

**My Memory, Any Agent.** PMI is a long-term memory store that runs on your own machine. Give it PRDs, meeting notes, Notion pages, Linear tickets, GitHub PRs and Slack discussions: it keeps the original text and distills it into knowledge that always cites its source. Claude Code, Codex, Cursor and any other AI agent read and write the same memory through MCP (or the command line).

- **Sourced**: every memory carries a verbatim quote from the original text. Quotes are checked on write; a quote that does not match is rejected.
- **Evolving**: a newer decision automatically supersedes an older one, and the old one becomes history with a validity period. When new content conflicts with a conclusion you confirmed, PMI does not overwrite it silently; the change goes to review.
- **Clear status**: search results say which category each item is in: current, superseded, disputed, open question, or insufficient evidence.
- **No services**: no database server, no Docker, no background process. One SQLite file plus a folder of originals is all the data; copy the folder to move it.
- **Model-agnostic**: PMI never calls an LLM itself. The agent you are already using does the reading and understanding. An optional local multilingual embedding model is used only for semantic search.

PMI handles Chinese and English text, including questions asked in one language about content written in the other (see [Questions in Chinese and English](#questions-in-chinese-and-english)). The bundled sample data and the instructions in the three Claude Code skills are in Chinese; the memories the skills write are in both languages.

---

## Contents

1. [Quick start (5 minutes)](#1-quick-start-5-minutes)
2. [Installation](#2-installation)
3. [Connecting AI agents](#3-connecting-ai-agents)
4. [Adding knowledge](#4-adding-knowledge)
5. [Updating knowledge](#5-updating-knowledge)
6. [Deleting knowledge](#6-deleting-knowledge)
7. [Querying knowledge](#7-querying-knowledge)
8. [Web UI](#8-web-ui)
9. [Write policy and trust](#9-write-policy-and-trust)
10. [Data, backup and migration](#10-data-backup-and-migration)
11. [Command reference](#11-command-reference)
12. [Security boundary](#12-security-boundary)
13. [Troubleshooting](#13-troubleshooting)
14. [Development and project layout](#14-development-and-project-layout)

---

## 1. Quick start (5 minutes)

Requirements: macOS or Linux, `git`, and [uv](https://docs.astral.sh/uv/). uv provides Python 3.12 automatically.

```bash
# 0. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 1. Clone and install: dependencies, data folder, `pmi` linked into ~/.local/bin, Claude Code skills
git clone https://github.com/yunzePlaud/personal-memory.git ~/personal-memory
cd ~/personal-memory
./scripts/install.sh            # add --with-model to also download the semantic search model (~220 MB)

# 2. Import the sample data and ask questions (the samples are in Chinese)
pmi import examples/project-a --extract
pmi context "为什么 Project A 不采用方案 B"                    # Why didn't Project A choose option B?
pmi context "Project A 现在什么时候上线"                       # When does Project A launch now?
pmi context "Project A 什么时候上线" --as-of 2026-10-15        # The same question, as of an earlier day

# 3. Connect Claude Code (available in all projects)
claude mcp add --scope user pmi -- ~/personal-memory/pmi mcp
```

Restart Claude Code and ask: "Why didn't Project A choose option B?" The agent calls `memory_get_context` first and answers with sources.

> The data in `examples/` is fictional. To start over, delete the `data/` folder and run `pmi init`.

---

## 2. Installation

### What `scripts/install.sh` does

| Step | What happens |
|---|---|
| 1 | `uv sync --frozen --extra semantic`: installs the dependencies pinned in `uv.lock`, including fastembed for local semantic search |
| 2 | `pmi init`: creates the `data/` folder for your memory data (already in `.gitignore`) |
| 3 | Links `pmi` into `~/.local/bin/pmi`. If that folder is not on your PATH, the script tells you how to add it |
| 4 | Links the 3 skills under `integrations/claude-code/skills/` into `~/.claude/skills/` |
| Optional | `--with-model`: downloads the local multilingual embedding model `paraphrase-multilingual-MiniLM-L12-v2` (~220 MB) |

Options:

- `--no-skills`: do not install the skills.
- `--no-semantic`: keyword search only, fewer dependencies. Afterwards add `export PMI_NO_SEMANTIC=1` to your shell profile, and pass `-e PMI_NO_SEMANTIC=1` to `claude mcp add`; otherwise the first `pmi` run installs the semantic search dependencies anyway.
- `--with-model`: also download the semantic search model.
- `PMI_BIN_DIR` / `PMI_SKILLS_DIR`: environment variables that change where the links go.

The script is safe to run again. If a real file or folder already exists where a link would go, the script leaves it untouched.

### Manual installation

```bash
uv sync --frozen --extra semantic
./pmi init
./pmi doctor          # checks Python, SQLite, FTS5, the model and folder permissions
./pmi model download  # optional: semantic search model
```

`./pmi` is a launcher script: it sets `PMI_HOME` to the repository folder and runs PMI with uv. It also finds the repository when called through a symlink such as `~/.local/bin/pmi`. Every command accepts `--json`.

### External tools (only when needed)

| Purpose | Requirement |
|---|---|
| Importing from Notion / Linear / Slack | Connect the matching connector in Claude (claude.ai → Settings → Connectors) |
| Importing GitHub PRs (`/pmi-linear`, `pmi github-pr`) | Install the [GitHub CLI](https://cli.github.com/) and run `gh auth login` |

### Configuration (optional)

Copy `config/settings.example.toml` to `config/settings.toml`. Common settings:

```toml
[embedding]
provider = "none"                 # turn off semantic search; the default is "fastembed"
[capture]
import_roots = ["~/Documents", "~/Desktop", "~/Downloads"]   # folders an agent may ask PMI to read; hidden folders are always refused
[policy]
inferred_create = "review"        # agent-proposed claims without a source quote: review / apply / deny
```

**Never put secrets in the configuration file**: the whole folder is meant to be copied around.

---

## 3. Connecting AI agents

Every agent starts the PMI MCP server (stdio) with the same command:

```
command: /absolute/path/to/personal-memory/pmi
args:    ["mcp"]
```

### Claude Code

```bash
claude mcp add --scope user pmi -- ~/personal-memory/pmi mcp    # --scope user: available in all projects
claude mcp list                                                 # check that it is connected
```

`install.sh` has already installed the 3 skills: `/pmi-notion`, `/pmi-linear` and `/pmi-slack` (see [section 4](#4-adding-knowledge)).

### Codex CLI

Add this to `~/.codex/config.toml`:

```toml
[mcp_servers.pmi]
command = "/Users/<you>/personal-memory/pmi"
args = ["mcp"]
```

### Cursor / Claude Desktop / Gemini CLI / other MCP clients

These clients share the same JSON structure; only the file location differs:

| Client | Configuration file |
|---|---|
| Cursor | `~/.cursor/mcp.json` (global), or `.cursor/mcp.json` inside a project |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |

```json
{
  "mcpServers": {
    "pmi": { "command": "/Users/<you>/personal-memory/pmi", "args": ["mcp"] }
  }
}
```

### The 6 MCP tools

The server's `instructions` field explains how to use them, so a newly connected agent can start right away:

| Tool | Purpose |
|---|---|
| `memory_get_context` | **Start here.** Given a question, returns the current memories (with quotes), history, conflicts and relevant source passages; says so when the evidence is insufficient |
| `memory_search` | Low-level search over memories or source passages |
| `memory_get` | Read by id: memories (with version history), sources (paged), entities, pending changes |
| `memory_add_source` | Store an original text or file. Passing `url` gives the source a stable identity so later imports become new versions. Defaults to `trust="external"` |
| `memory_propose` | **The only write path**: create / supersede / update / link / archive / restore |
| `memory_list` | Browse memories, entities, slots, pending changes and recent activity. `source=<source id>` lists the memories that cite a source |

Confirming, deleting and approving are **not available over MCP**. Only you can do them, from the command line.

### Make agents use PMI proactively

Add a paragraph like this to a project's `CLAUDE.md` / `AGENTS.md` so agents check PMI first more reliably:

```markdown
Before answering questions about my past work (requirement background, why a decision was made, who owns what, the current state of a feature), call PMI's `memory_get_context` and cite the sources it returns. If PMI reports insufficient evidence, say so instead of guessing.
```

### Agents and scripts without MCP

Any agent that can run commands can use the CLI:

```bash
pmi --json context "some question"
```

From Python:

```python
from pmi import Memory
with Memory.open("~/personal-memory") as m:
    ctx = m.get_context("Why didn't Project A choose option B?")
```

---

## 4. Adding knowledge

PMI stores data in two layers:

- **Sources**: the original texts, which serve as evidence.
- **Memories**: knowledge distilled from sources; every memory quotes a source.

### 4.1 Import files and folders

```bash
pmi import ~/Documents/prds                 # import a folder: Markdown / TXT / PDF / DOCX; GBK-encoded Chinese text works too
pmi import meeting.md --extract             # meeting notes in the convention below become memories automatically
pmi import notes/ --trust external          # mark material written by others as external
```

On import:

- Hidden files and unsupported formats are skipped, with the reason listed. One broken file does not stop the rest.
- Identical content is deduplicated automatically.
- **Importing a file again after it changed stores a new version**; the old version is kept.

**Meeting notes convention** (extracted with `--extract`, no LLM needed):

```markdown
---
title: Project A weekly sync
date: 2026-10-01
project: Project A
attendees: [Alice, Bob]
type: meeting
---
# Project A weekly sync 2026-10-01

## Decisions
- [slot: launch-date] Project A will launch on November 15.

## Facts
- The push platform is maintained by Bob's team.

## Action Items
- @Bob finish the integration review by October 10.
```

Notes on the format:

- Section names can be `Decisions`, `Facts` / `Context`, `Action Items` / `TODO`, or their Chinese equivalents (`决策`, `事实` / `背景` / `结论`, `待办` / `行动项`).
- `[slot: …]` is a topic key; see [section 5](#5-updating-knowledge).
- Put a space after `@name`.
- Free-form documents such as PRDs yield nothing this way. Let an agent distill them instead (see 4.3).

### 4.2 Store a piece of text

```bash
pmi note "Project A's release window moves to Thursdays"
pbpaste | pmi note - --kind document --title "Some PRD" --url "<original link>" --trust external
```

### 4.3 Let an agent distill it (recommended)

In Claude Code, say: "Store this PRD in PMI and distill the knowledge." The agent will:

1. Call `memory_add_source` to store the original text.
2. Call `memory_propose` to submit memories, each with a verbatim `quote`.

PMI checks every quote and handles deduplication, superseding and conflicts automatically.

If you ask questions in both Chinese and English, ask the agent to write each memory's `content` in both languages: Chinese first, then English on a line starting with `EN:`. Quotes stay verbatim in the source's language. The Notion / Linear / Slack skills do this by default; see [Questions in Chinese and English](#questions-in-chinese-and-english).

### 4.4 Import from Notion / Linear / Slack (Claude Code skills)

| You say | What happens |
|---|---|
| `/pmi-notion <Notion page link>` | Fetches the whole page and **shows you the title, location and length; only after you confirm** does it store the text, then it distills decisions, rules, procedures and owners |
| `/pmi-linear <ticket link or ID>` | Fetches the ticket (description, comments, status history) and its linked GitHub PRs (description, human review discussion, list of changed files; **no diff**; generated verbatim by your local `gh`). After you confirm, it stores them and distills root causes, fixes, trade-offs and regression notes |
| `/pmi-slack <thread link>` | Reads the whole thread. The confirmation **flags private channels and DMs**. After storing, it distills the discussion by its final outcome, separating settled decisions, **open questions** and action items |

You can also just say "import this ticket into PMI". All three skills follow the same rules:

- **They run only when you explicitly ask for an import.** A link that is part of another task ("implement this PRD", "help me reply to this thread") is not stored in PMI.
- **They always ask before writing.** One confirmation covers both storing the text and distilling it. Say "store the text only" to skip distilling.
- **Everything is stored as an external document.** Memories backed by these sources cannot automatically change memories you confirmed, or memories backed by your own documents.
- **Memories are bilingual.** Whatever the language of the source, each memory states the knowledge in Chinese and then in English on an `EN:` line, so a question in either language finds it. Quotes stay verbatim in the source's language; the original text is stored once, never translated.
- **One object, one identity.** All link forms of the same page, ticket, PR or thread map to one source identity, such as `notion:<page id>`, `linear:APP-184`, `github:owner/repo#123` or `slack:<channel>/<thread timestamp>`. Importing it again creates a new version.
- **No personal identifiers are kept.** Email addresses and user IDs are not stored; images keep only their caption or file name.

Other agents can follow the steps in `integrations/claude-code/skills/*/SKILL.md` with their own Notion / Linear / Slack connectors.

### 4.5 Write memories directly (JSON)

```bash
pmi propose ops.json                      # runs as you; takes effect immediately
pmi propose ops.json --as-agent my-bot    # runs as an agent: write policy applies and conflicts go to review
```

Example `ops.json` (full example in `examples/agent-ops.json`):

```json
[
  {"op": "create", "memory": {
    "kind": "decision",
    "content": "Project A sends push notifications through option A (the existing push platform)",
    "slot": "project-a/architecture",
    "entities": [{"name": "Project A", "kind": "project"}],
    "valid_from": "2026-10-01",
    "evidence": [{"source_id": "src_…", "quote": "option A (the existing push platform)"}]
  }}
]
```

Fields:

- `kind` is one of `episodic` (event), `semantic` (fact), `project`, `person`, `decision`, `procedure`, `preference`.
- `evidence.quote` must appear verbatim in the source named by `source_id` and be at least 4 characters long.

---

## 5. Updating knowledge

### Slots: one current conclusion per topic

A `slot` is a memory's topic key, such as `project-a/launch-date`. A slot has at most one active memory at any time. When a new claim arrives:

| New claim | Result |
|---|---|
| Dated later than the current conclusion | **Automatically supersedes** it. The old conclusion becomes superseded, with its validity period and successor recorded |
| Dated earlier (backfilled history) | Inserted into the history; the current conclusion stays. **Import order does not change the outcome** |
| Conflicts with a conclusion you confirmed, or its evidence is less trusted | The new claim is marked `disputed` and a pending change is created; the current conclusion stays |
| Undated, so the order is unclear | Goes to review |
| Same content as an existing memory | Merged into it; only the quote is added |

### Common update operations

```bash
pmi import changed-file.md            # stored as a new version; quoted sentences that disappeared from it are reported
pmi confirm mem_…                     # confirm a memory; agents can no longer change it automatically
pmi review list                       # list pending changes, with the full text and quotes of what would be superseded or activated
pmi review approve prp_…              # approve; the approved memory is marked as confirmed
pmi review reject prp_… --reason "The Slack message was wrong"
pmi show mem_… --history              # full version history and quotes of a memory
pmi entity alias ent_… "alias"        # make different names point to the same person or project
```

You can also change memories with JSON, through `pmi propose` or MCP `memory_propose`:

- Change the content: `{"op":"update","id":"mem_…","changes":{"content":"…"}}`
- Supersede old memories: `{"op":"supersede","old_ids":["mem_…"],"memory":{…}}`
- Link two memories: `{"op":"link","from_id":"…","to_id":"…","type":"contradicts"}`

---

## 6. Deleting knowledge

```bash
pmi archive mem_… --reason "no longer relevant"     # soft delete: hidden from search by default, can be restored
pmi restore-memory mem_…                            # restore an archived memory

pmi forget mem_… --reason "not needed"              # hard-delete a memory
pmi forget src_… --reason "sensitive"               # hard-delete a source; refused if memories depend on it, listing them
pmi forget src_… --reason "sensitive" --cascade     # also delete the memories that rest only on this source
pmi forget src_… --reason "sensitive" --keep-memories   # keep those memories, downgraded to "inferred"
pmi forget src_… --reason "…" --this-version        # delete only this version (default: every version of the document)
```

About forgetting:

- Forgetting cannot be undone. Only a tombstone without content remains (id, time, reason).
- The database runs with `secure_delete`, and forgetting also compacts the search index and truncates the WAL, so the deleted content does not linger in the database files.
- **Backups made before forgetting still contain the content.** Delete old backups yourself if needed.

---

## 7. Querying knowledge

```bash
pmi context "When does Project A launch now?"                # assembled context: current, conflicts, history, source passages
pmi context "launch date" --project "Project A" --history
pmi context "launch date" --as-of 2026-10-15                 # answer as of a point in time
pmi search "push platform" --scope sources                   # low-level search
pmi show mem_… src_…                                         # look up by id
pmi list slots | entities | proposals | recent | memories    # browse
```

Fields in a `context` result:

- `status`: active / superseded / disputed.
- `verification`: user_confirmed / source_backed / inferred.
- `trust`: the trust level of each quote.
- `insufficient_evidence: true`: PMI does not have enough evidence to answer. Items found only through entity links or weak semantic similarity are still listed, but do not count as evidence.

### Questions in Chinese and English

Keyword search matches words in the language of the question, so it finds text written in that same language. Two things carry a question across languages:

- **Bilingual memories.** The Notion / Linear / Slack skills write every memory in Chinese and then in English on an `EN:` line, so a question in either language matches it by keyword. Ask other agents to do the same (see 4.3).
- **The multilingual embedding model.** It finds text in the other language by meaning, for example source passages that no memory covers yet. A match found only this way counts as evidence when its similarity is at least 0.65, which cross-language matches often miss; such matches are still listed, with `insufficient_evidence: true`.

Without the embedding model (`--no-semantic` or `PMI_NO_SEMANTIC=1`) there is no cross-language search. When a project or person has a different name in each language, add an alias so entity search finds both: `pmi entity alias <entity id> Android` (ids come from `pmi entity list`).

---

## 8. Web UI

```bash
pmi ui                 # opens http://127.0.0.1:8765 in your browser; Ctrl-C stops it
pmi ui --port 9000     # use another port
pmi ui --no-open       # print the link instead of opening a browser
pmi ui --new-token     # replace the access token; links opened before stop working
```

Like every other `pmi` command, it runs only while you use it; nothing stays in the background.

| Tab | What you can do |
|---|---|
| Overview | Counts of memories and pending reviews, disk usage, the status of each watched log, the latest memories and log lines |
| Memories | Search (Chinese or English) and filter by status, kind and slot. Open a memory to see its full content, quotes with links to the original, entities and history, then **Confirm**, **Archive**, **Restore** or **Delete permanently** |
| Review | Changes agents proposed that need your decision, with the new content next to what it would replace: **Approve** or **Reject** |
| Logs | Follow the log files of your own agents live |
| Storage | How much space the database, sources, index, model and backups take |

### Watching agent logs

Add one `[[ui.logs]]` entry per log to `config/settings.toml`:

```toml
[[ui.logs]]
name = "slack-dm-responder"                          # shown in the page
path = "~/.slack-dm-responder/logs/session-*.log"    # ~ and wildcards work; the newest file name comes first
stale_minutes = 10                                   # optional: no new output for this long turns the status yellow
hide_pattern = "扫描 0 条"                            # optional: lines containing this text can be hidden ("Hide idle lines")
```

The page shows the newest file and keeps following it, also when a new file starts (for example a new day). The status is **Active** when the file changed within `stale_minutes`, **No output for N min** when it did not, and **No log file** when nothing matches. PMI only reads these files. A mistake in the `[ui]` section stops only `pmi ui`: `pmi doctor` reports it, and every other command, including `pmi mcp`, keeps working.

### Security

- It listens on `127.0.0.1` only, so other machines cannot reach it.
- Every data request needs the access token from the link `pmi ui` prints. The token lives in `data/ui-token` (readable only by you) and is reused across restarts, so bookmarks keep working. It is not included in `pmi backup` or `pmi export`. `pmi ui --new-token` replaces it.
- Requests from other websites open in your browser are refused: the Host header is checked, no cross-origin access is allowed, and writes accept JSON only.
- Actions in the page act as you, exactly like the command line, and the audit log records them as `owner:ui`. Agents still cannot confirm, delete or approve through MCP.
- The page shows memory content and log lines, which may include other people's messages, on your machine only.

---

## 9. Write policy and trust

| Who | What they can do |
|---|---|
| Agents (over MCP, or the CLI with `--as-agent`) | New memories with a source quote take effect immediately (marked unconfirmed). These go to review: claims without a quote, changes to protected memories, restoring a memory you archived. Agents cannot confirm, delete or approve |
| You (CLI) | Everything |

**Protected memories** are memories you confirmed, or memories backed by a more trusted source.

Trust levels, from highest to lowest:

- `owner`: content you imported yourself.
- `agent`: an agent's own analysis.
- `external`: Slack, web pages, other people's documents, and Notion / Linear imports.

Whether a change takes effect automatically depends on the **lowest** trust level among the new evidence it brings. Adding a sentence that already exists in the target's own source does not raise the trust level.

---

## 10. Data, backup and migration

```
personal-memory/
├── pmi                         launcher script
├── scripts/install.sh          one-step installer
├── integrations/claude-code/   Claude Code skills (pmi-notion / pmi-linear / pmi-slack)
├── src/  tests/  docs/  examples/
├── config/settings.toml        optional configuration (not committed)
└── data/                       your memory (not committed)
    ├── memory/pmi.db           ← required: the source of truth
    ├── sources/                ← required: original files
    ├── memory/backups/         backups
    ├── index/search.db         disposable: search index, rebuilt automatically
    └── models/                 disposable: embedding model, can be downloaded again
```

```bash
pmi verify            # integrity check: database, hashes, every quote still in its source, version chains, complete forgetting
pmi verify --fix      # also remove orphan files that no source references
pmi backup            # consistent snapshot → data/memory/backups/*.tar.gz (with checksums)
pmi restore <archive> --to ~/pmi-copy
pmi export --out all.jsonl     # full export, independent of format and vendor
```

**Moving to another computer:**

1. Run `pmi backup` first, or make sure no agent is writing.
2. Copy the whole folder to the new computer. `.venv/` and `data/index/` can be left out.
3. In the new location, run `./scripts/install.sh && pmi verify`.

All paths are relative, so the folder can live anywhere.

**Switching the embedding model:** change `[embedding] model` in `config/settings.toml`, then run `pmi reindex --vectors`. During the rebuild, semantic search is turned off automatically and keyword search keeps working; memories are not affected.

---

## 11. Command reference

| Command | Purpose |
|---|---|
| `pmi init` / `pmi doctor` / `pmi verify [--fix]` | Initialize / check the environment / check integrity |
| `pmi import <path…> [--extract] [--trust …] [--kind …]` | Import files or folders |
| `pmi note <text or -> [--kind --title --url --origin-key --source-time --trust --as-agent --extract]` | Store a piece of text; `-` reads standard input |
| `pmi context <question> [--project --participants --as-of --from --to --history --max-tokens]` | Assemble context for a question |
| `pmi search <query> [--scope --kind --status --entity --limit]` | Search |
| `pmi show <id…> [--history]` / `pmi list <category> [--source …]` | Show / browse |
| `pmi propose <ops.json> [--as-agent name]` | Write memories |
| `pmi confirm` / `pmi review list\|approve\|reject` | Confirm / review |
| `pmi archive` / `pmi restore-memory` / `pmi forget` | Archive / restore / forget |
| `pmi entity list\|alias` | Entities and aliases |
| `pmi backup` / `pmi restore` / `pmi export` / `pmi reindex [--vectors]` | Maintenance |
| `pmi model download` | Download the semantic search model |
| `pmi github-pr <PR link>` | Print a PR as Markdown via `gh` (prints only, stores nothing) |
| `pmi slack-ref <Slack link>` | Parse the channel ID, thread timestamp and source identity |
| `pmi mcp` | Start the MCP server for agents |
| `pmi ui [--port N] [--no-open] [--new-token]` | Open the local web UI (section 8) |

Global options:

- `--home <folder>`: the PMI folder; defaults to the repository folder or `$PMI_HOME`.
- `--json`: machine-readable JSON output.

---

## 12. Security boundary

- **Single user, local.** Permission checks limit what an agent can do through the tool interface; they are not operating-system isolation. Any local program that can read and write this folder can bypass them. In Claude Code, Claude Code's own permission prompts guard the commands an agent runs.
- **A portable folder is not access control.** Copying the folder takes all the data with it. If it holds company content (PRDs, Slack), follow your company's data policy before copying it to another device.
- **PMI takes no outbound actions.** It does not send messages or change other systems. Only two things touch the network: `pmi model download`, which you run explicitly, and `pmi github-pr`, which calls your local `gh`. The package stores no secrets.
- **External content is labeled.** Content with `trust=external` is marked in results, together with a notice that the text is data, not instructions. PMI's behavior depends only on who is calling and which operation is requested, never on the content.
- **Everything is traceable.** Every change is written to an audit log, and every memory has a full version chain.
- **The web UI is local.** `pmi ui` listens on 127.0.0.1 only and every data request needs its access token; see section 8.
- **Not implemented:** multiple users and permission domains, a built-in LLM extractor, a public HTTP API (the web UI's endpoints serve only its own page), automatic Slack / Notion sync.

---

## 13. Troubleshooting

| Symptom | Fix |
|---|---|
| `pmi`: command not found | Add `~/.local/bin` to your PATH, or use the full path to `./pmi` in the repository |
| Content you just wrote does not show up in search | Usually nothing to do: every call brings the index up to date. If it persists, run `pmi reindex` |
| The notes in a result say `semantic search unavailable` | Run `pmi model download`, or see the reason in `pmi doctor` |
| A question in one language misses content written in the other | Check that `pmi doctor` shows the embedding model as available. A memory whose `content` is in one language only is matched across languages by meaning alone: add the other language with an `update` op (see [section 5](#5-updating-knowledge)). Importing an unchanged source again does not re-distill it |
| You suspect data corruption | Run `pmi verify`. If the index is damaged, delete `data/index/`; it is rebuilt automatically |
| verify reports `orphan_file` | An original file is not referenced by any source (usually left by an interrupted delete). Run `pmi verify --fix` |
| `pmi ui` says the port is already in use | Another program (or another `pmi ui`) is using it: pass `--port <other>` |
| The schema is newer than the code | The database comes from a newer PMI. Run `git pull` to update the code, then open it again |

---

## 14. Development and project layout

```bash
uv sync --extra semantic
uv run --frozen --extra semantic pytest -q            # all tests; no network or model needed
uv run --frozen --extra semantic pytest -q -m slow    # tests with the real embedding model; run pmi model download first
```

Main modules in `src/pmi/`:

- `api.py`: the single business entry point.
- `evolution.py`: superseding, conflict and review rules.
- `policy.py`: write policy.
- `context.py`: context assembly.
- `search.py`: search (FTS5 over CJK bigrams, plus vectors).
- `index/`: the rebuildable search index.
- `mcp_server.py`, `cli.py`: the MCP server and command-line entry points.
- `ui/`: the local web UI (`server.py` HTTP and security checks, `handlers.py` endpoints, `static/` the page).
- `integrations/github_pr.py`: GitHub PR to Markdown.

Other folders and files:

- `tests/test_acceptance.py`: 10 end-to-end acceptance scenarios.
- `docs/design/`: the design document (in Chinese).
- `docs/implementation-notes.md`: trade-offs made during implementation (in Chinese).
- `docs/requirements.md`: the original requirements (in Chinese).

Dependencies: `mcp`, `pypdf`, `python-docx`, `pyyaml`, and optionally `fastembed`; all versions are pinned in `uv.lock`.

License: [MIT](LICENSE)
