# Document Structuring & Management Skill (Hermes Agent)
> **PDF & Word 文件結構化解析與管理工具**

This skill parses PDF and Word (`.docx`) documents, slices them into structured Markdown sections based on document headings, stores the segments in a local SQLite database, and supports full-text search, TOC viewing, chunk retrieval, and cascading deletion.

本工具（Skill）專為 Hermes 代理人 (Agent) 設計，用於解析 PDF 與 Word (`.docx`) 文件。它能根據標題層級將長文件切割成結構化的 Markdown 區塊，存入本地 SQLite 資料庫，並支援全文檢索、目錄（TOC）檢視、特定區塊讀取以及階層式刪除。

---

## Table of Contents / 目錄
1. [English Guide](#english-guide)
   - [Features](#features)
   - [Prerequisites](#prerequisites)
   - [Installation](#installation)
   - [Usage Guide](#usage-guide)
   - [Database Schema](#database-schema)
2. [中文使用指南](#中文使用指南)
   - [功能特點](#功能特點)
   - [環境要求](#環境要求)
   - [安裝步驟](#安裝步驟)
   - [使用說明](#使用說明)
   - [資料庫結構](#資料庫結構)
3. [Token Economics / Token 經濟效益](#token-economics--token-經濟效益)
4. [Troubleshooting / 常見問題與排除](#troubleshooting--常見問題與排除)

---

# English Guide

## Features
- **Batch Processing**: Parses PDF pages in 50-page batches using `pymupdf4llm` (~16% performance improvement on large files).
- **Heading Page-Number Synchronization**: Syncs headings to exact physical pages via PDF's internal Table of Contents (Bookmarks).
- **SQLite FTS5 Full-Text Search**: Instantly searches across thousands of text chunks using keyword logic with wildcard and LIKE fallback.
- **Crash-Safe Operations**: Writes DB records before physical file updates, eliminating orphaned files on disk if the script crashes.
- **Duplicate Document Guard**: Automatically removes old database entries and old physical folder structures upon re-parsing same filenames.

---

## Prerequisites
- **Python**: 3.10+ (Python 3.11+ recommended)
- **Required Libraries**:
  - `PyMuPDF` (for PDF rendering & layout parsing)
  - `pymupdf4llm` (for Markdown conversions)
  - `python-docx` (for Word document structure parsing)

---

## Installation

### 1. Register Skill
Depending on your agent setup, place this skill directory in one of the following locations:
- **Project Workspace Scope**: `.agents/skills/document_structuring/`
- **Global User Scope**: `~/.hermes/skills/document_structuring/` or `~/.gemini/config/skills/document_structuring/`

### 2. Install Dependencies
Run the following command in your terminal to install the necessary packages:
```bash
pip install PyMuPDF pymupdf4llm python-docx
```

---

## Usage Guide

Run the utility script via CLI. Always run commands from the **project workspace root**. Except for `delete`, all commands require a `--output` file path to write results.

### 1. Parse a Document
Parses a PDF/DOCX and stores chunks.
```bash
python scripts/document_tool.py parse --file "your-manual.pdf" --output "parse_result.json"
```
- **Output JSON Format (`parse_result.json`)**:
  ```json
  {
    "success": true,
    "document_id": 1,
    "filename": "your-manual.pdf",
    "chunk_count": 9834
  }
  ```

### 2. List All Parsed Documents
Lists all documents saved in the SQLite database.
```bash
python scripts/document_tool.py list --output "documents_list.json"
```
- **Output JSON Format (`documents_list.json`)**:
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

### 3. Retrieve Table of Contents (TOC)
Returns all headings and chunks of a document, sorted by section numbers.
```bash
python scripts/document_tool.py toc --doc-id 1 --output "toc_data.json"
```
- **Output JSON Format (`toc_data.json`)**:
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

### 4. Search across Chunks (Full-Text)
Searches headings and contents using SQLite FTS5 index.
```bash
python scripts/document_tool.py search --query "ACPI" --output "search_results.json"
```
- **Output JSON Format (`search_results.json`)**:
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

### 5. Retrieve a Specific Chunk's Content
Reads the full markdown content of a single section block.
```bash
python scripts/document_tool.py get-chunk --chunk-id 123 --output "chunk_content.json"
```
- **Output JSON Format (`chunk_content.json`)**:
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

### 6. Delete a Document
Performs a cascading deletion (removes database entries, cascading chunk records, and deletes files inside `output/<doc_id>/` from disk).
```bash
python scripts/document_tool.py delete --doc-id 1
```
- **Stdout Output**:
  `Success: Document 'your-manual.pdf' (ID: 1) and all its associated chunks have been deleted.`

---

## Database Schema
The SQLite database file `documents.db` contains three tables:
- **`_meta`**: Stores internal schema configuration (version checks).
- **`documents`**:
  - `id`: Auto-incrementing primary key.
  - `filename`: Name of the source file.
  - `upload_time`: Formatted timestamp.
  - `chunk_count`: Total chunks generated.
  - `status`: Import status (`success`, `failed`).
- **`chunks`**:
  - `id`: Auto-incrementing primary key (sequential rowid).
  - `document_id`: Foreign key cascading on delete.
  - `section_number`: Normalised section heading index (e.g. `1.1.2`).
  - `title`: Sanitised heading title text.
  - `content`: Extracted text/markdown body.
  - `page_start`: 1-based page start in the source PDF.
  - `file_path`: Relative path of physical `.md` file.

---
---

# 中文使用指南

## 功能特點
- **批次 PDF 解析**：改用 50 頁批次解析模式調用 `pymupdf4llm`，大幅降低排版分析開銷（大文件解析速度提升約 16%）。
- **書籤目錄頁碼同步**：透過 PDF 內建的 TOC (Table of Contents / Bookmark) 書籤頁碼比對，將標題解析的 `page_start` 與實體頁碼對齊。
- **SQLite FTS5 全文檢索**：將標題與內文同步更新至虛擬表，支援快速多詞 AND 檢索，並包含 LIKE 語法容錯。
- **斷電/崩潰安全設計**：採用「先 Commit 資料庫，後寫入實體檔案」順序。即使寫檔途中崩潰，資料庫與磁碟狀態也保持一致，不產生多餘髒檔案。
- **重置除錯機制**：重新 parse 同檔名文件時，會自動清除舊版資料庫列與實體磁碟目錄，防止空間洩漏。

---

## 環境要求
- **Python 版本**：3.10+ (建議 Python 3.11 以上)
- **依賴套件**：
  - `PyMuPDF` (PDF 渲染及版面結構提取)
  - `pymupdf4llm` (Markdown 文本轉換)
  - `python-docx` (Word 文件結構解析)

---

## 安裝步驟

### 1. 置放 Skill
根據您的代理人配置，將此 skill 資料夾放入以下其中一個目錄：
- **本機專案 scope**：`.agents/skills/document_structuring/`
- **全域環境 scope**：`~/.hermes/skills/document_structuring/` 或 `~/.gemini/config/skills/document_structuring/`

### 2. 安裝 Python 依賴
在終端機中執行以下指令以安裝必要的套件：
```bash
pip install PyMuPDF pymupdf4llm python-docx
```

---

## 使用說明

透過命令列 CLI 執行工具，指令請一律在 **專案工作目錄根路徑 (Workspace Root)** 下執行。除了 `delete` 指令外，其餘指令皆必須提供 `--output` 參數來寫出 JSON 結果。

### 1. 解析檔案 (Parse)
將 PDF 或 DOCX 長文件切片存入資料庫與磁碟。
```bash
python scripts/document_tool.py parse --file "your-manual.pdf" --output "parse_result.json"
```
- **輸出 JSON (`parse_result.json`)**：
  ```json
  {
    "success": true,
    "document_id": 1,
    "filename": "your-manual.pdf",
    "chunk_count": 9834
  }
  ```

### 2. 列出已解析文件 (List)
列出 SQLite 資料庫中目前所有管理的文件。
```bash
python scripts/document_tool.py list --output "documents_list.json"
```
- **輸出 JSON (`documents_list.json`)**：
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

### 3. 讀取目錄結構 (TOC)
取得指定文件底下的所有章節與檔案路徑，依章節號排序。
```bash
python scripts/document_tool.py toc --doc-id 1 --output "toc_data.json"
```
- **輸出 JSON (`toc_data.json`)**：
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

### 4. 全文檢索章節 (Search)
在所有文件的章節標題與內文進行關鍵字全文檢索。
```bash
python scripts/document_tool.py search --query "ACPI" --output "search_results.json"
```
- **輸出 JSON (`search_results.json`)**：
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

### 5. 獲取特定章節內文 (Get Chunk)
藉由資料庫 Chunk ID 提取該章節的完整 Markdown 格式內容。
```bash
python scripts/document_tool.py get-chunk --chunk-id 123 --output "chunk_content.json"
```
- **輸出 JSON (`chunk_content.json`)**：
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

### 6. 刪除文件項目 (Delete)
自資料庫刪除文件（階層刪除 chunks 外鍵），並抹除磁碟上的實體 `output/<doc_id>/` 資料夾。
```bash
python scripts/document_tool.py delete --doc-id 1
```
- **終端機輸出**：
  `Success: Document 'your-manual.pdf' (ID: 1) and all its associated chunks have been deleted.`

---

## 資料庫結構
SQLite 資料庫檔案預設為 `documents.db`，由以下三張資料表組成：
- **`_meta`**：記錄資料庫內部配置版本以提供升級防護。
- **`documents`** (文件主表)：
  - `id`: 資料庫自動遞增主鍵。
  - `filename`: 來源檔名。
  - `upload_time`: 上傳時間（`YYYY-MM-DD HH:MM:SS`）。
  - `chunk_count`: 切片後的總章節數。
  - `status`: 解析狀態 (`success`, `failed`)。
- **`chunks`** (章節切片表)：
  - `id`: 資料庫遞增主鍵（實體 rowid）。
  - `document_id`: 外鍵，串聯 `documents.id` (外鍵 ON DELETE CASCADE)。
  - `section_number`: 歸一化章節號 (例如 `1.1.2`)。
  - `title`: 標題文字。
  - `content`: 切出的實體文字/Markdown 內容。
  - `page_start`: 該段落起始頁碼 (1-based)。
  - `file_path`: 磁碟上實體 `.md` 檔案的相對路徑。

---

# Token Economics / Token 經濟效益

Large technical manuals (e.g., 2,000+ page PDFs) pose context limitations for LLMs. This tool compresses token usage drastically by serving targeted section chunks.

大型技術手冊（例如 2,000 頁以上之規格書）會帶來龐大的 Token 開銷。本工具利用全文檢索精確提取段落，可達到極高的 Token 壓縮效益：

| Scenario / 場景 | Raw PDF Tokens / 原始 Tokens | Via Chunk Retrieval / 使用切片提取 | Compression Ratio / 壓縮倍率 |
| --------------- | ---------------------------- | --------------------------------- | ---------------------------- |
| Full 2,400-page PPR | ~4.5M tokens | FTS5 search → top 5 hits → ~2.5K tokens | **~1,800×** |
| Chapter query (e.g. "MSR registers") | ~150K tokens (whole chapter) | get-chunk → ~3K tokens per section | **~50×** |
| Specific register lookup | Full PDF needed otherwise | Targeted chunk → ~1.2K tokens | **~3,750×** |

---

# Troubleshooting / 常見問題與排除

1. **`ModuleNotFoundError: No module named 'fitz'` / `'docx'`**
   - **Reason**: Dependencies are missing in your active environment.
   - **Solution**: Ensure your python environment is active and install libraries:
     `pip install PyMuPDF pymupdf4llm python-docx`
   - **原因**：目前作用中的 Python 環境尚未安裝必要的解析庫。
   - **解法**：請確保使用正確的 Python 環境，並執行：
     `pip install PyMuPDF pymupdf4llm python-docx`

2. **Missing `--output` Parameter / 缺少 `--output` 參數**
   - **Reason**: Except for `delete`, all CLI queries must output JSON to a designated file.
   - **Solution**: Always append `--output result.json` to your commands.
   - **原因**：除了刪除指令之外，CLI 工具預期一律將結構化 JSON 資料導出至指定路徑。
   - **解法**：請務必在指令尾端加入 `--output <檔案路徑.json>`。

3. **Relative Path / Working Directory Issues / 工作路徑混亂**
   - **Reason**: Running CLI from inside `scripts/` folder or global paths.
   - **Solution**: Always navigate to the **project workspace root** (where `documents.db` and `output/` should reside) before calling Python commands.
   - **原因**：於 `scripts/` 資料夾內或任意全域路徑執行指令，會導致資料庫與 Markdown 檔案被建立在錯誤的地方。
   - **解法**：請一律將終端機切換至 **專案工作目錄根路徑**，再行執行 `python scripts/document_tool.py ...`。
