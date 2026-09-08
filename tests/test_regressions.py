"""Regression tests for doc_structuring.

Pure-ASCII only; no network access; no model downloads.
Covers the fixes that historically rotted between local working copies
and pushed snapshots (heading filtering, unnumbered front-matter
numbering, DOCX table recovery, FTS/hybrid semantics).
"""
from __future__ import annotations

import logging
import sqlite3

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


def _seed_ranking(config: AppConfig) -> tuple[int, int]:
    """Seed two documents: an older doc whose single chunk contains the
    probe word far more often, and a newer doc that mentions it once.

    Returns (older_doc_id, newer_doc_id).
    """
    from doc_structuring import database

    old_id = database.save_document("old.pdf", [
        {"number": "1", "title": "Power Management",
         "content": "The watchdog timer is configured. The watchdog interrupt "
                    "handler calls the watchdog service routine; the watchdog "
                    "state machine advances on each watchdog tick.",
         "page_start": 1, "source": "old.pdf"},
        {"number": "2", "title": "Clock Gating",
         "content": "Idle clock gating is applied per logic block.",
         "page_start": 2, "source": "old.pdf"},
    ], config=config)
    new_id = database.save_document("new.pdf", [
        {"number": "1", "title": "Boot Flow",
         "content": "The watchdog reset timeout occurs when the watchdog is "
                    "not serviced in time.",
         "page_start": 1, "source": "new.pdf"},
    ], config=config)
    return old_id, new_id


def test_fts_ranking_is_relevance_not_recency(tmp_path):
    """F01 regression: results must be ordered by BM25 relevance, not by
    document upload time.  A more recently imported document mentioning
    the probe term once must not outrank an older document mentioning it
    many times."""
    from doc_structuring import database

    config = _make_config(tmp_path)
    old_id, new_id = _seed_ranking(config)

    results = database.search_chunks("watchdog", config=config)
    assert len(results) == 2
    assert results[0]["document_id"] == old_id, (
        f"most relevant chunk must rank first, got order "
        f"{[(r['document_id'], r['snippet'][:40]) for r in results]}"
    )
    assert results[1]["document_id"] == new_id


def test_import_order_does_not_change_relevance_ranking(tmp_path):
    """F01 acceptance criterion: reversing the import order of the same
    corpus must not change the relevance ranking."""
    from doc_structuring import database

    old_chunks = [
        {"number": "1", "title": "Power Management",
         "content": "The watchdog timer is configured. The watchdog interrupt "
                    "handler calls the watchdog service routine; the watchdog "
                    "state machine advances on each watchdog tick.",
         "page_start": 1, "source": "old.pdf"},
    ]
    new_chunks = [
        {"number": "1", "title": "Boot Flow",
         "content": "The watchdog reset timeout occurs when the watchdog is "
                    "not serviced in time.",
         "page_start": 1, "source": "new.pdf"},
    ]

    def _import_and_rank(order: list) -> list:
        base = tmp_path / (order[0] + "-" + order[1])
        base.mkdir(parents=True, exist_ok=True)
        cfg = _make_config(base)
        database.save_document(order[0],
                               [dict(c) for c in (old_chunks if order[0] == "old.pdf" else new_chunks)],
                               config=cfg)
        database.save_document(order[1],
                               [dict(c) for c in (old_chunks if order[1] == "old.pdf" else new_chunks)],
                               config=cfg)
        # rank by document NAME (doc ids differ between the two DBs)
        conn = sqlite3.connect(str(cfg.db_path))
        conn.row_factory = sqlite3.Row
        id2name = dict(conn.execute("SELECT id, filename FROM documents").fetchall())
        conn.close()
        return [id2name[r["document_id"]]
                for r in database.search_chunks("watchdog", config=cfg)]

    forward = _import_and_rank(["old.pdf", "new.pdf"])
    backward = _import_and_rank(["new.pdf", "old.pdf"])
    assert forward == backward, (
        f"ranking must be independent of import order: {forward} vs {backward}"
    )
    # the high-frequency doc ranks first in both import orders
    assert forward[0] == "old.pdf", (
        f"expected old.pdf to rank first, got {forward}"
    )


def test_fts5_escape_token_punctuation_contract():
    """F01 token contract: punctuation becomes whitespace so query terms
    map to the same unicode61 tokens as the stored text."""
    from doc_structuring import database

    assert database._fts5_escape_token("PCI-E") == '"PCI" "E"'
    assert database._fts5_escape_token("1.2.3") == '"1" "2" "3"'
    assert database._fts5_escape_token("l1.sub") == '"l1" "sub"'
    assert database._fts5_escape_token("!!!") is None
    assert database._fts5_escape_token("电源管理") == '"电源管理"'
    assert database._fts5_escape_token("") is None


def test_punctuated_identifier_query_hits_tokenized_content(tmp_path):
    """F01 end-to-end: an identifier typed exactly as it appears in the
    document must match the stored text (the unicode61 tokenizer split
    both the stored text and — via the punctuation→whitespace rule — the
    query at the same spots).  The old strip-everything rule turned
    'PCI-Express' into the unmatchable token 'PCIEpress'."""
    from doc_structuring import database

    config = _make_config(tmp_path)
    database.save_document("pci.pdf", [
        {"number": "1", "title": "Link Training",
         "content": "The PCI-Express link trains at Gen5 speeds.",
         "page_start": 1, "source": "pci.pdf"},
        {"number": "2", "title": "SPI",
         "content": "The SPI flash is polled for readiness.",
         "page_start": 2, "source": "pci.pdf"},
    ], config=config)

    hits = database.search_chunks("PCI-Express", config=config)
    assert hits, "hyphenated identifier query matched nothing (F01)"
    assert any(
        "PCI" in r["snippet"].upper() and "EXPRESS" in r["snippet"].upper()
        for r in hits
    ), f"expected the Link Training chunk to match, got {hits}"


def test_fts_search_limit_none_is_capped(tmp_path):
    """F01 (LIKE fallback + FTS): a missing explicit limit must fall back
    to the configured search_limit, never an unbounded row return."""
    from doc_structuring import database

    config = _make_config(tmp_path)
    chunks = [
        {"number": str(i), "title": f"Topic {i}",
         "content": f"Watchdog behavior note number {i} in this document.",
         "page_start": i, "source": "many.pdf"}
        for i in range(1, 11)
    ]
    database.save_document("many.pdf", chunks, config=config)

    # Default limit == config.search_limit (100 here), all 10 match → 10 rows.
    default = database.search_chunks("watchdog", config=config, limit=None)
    assert len(default) == 10

    # Explicit limit is still honored.
    tight = database.search_chunks("watchdog", config=config, limit=3)
    assert len(tight) == 3


def test_next_neighbor_truncation_is_still_returned(tmp_path):
    """F02 regression: when the budget forces truncation of the NEXT
    neighbor, the truncated dict must be written back to
    result['next_chunk'] (it used to be silently dropped)."""
    from doc_structuring import database

    config = _make_config(tmp_path)
    database.save_document("n.pdf", [
        {"number": "1", "title": "A", "content": "Short alpha.",
         "page_start": 1, "source": "n.pdf"},
        {"number": "2", "title": "B", "content": "Target beta body.",
         "page_start": 1, "source": "n.pdf"},
        {"number": "3", "title": "C",
         "content": "Gamma " + "gammaword " * 120,  # long next neighbor
         "page_start": 2, "source": "n.pdf"},
    ], config=config)
    target_id = 2
    result = database.get_chunk_with_neighbors(
        target_id, include_neighbors=True, max_context_tokens=120, config=config
    )
    assert "previous_chunk" in result
    assert "next_chunk" in result, (
        "truncated next neighbor was dropped from the result (F02)"
    )
    # it must be the truncated banner version, not the full text
    full = database.get_chunk(3, config=config)["content"]
    assert result["next_chunk"]["content"] != full
    assert "truncated due to token budget" in result["next_chunk"]["content"]


def test_budget_too_small_for_target_is_reported(tmp_path):
    """F02 contract: an explicit budget must at least cover the target
    chunk's own estimate or the CLI path reports ERROR_BUDGET_TOO_SMALL."""
    from doc_structuring import database
    from doc_structuring.utils import estimate_tokens

    config = _make_config(tmp_path)
    database.save_document("b.pdf", [
        {"number": "1", "title": "T",
         "content": "Big target " + "tokenword " * 64,
         "page_start": 1, "source": "b.pdf"},
    ], config=config)
    target = database.get_chunk(1, config=config)
    assert estimate_tokens(target["content"]) > 100
    # the function still returns the (full) target — the CLI is the layer
    # that refuses with a clear error; keep the function's own contract:
    result = database.get_chunk_with_neighbors(
        1, include_neighbors=False, max_context_tokens=10, config=config
    )
    assert result["chunk"]["id"] == 1
    assert result["chunk"]["content"] == target["content"]


# ---------------------------------------------------------------------------
# SQLite: F07 atomic replace (save_document)
# ---------------------------------------------------------------------------

def _seed_small_document(config, filename="f07.pdf", marker="f07old"):
    from doc_structuring import database
    return database.save_document(filename, [
        {"number": "1", "title": "Old Section",
         "content": f"The {marker} body for the first revision.",
         "page_start": 1, "source": filename},
    ], config=config)


def test_failed_rewrite_keeps_previous_version(tmp_path, monkeypatch):
    """F07 regression: a failure while writing the NEW version's files must
    roll the whole transaction back — the previous version's rows, FTS
    entries, and files stay fully intact, and the half-written new
    output tree is removed."""
    import doc_structuring.database as database
    import builtins as _builtins
    real_open = _builtins.open

    config = _make_config(tmp_path)
    old_id = _seed_small_document(config)

    failed = {"flag": False}

    def fake_open(path, mode="r", *args, **kwargs):
        # Fail the first write of the rewrite (any file write of the new
        # version) and let everything else through.
        if "w" in str(mode) and "output" in str(path):
            if not failed["flag"]:
                failed["flag"] = True
                raise OSError("simulated disk failure during rewrite")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(_builtins, "open", fake_open)

    with pytest.raises(OSError, match="simulated disk failure"):
        database.save_document("f07.pdf", [
            {"number": "1", "title": "New Section",
             "content": "The completely different new body.",
             "page_start": 1, "source": "f07.pdf"},
        ], config=config)

    # Previous version must be fully intact in the DB.
    conn = sqlite3.connect(str(config.db_path))
    conn.row_factory = sqlite3.Row
    docs = [dict(r) for r in conn.execute(
        "SELECT id, filename, status FROM documents")]
    assert len(docs) == 1, f"expected exactly the old doc, got {docs}"
    assert docs[0]["id"] == old_id
    assert docs[0]["status"] == "success"

    # Old FTS entry for the old body must still be searchable.
    n = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'f07old'"
    ).fetchone()[0]
    # The new (rolled-back) insert must have left no FTS trace.
    n_new = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'completely'"
    ).fetchone()[0]
    conn.close()
    assert n >= 1, "old FTS entry lost by the rollback"
    assert n_new == 0, "rolled-back insert left FTS garbage"

    # Old physical files intact; no half-written new output tree remains.
    out = config.output_dir
    old_dir = out / str(old_id)
    assert old_dir.is_dir() and any(old_dir.rglob("*.md")), \
        "previous version's files were damaged"
    stray = [p.name for p in out.iterdir()
             if p.is_dir() and p.name.isdigit() and int(p.name) != old_id]
    assert stray == [], f"partial new-version tree left behind: {stray}"

    # And a normal re-run afterwards must succeed (no lock damage).
    monkeypatch.setattr(_builtins, "open", real_open)
    new_id = database.save_document("f07.pdf", [
        {"number": "1", "title": "New Section",
         "content": "The completely different new body.",
         "page_start": 1, "source": "f07.pdf"},
    ], config=config)
    assert new_id != old_id
    # old version is now (post-commit) cleaned up
    assert not (out / str(old_id)).exists(), "old output tree not removed"
    conn = sqlite3.connect(str(config.db_path))
    n_docs = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    conn.close()
    assert n_docs == 1


def test_rewrite_is_atomic_swap(tmp_path):
    """F07 happy path: on success the old rows AND files are replaced,
    no duplicate filename row survives, and new content is searchable."""
    import doc_structuring.database as database

    config = _make_config(tmp_path)
    old_id = _seed_small_document(config)

    new_id = database.save_document("f07.pdf", [
        {"number": "1", "title": "New Section",
         "content": "The atomic replacement body.",
         "page_start": 1, "source": "f07.pdf"},
    ], config=config)

    assert new_id != old_id
    conn = sqlite3.connect(str(config.db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, filename FROM documents WHERE filename = 'f07.pdf'")]
    assert [r["id"] for r in rows] == [new_id], \
        f"old doc row survived the atomic swap: {rows}"
    n_new = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'atomic'"
    ).fetchone()[0]
    n_old = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'first'"
    ).fetchone()[0]
    conn.close()
    assert n_new == 1
    assert n_old == 0, "old FTS entry survived the swap"

    out = config.output_dir
    assert not (out / str(old_id)).exists()
    assert (out / str(new_id) / "toc.json").is_file()
    # new chunk file exists on disk with a real (non-placeholder) file_path
    rows = database.search_chunks("atomic", config=config)
    assert rows and rows[0]["document_id"] == new_id


def test_rewrite_tags_follow_new_version(tmp_path):
    """F07: tags attached to the re-parse apply to the NEW document row,
    not lost on the rolled/deleted old one."""
    import doc_structuring.database as database

    config = _make_config(tmp_path)
    old_id = _seed_small_document(config, marker="f07tagold")
    new_id = database.save_document("f07.pdf", [
        {"number": "1", "title": "New Section",
         "content": "The tagged re-parse body.",
         "page_start": 1, "source": "f07.pdf"},
    ], config=config, tags=["demo", "amd"])

    conn = sqlite3.connect(str(config.db_path))
    conn.row_factory = sqlite3.Row
    tags = [r["tag"] for r in conn.execute(
        "SELECT tag FROM document_tags WHERE document_id = ? ORDER BY tag",
        (new_id,))]
    n = conn.execute(
        "SELECT count(*) FROM document_tags WHERE document_id = ?",
        (old_id,)).fetchone()[0]
    conn.close()
    assert sorted(tags) == ["amd", "demo"]
    assert n == 0, "tags should not linger on the deleted old doc"


# ---------------------------------------------------------------------------
# SQLite: F03 page provenance (physical pages, unknown for no-pagination)
# ---------------------------------------------------------------------------

def test_parser_tracks_physical_page_span():
    """F03: a chunk's page_start/page_end span its first to last line's
    physical page — no heading-bookmark guessing involved."""
    lines = [
        (7, "## 3.1 Power Sequence"),
        (7, "The rails power up in order."),
        (8, "A continuation sentence on the next physical page."),
        (9, "And the tail on a third page."),
    ]
    chunks = parse_into_chunks(lines, "manual.pdf")
    assert len(chunks) == 1
    c = chunks[0]
    assert c["page_start"] == 7
    assert c["page_end"] == 9


def test_parser_pages_none_when_extractor_supplies_none():
    """F03: DOCX/code lines carry page None; chunks must expose None,
    never a fabricated page 1."""
    lines = [
        (None, "## 1. Scope"),
        (None, "This document defines scope."),
    ]
    chunks = parse_into_chunks(lines, "spec.docx")
    assert chunks
    for c in chunks:
        assert c["page_start"] is None
        assert c["page_end"] is None


def test_v6_to_v7_migration_makes_page_start_nullable(tmp_path):
    """F03 migration: an old schema (page_start NOT NULL, schema_version 6)
    is upgraded in place — column becomes nullable, page_end appears, and
    existing rows keep their data and rowids."""
    from doc_structuring import database

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            upload_time TEXT NOT NULL,
            chunk_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success'
        );
    """)
    cursor.execute("""
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            section_number TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            file_path TEXT,
            cross_references TEXT,
            doc_type TEXT DEFAULT 'spec',
            symbol_name TEXT,
            line_start INTEGER,
            line_end INTEGER,
            FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        );
    """)
    cursor.execute(
        "INSERT INTO documents (filename, upload_time, chunk_count, status) "
        "VALUES ('legacy.pdf', '2026-01-01 00:00:00', 1, 'success')"
    )
    cursor.execute(
        "INSERT INTO chunks (document_id, section_number, title, content, "
        "page_start, file_path) VALUES (1, '1', 'Legacy', 'Legacy content', 5, '')"
    )
    cursor.execute(
        "CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT);"
    )
    cursor.execute(
        "INSERT INTO _meta (key, value) VALUES ('schema_version', '6');"
    )
    conn.commit()
    conn.close()

    # init_db against the old db must run the v6->v7 migration.
    # AppConfig.db_path resolves to <base_dir>/documents.db, so install
    # the pre-made legacy db there first.
    (tmp_path / "documents.db").write_bytes(db_path.read_bytes())
    config = _make_config(tmp_path)
    database.init_db(config)

    # nullable now?
    info = dict(
        (r[1], r[3])
        for r in sqlite3.connect(str(config.db_path))
        .execute("PRAGMA table_info(chunks);")
        .fetchall()
    )
    assert info["page_start"] == 0, "page_start should no longer be NOT NULL"
    assert "page_end" in info, "page_end column missing after migration"
    row = sqlite3.connect(str(config.db_path)).execute(
        "SELECT id, page_start FROM chunks"
    ).fetchone()
    assert row == (1, 5), f"legacy row value not preserved: {row}"

    # FTS triggers must exist after the table rebuild (triggers fire
    # transactionally; the FTS setup re-creates them after init_db returns).
    trigger_count = sqlite3.connect(str(config.db_path)).execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name='chunks'"
    ).fetchone()[0]
    assert trigger_count >= 2, "FTS triggers missing after rebuild"


def test_xml_page_number_unknown_true_for_none():
    """F03 contract: format_chunk_to_xml emits <page_number unknown="true"/>
    when the chunk has no page, and a start/end span for cross-page chunks."""
    from doc_structuring.utils import format_chunk_to_xml

    unknown = format_chunk_to_xml({"chunk": {
        "section_number": "1", "title": "T", "content": "c",
        "page_start": None, "page_end": None,
    }})
    assert '<page_number unknown="true" />' in unknown
    assert "<page_number>1</page_number>" not in unknown

    span = format_chunk_to_xml({"chunk": {
        "section_number": "1", "title": "T", "content": "c",
        "page_start": 12, "page_end": 14,
    }})
    assert '<page_number start="12" end="14" />' in span

    single = format_chunk_to_xml({"chunk": {
        "section_number": "1", "title": "T", "content": "c",
        "page_start": 3, "page_end": 3,
    }})
    assert "<page_number>3</page_number>" in single


def test_save_document_roundtrips_page_span_and_none(tmp_path):
    """E2E F03: page spans survive save_document; None pages stay None
    (not coerced to 1) and come back through get_document_toc / search."""
    from doc_structuring import database

    config = _make_config(tmp_path)
    doc_id = database.save_document(
        "pages.pdf",
        [
            {"number": "1", "title": "First", "content": "Alpha content.",
             "page_start": 2, "page_end": 4, "source": "pages.pdf"},
            {"number": "2", "title": "Second", "content": "Beta content.",
             "page_start": None, "page_end": None, "source": "pages.pdf"},
        ],
        config=config,
    )
    assert doc_id is not None
    rows = database.get_document_toc(doc_id, config=config)
    by_num = {r["section_number"]: r for r in rows}
    assert by_num["1"]["page_start"] == 2
    assert by_num["1"]["page_end"] == 4
    assert by_num["2"]["page_start"] is None
    assert by_num["2"]["page_end"] is None


def test_docx_extractor_emits_unknown_page(tmp_path):
    """F03: the DOCX extractor must emit page None (not a fake 1), and that
    None must survive parse_into_chunks and save_document.  DOCX has no
    reliable page map, so any page number it invents poisons the XML
    grounding contract."""
    from docx import Document
    from doc_structuring import database
    from doc_structuring.extractors.docx import DocxExtractor
    from doc_structuring.parser import parse_into_chunks

    docx_path = tmp_path / "mini.docx"
    d = Document()
    d.add_heading("1. Scope", level=1)
    d.add_paragraph("This document defines the scope of the controller.")
    d.add_heading("2. Flash", level=1)
    d.add_paragraph("Commands go over EC port 80h.")
    d.save(str(docx_path))

    lines = DocxExtractor().extract_lines(str(docx_path), temp_dir=str(tmp_path))
    assert lines, "extractor produced no lines"
    fabricated = [page for page, _line in lines if page is not None]
    assert not fabricated, f"DOCX lines carry fabricated pages: {fabricated}"

    chunks = parse_into_chunks(lines, "mini.docx")
    assert chunks, "parser produced no chunks"
    for c in chunks:
        assert c["page_start"] is None, f"chunk {c['number']} got a page"
        assert c["page_end"] is None

    config = _make_config(tmp_path)
    doc_id = database.save_document("mini.docx", chunks, config=config)
    assert doc_id is not None
    bad = [
        (r["section_number"], r["page_start"])
        for r in database.get_document_toc(doc_id, config=config)
        if r["page_start"] is not None
    ]
    assert not bad, f"DOCX chunks stored with pages: {bad}"


# ---------------------------------------------------------------------------
# version consistency
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# F04: tag-scoped search (--tags), AND semantics, applied to BOTH legs
# ---------------------------------------------------------------------------

def _seed_tagged(config, filename, tags):
    from doc_structuring import database
    database.init_db(config)
    chunks = [
        {"number": "1", "title": "Overview",
         "content": "Shared word about ASPM power states.",
         "page_start": 1, "source": filename},
    ]
    return database.save_document(filename, chunks, config=config, tags=tags)


def test_tag_filter_single_tag(tmp_path):
    config = _make_config(tmp_path)
    from doc_structuring import database
    amd_id = _seed_tagged(config, "amd_spec.pdf", ["amd", "spec"])
    intel_id = _seed_tagged(config, "intel_spec.pdf", ["intel", "spec"])
    assert intel_id != amd_id

    # Single tag 'amd' must return only the amd document's chunk.
    res = database.search_chunks("ASPM", tag_filter=["amd"], config=config)
    assert res, "expected at least one amd-tagged result"
    assert all(r["document_id"] == amd_id for r in res), res
    assert any(r["document_name"] == "amd_spec.pdf" for r in res)

    # Unscoped search returns both documents (proves the filter actually
    # narrows, i.e. the baseline was not already single-document).
    unscoped = database.search_chunks("ASPM", config=config)
    assert {r["document_id"] for r in unscoped} == {amd_id, intel_id}


def test_tag_filter_and_semantics(tmp_path):
    config = _make_config(tmp_path)
    from doc_structuring import database
    doc_amd = _seed_tagged(config, "a.pdf", ["amd", "rap"])
    _seed_tagged(config, "b.pdf", ["amd", "notrap"])
    doc_intel_rap = _seed_tagged(config, "c.pdf", ["intel", "rap"])

    # AND: both tags required -> only doc_amd (has amd + rap).
    res = database.search_chunks(
        "ASPM", tag_filter=["amd", "rap"], config=config
    )
    assert res
    assert all(r["document_id"] == doc_amd for r in res), res

    # Relaxation: tag 'rap' alone matches doc_amd AND c.pdf.
    res2 = database.search_chunks("ASPM", tag_filter=["rap"], config=config)
    assert {r["document_id"] for r in res2} == {doc_amd, doc_intel_rap}, res2


def test_tag_filter_case_insensitive_and_no_match(tmp_path):
    config = _make_config(tmp_path)
    from doc_structuring import database
    amd_id = _seed_tagged(config, "amd.pdf", ["AM", "D "])
    # Case-insensitive + whitespace-tolerant match.
    res = database.search_chunks("ASPM", tag_filter=["aM", "d"], config=config)
    assert res and all(r["document_id"] == amd_id for r in res)

    # No document carries tag 'nonexistent' -> empty result (not None, no
    # results). Empty/whitespace filter list -> no constraint (None).
    empty = database.search_chunks("ASPM", tag_filter=["nonexistent"], config=config)
    assert empty == []
    # Whitespace-only entries are dropped -> behaves as unscoped.
    unscoped = database.search_chunks("ASPM", tag_filter=[" ", ""], config=config)
    assert {r["document_id"] for r in unscoped} == {amd_id}


def test_tag_filter_applies_to_vector_leg(tmp_path, monkeypatch):
    """The RRF union must not reintroduce out-of-scope chunks: the vector
    candidate set is narrowed by the same tag filter as the FTS leg."""
    import numpy as np
    config = _make_config(tmp_path)
    from doc_structuring import database
    amd_id = _seed_tagged(config, "amd.pdf", ["amd"])
    intel_id = _seed_tagged(config, "intel.pdf", ["intel"])

    # Get the two chunk ids (one chunk per seeded document) and write
    # synthetic embeddings directly into the DB (no model needed).
    amd_chunk = database.search_chunks("ASPM", document_id=amd_id, config=config)[0]["id"]
    intel_chunk = database.search_chunks("ASPM", document_id=intel_id, config=config)[0]["id"]
    dim = 4
    database.save_embeddings(
        {amd_chunk: np.ones(dim, dtype=np.float32),
         intel_chunk: np.zeros(dim, dtype=np.float32)},
        config=config,
    )

    # Fake fastembed: query vector aligned with the amd embedding
    # (same dim=4 as the stored synthetic vectors).
    monkeypatch.setattr(
        "doc_structuring.embeddings.generate_embeddings",
        lambda texts, *a, **k: [np.ones(dim, dtype=np.float32)],
    )

    res = database.hybrid_search(
        "ASPM", tag_filter=["amd"], top_k=5, config=config
    )
    # Only the amd chunk may survive the fusion.
    assert res, "expected at least one fused result"
    assert all(r["id"] == amd_chunk or r["document_id"] == amd_id for r in res), res
    assert intel_chunk not in {r["id"] for r in res}

    # And the unscoped hybrid DOES surface the intel chunk (vec leg active).
    unscoped = database.hybrid_search("ASPM", top_k=5, config=config)
    assert intel_chunk in {r["id"] for r in unscoped}


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
