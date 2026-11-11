"""Owner command line interface: a thin layer over pmi.api.Memory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .actors import Actor
from .api import Memory
from .errors import PmiError, ValidationError
from .paths import resolve_home

EMBEDDING_COMMANDS = {"search", "context", "import", "note", "propose", "reindex"}


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--home", default=argparse.SUPPRESS, help="PMI home folder (default: $PMI_HOME or package folder)")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable JSON output")
    return p


def _parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(prog="pmi", description="Personal Memory Infrastructure", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_, parents=[common])

    add("init", "create the data layout (idempotent)")
    p = add("import", "import files or folders")
    p.add_argument("paths", nargs="+")
    p.add_argument("--extract", action="store_true", help="run the structured meeting-notes extractor")
    p.add_argument("--trust", default="owner", choices=["owner", "agent", "external"])
    p.add_argument("--kind", default="document", choices=["document", "note", "conversation", "agent_output"])
    p.add_argument("--no-recursive", action="store_true")
    p = add("note", "store text as a source ('-' reads stdin)")
    p.add_argument("text", help="the text, or '-' to read it from stdin")
    p.add_argument("--title")
    p.add_argument("--source-time")
    p.add_argument("--kind", default="note", choices=["document", "note", "conversation", "agent_output"])
    p.add_argument("--origin-key", help="stable identity; changed content becomes a new version")
    p.add_argument("--url", help="where the text came from (e.g. a Notion page); derives the origin key")
    p.add_argument("--trust", choices=["owner", "agent", "external"])
    p.add_argument("--extract", action="store_true")
    p.add_argument("--as-agent", metavar="NAME", help="act as agent NAME (agent policy, trust ≤ agent)")
    p = add("search", "keyword/semantic search")
    p.add_argument("query")
    p.add_argument("--scope", default="all", choices=["all", "memories", "sources"])
    p.add_argument("--kind")
    p.add_argument("--status")
    p.add_argument("--entity")
    p.add_argument("--limit", type=int, default=10)
    p = add("context", "assemble context for a question")
    p.add_argument("query")
    p.add_argument("--project")
    p.add_argument("--participants", help="comma-separated names")
    p.add_argument("--as-of")
    p.add_argument("--from", dest="time_from")
    p.add_argument("--to", dest="time_to")
    p.add_argument("--history", action="store_true")
    p.add_argument("--max-tokens", type=int)
    p = add("show", "show memories/sources/entities/proposals by id")
    p.add_argument("ids", nargs="+")
    p.add_argument("--history", action="store_true")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=4000)
    p = add("propose", "apply a JSON list of ops (as the owner unless --as-agent)")
    p.add_argument("ops_file")
    p.add_argument("--reason")
    p.add_argument("--as-agent", metavar="NAME", help="act as agent NAME: protected changes become proposals")
    p = add("list", "browse memories, entities, slots, proposals or recent changes")
    p.add_argument("what", choices=["memories", "entities", "slots", "proposals", "recent"])
    p.add_argument("--kind")
    p.add_argument("--status")
    p.add_argument("--entity")
    p.add_argument("--source", help="only memories citing this source id")
    p.add_argument("--limit", type=int, default=50)
    p = add("confirm", "mark a memory as user-confirmed")
    p.add_argument("memory_id")
    p = add("review", "list/approve/reject pending proposals")
    rsub = p.add_subparsers(dest="review_cmd", required=True)
    rsub.add_parser("list", parents=[common])
    r = rsub.add_parser("approve", parents=[common])
    r.add_argument("proposal_id")
    r = rsub.add_parser("reject", parents=[common])
    r.add_argument("proposal_id")
    r.add_argument("--reason", required=True)
    p = add("archive", "archive a memory")
    p.add_argument("memory_id")
    p.add_argument("--reason")
    p = add("restore-memory", "restore an archived memory")
    p.add_argument("memory_id")
    p = add("forget", "irreversibly forget a memory or source")
    p.add_argument("target_id")
    p.add_argument("--reason", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--cascade", action="store_true", help="also forget memories that rest only on this source")
    mode.add_argument("--keep-memories", action="store_true", help="keep them, downgraded to inferred")
    p.add_argument("--this-version", action="store_true", help="forget only this version of the document")
    p = add("entity", "list entities or add an alias")
    esub = p.add_subparsers(dest="entity_cmd", required=True)
    e = esub.add_parser("list", parents=[common])
    e.add_argument("--kind")
    e = esub.add_parser("alias", parents=[common])
    e.add_argument("entity_id")
    e.add_argument("alias")
    p = add("reindex", "rebuild the derived index")
    p.add_argument("--vectors", action="store_true", help="recompute embeddings only")
    p = add("verify", "check data integrity")
    p.add_argument("--fix", action="store_true", help="delete orphan files under data/sources")
    add("doctor", "check the environment")
    p = add("backup", "write a consistent backup archive")
    p.add_argument("--out")
    p = add("restore", "restore a backup archive")
    p.add_argument("archive")
    p.add_argument("--to", help="target home (default: this home)")
    p.add_argument("--force", action="store_true")
    p = add("export", "export everything as JSONL")
    p.add_argument("--out", default="pmi-export.jsonl")
    p = add("model", "manage the embedding model")
    msub = p.add_subparsers(dest="model_cmd", required=True)
    msub.add_parser("download", parents=[common])
    add("mcp", "run the MCP stdio server (agent tools)")
    p = add("ui", "open the local web UI (Ctrl-C to stop)")
    p.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    p.add_argument("--no-open", action="store_true", help="print the link instead of opening a browser")
    p.add_argument("--new-token", action="store_true", help="replace the access token; old links stop working")
    p = add("github-pr", "print a GitHub PR as Markdown via your gh login (stores nothing)")
    p.add_argument("url")
    p = add("slack-ref", "parse a Slack message link: channel id, thread ts, origin key")
    p.add_argument("url")
    return parser


# ---------------------------------------------------------------------------- human output

def _line(m: dict[str, Any]) -> str:
    when = (m.get("valid_from") or "")[:10]
    until = f"..{m['valid_until'][:10]}" if m.get("valid_until") else ""
    return f"- [{m['status']}/{m['verification']}] {m['content']}  ({m['kind']}, {when}{until}, {m['id']})"


def _human_context(out: dict[str, Any]) -> str:
    lines = [f"Query: {out['query']}  (as of {out['as_of']})"]
    lines.append("\nCurrent:")
    for m in out["memories"]:
        lines.append(_line(m))
        for c in m["citations"]:
            lines.append(f"    ↳ {c['title']} [{c['trust']}] “{c['quote']}”")
    if out["conflicts"]:
        lines.append("\nConflicts:")
        lines += [f"- {c['summary']} (proposal {c['proposal_id']})" for c in out["conflicts"]]
    lines.append("\nHistory:")
    lines += [_line(m) for m in out["history"]] or ["- (none)"]
    lines.append("\nSources:")
    lines += [f"- {s['title']} [{s['trust']}]: {s['excerpt'][:160]}" for s in out["sources"]] or ["- (none)"]
    if out["insufficient_evidence"]:
        lines.append("\n⚠ insufficient evidence")
    if out["notes"]:
        lines.append("Notes: " + "; ".join(out["notes"]))
    return "\n".join(lines)


def _human_review(out: dict[str, Any]) -> str:
    lines = []
    for p in out["items"]:
        lines.append(f"{p['id']}  by {p['actor']}  — {p['risk']}")
        for item in p["preview"]:
            for label in ("replaces", "activates", "target", "new_memory"):
                values = item.get(label)
                for v in (values if isinstance(values, list) else [values] if values else []):
                    status = f"[{v.get('status')}/{v.get('verification')}] " if v.get("status") else ""
                    lines.append(f"    {item['op']} {label}: {status}{v['content']}")
                    lines += [f"        ↳ {c['title']} [{c['trust']}] “{c['quote']}”" for c in v["citations"]]
            if item.get("changes"):
                lines.append(f"    changes: {json.dumps(item['changes'], ensure_ascii=False)}")
    return "\n".join(lines) or "no pending proposals"


def _human(command: str, out: Any) -> str:
    if command == "context":
        return _human_context(out)
    if command == "review" and isinstance(out, dict) and "items" in out:
        return _human_review(out)
    if command == "search":
        lines = ["Memories:"] + [_line(m) for m in out["memories"]] + ["Sources:"]
        lines += [f"- {s['title']} [{s['trust']}]: {s['excerpt'][:160]}" for s in out["sources"]]
        return "\n".join(lines)
    if command == "import":
        lines = [f"imported {len(out['imported'])}, skipped {len(out['skipped'])}, errors {len(out['errors'])}"]
        lines += [f"  ✗ {e['path']}: {e['error']}" for e in out["errors"]]
        return "\n".join(lines)
    return json.dumps(out, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------- dispatch

def _actor(args: argparse.Namespace) -> Actor | None:
    """--as-agent is a voluntary downgrade: the caller is treated exactly like an MCP agent."""
    name = getattr(args, "as_agent", None)
    return Actor.agent(name) if name else None


def _run(args: argparse.Namespace, m: Memory) -> tuple[Any, int]:
    c = args.command
    if c == "init":
        return {"initialised": str(m.paths.home)}, 0
    if c == "import":
        out = m.import_paths(args.paths, recursive=not args.no_recursive, extract=args.extract, trust=args.trust,
                             kind=args.kind)
        return out, 0
    if c == "note":
        text = sys.stdin.read() if args.text == "-" else args.text
        return m.add_source(text=text, title=args.title, kind=args.kind, source_time=args.source_time,
                            origin_key=args.origin_key, url=args.url, trust=args.trust, extract=args.extract,
                            actor=_actor(args)), 0
    if c == "list":
        return m.list(args.what, kind=args.kind, status=args.status, entity=args.entity, source=args.source,
                      limit=args.limit), 0
    if c == "search":
        return m.search(args.query, scope=args.scope, kind=args.kind, status=args.status, entity=args.entity,
                        limit=args.limit), 0
    if c == "context":
        people = [p.strip() for p in args.participants.split(",")] if args.participants else None
        rng = (args.time_from, args.time_to) if (args.time_from or args.time_to) else None
        return m.get_context(args.query, project=args.project, participants=people, time_range=rng,
                             as_of=args.as_of, include_history=args.history, max_tokens=args.max_tokens), 0
    if c == "show":
        out = m.get(args.ids, include_history=args.history, text_offset=args.offset, text_limit=args.limit)
        return out, 0 if not out["not_found"] else 1
    if c == "propose":
        try:
            ops = json.loads(Path(args.ops_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"cannot read ops file: {exc}") from exc
        return m.propose(ops, reason=args.reason, actor=_actor(args)), 0
    if c == "confirm":
        return m.confirm(args.memory_id), 0
    if c == "review":
        if args.review_cmd == "list":
            return m.list("proposals"), 0
        if args.review_cmd == "approve":
            out = m.approve(args.proposal_id)
            return out, 0 if out["status"] == "applied" else 1
        return m.reject(args.proposal_id, args.reason), 0
    if c == "archive":
        return m.propose([{"op": "archive", "id": args.memory_id}], reason=args.reason), 0
    if c == "restore-memory":
        return m.propose([{"op": "restore", "id": args.memory_id}]), 0
    if c == "forget":
        if args.target_id.startswith("src_"):
            mode = "cascade" if args.cascade else "keep_memories" if args.keep_memories else "refuse"
            return m.forget_source(args.target_id, args.reason, mode=mode, all_versions=not args.this_version), 0
        return m.forget_memory(args.target_id, args.reason), 0
    if c == "entity":
        if args.entity_cmd == "list":
            return m.list("entities", kind=args.kind, limit=500), 0
        return m.add_alias(args.entity_id, args.alias), 0
    if c == "reindex":
        return m.reindex(vectors_only=args.vectors), 0
    if c == "verify":
        out = m.verify(fix=args.fix)
        return out, 0 if out["ok"] else 1
    if c == "doctor":
        out = m.doctor()
        return out, 0 if out["ok"] else 1
    if c == "backup":
        return {"backup": str(m.backup(args.out))}, 0
    if c == "export":
        return m.export(args.out), 0
    if c == "model":
        return m.download_model(), 0
    raise ValidationError(f"unknown command {c}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    as_json = getattr(args, "json", False)
    try:
        home = resolve_home(getattr(args, "home", None))
        if args.command == "slack-ref":
            from .origins import slack_thread_ref
            ref = slack_thread_ref(args.url)
            print(json.dumps(ref, ensure_ascii=False, indent=2) if as_json else " ".join(ref.values()))
            return 0
        if args.command == "github-pr":
            from .integrations.github_pr import pr_markdown
            out = pr_markdown(args.url)
            print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else out["markdown"], end="" if not as_json else "\n")
            return 0
        if args.command == "ui":
            from .ui.server import serve
            serve(home, port=args.port, open_browser=not args.no_open, new_token=args.new_token)
            return 0
        if args.command == "mcp":
            from .mcp_server import run_stdio
            run_stdio(home)
            return 0
        if args.command == "restore":
            out = Memory.restore_backup(args.archive, args.to or home, force=args.force)
            code = 0 if out["verify"]["ok"] else 1
        else:
            with Memory.open(home, embedder="auto" if args.command in EMBEDDING_COMMANDS else None) as m:
                out, code = _run(args, m)
    except PmiError as exc:
        if as_json:
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str) if as_json else _human(args.command, out))
    return code
