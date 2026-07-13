# Document Structuring

**Parse PDF & Word documents into structured, searchable Markdown chunks**

This package (and companion agent skill) slices long PDF/DOCX files by heading
structure, stores segments in a local SQLite database with FTS5 full-text
search, and supports TOC browsing, chunk retrieval, tagging, and cascading
delete. It is designed for LLM / RAG workflows where loading an entire manual
into context is too expensive.

本工具可將長篇 PDF / Word (`.docx`) 依標題切成結構化 Markdown 區塊，存入本地
SQLite（含 FTS5 全文檢索），並支援目錄、單段讀取、標籤與刪除。適合 Agent /
RAG 場景，避免把整份文件塞進 context。

| | |
| --- | --- |
| **Package** | `doc-structuring` `0.1.0` |
| **Python** | ≥ 3.10 (3.11+ recommended) |
| **License** | Apache-2.0 |
| **CLI** | `doc-structuring` |
| **Formats** | `.pdf`, `.docx` (pluggable for more) |

---

## Features

- **Batch PDF parsing** via `pymupdf4llm` (configurable page batch size)
- **Single-pass PDF serialisation** — graphics preprocessing runs on all pages,
  then the document is written/reloaded **once** before markdown batches
  (avoids per-batch full-PDF I/O on large manuals)
- **Heading page sync** from PDF bookmarks (internal TOC)
- **SQLite FTS5** search with keyword sanitisation and LIKE fallback
- **Crash-safer writes**: commit DB rows before writing physical files
- **Re-parse guard**: same filename replaces prior DB + `output/<id>/` tree
- **Graphics structuring**: cluster drawings/images, crop high-res PNGs,
  redact overlapping “text salad”, insert metadata anchors
- **Tag catalog**: optional tags + auto-maintained `global_catalog.md`
- **Locale-aware labels**: generated catalog/index text in `en` (default) or `zh`
- **Configurable filters**: ignore patterns and bad-heading keywords via
  `AppConfig` (no vendor-specific hardcoding)
- **Lazy extractors**: PyMuPDF / python-docx load only when parsing that format;
  list/search/TOC work without pulling heavy deps until needed
- **Clear dependency errors**: missing PDF/DOCX libraries raise `ImportError`
  with the original cause (not a misleading “unsupported format”)
- **Pluggable extractors**: `@register(".ext")` for new formats
- **Unified temp dir**: intermediate images under `.doc_structuring_tmp`
  (cleaned after successful save)

---

## Prerequisites

- Python 3.10+
- Runtime dependencies (installed with the package):
  - `PyMuPDF` / `fitz` — PDF rendering & layout
  - `pymupdf4llm` — PDF → Markdown
  - `python-docx` — Word structure

Dev optional: `pytest` (`pip install -e ".[dev]"`).

---

## Installation

```bash
pip install -e .
```

As an agent skill, place this directory where your agent loads skills (project
or user skill path), then install the package as above.

---

## Quick start

Always run from the workspace root (or pass `--base-dir`).

```bash
# Parse
doc-structuring parse --file "manual.pdf" --tags "hardware,spec" --output parse_result.json

# List / TOC / search / get chunk
doc-structuring list --output documents_list.json
doc-structuring toc --doc-id 1 --output toc_data.json
doc-structuring search --query "power management" --output search_results.json
doc-structuring get-chunk --chunk-id 123 --output chunk_content.json

# Tag / delete
doc-structuring tag --doc-id 1 --tags "hardware,v2"
doc-structuring delete --doc-id 1
```

Global options (before the subcommand):

```bash
doc-structuring --base-dir /path/to/workspace --locale zh -v parse --file doc.pdf --output out.json
```

| Flag / env | Purpose |
| ---------- | ------- |
| `--base-dir` / `DOC_STRUCTURING_BASE_DIR` | Root for `documents.db` and `output/` |
| `--locale en\|zh` / `DOC_STRUCTURING_LOCALE` | Labels in generated catalog/index |
| `-v` / `-vv` | INFO / DEBUG logging |

Except for `delete` and `tag`, commands require `--output <file.json>`.

### Python API

```python
from doc_structuring import AppConfig, parse_file

config = AppConfig(
    locale="en",
    extra_ignore_patterns=[r"^Acme Confidential.*$"],
    bad_heading_keywords=["revision history", "change log"],
    pdf_batch_size=50,
    search_limit=100,
)
result = parse_file("manual.pdf", config=config, tags=["spec"])
print(result["document_id"], result["chunk_count"])
```

---

## Data layout

Relative to the configured base directory:

```text
<base_dir>/
├── documents.db                 # metadata + FTS5 index
├── .doc_structuring_tmp/        # scratch images (auto-cleaned after save)
└── output/
    ├── global_catalog.md        # documents grouped by tags
    └── <document_id>/
        ├── toc.json
        ├── index.md
        ├── chunks/*.md
        └── images/              # when graphics were extracted
```

---

## Database schema

`documents.db` tables:

| Table | Role |
| ----- | ---- |
| `_meta` | Schema version |
| `documents` | Filename, upload time, chunk count, status |
| `chunks` | Section number, title, content, page_start, file_path |
| `document_tags` | Tags per document |
| `chunks_fts` | FTS5 virtual table (title + content) |

---

## Architecture notes

| Area | Behaviour |
| ---- | --------- |
| PDF pipeline | Preprocess graphics on all pages → **one** `doc.write()` → batch `to_markdown` |
| Extractor load | Lazy registry; missing deps → `ImportError: Required dependencies for '.pdf'...` |
| Temp files | Under `base_dir/.doc_structuring_tmp` (or CWD fallback); removed after DB commit + file write |
| Filtering | Generic page/TOC/confidential banners; extend with `extra_ignore_patterns` |
| Search | FTS5 MATCH (AND keywords) → LIKE fallback on syntax failure |

Full CLI JSON contracts: [references/cli_spec.md](references/cli_spec.md).

---

## Testing

Lightweight parser tests (no PDF/DOCX runtime required for the test logic itself):

```bash
pip install -e ".[dev]"
python -m pytest tests/test_parser.py -q
```

Covers heading validation, ignore filters, chunk splitting, and config-driven
extra ignore patterns.

---

## Token economics

| Scenario | Raw tokens (order of magnitude) | Via chunk retrieval | Compression |
| -------- | ------------------------------- | ------------------- | ----------- |
| Full multi-thousand-page manual | millions | Top search hits ~2–5K | ~10²–10³× |
| Chapter-scale context | tens–hundreds of K | One section ~1–5K | ~10–50× |
| Point lookup | often full PDF otherwise | One chunk ~1–2K | high |

Exact ratios depend on document length and retrieval strategy.

---

## Troubleshooting

1. **`ImportError: Required dependencies for '.pdf' are missing`** (or `.docx`)  
   — Install package deps in the active environment: `pip install -e .`  
   Original cause (e.g. `No module named 'fitz'`) is included in the message.

2. **Missing `--output`** — Required for parse / list / toc / search / get-chunk.

3. **DB or files in the wrong folder** — Run from workspace root, or set
   `--base-dir` / `DOC_STRUCTURING_BASE_DIR`.

4. **Vendor headers still appearing in chunks** — Add patterns via
   `AppConfig.extra_ignore_patterns`.

5. **Orphan temp folders** — Successful `parse` cleans `.doc_structuring_tmp`.
   Interrupted runs may leave it; safe to delete manually.

---

## Extending

**Domain filters** (Python API):

```python
AppConfig(
    extra_ignore_patterns=[r"^MyCompany Internal.*$"],
    bad_heading_keywords=["revision history"],
)
```

**New formats**: implement `extract_lines()` and register with
`@register(".ext")` under `src/doc_structuring/extractors/`.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
