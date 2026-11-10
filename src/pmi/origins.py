"""Stable document identities (origin_key) derived from URLs, so re-imports become new versions."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from .errors import ValidationError

_HEX32 = re.compile(r"[0-9a-f]{32}")
_LINEAR_ISSUE = re.compile(r"^/[^/]+/issue/([A-Za-z][A-Za-z0-9]*-\d+)(?:/|$)")
_GITHUB_PR = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)(?:/|$)")
_SLACK_MESSAGE = re.compile(r"^/archives/([A-Za-z0-9]+)/p(\d{10})(\d{6})/?$")
_SLACK_TS = re.compile(r"^\d{10}\.\d{6}$")


def slack_thread_ref(url: str) -> dict[str, str]:
    """Channel and thread (parent message) of a Slack permalink. A reply link carries the parent in
    ?thread_ts=, so every link into one thread maps to the same origin key."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    m = _SLACK_MESSAGE.match(parts.path)
    if not (host == "slack.com" or host.endswith(".slack.com")) or not m:
        raise ValidationError("not a Slack message link (…slack.com/archives/<channel>/p<timestamp>)")
    channel = m.group(1).upper()
    thread_ts = next((t for t in parse_qs(parts.query).get("thread_ts", []) if _SLACK_TS.match(t)),
                     f"{m.group(2)}.{m.group(3)}")
    return {"channel_id": channel, "thread_ts": thread_ts, "origin_key": f"slack:{channel}/{thread_ts}"}
_NOTION_DOMAINS = ("notion.so", "notion.site", "notion.com")  # app.notion.com/p/<id> is the newest form


def _is_notion(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in _NOTION_DOMAINS)


def _notion_page_id(candidate: str) -> str | None:
    """A Notion id is 32 hex chars, raw or as a dashed UUID, or the last '-'-token of a slug
    ("Project-A-PRD-<id>"). Never strip dashes from a slug: title letters a-f would merge into it."""
    value = candidate.strip().lower()
    compact = value.replace("-", "")
    if _HEX32.fullmatch(compact):
        return compact
    last = value.rsplit("-", 1)[-1]
    return last if _HEX32.fullmatch(last) else None


def origin_key_from_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValidationError(f"not an http(s) URL: {url!r}")
    host = parts.hostname or ""
    if _is_notion(host):
        candidates = parse_qs(parts.query).get("p", [])  # a page opened as a peek from a database
        segments = [s for s in parts.path.split("/") if s]
        candidates += segments[-1:]
        for c in candidates:
            page = _notion_page_id(c)
            if page:
                return f"notion:{page}"
        raise ValidationError("this Notion URL has no page id")
    if host == "slack.com" or host.endswith(".slack.com"):
        return slack_thread_ref(url)["origin_key"]
    if host == "linear.app":
        m = _LINEAR_ISSUE.match(parts.path)
        if not m:
            raise ValidationError("this Linear URL is not an issue link")
        return f"linear:{m.group(1).upper()}"
    if host in ("github.com", "www.github.com"):
        m = _GITHUB_PR.match(parts.path)
        if m:
            return f"github:{m.group(1).lower()}/{m.group(2).lower()}#{m.group(3)}"
    return f"url:{parts.scheme.lower()}://{host}{parts.path or '/'}"
