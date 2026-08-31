"""Regression tests for doc_structuring.

Pure-ASCII only; no network access; no model downloads.
Covers the fixes that historically rotted between local working copies
and pushed snapshots (heading filtering, unnumbered front-matter
numbering, DOCX table recovery, FTS/hybrid semantics).
"""
from __future__ import annotations

import logging

import pytest

from doc_structuring import __version__
from doc_structuring.config import AppConfig, _default_bad_heading_keywords
from doc_structuring.parser import parse_into_chunks
from doc_structuring.utils import sanitize_filename


# ---------------------------------------------------------------------------
# Heading / bad-keyword filtering (parser)
# ---------------------------------------------------------------------------

def test_bare_release_section_is_not_swallowed():
    """Regression: bare 'release' used to be a bad keyword and silently
    dropped legitimate numbered sections such as '5.5 Release
    Remediation'."""
    lines = [
        (1, "5.5 Release Remediation"),
        (1, "The system shall recover from a release abort within 100 ms."),
    ]
    chunks = parse_into_chunks(lines, "spec.pdf")
    numbered = [c for c in chunks if c["number"] != "0"]
    assert any(c["number"] == "5.5" and "release remediation" in c["title"].lower()
               for c in numbered), [c["number"] for c in chunks]


def test_release_history_still_filtered():
    """Phrases like 'Release History' must still be rejected as headings."""
    lines = [
        (1, "4. Release History"),
        (1, "Some body text under the release history section."),
    ]
    chunks = parse_into_chunks(lines, "spec.pdf")
    assert not any(c["number"] == "4" and "release history" in c["title"].lower()
                   for c in chunks)


def test_default_bad_keywords_no_bare_release():
    assert "release" not in _default_bad_heading_keywords()
    assert "release history" in _default_bad_heading_keywords()


def test_unnumbered_frontmatter_numbering_via_anchor():
    """Prepass: a run of unnumbered H1s before an explicitly numbered H2
    is numbered so its last member becomes the anchor's parent major."""
    lines = [
        (1, "# Purpose"),
        (1, "# Scope"),
        (1, "# Abbreviations and Definitions"),
        (1, "## 3.1 Detailed behavior"),
        (1, "Body text."),
    ]
    chunks = parse_into_chunks(lines, "manual.docx")
    by_title = {c["title"].lower(): c["number"] for c in chunks}
    assert by_title.get("purpose") == "1"
    assert by_title.get("scope") == "2"
    assert by_title.get("abbreviations and definitions") == "3"
    assert by_title.get("detailed behavior") == "3.1"


def test_unnumbered_table_of_contents_rejected():
    """'Table of Contents' is navigation boilerplate, not a section."""
    lines = [
        (1, "# Table of Contents"),
        (1, "## 1. Overview"),
        (1, "Text."),
    ]
    chunks = parse_into_chunks(lines, "manual.docx")
    assert not any(c["title"].lower() == "table of contents" and c["number"] != "0"
                   for c in chunks)


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------

def test_sanitize_filename_strips_control_chars():
    # Tab-leader artifacts from Word TOC styles must not survive into
    # filenames (they crash Windows file I/O).
    cleaned = sanitize_filename("2.1\t\tSection\tTitle")
    assert "\t" not in cleaned and "\x00" not in cleaned


# ---------------------------------------------------------------------------
# DOCX table recovery (extractor)
# ---------------------------------------------------------------------------

def test_docx_table_rendered_as_markdown(tmp_path):
    """Tables are direct children of <w:body>; iterating doc.paragraphs
    alone drops them entirely. Regression: table content must appear."""
    from docx import Document
    from doc_structuring.extractors.docx import DocxExtractor

    path = tmp_path / "with_table.docx"
    doc = Document()
    doc.add_paragraph("## 1. Compliance Matrix")
    table = doc.add_table(rows=2, cols=2)
    for i, (col1, col2) in enumerate([("REQ-1", "Pass"), ("REQ-2", "Fail")]):
        table.rows[i].cells[0].text = col1
        table.rows[i].cells[1].text = col2
    doc.save(str(path))

    lines = DocxExtractor().extract_lines(str(path), temp_dir=str(tmp_path))
    text = "\n".join(line for _, line in lines)
    assert "REQ-1" in text and "REQ-2" in text
    assert "Pass" in text
    # Markdown table separator emitted by _render_table_markdown
    assert any("| --- " in line or "|---" in line for _, line in lines)


# ---------------------------------------------------------------------------
# SQLite: FTS search, min_fts_rank, hybrid fallback warning
# ---------------------------------------------------------------------------

def _make_config(tmp_path) -> AppConfig:
    # AppConfig.base_dir is a Path (its properties do base_dir / "documents.db").
    return AppConfig(base_dir=tmp_path)


def _seed(config: AppConfig) -> int:
    from doc_structuring import database

    database.init_db(config)
    chunks = [
        {"number": "1", "title": "Power Management", "content": "The ASPM L1 substates are enabled for the root port.",
         "page_start": 1, "source": "t.pdf"},
        {"number": "2", "title": "Clock Gating", "content": "Idle clock gating is applied per logic block.",
         "page_start": 2, "source": "t.pdf"},
    ]
    return database.save_document("t.pdf", chunks, config=config)


def test_fts_search_and_min_fts_rank(tmp_path, caplog):
    config = _make_config(tmp_path)
    doc_id = _seed(config)
    from doc_structuring import database

    with caplog.at_level(logging.WARNING):
        results = database.hybrid_search(
            "ASPM substates", document_id=doc_id, top_k=5, config=config
        )
    # No embeddings were ever written, so the hybrid search must have
    # degraded to FTS-only AND warned about it.
    assert any("no embeddings" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]
    assert len(results) >= 1
    assert "aspm" in results[0]["content"].lower()

    # min_fts_rank=1 keeps only the top-ranked FTS hit.
    tight = database.hybrid_search(
        "clock", document_id=doc_id, top_k=5, min_fts_rank=1, config=config
    )
    assert all(r["fts_rank"] == 1 for r in tight)

    # min_fts_rank=0 drops everything (no rank satisfies rank <= 0).
    none = database.hybrid_search(
        "clock", document_id=doc_id, top_k=5, min_fts_rank=0, config=config
    )
    assert none == []


def test_delete_cascades_tags_and_rows(tmp_path):
    config = _make_config(tmp_path)
    from doc_structuring import database

    doc_id = _seed(config)
    database.delete_document(doc_id, config=config)
    assert database.get_document_embeddings(document_id=doc_id, config=config) == {}
    res = database.search_chunks("aspm", config=config)
    assert all(r.get("document_id") != doc_id for r in res)


# ---------------------------------------------------------------------------
# version consistency
# ---------------------------------------------------------------------------

def test_version_matches_pyproject():
    """Version single source of truth: __init__.py defines __version__,
    and pyproject.toml must take it dynamically (no duplicate literal)."""
    import re
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    init_src = (root / "src" / "doc_structuring" / "__init__.py").read_text(
        encoding="utf-8"
    )

    m = re.search(r'^__version__\s*=\s*"([^"]+)"', init_src, re.MULTILINE)
    assert m, "__init__.py missing __version__"
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", m.group(1)), f"not semver-ish: {m.group(1)}"

    # pyproject must source version dynamically from __init__.py ...
    assert re.search(r'^dynamic\s*=\s*\["version"\]', pyproject, re.MULTILINE), (
        "pyproject.toml must declare version as dynamic"
    )
    # ... and no stale hardcoded version remains.
    assert not re.search(r'^version\s*=\s*"', pyproject, re.MULTILINE), (
        "pyproject.toml still hardcodes version (single-source broken)"
    )

    # and the runtime value agrees with the file.
    assert __version__ == m.group(1)
