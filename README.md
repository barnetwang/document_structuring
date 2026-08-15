# Document Structuring & Spec-to-Code Knowledge Engine

**Parse PDF/DOCX Specs, C/H Source Code, and EDK2 Build Configs into Structured, Searchable Markdown & AST Chunks**

`doc-str` is an enterprise-grade document structuring engine and LLM agent skill. It parses multi-thousand-page hardware specifications (PDF/DOCX), C/H firmware source code, and EDK2 build metadata (`.inf`, `.dec`, `.dsc`, `.fdf`), stores them in a unified SQLite database, and provides **TOC-First Agent Workflows**, **Reciprocal Rank Fusion (RRF) Hybrid Search**, **Token-Budgeted Context Truncation**, and **Spec-to-Code Cross-Domain XML Grounding**.

本工具可將數千頁 PDF / Word 規格書、C/H 韌體原始碼與 EDK2 設定檔依結構切成 Markdown 與 AST 區塊，存入 SQLite（含 FTS5 全文檢索與 CPU ONNX 向量嵌入），並支援 RRF 混合檢索、TOC-First 檢索流程與動態 Token 預算 XML 封裝。專為 本地端 Qwen 27B / Antigravity / Hermes Agent 設計，防止 Context 爆炸與模型幻覺。

---

## 🌟 Key Architectural Features

### Phase 1: TOC-First Workflow, Dynamic Token Budgeting & XML Grounding
- **TOC-First Navigation**: Forces agents to inspect document Table of Contents before search, avoiding "Lost in the Middle" hallucination.
- **Dynamic Token Budget Truncation**: Strictly enforces user token budgets (`--max-context-tokens`) with dynamic XML entity safety reserves.
- **Trailing Preservation**: Intelligently preserves the nearest tail section of adjacent chunks at natural line boundaries.

### Phase 2: Borderless Table Recovery, Structured Cross-References & Cross-Section Guarding
- **Borderless Table Fallback**: Heuristically merges adjacent borderless register tables (`find_tables(strategy="text")`) split across page boundaries.
- **Structured Cross-Reference Links**: Automatically extracts explicit section/table/figure links (`<ref type="..." target="..." />`).
- **Cross-Section Neighbor Guarding**: Tags adjacent chunks from different parent branches with `cross_section="true"` to prevent context contamination.

### Phase 3: CPU ONNX Hybrid Search & RRF Engine
- **0 VRAM CPU Inference**: Uses `fastembed` (`BAAI/bge-small-en-v1.5`, 384 dimensions) on CPU ONNX Runtime, keeping 100% GPU VRAM available for LLM inference (e.g. Qwen 27B).
- **SQLite BLOB Vector Storage**: Stores L2-normalized float32 byte arrays in SQLite schema v6 (`chunk_embeddings`).
- **Reciprocal Rank Fusion (RRF)**: Merges FTS5 BM25 keyword rankings ($r_{\text{fts}}$) and ONNX vector similarity ($r_{\text{vec}}$):
  $$\text{RRF Score} = 0.6 \cdot \frac{1}{60 + r_{\text{fts}}} + 0.4 \cdot \frac{1}{60 + r_{\text{vec}}}$$

### Phase 4: Spec-to-Code Extractor (C/H Tree-sitter & EDK2 Config Parser)
- **Tree-sitter C/H AST Parser**: Accurately extracts functions, typedef structs, unions, enums, macros, and preceding doc comments (`//` & `/* ... */`).
- **EDK2 Config Parser**: Slices `.inf`, `.dec`, `.dsc`, `.fdf` metadata into section blocks (`[Defines]`, `[Protocols]`, `[Guids]`, `[Pcd]`).
- **Cross-Domain XML Grounding**: Formats code symbol chunks with `<metadata doc_type="..." symbol_name="..." file_path="..." line_range="..." />` for precise LLM attribution.

---

## 🚀 Quick Start

### Installation

```bash
pip install -e .
```

This installs the `doc-str` CLI (entry point: `doc_structuring.cli:main`). Dependencies include `PyMuPDF`, `pymupdf4llm`, `python-docx`, `tree-sitter(-c)`, and `fastembed`. On first `embed` run, fastembed downloads the ~130 MB ONNX model (`BAAI/bge-small-en-v1.5`) into its cache directory — subsequent runs are offline.

### Use as a Hermes Agent Skill

The repo doubles as an agent skill: copy (or symlink) this folder into your skills directory and install it editable in the agent's Python environment:

```bash
cp -r document_structuring ~/.local/share/hermes/skills/doc-str   # or your profile's skills dir
pip install -e <path-to>/doc-str
```

`SKILL.md` drives the agent workflow (TOC-First → Hybrid Search → XML Grounding); `references/cli_spec.md` carries the full CLI reference.

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

## 📂 Data Layout

```text
<base_dir>/
├── documents.db                 # SQLite DB (schema v6: metadata, FTS5, embeddings, code chunks)
├── output/
│   ├── global_catalog.md        # Documents grouped by tags
│   └── <document_id>/
│       ├── toc.json
│       ├── index.md
│       └── chunks/*.md
```

---

## 🧪 Testing

A `dev` extra ships with pytest:

```bash
pip install -e ".[dev]"
python -m pytest -v
```

(No test suite is committed yet — the package builds and runs cleanly against real PDF/DOCX/C inputs; see the Quick Start examples above for manual smoke tests.)

---

## 📜 License

Apache License 2.0 — see [LICENSE](LICENSE).
