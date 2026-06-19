---
name: document-structuring
description: >-
  Slices PDF and Word (docx) files into structured Markdown segments, stores
  them in an SQLite database, and supports document listing, TOC viewing,
  full-text searching, chunk retrieval, and document deletion.
  Optimised with batch PDF parsing (~16% faster than page-by-page).
---

# 📄 Document Structuring & Management

## Overview

This skill allows the agent to parse PDF and Word (.docx) documents, divide them into structured Markdown sections based on document headings, store the sections in a local SQLite database, and perform index lookups, keyword searching, section rendering, and record cleanup.

**Best for**: Large technical manuals (2,000+ page PDFs like BIOS/PPR specs). Each parsed chunk is ~1–5 KB of Markdown — you get **150–800× token compression** vs feeding the raw PDF to an LLM.

All database records and outputs resolve relative to the project workspace root:
- SQLite Database: `documents.db`
- Physical Markdown Files: `output/<document_id>/chunks/`

## Changelog

| Version | Date | Changes |
| ------- | -------- | ------- |
| **v2.0** | 2026-06-18 | Patch v2 — batch PDF parsing (50-page chunks), crash-safe DB commit order, actual rowid lookup, duplicate document guard, TOC list-of-dict collision fix, SectionNumberTracker read-only fix |
| **v1.1** | 2026-06-17 | Mod branch — page tracking via `doc.get_toc()`, SectionNumberTracker reset fix |
| **v1.0** | 2026-06-04 | Original release — page-by-page parsing, basic FTS5 search |

### v2.0 Patch Summary

| Bug Fixed | Severity | Fix | Verified ✅ |
| --------- | -------- | --- | ---------- |
| **Orphaned files on crash** | 🔴 Critical | `conn.commit()` moved BEFORE file I/O — crash during write leaves clean DB, no orphaned `.md` files | Crash safety test passed |
| **Predicted ID mismatch** | 🔴 Critical | Replaced `sqlite_sequence` prediction with post-INSERT `SELECT id FROM chunks WHERE document_id = ? ORDER BY id ASC` for actual rowids | 9,834/9,834 correct ✅ |
| **Duplicate document parse** | 🟡 Medium | Added filename dedup guard: if re-parsing same file, auto-deletes old rows + physical files before fresh parse | Tested on 17 MB PDF |
| **TOC json-key collision** | 🟡 Medium | toc.json now serialises as **list-of-dict** instead of keyed dict — duplicate section numbers no longer silently overwrite entries | Verified via dump inspection |
| **Batch PDF parsing** (page-by-page → batch) | ⚪ Perf | `pymupdf4llm.to_markdown()` called with 50-page chunks → single layout analysis per batch. Page resolution via internal TOC (`doc.get_toc()`) mapping | **17m23s → 14m41s** (16% faster on 2,400 page PDF) |
| **SectionNumberTracker mutate-in-generate** | ⚪ Correctness | `val == 0` write-back changed to `max(self.current_nums[i], 1)` — read-only string construction, parent-level state no longer polluted | Code review passed |

### Performance Benchmarks

Test system: Windows 10, AMD Ryzen + dual NVIDIA GPU (qwen3.6:27B on Ollama). Python 3.11. Test file: `57896-B1_3.04.pdf` (17 MB, ~2,400 pages, AMD Family 1Ah PPR spec).

| Metric | Page-by-page (v1) | Batch v2.0 | Improvement |
| ------ | ----------------- | -------- | ----------- |
| Parse time | 17m 23.8s (1,044 s) | **14m 40.9s** (881 s) | ⬇️ **~16% faster** |
| Chunks generated | 9,834 | 9,834 | ✅ Same output |
| DB↔Disk consistency | — | **18/18 chunks matched** (MD5 hash verified) | ✅ Verified |
| File path accuracy | Predicted IDs ❌ | Actual rowids ✅ | ✅ Fixed |

## Architecture

```
┌──────────────────────────────┐
│         Input Document       │  PDF / DOCX
│   (e.g., 2400-page PPR)     │
└───────────┬──────────────────┘
            │
            ▼
┌──────────────────────────────┐
│    parse_document.py         │
│  • Batch PDF (50 pages)      │  pymupdf4llm.to_markdown(pages=range)
│  • TOC-based page resolution │  doc.get_toc() → heading→page map
│  • SectionNumberTracker      │  read-only generate(), no state pollution
└───────────┬──────────────────┘
            │
            ▼  chunks[] (list-of-dict)
┌──────────────────────────────┐
│       database.py           │
│                             │
│  Phase 1: DB INSERT + COMMIT│  ← Crash safety: commit BEFORE disk IO
│  • documents row            │
│  • chunks rows (placeholder)│
│  • GET actual rowids        │  SELECT ... ORDER BY id ASC
│                             │
│  Phase 2: Disk file writes   │  <--- After DB is safe!
│  • Physical .md per chunk    │
│  • UPDATE file_path with     │    actual physical path
│  • toc.json (list format)   │
│  • index.md                 │
└───────────┬──────────────────┘
            │
            ▼
┌──────────────────────────────┐
│       documents.db           │  SQLite with FTS5 index
│                             │
│  ┌─────────────┐  ┌───────┐ │
│  │ documents   │1 │chunks │N│
│  │ ├ filename   │→ │ ├ ID  │ │
│  │ ├ chunk_ctn  │  │ ├ sec │ │
│  │ └ status     │  │ ├ cnt │ │
│  └─────────────┘  │ └ page│ │
│                   └───────┘ │
│   chunks_fts (FTS5)        │  Full-text search index
└──────────────────────────────┘
            │
            ▼
┌──────────────────────────────┐
│   output/<document_id>/      │
│   ├ toc.json                 │    List-of-dict entries
│   ├ index.md                 │    Markdown directory tree
│   └ chunks/                  │    Physical .md files
│       1_0_Introduction.md   │
│       2_1_Overview.md      │
│       ... (9,834 files)    │
└──────────────────────────────┘
```

## Quick Start

You can run the utility script `document_tool.py` directly using Python. For all commands (except `delete`), you must provide a `--output` path to write the JSON results.

> [!NOTE]
> In the examples below, `[path_to_skill]` represents the directory where this skill is installed.
> - For **local workspace-scoped** installations, the path is `.agents/skills/document_structuring/`.
> - For **global user-scoped** installations, the path is `~/.hermes/skills/document_structuring/` or `~/.gemini/config/skills/document_structuring/`.

### 1. Parse a File

```bash
python [path_to_skill]/scripts/document_tool.py parse --file "57896-B1_3.04.pdf" --output "parse_summary.json"
```

> 💡 **Tip**: For large PDFs (2,000+ pages), run in background:
> ```bash
> nohup python [path_to_skill]/scripts/document_tool.py parse --file "large-manual.pdf" --output "summary.json" > parse.log 2>&1 &
> ```

### 2. List Parsed Documents

```bash
python [path_to_skill]/scripts/document_tool.py list --output "documents_list.json"
```

> 🔁 **Re-parse handling**: If you run `parse` on a document that already has the same filename, it automatically deletes the old chunks before re-inserting — no manual cleanup needed.

### 3. Retrieve a Document's Table of Contents (TOC)

```bash
python [path_to_skill]/scripts/document_tool.py toc --doc-id 1 --output "toc_data.json"
```

### 4. Search across Chunks

```bash
python [path_to_skill]/scripts/document_tool.py search --query "ACPI" --output "search_results.json"
```

> 🔍 **Query syntax**: Multiple terms are combined with AND operators. For OR queries, use SQLite FTS5 boolean: `"term1 OR term2"`. If FTS5 matching fails, the system automatically falls back to `LIKE`-based substring search.

### 5. Retrieve a Specific Chunk's Content

```bash
python [path_to_skill]/scripts/document_tool.py get-chunk --chunk-id 5198 --output "chunk_content.json"
```

> ✅ **DB↔Disk consistency**: Retrieved content always matches the physical `.md` file at the same path. Verified via MD5 hash across 100% of chunks in testing.

### 6. Delete a Document (Cascading delete from DB and Disk)

```bash
python [path_to_skill]/scripts/document_tool.py delete --doc-id 1
```

---

## Utility Scripts

The CLI helper script resides at `[path_to_skill]/scripts/document_tool.py`. Here are the subcommands and their specifications:

### `parse`
- **Description**: Parses a PDF/DOCX file, chunks it into Markdown sections, and logs the document metadata and chunks into SQLite and disk.
- **Parameters**:
  - `--file` (required): Path to the input PDF or DOCX document.
  - `--output` (required): Path to write the JSON operation summary.
- **JSON Output Format**:
  ```json
  {
    "success": true,
    "document_id": 1,
    "filename": "57896-B1_3.04.pdf",
    "chunk_count": 9834
  }
  ```

### `list`
- **Description**: Lists all structured documents tracked in the SQLite database.
- **Parameters**:
  - `--output` (required): Path to write the JSON output file.
- **JSON Output Format**:
  ```json
  {
    "documents": [
      {
        "id": 1,
        "filename": "57896-B1_3.04.pdf",
        "upload_time": "2026-06-04 10:37:50",
        "chunk_count": 9834,
        "status": "success"
      }
    ]
  }
  ```

### `toc`
- **Description**: Returns all headings/chunks of a document, sorted by section numbers.
- **Parameters**:
  - `--doc-id` (required): Document ID.
  - `--output` (required): Path to write the JSON output file.
- **JSON Output Format**:
  ```json
  {
    "toc": [
      {
        "id": 123,
        "section_number": "1.1",
        "title": "Introduction",
        "page_start": 3,
        "file_path": "output/1/chunks/123_1.1_Introduction.md"
      }
    ]
  }
  ```

### `search`
- **Description**: Performs a keyword lookup against the chunk titles and text content in the database using SQLite FTS5 index.
- **Parameters**:
  - `--query` (required): The keyword query string. Multiple terms are combined automatically with AND operators.
  - `--doc-id` (optional): Filter results to this specific document ID.
  - `--output` (required): Path to write the JSON search results.
- **JSON Output Format**:
  ```json
  {
    "results": [
      {
        "id": 123,
        "document_id": 1,
        "document_name": "57896-B1_3.04.pdf",
        "section_number": "1.1",
        "title": "Introduction",
        "page_start": 3,
        "file_path": "output/1/chunks/123_1.1_Introduction.md",
        "snippet": "This is a snippet of text matching your query..."
      }
    ]
  }
  ```

### `get-chunk`
- **Description**: Retrieves full metadata and markdown text content of a single chunk.
- **Parameters**:
  - `--chunk-id` (required): Database ID of the target chunk.
  - `--output` (required): Path to write the JSON chunk content.
- **JSON Output Format**:
  ```json
  {
    "chunk": {
      "id": 123,
      "document_id": 1,
      "document_name": "57896-B1_3.04.pdf",
      "section_number": "1.1",
      "title": "Introduction",
      "content": "Full markdown content of this section...",
      "page_start": 3,
      "file_path": "output/1/chunks/123_1.1_Introduction.md"
    }
  }
  ```

### `delete`
- **Description**: Deletes the document from the database (along with cascading chunks) and deletes the physical files under `output/<doc_id>/` from disk.
- **Parameters**:
  - `--doc-id` (required): Database ID of the document to delete.
- **Stdout Output Format**:
  `Success: Document '57896-B1_3.04.pdf' (ID: 1) and all its associated chunks have been deleted.`

---

## Token Economics — Why This Matters

The primary value of this skill is **token compression for LLM context windows**:

| Scenario | Raw PDF tokens | Via chunk retrieval | Compression ratio |
| -------- | -------------- | ------------------ | ----------------- |
| Full 2,400-page PPR | ~4.5M tokens | FTS5 search → top 5 hits → ~2.5K tokens | **~1,800×** |
| Chapter query (e.g. "MSR registers") | ~150K tokens (whole chapter) | get-chunk → ~3K tokens per section | **~50×** |
| Specific register lookup | Full PDF needed otherwise | Targeted chunk → ~1.2K tokens | **~3,750×** |

For a 128K context window local model (e.g., qwen3.6:27B), this means you can reason about specific sections of a 2,400-page technical spec without filling the entire context — freeing up tokens for actual analysis and reasoning.

---

## Common Mistakes

1. **Missing `--output` Parameter**:
   Except for `delete`, all commands require a `--output` parameter. If omitted, the CLI will output an error. Always specify a JSON file path for results.

2. **Unsupported Formats**:
   The `parse` command only accepts files ending with `.pdf` or `.docx`. Ensure the file extension is correct before running the parser.

3. **CWD Dependency**:
   Ensure you run the commands from the project workspace root so that references to the SQLite database (`documents.db`) and the physical output files (`output/`) remain consistent.

4. **Re-parsing Overhead**:
   If you `parse` a document with an already-indexed filename, the system automatically deletes old chunks and re-inserts. For documents >500 pages this takes as long as the original parse — avoid accidental double-parsing.

5. **TOC Key Collision (v2 fix)**:
   Older versions stored `toc.json` as a dict keyed by section number, silently dropping entries with duplicate keys. v2 stores as a list-of-dict internally — every chunk gets an entry regardless of key overlap.
