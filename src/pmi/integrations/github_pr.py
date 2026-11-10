"""Render a GitHub pull request as Markdown for PMI, using the user's own `gh` CLI login.

Kept: title, state, author, branches, size, labels, description, changed file names, human review
threads (with replies), human conversation comments and review verdicts. Dropped: the code diff and
bot output (a bot-started thread is kept only when a human replied to it).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from ..errors import ValidationError

KNOWN_BOTS = frozenset({"linear-code", "linear", "chatgpt-codex-connector", "copilot-pull-request-reviewer",
                        "github-actions", "dependabot"})
PR_FIELDS = ("number,title,state,isDraft,mergedAt,closedAt,createdAt,updatedAt,author,baseRefName,headRefName,"
             "additions,deletions,changedFiles,url,labels,body,files,comments")
_PR_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$")


def is_bot(user: dict[str, Any] | None) -> bool:
    if not user:
        return True
    login = str(user.get("login") or "")
    return user.get("type") == "Bot" or bool(user.get("is_bot")) or login.endswith("[bot]") or login in KNOWN_BOTS


def parse_pr_url(url: str) -> tuple[str, int]:
    m = _PR_URL.match(url.strip())
    if not m:
        raise ValidationError(f"not a GitHub pull request URL: {url!r}")
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


def _gh(args: list[str]) -> str:
    if shutil.which("gh") is None:
        raise ValidationError("gh CLI not found; install it and run `gh auth login`")
    done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        first = (done.stderr or done.stdout).strip().splitlines()[:1]
        raise ValidationError(f"gh failed: {first[0] if first else 'unknown error'}")
    return done.stdout


def _items(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def fetch_pr(url: str) -> dict[str, Any]:
    repo, number = parse_pr_url(url)
    pr = json.loads(_gh(["pr", "view", str(number), "-R", repo, "--json", PR_FIELDS]))
    base = f"repos/{repo}/pulls/{number}"
    review_comments = _items(_gh(["api", "--paginate", "--jq", ".[]", f"{base}/comments"]))
    reviews = _items(_gh(["api", "--paginate", "--jq", ".[]", f"{base}/reviews"]))
    return {"repo": repo, "pr": pr, "review_comments": review_comments, "reviews": reviews}


def source_time(pr: dict[str, Any]) -> str | None:
    return pr.get("mergedAt") or pr.get("closedAt") or pr.get("updatedAt")


def _quote_block(text: str, indent: str) -> str:
    lines = (text or "").strip().splitlines() or [""]
    return lines[0] + "".join(f"\n{indent}{line}" for line in lines[1:])


def _state(pr: dict[str, Any]) -> str:
    if pr.get("mergedAt"):
        return f"MERGED (merged {pr['mergedAt']})"
    if pr.get("state") == "CLOSED":
        return f"CLOSED (closed {pr.get('closedAt')})"
    return "OPEN (draft)" if pr.get("isDraft") else "OPEN"


def _threads(review_comments: list[dict[str, Any]]) -> list[str]:
    replies: dict[Any, list[dict[str, Any]]] = {}
    roots = []
    for c in sorted(review_comments, key=lambda c: c.get("created_at") or ""):
        if c.get("in_reply_to_id"):
            replies.setdefault(c["in_reply_to_id"], []).append(c)
        else:
            roots.append(c)
    out: list[str] = []
    for root in roots:
        human_replies = [r for r in replies.get(root["id"], []) if not is_bot(r.get("user"))]
        if is_bot(root.get("user")) and not human_replies:
            continue
        out.append(f"### {root.get('path') or '(general)'}")
        if is_bot(root.get("user")):
            out.append(f"- *({root['user'].get('login')} comment omitted)*")
        else:
            out.append(f"- **{root['user']['login']}** ({root.get('created_at')}): {_quote_block(root.get('body'), '  ')}")
        for r in human_replies:
            out.append(f"  - **{r['user']['login']}** ({r.get('created_at')}): {_quote_block(r.get('body'), '    ')}")
        out.append("")
    return out


def render_pr(repo: str, pr: dict[str, Any], review_comments: list[dict[str, Any]],
              reviews: list[dict[str, Any]]) -> str:
    labels = ", ".join(lbl.get("name", "") for lbl in pr.get("labels") or []) or "-"
    lines = [
        f"# PR #{pr['number']}: {pr['title']}", "",
        f"- Repository: {repo}", f"- URL: {pr.get('url')}", f"- State: {_state(pr)}",
        f"- Author: {(pr.get('author') or {}).get('login')}",
        f"- Branch: {pr.get('headRefName')} → {pr.get('baseRefName')}",
        f"- Size: {pr.get('changedFiles')} files, +{pr.get('additions')}/−{pr.get('deletions')}",
        f"- Labels: {labels}", "", "## Description", "", (pr.get("body") or "(no description)").strip(), "",
        "## Changed files", "",
    ]
    lines += [f"- {f['path']} (+{f.get('additions', 0)}/−{f.get('deletions', 0)})" for f in pr.get("files") or []]
    threads = _threads(review_comments)
    if threads:
        lines += ["", "## Review threads", "", *threads]
    talk = [c for c in pr.get("comments") or [] if not is_bot(c.get("author"))]
    if talk:
        lines += ["", "## Conversation", ""]
        lines += [f"- **{c['author']['login']}** ({c.get('createdAt')}): {_quote_block(c.get('body'), '  ')}"
                  for c in talk]
    verdicts = [r for r in reviews if not is_bot(r.get("user"))
                and (r.get("state") in ("APPROVED", "CHANGES_REQUESTED") or (r.get("body") or "").strip())]
    if verdicts:
        lines += ["", "## Reviews", ""]
        for r in verdicts:
            body = (r.get("body") or "").strip()
            lines.append(f"- {r['user']['login']}: {r.get('state')} ({r.get('submitted_at')})"
                         + (f" — {_quote_block(body, '  ')}" if body else ""))
    return "\n".join(lines).rstrip() + "\n"


def pr_markdown(url: str) -> dict[str, Any]:
    data = fetch_pr(url)
    pr = data["pr"]
    return {"title": f"PR #{pr['number']}: {pr['title']}", "url": pr.get("url") or url,
            "source_time": source_time(pr), "markdown": render_pr(data["repo"], pr, data["review_comments"],
                                                                  data["reviews"])}
