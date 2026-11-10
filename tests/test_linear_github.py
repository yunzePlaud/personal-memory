"""Linear tickets and GitHub PRs as sources: stable origin keys and PR rendering via the gh CLI."""
import json

import pytest

from pmi.cli import main
from pmi.errors import ValidationError
from pmi.integrations import github_pr
from pmi.origins import origin_key_from_url


@pytest.mark.parametrize("url,key", [
    ("https://linear.app/acme/issue/APP-184/fix-the-reminder-tag-position", "linear:APP-184"),
    ("https://linear.app/acme/issue/app-184", "linear:APP-184"),
    ("https://linear.app/acme/issue/WEB-2436/会员提醒?foo=1", "linear:WEB-2436"),
    ("https://github.com/acme/mobile-app/pull/4917", "github:acme/mobile-app#4917"),
    ("https://github.com/acme/mobile-app/pull/4917/files#diff-1", "github:acme/mobile-app#4917"),
])
def test_linear_and_github_origin_keys(url, key):
    assert origin_key_from_url(url) == key


def test_linear_url_without_issue_id_rejected():
    with pytest.raises(ValidationError):
        origin_key_from_url("https://linear.app/acme/team/APP/active")


PR = {
    "number": 4917, "title": "fix: align the reminder tag with the design (APP-184)", "state": "MERGED", "isDraft": False,
    "mergedAt": "2026-11-10T03:42:03Z", "closedAt": "2026-11-10T03:42:03Z", "updatedAt": "2026-11-10T04:00:00Z",
    "createdAt": "2026-11-06T10:00:00Z", "author": {"login": "alice-dev"}, "baseRefName": "main",
    "headRefName": "fix/alice/app-184", "additions": 275, "deletions": 139, "changedFiles": 2,
    "url": "https://github.com/acme/mobile-app/pull/4917", "labels": [{"name": "bug"}],
    "body": "## What changed\nThe tag moved above the input bar.",
    "files": [{"path": "A.swift", "additions": 200, "deletions": 100}, {"path": "B.swift", "additions": 75, "deletions": 39}],
    "comments": [
        {"author": {"login": "linear-code"}, "body": "APP-184 linked", "createdAt": "2026-11-06T10:01:00Z"},
        {"author": {"login": "bob-reviewer"}, "body": "Looks good after the padding fix.", "createdAt": "2026-11-09T02:00:00Z"},
    ],
}
REVIEW_COMMENTS = [
    {"id": 1, "in_reply_to_id": None, "user": {"login": "chatgpt-codex-connector[bot]", "type": "Bot"},
     "path": "A.swift", "body": "P2: something", "created_at": "2026-11-07T01:00:00Z"},
    {"id": 2, "in_reply_to_id": None, "user": {"login": "bob-reviewer", "type": "User"},
     "path": "B.swift", "body": "The sheet height ignores the bottom safe area.", "created_at": "2026-11-07T02:00:00Z"},
    {"id": 3, "in_reply_to_id": 2, "user": {"login": "alice-dev", "type": "User"},
     "path": "B.swift", "body": "Checked on device: the card is not cut off.", "created_at": "2026-11-07T03:00:00Z"},
]
REVIEWS = [
    {"user": {"login": "copilot-pull-request-reviewer", "type": "Bot"}, "state": "COMMENTED", "body": "Overview",
     "submitted_at": "2026-11-07T00:00:00Z"},
    {"user": {"login": "bob-reviewer", "type": "User"}, "state": "APPROVED", "body": "",
     "submitted_at": "2026-11-09T03:00:00Z"},
]


def test_render_pr_keeps_humans_and_threads_drops_bots_and_diff():
    md = github_pr.render_pr("acme/mobile-app", PR, REVIEW_COMMENTS, REVIEWS)
    assert md.startswith("# PR #4917: fix: align the reminder tag with the design (APP-184)")
    assert "MERGED" in md and "2026-11-10T03:42:03Z" in md and "fix/alice/app-184 → main" in md
    assert "The tag moved above the input bar." in md
    assert "- B.swift (+75/−39)" in md
    assert "The sheet height ignores the bottom safe area." in md
    assert md.index("The sheet height ignores") < md.index("Checked on device")  # reply follows its thread
    assert "Looks good after the padding fix." in md and "bob-reviewer: APPROVED" in md
    for bot_text in ("P2: something", "APP-184 linked", "Overview"):
        assert bot_text not in md
    assert "diff" not in md.lower()


def test_pr_source_time_prefers_merge_time():
    assert github_pr.source_time(PR) == "2026-11-10T03:42:03Z"
    assert github_pr.source_time({**PR, "mergedAt": None, "closedAt": None}) == "2026-11-10T04:00:00Z"


def test_parse_pr_url():
    assert github_pr.parse_pr_url("https://github.com/acme/mobile-app/pull/4917/files") == (
        "acme/mobile-app", 4917)
    with pytest.raises(ValidationError):
        github_pr.parse_pr_url("https://github.com/acme/mobile-app/issues/1")


def test_fetch_uses_gh_and_cli_prints_markdown(monkeypatch, capsys, home):
    calls = []

    def fake_gh(args):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return json.dumps(PR)
        if args[-1].endswith("/comments"):
            return json.dumps(REVIEW_COMMENTS)
        return json.dumps(REVIEWS)
    monkeypatch.setattr(github_pr, "_gh", fake_gh)
    assert main(["--home", str(home), "github-pr", "https://github.com/acme/mobile-app/pull/4917"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# PR #4917") and calls[0][:3] == ["pr", "view", "4917"]
    assert main(["--home", str(home), "--json", "github-pr", "https://github.com/acme/mobile-app/pull/4917"]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["source_time"] == "2026-11-10T03:42:03Z" and meta["title"].startswith("PR #4917")


def test_gh_missing_is_a_clear_error(monkeypatch, capsys, home):
    def no_gh(args):
        raise ValidationError("gh CLI not found or not logged in")
    monkeypatch.setattr(github_pr, "_gh", no_gh)
    assert main(["--home", str(home), "github-pr", "https://github.com/a/b/pull/1"]) == 1
