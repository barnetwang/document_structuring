---
name: document_structuring
description: >-
  Parses long PDF and Word (.docx) manuals into structured Markdown chunks,
  indexes them in a local SQLite database, and supports document listing,
  TOC viewing, full-text search, chunk retrieval, and deletion. Optimised
  with batch PDF parsing (~16% faster than page-by-page). Use this skill
  whenever the user uploads or references a large PDF/Word document —
  especially 100+ page technical manuals, BIOS/PPR specs, register
  references, or other reference documents — and wants to search, look up,
  summarize, or browse specific sections without loading the entire file
  into context. Also use it when the user asks for a table of contents,
  wants to list or manage previously parsed documents, or needs to delete
  an indexed document, even if they don't explicitly say "parse," "chunk,"
  or "SQLite." Do not read or extract text from large PDFs/DOCX files
  directly with custom scripts — always route through this skill instead.
compatibility: Requires Python 3.10+ with PyMuPDF, pymupdf4llm, and python-docx installed.
---

# Document Structuring & Management Specialist

This skill provides instructions for parsing long PDF/DOCX manuals, chunking them into structured Markdown sections, indexing them in a local SQLite database, and retrieving them on demand — keeping large technical documents searchable and inspectable without overwhelming the LLM's context window.

## Prerequisites

**Before running any of the CLI commands below, verify that the required Python packages are installed in the active environment by executing `python -c "import pymupdf4llm; print('OK')"`. If it fails, install them via:**
```bash
pip install PyMuPDF pymupdf4llm python-docx
```

1. **Python Environment**: Ensure Python 3.10+ is available.
2. **Dependencies**: The following packages must be installed:
   - `PyMuPDF` (for layout analysis and PDF extraction)
   - `pymupdf4llm` (for converting PDF contents to Markdown)
   - `python-docx` (for parsing Word documents)

## Overview

Large files are converted into individual Markdown chunks (typically 1–5 KB) corresponding to document heading levels. This provides up to **150–800× token compression** vs. reading the raw document.

### Data Layout
All file pathways are relative to the project workspace root:
- **SQLite Database**: `documents.db` (contains metadata, chunk definitions, and the FTS5 search index)
- **Chunk Directory**: `output/<document_id>/chunks/` (contains physical `.md` files for each parsed segment)
- **TOC & Index**: `output/<document_id>/toc.json` (list of headings) and `output/<document_id>/index.md` (Markdown directory tree)

---

## Core Rules

- **Workspace Root Constraint**: Always run the CLI commands from the **project workspace root** (where `documents.db` and the `output/` directory are managed). Never run them from inside the `scripts/` directory.
- **Path Formatting (Windows/MSYS)**: Always use forward slashes `/`, never backslashes `\`, for all file paths passed as arguments to guarantee compatibility with Python scripts and MSYS bash terminal.
- **Required Output Parameter**: Except for the `delete` command, you must always provide the `--output <path.json>` flag. The CLI writes JSON-formatted results to the specified file.
- **No Direct File Reading of Raw PDFs**: Never attempt to parse or extract text from large raw PDFs directly using Python scripts or custom PDF parsers; always use the `document_tool.py` wrapper.
- **Re-parsing Safety**: Re-parsing a file with the same name automatically removes the old database entries and physical folder structure first. There is no need for manual deletion before re-importing.
- **Consistency Guarantee**: Physical Markdown files on disk and the SQLite database are kept in sync. DB records are committed *before* writing files, ensuring no orphaned file handles or mismatched indexes if the script is interrupted.

---

## Use Cases

- **Large Spec/Manual Analysis**: Slicing BIOS/PPR/registers manuals (2,000+ pages) for granular reading.
- **Context Preservation**: Retrieving only relevant document sub-sections (e.g., register details, ACPI states) to keep the LLM context clean.
- **Full-Text Spec Searching**: Locating specific configuration keys or registers across document structures.
- **TOC Inspection**: Verifying the document organization structure.

---

## Available CLI Subcommands

The CLI helper script is located at `scripts/document_tool.py`.

### `parse`
- **Description**: Parses a PDF or DOCX file, chunks it into Markdown sections, and logs the metadata and chunks into SQLite and disk.
- **Arguments**:
  - `--file <path>` (required): Path to the input PDF or DOCX document.
  - `--output <path.json>` (required): Path to write the JSON operation summary.
- **Output JSON Format**:
  ```json
  {
    "success": true,
    "document_id": 1,
    "filename": "your-manual.pdf",
    "chunk_count": 9834
  }
  ```

### `list`
- **Description**: Lists all structured documents tracked in the SQLite database.
- **Arguments**:
  - `--output <path.json>` (required): Path to write the JSON output file.
- **Output JSON Format**:
  ```json
  {
    "documents": [
      {
        "id": 1,
        "filename": "your-manual.pdf",
        "upload_time": "2026-06-19 07:44:28",
        "chunk_count": 9834,
        "status": "success"
      }
    ]
  }
  ```

### `toc`
- **Description**: Returns all headings/chunks of a document, sorted by section numbers.
- **Arguments**:
  - `--doc-id <id>` (required): The document ID.
  - `--output <path.json>` (required): Path to write the JSON output file.
- **Output JSON Format**:
  ```json
  {
    "toc": [
      {
        "id": 1,
        "section_number": "1",
        "title": "Introduction",
        "page_start": 1,
        "file_path": "output/1/chunks/1_1_Introduction.md"
      },
      {
        "id": 2,
        "section_number": "1.1",
        "title": "Background",
        "page_start": 2,
        "file_path": "output/1/chunks/2_1.1_Background.md"
      }
    ]
  }
  ```

### `search`
- **Description**: Performs a keyword lookup against the chunk titles and text content in the database using SQLite FTS5 index.
- **Arguments**:
  - `--query <string>` (required): The keyword query string. Multiple terms are combined automatically with AND operators. For OR queries, use SQLite FTS5 syntax (e.g. `"term1 OR term2"`).
  - `--doc-id <id>` (optional): Filter results to a specific document.
  - `--output <path.json>` (required): Path to write the JSON search results.
- **Output JSON Format**:
  ```json
  {
    "results": [
      {
        "id": 123,
        "document_id": 1,
        "section_number": "3.2",
        "title": "ACPI States",
        "page_start": 45,
        "file_path": "output/1/chunks/123_3.2_ACPI_States.md",
        "document_name": "your-manual.pdf",
        "snippet": "This section explains ==ACPI== states and sleep modes..."
      }
    ]
  }
  ```

### `get-chunk`
- **Description**: Retrieves full metadata and markdown text content of a single chunk.
- **Arguments**:
  - `--chunk-id <id>` (required): Database ID of the target chunk.
  - `--output <path.json>` (required): Path to write the JSON chunk content.
- **Output JSON Format**:
  ```json
  {
    "chunk": {
      "id": 123,
      "document_id": 1,
      "section_number": "3.2",
      "title": "ACPI States",
      "content": "Full markdown content of this section...",
      "page_start": 45,
      "file_path": "output/1/chunks/123_3.2_ACPI_States.md",
      "document_name": "your-manual.pdf"
    }
  }
  ```

### `delete`
- **Description**: Deletes the document from the database (cascading chunks) and deletes the physical files under `output/<doc_id>/` from disk.
- **Arguments**:
  - `--doc-id <id>` (required): Database ID of the document to delete.
- **Stdout Output Format**:
  `Success: Document 'your-manual.pdf' (ID: 1) and all its associated chunks have been deleted.`

---

## Workflows

### 1. Document Ingestion / Parsing Workflow
Follow this checklist to import a new document:
- [ ] **Step 0**: Quickly check dependencies by running `python -c "import pymupdf4llm; print('OK')"`. If it throws an error, install them using pip before proceeding.
- [ ] **Step 1**: Verify the document file extension is `.pdf` or `.docx`.
- [ ] **Step 2**: Run `list` to check if a document with the same filename already exists.
- [ ] **Step 3**: Execute the `parse` command from the workspace root. (Make sure to use forward slashes for paths).
  *Note: Large PDFs (2,000+ pages) may take several minutes to parse. Do not interrupt execution.*
- [ ] **Step 4**: Verify the result JSON contains `"success": true` and note the `document_id`.

### 2. Document Search & Retrieval Workflow
Follow this checklist to lookup information in indexed documents:
- [ ] **Step 1**: Formulate your keyword query. Use boolean operators if needed (e.g., `ACPI AND C-States`).
- [ ] **Step 2**: Execute the `search` command, outputting to a temporary JSON file.
- [ ] **Step 3**: Examine the matched snippets and retrieve the most relevant `chunk_id`s.
- [ ] **Step 4**: Execute the `get-chunk` command for each desired `chunk_id` to retrieve the full Markdown content.
- [ ] **Step 5**: If context is missing, use `toc` to view surrounding sections, and retrieve adjacent `chunk_id`s.

### 3. Document Deletion Workflow
Follow this checklist to clean up documents:
- [ ] **Step 1**: Find the `document_id` by running `list`.
- [ ] **Step 2**: Execute `delete` using the targeted `--doc-id`.
- [ ] **Step 3**: Verify stdout success message and ensure `output/<document_id>/` folder has been removed from disk.

---

## Token Economics — Reference
Keep these approximate token metrics in mind:
- **Full 2,400-page spec**: ~4.5M tokens (too large/expensive for standard contexts).
- **Targeted Search Retrieval (Top 5 Chunks)**: ~2.5K tokens (**~1,800× compression**).
- **Targeted Chapter/Section Retrieval**: ~3K tokens (**~50× compression**).

---

## Common Mistakes & Troubleshooting

- **Forgot `--output` Parameter**:
  If the CLI fails with a missing output argument, re-run with `--output <some_temp_file.json>`.
- **CWD Location Error**:
  If `documents.db` is created in a subdirectory (like `scripts/`), delete the database file, change directory to the workspace root, and run the command again.
- **Querying Special Characters**:
  SQLite FTS5 syntax does not support raw special character queries. If a query fails, strip special characters or use a single keyword search to trigger the database's `LIKE` query fallback mechanism.
- **Incorrect Python Environment**:
  If modules like `fitz` or `docx` are missing, verify that dependencies are installed and that you are using the correct Python binary/virtual environment.
