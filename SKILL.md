---
name: doc-str
description: >-
  Parses long PDF and Word (.docx) manuals into structured Markdown chunks,
  indexes them in a local SQLite database, and supports document listing,
  TOC viewing, full-text search, chunk retrieval, and deletion. Use this skill
  whenever the user uploads or references a large PDF/Word document —
  especially 100+ page technical manuals, BIOS/PPR specs, register
  references, or other reference documents — and wants to search, look up,
  summarize, or browse specific sections without loading the entire file
  into context. Also use it when the user asks for a table of contents,
  wants to list or manage previously parsed documents, or needs to delete
  an indexed document. Do not read or extract text from large PDFs/DOCX files
  directly with custom scripts — always route through this skill instead.
---

# Document Structuring & Management Specialist

Use this skill to parse, index, search, and retrieve chunks of long PDF/DOCX manuals. Detailed CLI parameters, data schema, and troubleshooting details can be found in the [CLI Specification Reference](references/cli_spec.md).

## Core Rules

- **Workspace Root Constraint**: Always run all CLI commands from the **project workspace root** (where `documents.db` and the `output/` directory are managed).
- **Path Formatting**: Always use forward slashes `/` (never backslashes `\`) for all file paths passed as arguments to CLI commands.
- **Required Output Parameter**: Except for the `delete` command, always provide the `--output <path.json>` flag to write operations results to JSON.
- **Unified Tool Usage**: Always parse and extract text using the `doc-structuring` CLI. Do not write custom extraction scripts or use alternative PDF parsing libraries.
- **Re-parsing Safety**: When parsing a file with an already-existing filename, allow the tool to automatically clear previous database records and physical folders.

---

## Primary Workflows

### 1. Document Ingestion / Parsing

Follow these steps to parse and index a new manual:

- **Step 1: Verify Prerequisites**. Confirm the file ends with `.pdf` or `.docx`. Ensure the tool package is installed (`pip install -e .`).
  *Completion Criterion*: The package installation status and file extension are validated.
- **Step 2: Check for Duplicates**. Run `doc-structuring list --output <temp_list.json>` to see if a document with the exact same filename is already registered.
  *Completion Criterion*: A temporary list file is inspected and the duplicate status is verified.
- **Step 3: Execute Ingestion**. Run the parsing CLI command from the project workspace root:
  ```bash
  doc-structuring parse --file <path/to/manual.pdf> --output <temp_parse.json>
  ```
  *Completion Criterion*: The command exits successfully, producing the output JSON with `"success": true`.
- **Step 4: Interactive Categorization / Tagging**.
  - Read the intro chunk (e.g. `0_Introduction.md` or output of `get-chunk`) to understand the topic of the document.
  - Predict the most likely category/tags (e.g. `[技術文件] [Intel Platform]`).
  - Prompt the user in the chat: *"I have structured the file and predicted these tags: [predicted_tags]. Would you like to save it with these tags, or assign different tags?"*
  - Based on user response, run the tagging command:
    ```bash
    doc-structuring tag --doc-id <id> --tags "<comma-separated-tags>"
    ```
  - Verify that the global catalog `output/global_catalog.md` is updated.
  *Completion Criterion*: The tagging CLI command completes successfully and the global catalog is verified.
- **Step 5: Verify Database Logging**. Check that `documents.db` is updated, physical chunks are generated under `output/<document_id>/chunks/`, and physical images are extracted under `output/<document_id>/images/`.
  *Completion Criterion*: The output directory containing structured `.md` files, extracted drawings, and the SQLite records are verified to exist.

### 2. Document Search & Retrieval

Follow these steps to locate and retrieve granular information:

- **Step 1: Perform Search query**. Run the search CLI command using relevant keywords:
  ```bash
  doc-structuring search --query "<keywords>" --output <temp_search.json>
  ```
  *Completion Criterion*: The search command completes, and the matched result list in the output JSON is retrieved.
- **Step 2: Inspect Matches**. Review the returned text snippets and identify the target `chunk_id` values.
  *Completion Criterion*: You have mapped the most relevant snippets to their respective IDs.
- **Step 3: Retrieve Section Content**. Load the full text for target sections by executing:
  ```bash
  doc-structuring get-chunk --chunk-id <id> --output <temp_chunk.json>
  ```
  *Completion Criterion*: The full markdown content of the selected chunks is successfully loaded and integrated into the assistant context.
- **Step 4: Context Expansion**. If adjacent text is required, run `doc-structuring toc --doc-id <id> --output <temp_toc.json>` to explore the document structure, then retrieve adjacent chunks using `get-chunk`.
  *Completion Criterion*: A wider range of related sections is read and synthesized.

### 3. Document Deletion

Follow these steps to clean up an indexed document:

- **Step 1: Identify Document ID**. Run `doc-structuring list` to find the exact ID of the target document.
  *Completion Criterion*: The `doc-id` is successfully identified.
- **Step 2: Remove Records**. Execute the deletion command:
  ```bash
  doc-structuring delete --doc-id <id>
  ```
  *Completion Criterion*: The CLI outputs a success message indicating the document and its chunks have been removed.
- **Step 3: Verify Disk Cleanup**. Confirm that the database entry is gone and the `output/<document_id>/` folder has been removed from disk.
  *Completion Criterion*: Files and folders related to the deleted document no longer exist.
