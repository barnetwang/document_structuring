# CLI Specification Reference — doc-structuring

This reference document contains the detailed CLI specifications, output formats, token economics, and troubleshooting tips for the `doc-structuring` tool.

---

## Prerequisites

Before running any of the CLI commands below, verify that the package is installed in the active environment:
```bash
pip install -e .
```
- **Python Environment**: Python 3.10+
- **Dependencies**: `PyMuPDF`, `pymupdf4llm`, `python-docx`

---

## Data Layout

All file pathways are relative to the project workspace root:
- **SQLite Database**: `documents.db` (contains metadata, chunk definitions, and the FTS5 search index)
- **Chunk Directory**: `output/<document_id>/chunks/` (contains physical `.md` files for each parsed segment)
- **TOC & Index**: `output/<document_id>/toc.json` (list of headings) and `output/<document_id>/index.md` (Markdown directory tree)

---

## CLI Subcommands

The CLI tool is installed as `doc-structuring`.

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

## Token Economics

- **Full 2,400-page spec**: ~4.5M tokens (too large/expensive for standard contexts).
- **Targeted Search Retrieval (Top 5 Chunks)**: ~2.5K tokens (~1,800× compression).
- **Targeted Chapter/Section Retrieval**: ~3K tokens (~50× compression).

---

## Troubleshooting & Common Mistakes

- **Forgot `--output` Parameter**:
  If the CLI fails with a missing output argument, re-run with `--output <some_temp_file.json>`.
- **CWD Location Error**:
  If `documents.db` is created in a subdirectory (like `scripts/`), delete the database file, change directory to the workspace root, and run the command again.
- **Querying Special Characters**:
  SQLite FTS5 syntax does not support raw special character queries. If a query fails, strip special characters or use a single keyword search to trigger the database's `LIKE` query fallback mechanism.
- **Incorrect Python Environment**:
  If modules like `fitz` or `docx` are missing, verify that dependencies are installed and that you are using the correct Python binary/virtual environment.
