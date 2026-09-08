# Document Structuring & Spec-to-Code Knowledge Engine

**Parse PDF/DOCX Specs, C/H Source Code, and EDK2 Build Configs into Structured, Searchable Markdown & AST Chunks**

`doc-str` is a document structuring toolkit and LLM agent skill. It parses multi-thousand-page hardware specifications (PDF/DOCX), C/H firmware source code, and EDK2 build metadata (`.inf`, `.dec`, `.dsc`, `.fdf`), stores them in a unified SQLite database, and provides **TOC-First Agent Workflows**, **RRF Hybrid Search**, **Token-Budgeted Context Truncation**, and **Spec-to-Code Cross-Domain XML Grounding**. The project is an early-stage practical tool — see [Known Limitations](#known-limitations) for the current precision and contract boundaries.

本工具可將數千頁 PDF / Word 規格書、C/H 韌體原始碼與 EDK2 設定檔依結構切成 Markdown 與 AST 區塊，存入 SQLite（含 FTS5 全文檢索與 CPU ONNX 向量嵌入），並支援 RRF 混合檢索、TOC-First 檢索流程與動態 Token 預算 XML 封裝。適合搭配任何本地 LLM（如 Qwen 27B）或 agentic 編程環境使用，防止 Context 爆炸與模型幻覺。

---

## Key Architectural Features

### Phase 1: TOC-First Workflow, Dynamic Token Budgeting & XML Grounding
- **TOC-First Navigation**: Forces agents to inspect document Table of Contents before search, avoiding "Lost in the Middle" hallucination.
- **Dynamic Token Budget Truncation**: `--max-context-tokens` (an *estimated* count) constrains neighbor expansion with a fixed XML entity-escape reserve; the target chunk is always returned in full.
- **Trailing Preservation**: Intelligently preserves the nearest tail section of adjacent chunks at natural line boundaries.

### Phase 2: Borderless Table Recovery, Structured Cross-References & Cross-Section Guarding
- **Borderless Table Fallback** (implemented, not yet wired into the pipeline): heuristically merges adjacent borderless register tables (`find_tables(strategy="text")`) split across page boundaries — coverage currently depends on the built-in converter alone.
- **Structured Cross-Reference Links**: Automatically extracts explicit section/table/figure links (`<ref type="..." target="..." />`).
- **Cross-Section Neighbor Guarding**: Tags adjacent chunks from different parent branches with `cross_section="true"` to prevent context contamination.

### Phase 3: CPU ONNX Hybrid Search & RRF Engine
- **0 VRAM CPU Inference**: Uses `fastembed` (`BAAI/bge-small-en-v1.5`, 384 dimensions) on CPU ONNX Runtime, keeping 100% GPU VRAM available for LLM inference (e.g. Qwen 27B).
- **SQLite BLOB Vector Storage**: Stores L2-normalized float32 byte arrays in SQLite schema v6 (`chunk_embeddings`).
- **Reciprocal Rank Fusion (RRF)**: Merges FTS5 keyword candidate rankings ($r_{\text{fts}}$; currently ordered by document upload recency / section, not yet by BM25 score) and ONNX vector similarity ($r_{\text{vec}}$):
 $$\text{RRF Score} = 0.6 \cdot \frac{1}{60 + r_{\text{fts}}} + 0.4 \cdot \frac{1}{60 + r_{\text{vec}}}$$

### Phase 4: Spec-to-Code Extractor (C/H Tree-sitter & EDK2 Config Parser)
- **Tree-sitter C/H AST Parser**: Extracts top-level functions, typedefs, structs, unions, enums, and object-form macros with preceding doc comments (`//` & `/* ... */`). Declarations inside `#if`/header-guard blocks and function-like macros are not yet covered.
- **EDK2 Config Parser**: Slices `.inf`, `.dec`, `.dsc`, `.fdf` metadata into section blocks (`[Defines]`, `[Protocols]`, `[Guids]`, `[Pcd]`) — source-level slicing only; no include/macro or build-graph resolution.
- **Cross-Domain XML Grounding**: Formats code symbol chunks with `<metadata doc_type="..." symbol_name="..." file_path="..." line_range="..." />` for precise LLM attribution.

---

## Quick Start

### Installation

```bash
pip install .
```

For local development: `pip install -e .` (adds the `dev` extra when you write `pip install -e ".[dev]"`).

This installs the `doc-str` CLI (entry point: `doc_structuring.cli:main`). Dependencies include `PyMuPDF`, `pymupdf4llm`, `python-docx`, `tree-sitter(-c)`, and `fastembed`. On first `embed` run, fastembed downloads the ~130 MB ONNX model (`BAAI/bge-small-en-v1.5`) into its cache directory — subsequent runs are offline.

Note: `--mode hybrid` (the default) degrades silently to FTS-only, and `--mode vec` returns nothing, until you have run `doc-str embed --doc-id <id> ...` for that document. The CLI warns on stderr in both cases.

### Use as an Agent Skill (optional)

The repo doubles as an agent skill. Copy (or symlink) the folder into your agent's skills directory and install it editable in the agent's Python environment:

```bash
cp -r document_structuring <your-skills-dir>/doc-str
pip install -e <path-to>/doc-str
```

Skills-directory locations vary by agent (e.g. `~/.local/share/hermes/skills/` on Hermes Agent, `~/.claude/skills/` on Claude Code). `SKILL.md` drives the agent workflow (TOC-First → Hybrid Search → XML Grounding); `references/cli_spec.md` carries the full CLI reference.

### Data Directory

The CLI creates `documents.db` and an `output/` tree in the current working directory unless you pass `--base-dir <path>` (or set `DOC_STRUCTURING_BASE_DIR`). Keep the base dir stable between runs, or the index will not be found.

### CLI Usage Examples

#### 1. Ingest Hardware Specification (PDF)
```bash
doc-str parse --file "AMD_PPR_Spec.pdf" --tags "bios,amd,ppr" --output parse_result.json
```

#### 2. Ingest Firmware Source Code & EDK2 Configs
```bash
doc-str parse-code --file "Platform/Pei/S3Resume.c" --output parse_c.json
doc-str parse-code --file "Platform/Pei/S3ResumePei.inf" --output parse_inf.json
```

#### 3. Generate Vector Embeddings (ONNX fastembed)
```bash
doc-str embed --doc-id 1 --output embed_result.json
```

#### 4. TOC-First Inspection
```bash
doc-str toc --doc-id 1 --output toc.json
```

#### 5. Cross-Domain RRF Hybrid Search
```bash
# RRF Hybrid Search across specs and code
doc-str search --query "S3ResumeBoot gEfiSmmBase2ProtocolGuid" --mode hybrid --limit 5 --output search.json

# Strict keyword query with FTS rank filter
doc-str search --query "0x1A4" --mode hybrid --min-fts-rank 10 --output search_strict.json
```

#### 6. Token-Budgeted XML Retrieval
```bash
doc-str get-chunk --chunk-id 100 --include-neighbors --max-context-tokens 2000 --format xml --output chunk100.xml
```

---

## Data Layout

```text
<base_dir>/
├── documents.db # SQLite DB (schema v6: metadata, FTS5, embeddings, code chunks)
├── output/
│ ├── global_catalog.md # Documents grouped by tags
│ └── <document_id>/
│ ├── toc.json
│ ├── index.md
│ └── chunks/*.md
```

---

## Known Limitations

Verified at v0.1.2; tracked for fix in order of priority:

- **Search ordering**: FTS results are ordered by upload recency / section, not BM25 relevance; `--min-fts-rank` filters that positional order (hybrid mode only). Newer documents can outrank more relevant older ones.
- **Token budget**: `--max-context-tokens` is an estimate applied to neighbors only; the target chunk is never truncated, and counts are not tokenizer-exact.
- **Page location**: PDF `page_start` is reliable only where headings match PDF bookmarks; DOCX page numbers are always the placeholder 1 (cite by section/symbol instead).
- **Tables**: DOCX rendering escapes `|` oddly and deduplicates consecutive identical rows; the advertised PDF borderless fallback is not yet active.
- **C/H extraction**: top-level symbols only; no `#if`-container or function-like-macro coverage; comment attachment is inconsistent.
- **Document identity**: `parse` keys by filename basename (same-named files replace each other) and re-parse is destructive; ingestion is not safe to run concurrently.
- **Embeddings**: recomputed in full per run, no staleness check, English-oriented model.

---

## Testing

A small regression suite lives in [`tests/`](tests/). Install with the `dev` extra:

```bash
pip install -e ".[dev]"
python -m pytest -v
```

CI runs the suite (plus a wheel build + `--help` smoke check) on Python 3.10 and 3.12 on every push to `main` and on pull requests. See `.github/workflows/tests.yml`.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
