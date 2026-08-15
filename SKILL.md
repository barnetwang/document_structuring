---
name: doc-str
description: >-
  Parses long PDF/DOCX manuals, C/H source code, and EDK2 build configs (.inf, .dec, .dsc, .fdf)
  into structured Markdown & AST chunks. Indexes them in SQLite with FTS5 and CPU ONNX vector
  embeddings (fastembed BAAI/bge-small-en-v1.5) for RRF Hybrid Search, TOC-First browsing,
  token-budgeted XML grounding, and Spec-to-Code cross-domain retrieval.
  Use this skill whenever the user uploads or references large technical specs, BIOS manuals,
  firmware codebases and wants to search, look up, summarize, or browse specific sections without
  loading the entire document into context. Also use it when the user asks for a table of contents,
  wants to list or manage previously parsed documents, needs to tag or delete an indexed document,
  or wants to index/search C/H source code and EDK2 build configs — even if they don't explicitly
  say "parse," "chunk," "index," or "SQLite." Do not read or extract text from large PDFs/DOCX files
  directly with custom scripts — always route through this skill instead.
---

# Document Structuring & Management

Use this skill to parse, index, search, and retrieve chunks of PDF/DOCX documents, C/H source code, and EDK2 configuration files. Detailed CLI parameters and output schemas are in [references/cli_spec.md](references/cli_spec.md). Product overview and architecture notes: [README.md](README.md).

## Core Rules

- **Workspace root**: Run all CLI commands from the project workspace root (where `documents.db` and `output/` live), or pass `--base-dir <path>`.
- **Path formatting**: Prefer forward slashes `/` in paths passed to the CLI. On Windows (MSYS/git-bash), if `terminal()` mangles paths, fall back to `execute_code` with raw-string absolute paths — see [references/windows-path-workaround.md](references/windows-path-workaround.md).
- **Required `--output`**: Except for `delete` and `tag`, always pass `--output <path.json>` so results are written as JSON.
- **Use this tool only**: Parse and extract via `doc-str`. Do not write ad-hoc PDF or C parser scripts for the same task.
- **Re-parse safety**: Re-parsing a file with the same filename replaces previous DB rows and `output/<id>/` tree automatically.
- **Dependencies**: Uses `tree-sitter-c` for C/H AST parsing and `fastembed` for CPU ONNX vector embeddings.

## Primary Workflows

### 1. Document & Code Ingestion

#### Specification Ingestion (PDF / DOCX)
```bash
doc-str parse --file <path/to/document.pdf> --tags "bios,spec" --output <temp_parse.json>
```

#### Firmware Code & Config Ingestion (C / H / INF / DEC / DSC / FDF)
```bash
doc-str parse-code --file <path/to/S3Resume.c> --output <temp_code.json>
doc-str parse-code --file <path/to/S3ResumePei.inf> --output <temp_inf.json>
```

#### Vector Embedding Generation (ONNX fastembed)
```bash
doc-str embed --doc-id <id> --output <temp_embed.json>
```

---

### 2. Search & Retrieval (TOC-First Agent Workflow)

To prevent Context Explosion and Hallucinations in LLM agents (e.g., Qwen 27B):

#### Step 1: Check Document TOC (First Priority)
```bash
doc-str toc --doc-id <id> --output <temp_toc.json>
```
Examine section titles and structure to identify exact target `chunk_id`s.

#### Step 2: Hybrid Search (FTS5 BM25 + fastembed Vector + RRF Fusion)
```bash
doc-str search --query "<keywords_or_phrase>" --mode hybrid --limit 5 --output <temp_search.json>
```
- `--mode hybrid` (default): Reciprocal Rank Fusion ($0.6 \cdot \text{FTS} + 0.4 \cdot \text{Vector}$).
- `--mode fts`: Exact BM25 keyword matching (useful for error codes or register names).
- `--mode vec`: Semantic vector similarity matching.
- `--min-fts-rank <N>`: Optional filter to enforce keyword precision and eliminate pure vector noise.

#### Step 3: Precise Chunk Retrieval & XML Grounding
```bash
doc-str get-chunk --chunk-id <id> --format xml --output <temp_chunk.xml>
```
Optionally add `--include-neighbors` and `--max-context-tokens 2000` ONLY if context from adjacent sections is strictly necessary:
```bash
doc-str get-chunk --chunk-id <id> --include-neighbors --max-context-tokens 2000 --format xml --output <temp_chunk.xml>
```

---

### 3. XML Grounding & Citation Rules for Agents

When inspecting `<document_context>` XML from `--format xml`:

1. **Strict Context Boundaries**: Answer questions using ONLY information contained within `<document_context>`.
2. **Metadata Citation**: Cite explicit document titles, page numbers, section numbers, C symbol names, and line ranges:
   - *Spec Citation*: `[Source: Page 682, Section 9.2.6]`
   - *Code Citation*: `[Source: Platform/Pei/S3Resume.c, Symbol: S3ResumeBoot, Lines 20-28]`
3. **Cross-Section Neighbor Caution**: Note `<neighbor_context cross_section="true">` flags when context crosses section boundaries.
4. **Zero-Hallucination Guard**: If the specific register bitfield or code parameter is not present in the context, explicitly state "Insufficient evidence in context." Do NOT extrapolate.

---

### 4. Tagging & Deletion

```bash
# Tagging
doc-str tag --doc-id <id> --tags "bios,amd,smm"

# Deletion
doc-str delete --doc-id <id>
```
