# CLI Specification Reference — doc-str

Detailed CLI specifications, output formats, configuration, token economics, and troubleshooting for the `doc-str` tool.

---

## Prerequisites

```bash
pip install -e .
```

- **Python**: 3.10+
- **Dependencies**: `PyMuPDF`, `pymupdf4llm`, `python-docx`, `tree-sitter-c`, `fastembed`

---

## Configuration

| Source | Effect |
| ------ | ------ |
| CWD / `--base-dir` / `DOC_STRUCTURING_BASE_DIR` | Root for `documents.db` and `output/` |
| `--locale` / `DOC_STRUCTURING_LOCALE` | Generated catalog/index language (`en` default, `zh` supported) |
| `AppConfig.extra_ignore_patterns` | Extra regexes for header/footer line filtering (Python API) |
| `AppConfig.bad_heading_keywords` | Title substrings rejected as section headings |
| `AppConfig.pdf_batch_size` | Pages per PDF markdown batch (default 50) |
| `AppConfig.search_limit` | Max FTS/LIKE search hits (default 100) |

Global flags (before the subcommand):

```bash
doc-str [--base-dir PATH] [--locale en|zh] [-v|-vv] <command> ...
```

---

## CLI Subcommands

### `parse`

- **Description**: Parse a PDF or DOCX, chunk into Markdown sections, persist to SQLite and disk.
- **Arguments**:
  - `--file <path>` (required)
  - `--tags "<comma-separated-tags>"` (optional)
  - `--output <path.json>` (required)
- **Identity**: documents are keyed by **filename (basename)** only — re-parsing the same filename anywhere replaces the existing document. Old DB rows are deleted and the old `output/<id>/` tree removed *before* the new files are written; a failed new write can therefore leave the database and the filesystem out of sync.

### `parse-code`

- **Description**: Parse C/H source code (via Tree-sitter AST) or EDK2 build config files (`.inf`, `.dec`, `.dsc`, `.fdf`) into structured DB symbol/section chunks.
- **Arguments**:
  - `--file <path>` (required)
  - `--output <path.json>` (required)
- **Semantics**: **appends** a new document on every run (no replace semantics); delete the previous code document first for a clean replace.
- **Output JSON**:
  ```json
  {
    "success": true,
    "document_id": 2,
    "filename": "S3Resume.c",
    "doc_type": "code_c",
    "chunk_count": 5
  }
  ```

### `embed`

- **Description**: Generate 384-dimensional ONNX vector embeddings for document chunks using `fastembed` (`BAAI/bge-small-en-v1.5`).
- **Arguments**:
  - `--doc-id <id>` (optional)
  - `--output <path.json>` (required)
- **Semantics**: recomputes all chunks in scope on every run; there is no staleness or content-hash check. Re-run after any re-parse of an embedded document. The model is English-oriented — CJK semantic quality is unverified.

### `list`

- **Arguments**: `--output <path.json>` (required)

### `toc`

- **Arguments**: `--doc-id <id>`, `--output <path.json>` (required)

### `search`

- **Arguments**:
  - `--query <string>` (required)
  - `--mode <hybrid|fts|vec>` (optional, default: `hybrid`) — RRF hybrid, keyword, or vector similarity.
  - `--min-fts-rank <N>` (optional, **hybrid mode only**) — keep only chunks among the FTS candidate positions 1..N; chunks with no keyword match are dropped. Note: the current FTS candidate list is ordered by document upload recency / section order, *not* by BM25 relevance, so "top N" is positional, not relevance-ranked.
  - `--limit <N>` (optional, default: 10)
  - `--doc-id <id>` (optional)
  - `--output <path.json>` (required)
- **Result shapes**: `fts` returns metadata rows plus a ~150-char snippet (no full content); `hybrid`/`vec` return the **full** chunk content — keep `--limit` small.
- **Mode transparency**: the output `"mode"` field reflects the *requested* mode. Without embeddings, hybrid silently degrades to FTS-only (warning on stderr only) and `vec` returns no results; an agent reading JSON only cannot otherwise tell which path actually ran.

### `get-chunk`

- **Arguments**:
  - `--chunk-id <id>` (required)
  - `--include-neighbors` (optional) — include previous and next adjacent chunks if context budget permits.
  - `--max-context-tokens <N>` (optional) — maximum total token **estimate** budget. Current semantics: the target chunk is always returned in full; the budget (minus a fixed XML-escape reserve) constrains only the neighbors. Without `--include-neighbors` the flag has no effect. The count is a lightweight estimate, not a tokenizer-exact token count — do not treat it as a hard model token cap.
  - `--format <json|xml>` (optional, default: `json`) — output format (`json` or `xml` grounding structure).
  - `--output <path>` (required)

### `delete`

- **Arguments**: `--doc-id <id>` (required)

### `tag`

- **Arguments**: `--doc-id <id>`, `--tags "..."` (required)
