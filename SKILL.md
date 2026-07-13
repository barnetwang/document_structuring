---
name: doc-structuring
description: >-
  Parses long PDF and Word (.docx) documents into structured Markdown chunks,
  indexes them in a local SQLite database, and supports document listing,
  TOC viewing, full-text search, chunk retrieval, tagging, and deletion.
  Use this skill whenever the user uploads or references a large PDF/Word
  document — technical manuals, product specs, research papers, legal docs,
  handbooks, or other long-form references — and wants to search, look up,
  summarize, or browse specific sections without loading the entire file
  into context. Also use it when the user asks for a table of contents,
  wants to list or manage previously parsed documents, or needs to delete
  an indexed document. Do not read or extract text from large PDFs/DOCX files
  directly with custom scripts — always route through this skill instead.
---

# Document Structuring & Management

Use this skill to parse, index, search, and retrieve chunks of long PDF/DOCX
documents. Detailed CLI parameters and output schemas are in
[references/cli_spec.md](references/cli_spec.md). Product overview and
architecture notes: [README.md](README.md).

## Core Rules

- **Workspace root**: Run all CLI commands from the project workspace root
  (where `documents.db` and `output/` live), or pass `--base-dir <path>`.
- **Path formatting**: Prefer forward slashes `/` in paths passed to the CLI.
- **Required `--output`**: Except for `delete` and `tag`, always pass
  `--output <path.json>` so results are written as JSON.
- **Use this tool only**: Parse and extract via `doc-structuring`. Do not
  write ad-hoc PDF extraction scripts or call alternate libraries for the
  same task.
- **Re-parse safety**: Re-parsing a file with the same filename replaces the
  previous DB rows and `output/<id>/` tree automatically.
- **Optional locale**: Generated catalog/index labels default to English.
  Use `--locale zh` (or `DOC_STRUCTURING_LOCALE=zh`) for Chinese labels.
- **Dependencies**: If parse fails with
  `ImportError: Required dependencies for '.pdf' / '.docx' are missing`,
  install the package in the active env (`pip install -e .`) — do not invent
  a workaround parser.

## Environment

| Variable / flag | Purpose |
| --------------- | ------- |
| `DOC_STRUCTURING_BASE_DIR` / `--base-dir` | Workspace root for DB + output |
| `DOC_STRUCTURING_LOCALE` / `--locale en\|zh` | Generated Markdown labels |
| `-v` / `-vv` | INFO / DEBUG logging |

Install once from the skill/package root:

```bash
pip install -e .
```

Optional smoke test (parser only, fast):

```bash
pip install -e ".[dev]"
python -m pytest tests/test_parser.py -q
```

## Data layout (after parse)

```text
<base_dir>/
├── documents.db
├── .doc_structuring_tmp/     # scratch; cleaned after successful save
└── output/
    ├── global_catalog.md
    └── <document_id>/
        ├── toc.json
        ├── index.md
        ├── chunks/*.md
        └── images/           # if graphics were extracted
```

## Primary Workflows

### 1. Document ingestion / parsing

1. **Prerequisites**: File ends with `.pdf` or `.docx`; package installed.
2. **Duplicates**: `doc-structuring list --output <temp_list.json>` — check
   whether the same filename is already indexed.
3. **Parse** (from workspace root):
   ```bash
   doc-structuring parse --file <path/to/document.pdf> --output <temp_parse.json>
   ```
   Optional tags at parse time: `--tags "category,topic"`.  
   Optional: `--base-dir <path>`, `--locale zh`.
4. **Tagging** (if not set at parse time):
   - Skim the intro chunk or `get-chunk` for topic.
   - Suggest tags (domain, product, language, doc type — whatever fits).
   - Confirm with the user when tags are ambiguous, then:
     ```bash
     doc-structuring tag --doc-id <id> --tags "<comma-separated-tags>"
     ```
   - Confirm `output/global_catalog.md` updated.
5. **Verify**: `documents.db` exists; chunks under
   `output/<document_id>/chunks/`; images under
   `output/<document_id>/images/` when present.

**On failure**:

| Symptom | Action |
| ------- | ------ |
| `ImportError` … dependencies for `.pdf` / `.docx` | `pip install -e .` in the correct env |
| Missing `--output` | Re-run with `--output <temp.json>` |
| Empty / wrong DB location | `cd` to workspace root or use `--base-dir` |

### 2. Search & retrieval

1. Search:
   ```bash
   doc-structuring search --query "<keywords>" --output <temp_search.json>
   ```
   Optional: `--doc-id <id>` to scope one document.  
   Multi-word queries default to AND; keep queries simple if FTS fails.
2. Review snippets; note target `chunk_id` values.
3. Load full section(s):
   ```bash
   doc-structuring get-chunk --chunk-id <id> --output <temp_chunk.json>
   ```
4. Expand context via TOC when needed:
   ```bash
   doc-structuring toc --doc-id <id> --output <temp_toc.json>
   ```
   then fetch neighboring chunks with `get-chunk`.

Prefer **search → top chunks → get-chunk** over loading entire manuals into
context (large token savings).

### 3. Deletion

1. `doc-structuring list --output <temp_list.json>` → find `doc-id`.
2. `doc-structuring delete --doc-id <id>`
3. Confirm DB row and `output/<id>/` are gone; catalog regenerates.

## Extending for a domain

Vendor-specific headers/footers or title noise — configure via Python API
(not hardcoding in the skill):

```python
from doc_structuring import AppConfig, parse_file

config = AppConfig(
    extra_ignore_patterns=[r"^MyCompany Confidential.*$"],
    bad_heading_keywords=["revision history", "change log"],
    locale="en",
)
parse_file("manual.pdf", config=config, tags=["hardware", "spec"])
```

New file formats: `@register(".ext")` + `extract_lines()` under
`src/doc_structuring/extractors/` (see [README.md](README.md)).

## Implementation notes (for the agent)

- PDF extractors preprocess graphics once, serialise the PDF once, then batch
  markdown — large files should not need custom batching scripts.
- List / search / tag / delete do not require re-parsing the source file.
- Do not manually edit `documents.db` or invent parallel chunk folders;
  always use the CLI so FTS and `global_catalog.md` stay consistent.
