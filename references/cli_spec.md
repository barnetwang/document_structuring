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

### `parse-code`

- **Description**: Parse C/H source code (via Tree-sitter AST) or EDK2 build config files (`.inf`, `.dec`, `.dsc`, `.fdf`) into structured DB symbol/section chunks.
- **Arguments**:
  - `--file <path>` (required)
  - `--output <path.json>` (required)
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

### `list`

- **Arguments**: `--output <path.json>` (required)

### `toc`

- **Arguments**: `--doc-id <id>`, `--output <path.json>` (required)

### `search`

- **Arguments**:
  - `--query <string>` (required)
  - `--mode <hybrid|fts|vec>` (optional, default: `hybrid`) — RRF Hybrid, BM25 Keyword, or Vector Similarity.
  - `--min-fts-rank <N>` (optional) — keep only chunks whose FTS5 rank is in the top N; chunks with no keyword match are dropped.
  - `--limit <N>` (optional, default: 10)
  - `--doc-id <id>` (optional)
  - `--output <path.json>` (required)

### `get-chunk`

- **Arguments**:
  - `--chunk-id <id>` (required)
  - `--include-neighbors` (optional) — include previous and next adjacent chunks if context budget permits.
  - `--max-context-tokens <N>` (optional) — maximum total token budget for target chunk and neighbors.
  - `--format <json|xml>` (optional, default: `json`) — output format (`json` or `xml` grounding structure).
  - `--output <path>` (required)

### `delete`

- **Arguments**: `--doc-id <id>` (required)

### `tag`

- **Arguments**: `--doc-id <id>`, `--tags "..."` (required)
