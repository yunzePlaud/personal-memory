"""Slack thread permalinks map to one stable identity: slack:<CHANNEL>/<thread_ts>."""
import pytest

from pmi.errors import ValidationError
from pmi.origins import origin_key_from_url, slack_thread_ref

PARENT = "https://acme.slack.com/archives/C0123ABCDEF/p1700000000123456"
REPLY = ("https://acme.slack.com/archives/C0123ABCDEF/p1700000100000100"
         "?thread_ts=1700000000.123456&cid=C0123ABCDEF")


@pytest.mark.parametrize("url", [PARENT, REPLY, PARENT + "?cid=C0123ABCDEF", PARENT.replace("C0123ABCDEF", "c0123abcdef")])
def test_parent_and_reply_links_share_the_thread_identity(url):
    assert origin_key_from_url(url) == "slack:C0123ABCDEF/1700000000.123456"


def test_slack_thread_ref_gives_connector_arguments():
    assert slack_thread_ref(REPLY) == {"channel_id": "C0123ABCDEF", "thread_ts": "1700000000.123456",
                                       "origin_key": "slack:C0123ABCDEF/1700000000.123456"}


@pytest.mark.parametrize("bad", ["https://acme.slack.com/archives/C0123ABCDEF",
                                 "https://acme.slack.com/client/T123/C0123ABCDEF"])
def test_non_message_slack_links_rejected(bad):
    with pytest.raises(ValidationError):
        origin_key_from_url(bad)


def test_cli_slack_ref(home, capsys):
    import json

    from pmi.cli import main
    assert main(["--home", str(home), "--json", "slack-ref", REPLY]) == 0
    assert json.loads(capsys.readouterr().out)["thread_ts"] == "1700000000.123456"
