import pytest

from pmi.actors import Actor
from pmi.entities import (add_alias, find_entities_in_text, get_entity, list_entities, norm_name,
                          resolve_entity)
from pmi.errors import ValidationError

OWNER = Actor.owner()


def test_norm_name():
    assert norm_name("  Project   A ") == "project a"
    assert norm_name("ＰＲＯＪＥＣＴ Ａ") == "project a"


def test_resolve_creates_then_reuses(truth):
    a = resolve_entity(truth, "Project A", "project", actor=OWNER)
    b = resolve_entity(truth, "project  a", "project", actor=OWNER)
    assert a["id"] == b["id"] and a["name"] == "Project A"
    assert truth.execute("select count(*) from entities").fetchone()[0] == 1


def test_lookup_only_and_actor_required(truth):
    assert resolve_entity(truth, "Nobody", "person", create=False) is None
    with pytest.raises(ValueError):
        resolve_entity(truth, "Nobody", "person")


def test_alias_resolution(truth):
    e = resolve_entity(truth, "Project A", "project", actor=OWNER)
    add_alias(truth, e["id"], "PA", actor=OWNER)
    assert resolve_entity(truth, "pa", "project", create=False)["id"] == e["id"]
    assert get_entity(truth, e["id"])["aliases"] == ["PA"]


def test_alias_collision_rejected(truth):
    a = resolve_entity(truth, "Project A", "project", actor=OWNER)
    resolve_entity(truth, "Project B", "project", actor=OWNER)
    with pytest.raises(ValidationError):
        add_alias(truth, a["id"], "project b", actor=OWNER)


def test_find_entities_in_text(truth):
    pa = resolve_entity(truth, "Project A", "project", actor=OWNER)
    alice = resolve_entity(truth, "Alice", "person", actor=OWNER)
    found = {e["id"] for e in find_entities_in_text(truth, "关于 project a 和 Alice 的讨论")}
    assert found == {pa["id"], alice["id"]}


def test_find_entities_ascii_word_boundary(truth):
    resolve_entity(truth, "Bob", "person", actor=OWNER)
    assert find_entities_in_text(truth, "the bobsled team") == []
    assert len(find_entities_in_text(truth, "ask Bob.")) == 1


def test_find_entities_cjk_substring(truth):
    e = resolve_entity(truth, "推送平台", "system", actor=OWNER)
    assert [x["id"] for x in find_entities_in_text(truth, "接入现有推送平台的方案")] == [e["id"]]


def test_kind_scoped(truth):
    p = resolve_entity(truth, "Apollo", "project", actor=OWNER)
    q = resolve_entity(truth, "Apollo", "person", actor=OWNER)
    assert p["id"] != q["id"]
    assert [e["id"] for e in list_entities(truth, "person")] == [q["id"]]


def test_invalid_kind_rejected(truth):
    with pytest.raises(ValidationError):
        resolve_entity(truth, "X", "planet", actor=OWNER)
    with pytest.raises(ValidationError):
        resolve_entity(truth, "   ", "project", actor=OWNER)
