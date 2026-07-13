# CLI Specification Reference — doc-structuring

Detailed CLI specifications, output formats, configuration, token economics,
and troubleshooting for the `doc-structuring` tool.

---

## Prerequisites

```bash
pip install -e .
```

- **Python**: 3.10+
- **Dependencies**: `PyMuPDF`, `pymupdf4llm`, `python-docx` (these heavy packages are lazily loaded on-demand, meaning they are only required when running the `parse` command)

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
doc-structuring [--base-dir PATH] [--locale en|zh] [-v|-vv] <command> ...
```

---

## Data layout

Relative to the configured base directory:

- **SQLite**: `documents.db` (metadata, chunks, FTS5 index)
- **Chunks**: `output/<document_id>/chunks/*.md`
- **Images**: `output/<document_id>/images/` (when graphics were extracted)
- **TOC / index**: `output/<document_id>/toc.json`, `output/<document_id>/index.md`
- **Catalog**: `output/global_catalog.md` (documents grouped by tags)

---

## CLI subcommands

Installed entry point: `doc-structuring`.

### `parse`

- **Description**: Parse a PDF or DOCX, chunk into Markdown sections, persist
  to SQLite and disk.
- **Arguments**:
  - `--file <path>` (required)
  - `--tags "<comma-separated-tags>"` (optional)
  - `--output <path.json>` (required)
- **Output JSON**:
  ```json
  {
    "success": true,
    "document_id": 1,
    "filename": "manual.pdf",
    "chunk_count": 120
  }
  ```

### `list`

- **Arguments**: `--output <path.json>` (required)
- **Output JSON**:
  ```json
  {
    "documents": [
      {
        "id": 1,
        "filename": "manual.pdf",
        "upload_time": "2026-06-19 07:44:28",
        "chunk_count": 120,
        "status": "success"
      }
    ]
  }
  ```

### `toc`

- **Arguments**: `--doc-id <id>`, `--output <path.json>` (required)
- **Output JSON**: list of chunks with `id`, `section_number`, `title`,
  `page_start`, `file_path`.

### `search`

- **Arguments**:
  - `--query <string>` (required) — multi-word queries default to AND
  - `--doc-id <id>` (optional)
  - `--output <path.json>` (required)
- **Output JSON**: `results` array with snippet highlighting (`==term==`).

### `get-chunk`

- **Arguments**: `--chunk-id <id>`, `--output <path.json>` (required)
- **Output JSON**: full `chunk` object including `content` and `document_name`.

### `delete`

- **Arguments**: `--doc-id <id>` (required)
- **Stdout**: success message; removes DB rows (cascade) and `output/<id>/`.

### `tag`

- **Arguments**: `--doc-id <id>`, `--tags "..."` (required)
- **Stdout**: confirmation; regenerates `output/global_catalog.md`.

---

## Token economics

| Scenario | Rough tokens | Via this tool | Compression |
| -------- | ------------ | ------------- | ----------- |
| Full multi-thousand-page manual | millions | FTS top hits ~2–5K | ~10²–10³× |
| Whole chapter | tens–hundreds of K | one section ~1–5K | ~10–50× |
| Single topic lookup | full PDF otherwise | one chunk ~1–2K | high |

Exact ratios depend on document length and retrieval strategy.

---

## Troubleshooting

- **Missing `--output`**: Re-run with `--output <file.json>` (except delete/tag).
- **Wrong CWD**: Use workspace root or `--base-dir` so `documents.db` lands
  in the expected place.
- **FTS special characters**: Tokens are sanitised; if a query fails, use
  simpler keywords (LIKE fallback may still apply).
- **Missing modules (`fitz`, `docx`)**: Run `pip install -e .` in your active virtual environment. Because these heavy parser packages are lazily loaded, commands like `list`, `search`, `tag`, and `delete` will function without them. However, running `parse` on `.pdf` or `.docx` files will fail with a clear `ImportError` detailing the missing packages if they are not installed.
- **Temporary directories not cleaned up**: All extraction-related temp folders are created under `<base-dir>/.doc_structuring_tmp` (e.g. `doc_structuring_docx_xxxxxx`) and are automatically deleted upon successful database commit.
- **Domain-specific headers still polluting chunks**: add patterns via
  `AppConfig.extra_ignore_patterns` or pre-filter with a custom extractor.
