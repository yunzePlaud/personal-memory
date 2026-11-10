import pytest

from pmi.errors import ValidationError
from pmi.parsers import parse_bytes, parse_file, parse_markdown_text
from tests.helpers import make_docx, make_empty_pdf, make_pdf

MD = """---
title: Project A 周会
date: 2026-10-01
attendees: [Alice, Bob]
---
# 会议记录

## 决策
- 不采用方案 B
"""


def test_markdown_frontmatter_and_title(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text(MD, encoding="utf-8")
    doc = parse_file(p)
    assert doc.title == "Project A 周会"
    assert doc.frontmatter["date"] == "2026-10-01"
    assert doc.frontmatter["attendees"] == ["Alice", "Bob"]
    assert doc.mime == "text/markdown"
    assert doc.text.startswith("---\ntitle: Project A 周会")
    assert "- 不采用方案 B" in doc.text


def test_markdown_title_fallbacks():
    assert parse_bytes("# Heading One\nbody".encode(), "x.md").title == "Heading One"
    assert parse_bytes(b"just text", "my-notes.txt").title == "my-notes"


def test_markdown_bad_yaml_keeps_text():
    text = "---\ntitle: [unclosed\n---\nbody line\n"
    fm, body = parse_markdown_text(text)
    assert fm == {} and body == text
    doc = parse_bytes(text.encode(), "bad.md")
    assert doc.frontmatter == {} and "body line" in doc.text


def test_gbk_text_decodes(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("项目上线时间调整".encode("gbk"))
    assert parse_file(p).text == "项目上线时间调整"


def test_pdf_text_extracted():
    doc = parse_bytes(make_pdf(["Hello PMI", "Launch in December"]), "spec.pdf")
    assert "Hello PMI" in doc.text and "Launch in December" in doc.text
    assert doc.mime == "application/pdf"


def test_pdf_without_text_rejected():
    with pytest.raises(ValidationError, match="no extractable text"):
        parse_bytes(make_empty_pdf(), "scan.pdf")


def test_docx_paragraphs_and_table(tmp_path):
    p = tmp_path / "prd.docx"
    make_docx(p, ["需求背景", "目标：提升转化"], table=[["A", "B"], ["1", "2"]])
    doc = parse_file(p)
    assert "需求背景" in doc.text and "目标：提升转化" in doc.text
    assert "A | B" in doc.text and "1 | 2" in doc.text


def test_unsupported_extension_rejected():
    with pytest.raises(ValidationError, match="unsupported file type: .png"):
        parse_bytes(b"\x89PNG", "a.png")


def test_undecodable_text_rejected():
    with pytest.raises(ValidationError, match="cannot decode"):
        parse_bytes(b"\xff\xfe\xfa\xfb\x81\x30", "x.txt")
