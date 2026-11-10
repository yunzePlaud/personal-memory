import pytest

from pmi.errors import ValidationError
from pmi.textnorm import (content_key, estimate_tokens, find_quote, fts_tokens, normalize_slot,
                          normalize_text, query_terms, slugify)


def test_fts_tokens_cjk_bigrams():
    assert fts_tokens("上线时间") == "上线 线时 时间"
    assert fts_tokens("Project A 上线") == "project a 上线"
    assert fts_tokens("的") == "的"
    assert fts_tokens("v2版本上线") == "v2 版本 本上 上线"


def test_query_terms_drop_stopwords():
    assert query_terms("为什么 Project A 不采用方案 B") == ["project", "不采", "采用", "用方", "方案", "b"]
    assert query_terms("为什么") == []


def test_find_quote_whitespace_insensitive():
    text = "决策：\n- 不采用   方案 B，\n原因是成本高。"
    s, e = find_quote(text, "不采用 方案 B， 原因是成本高")
    assert text[s:e].startswith("不采用") and text[s:e].endswith("成本高")
    assert find_quote(text, "完全不存在") is None
    assert find_quote(text, "   ") is None


def test_content_key_ignores_case_space_punct():
    assert content_key("Launch on Nov 15.") == content_key("launch on  nov 15")
    assert content_key("上线：11 月") == content_key("上线11月")
    assert content_key("A") != content_key("B")


def test_normalize_text_rules():
    assert normalize_text("a\r\nb  \n\n\n\nc\x00") == "a\nb\n\nc"
    assert normalize_text("﻿  x\t y  ") == "x\t y"


def test_estimate_tokens():
    assert estimate_tokens("上线时间") == 4
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0


def test_normalize_slot():
    assert normalize_slot(" Launch Date ") == "launch-date"
    assert normalize_slot("project-a/上线时间") == "project-a/上线时间"
    with pytest.raises(ValidationError):
        normalize_slot("a b?*")
    with pytest.raises(ValidationError):
        normalize_slot("   ")


def test_slugify():
    assert slugify("Project A") == "project-a"
    assert slugify("项目 甲!") == "项目-甲"
