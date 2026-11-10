import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.policy import decide, is_protected, trust_rank

OWNER, AGENT, EXTRACTOR = Actor.owner(), Actor.agent("c"), Actor.extractor()
S = Settings()


def target(verification="source_backed", evidence_trust="agent", archived_by=None):
    return {"verification": verification, "evidence_trust": evidence_trust, "archived_by": archived_by}


def test_trust_rank():
    assert trust_rank(None) < trust_rank("external") < trust_rank("agent") < trust_rank("owner")


@pytest.mark.parametrize("t,new,expected", [
    (target("user_confirmed", "external"), "owner", True),
    (target("source_backed", "owner"), "external", True),
    (target("source_backed", "owner"), None, True),
    (target("source_backed", "owner"), "owner", False),
    (target("source_backed", "external"), "external", False),
    (target("inferred", None), None, False),
])
def test_is_protected(t, new, expected):
    assert is_protected(t, new) is expected


@pytest.mark.parametrize("op,actor,targets,new_trust,has_ev,expected", [
    ("create", OWNER, [], None, False, "apply"),
    ("create", AGENT, [], "external", True, "apply"),
    ("create", AGENT, [], None, False, "review"),
    ("create", EXTRACTOR, [], "owner", True, "apply"),
    ("update", AGENT, [target("source_backed", "agent")], "agent", True, "apply"),
    ("update", AGENT, [target("user_confirmed", "agent")], "owner", True, "review"),
    ("update", AGENT, [target("source_backed", "owner")], "external", True, "review"),
    ("supersede", AGENT, [target("source_backed", "external"), target("source_backed", "owner")],
     "external", True, "review"),
    ("supersede", EXTRACTOR, [target("source_backed", "owner")], "owner", True, "apply"),
    ("archive", AGENT, [target("user_confirmed", None)], None, False, "review"),
    ("archive", AGENT, [target("inferred", None)], None, False, "apply"),
    ("restore", AGENT, [target(archived_by="owner:cli")], None, False, "review"),
    ("restore", AGENT, [target(archived_by="agent:c")], None, False, "apply"),
    ("link", AGENT, [target("user_confirmed", "owner")], None, False, "apply"),
    ("confirm", AGENT, [target()], None, False, "deny"),
    ("forget", AGENT, [target()], None, False, "deny"),
    ("approve", EXTRACTOR, [], None, False, "deny"),
    ("forget", OWNER, [target("user_confirmed", "owner")], None, False, "apply"),
    ("supersede", OWNER, [target("user_confirmed", "owner")], None, False, "apply"),
])
def test_decide_matrix(op, actor, targets, new_trust, has_ev, expected):
    decision, reason = decide(op, actor, targets=targets, new_evidence_trust=new_trust,
                              has_evidence=has_ev, settings=S)
    assert decision == expected and reason


@pytest.mark.parametrize("setting", ["apply", "deny", "review"])
def test_inferred_create_follows_setting(setting):
    decision, _ = decide("create", AGENT, targets=[], new_evidence_trust=None, has_evidence=False,
                         settings=Settings(policy_inferred_create=setting))
    assert decision == setting if setting != "review" else decision == "review"


def test_unknown_op_denied():
    assert decide("explode", AGENT, targets=[], new_evidence_trust=None, has_evidence=False,
                  settings=S)[0] == "deny"
