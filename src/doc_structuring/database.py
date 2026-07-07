"""SQLite persistence layer: schema management, chunk storage, and FTS5 search."""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from .config import AppConfig
from .utils import sanitize_filename, section_sort_key

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
@contextmanager
def get_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that guarantees connection closure.

    ``sqlite3``'s built-in context manager only commits/rollbacks — it does
    **not** close the connection.  This wrapper ensures the connection is
    always closed, even on exceptions.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FTS5 safety
# ---------------------------------------------------------------------------
def _fts5_escape_token(token: str) -> str | None:
    """Sanitize a user token for safe FTS5 MATCH usage.

    Strips all FTS5 special characters, keeps only alphanumeric and CJK.
    Returns ``None`` if the cleaned token is empty.
    """
    cleaned = re.sub(r'[^\w\u4e00-\u9fff]', '', token, flags=re.UNICODE).strip()
    if not cleaned:
        return None
    return f'"{cleaned}"'


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------
def _schema_version_ok(conn: sqlite3.Connection) -> bool:
    """Return ``True`` if the DB schema is already up-to-date (idempotent guard)."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_meta';"
    )
    if not cursor.fetchone():
        return False
    cursor.execute("SELECT value FROM _meta WHERE key='schema_version';")
    row = cursor.fetchone()
    return row is not None and int(row['value']) == SCHEMA_VERSION


def init_db(config: AppConfig | None = None) -> None:
    """Initialise database tables, run migrations, and register FTS5 triggers.

    Skips all work when the schema is already at ``SCHEMA_VERSION``
    (~1 ms per call after first run).
    """
    if config is None:
        config = AppConfig()

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()

        # Fast path: schema already current → return immediately
        if _schema_version_ok(conn):
            return

        cursor.execute("PRAGMA foreign_keys = ON;")

        # -----------------------------------------------------------------
        # Schema Migration v1→v2: Remove UNIQUE constraint from
        # documents.filename.  Runs at most once per database.
        # -----------------------------------------------------------------
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='documents';"
        )
        table_exists = cursor.fetchone()

        if table_exists:
            cursor.execute("PRAGMA index_list(documents);")
            indexes = cursor.fetchall()
            has_unique = False
            for idx in indexes:
                safe_name = idx['name']
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', safe_name):
                    continue  # skip malformed names — safety guard
                cursor.execute(f"PRAGMA index_info({safe_name});")
                cols = cursor.fetchall()
                if idx['unique'] == 1 and any(c['name'] == 'filename' for c in cols):
                    has_unique = True
                    break

            if has_unique:
                fk_was_on = False
                try:
                    cursor.execute("PRAGMA foreign_keys = OFF;")
                    fk_was_on = True
                    cursor.execute("ALTER TABLE documents RENAME TO temp_documents;")
                    cursor.execute("""
                        CREATE TABLE documents (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            filename TEXT NOT NULL,
                            upload_time TEXT NOT NULL,
                            chunk_count INTEGER DEFAULT 0,
                            status TEXT DEFAULT 'success'
                        );
                    """)
                    cursor.execute("""
                        INSERT INTO documents (id, filename, upload_time, chunk_count, status)
                        SELECT id, filename, upload_time, chunk_count, status
                        FROM temp_documents;
                    """)
                    cursor.execute("DROP TABLE temp_documents;")
                finally:
                    if fk_was_on:
                        cursor.execute("PRAGMA foreign_keys = ON;")
                conn.commit()

        # Create documents table (if first run)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success'
            );
        """)

        # Create chunks table (if first run)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                section_number TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                file_path TEXT,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            );
        """)

        # Create document_tags table (if first run or upgrade to v3)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_tags (
                document_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (document_id, tag),
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            );
        """)

        # -----------------------------------------------------------------
        # FTS5 Virtual Table & Triggers
        # -----------------------------------------------------------------
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title,
                content,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61'
            );
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, title, content)
                VALUES('delete', old.id, old.title, old.content);
            END;
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, title, content)
                VALUES('delete', old.id, old.title, old.content);
                INSERT INTO chunks_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;
        """)

        # Index existing chunks if FTS is empty
        try:
            cursor.execute("SELECT COUNT(*) FROM chunks_fts_data;")
            fts_data_count = cursor.fetchone()[0]
            if fts_data_count <= 2:
                cursor.execute("SELECT COUNT(*) FROM chunks;")
                chunks_count = cursor.fetchone()[0]
                if chunks_count > 0:
                    cursor.execute(
                        "INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild');"
                    )
        except sqlite3.OperationalError:
            # FTS5 table may not exist yet on very old dbs — skip gracefully
            pass

        # Record schema version for idempotent init_db skip on future calls
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        cursor.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?);",
            (str(SCHEMA_VERSION),),
        )

        conn.commit()


# ---------------------------------------------------------------------------
# Document CRUD
# ---------------------------------------------------------------------------
def save_document(
    filename: str,
    chunks: list[dict],
    config: AppConfig | None = None,
    tags: list[str] | None = None,
) -> int:
    """Save document metadata and chunks, then write physical Markdown files.

    Safety: commits DB INSERTs BEFORE writing physical files so that a
    crash during file I/O cannot leave orphaned files with no DB reference.

    Uses ``executemany`` for the ``file_path`` UPDATE to eliminate the N+1
    query pattern.
    """
    if config is None:
        config = AppConfig()

    init_db(config)

    old_doc_id: int | None = None

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()

        try:
            # Check for duplicate document filename
            cursor.execute(
                "SELECT id FROM documents WHERE filename = ?", (filename,)
            )
            existing = cursor.fetchone()
            if existing:
                old_doc_id = existing['id']
                logger.warning(
                    "Document '%s' already exists (ID: %s). Deleting old copy.",
                    filename,
                    old_doc_id,
                )
                cursor.execute(
                    "DELETE FROM chunks WHERE document_id = ?", (old_doc_id,)
                )
                cursor.execute(
                    "DELETE FROM documents WHERE id = ?", (old_doc_id,)
                )

            # Step 1: Insert document row
            upload_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO documents (filename, upload_time, chunk_count, status) "
                "VALUES (?, ?, ?, 'success')",
                (filename, upload_time, len(chunks)),
            )
            document_id = cursor.lastrowid

            base_prefix = f"output/{document_id}/chunks/"

            # Step 2: Batch INSERT all chunks with placeholder file_path
            if chunks:
                cursor.executemany(
                    "INSERT INTO chunks "
                    "(document_id, section_number, title, content, page_start, file_path) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            document_id,
                            chunk["number"],
                            chunk["title"],
                            chunk["content"],
                            chunk["page_start"],
                            "",  # placeholder — filled after files written
                        )
                        for chunk in chunks
                    ],
                )

            # Commit DB NOW before any file I/O (crash safety)
            conn.commit()

            # Save tags if provided
            if tags:
                cursor.executemany(
                    "INSERT INTO document_tags (document_id, tag) VALUES (?, ?)",
                    [(document_id, tag.strip()) for tag in tags if tag.strip()]
                )
                conn.commit()

            # Get ACTUAL rowids via sequential query (not predicted)
            cursor.execute(
                "SELECT id FROM chunks WHERE document_id = ? ORDER BY id ASC",
                (document_id,),
            )
            actual_ids = [r["id"] for r in cursor.fetchall()]

        except Exception:
            conn.rollback()
            raise

    # Clean up old physical files (safe since DB commit succeeded)
    if old_doc_id is not None:
        old_doc_dir = config.output_dir / str(old_doc_id)
        if old_doc_dir.exists():
            shutil.rmtree(old_doc_dir)

    # Step 3: Write physical files
    doc_dir = config.output_dir / str(document_id)
    chunks_dir = doc_dir / config.chunks_subdir
    chunks_dir.mkdir(parents=True, exist_ok=True)

    images_dir = doc_dir / "images"

    toc_entries_list: list[dict] = []
    update_pairs: list[tuple[str, int]] = []

    image_pattern = re.compile(r'<!-- IMAGE: (\{.*?\}) -->')

    for idx, chunk in enumerate(chunks):
        if idx >= len(actual_ids):
            break  # safety guard
        chunk_db_id = actual_ids[idx]
        number = chunk["number"]
        title = chunk["title"]
        clean_title = sanitize_filename(title)
        chunk_filename = f"{chunk_db_id}_{number}_{clean_title}.md"

        chunk_images = []
        chunk_content = chunk["content"]
        img_seq = 1

        def replace_image_placeholder(match):
            nonlocal img_seq
            meta_json = match.group(1)
            try:
                meta = json.loads(meta_json)
                temp_path = meta.get("temp_path")
                caption = meta.get("caption", f"Image {img_seq}")
                contained_text_list = meta.get("contained_text", [])

                if temp_path and os.path.exists(temp_path):
                    images_dir.mkdir(parents=True, exist_ok=True)
                    clean_number = sanitize_filename(number)
                    img_name = f"{chunk_db_id}_{clean_number}_{img_seq}.png"
                    dest_path = images_dir / img_name

                    shutil.copy(temp_path, dest_path)
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

                    rel_img_path = f"images/{img_name}"
                    chunk_images.append({
                        "rel_path": rel_img_path,
                        "abs_path": str(dest_path),
                        "caption": caption,
                        "contained_text": contained_text_list
                    })

                    img_seq += 1
                    return f"\n![{caption}](../{rel_img_path})\n"
            except Exception as e:
                logger.error("Failed to parse image placeholder: %s", e)
            return match.group(0)

        new_content = image_pattern.sub(replace_image_placeholder, chunk_content)

        filepath = chunks_dir / chunk_filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {number} {title}\n\n")
            f.write("metadata:\n")
            f.write(f"- source file: {chunk.get('source', filename)}\n")
            f.write(f"- section number: {number}\n")
            f.write(f"- page start: {chunk['page_start']}\n")
            if chunk_images:
                f.write("- images:\n")
                for img in chunk_images:
                    f.write(f"  - output/{document_id}/{img['rel_path']}\n")
            f.write("\n")
            f.write("content:\n")
            f.write(new_content)
            if chunk_images:
                f.write("\n\n###### 圖表及包含文字 (Diagrams & Contained Text):\n")
                for img in chunk_images:
                    f.write(f"- **圖片: {img['caption']}** ({img['rel_path']})\n")
                    if img["contained_text"]:
                        words_str = ", ".join([w["text"] for w in img["contained_text"]])
                        f.write(f"  * 包含文字: {words_str}\n")

        update_pairs.append((f"{base_prefix}{chunk_filename}", chunk_db_id))

        if number != "0":
            entry = {
                "id": chunk_db_id,
                "section_number": number,
                "title": title,
                "file": chunk_filename,
                "page_start": chunk["page_start"],
            }
            if chunk_images:
                entry["images"] = [img["rel_path"] for img in chunk_images]
            toc_entries_list.append(entry)

    # Batch UPDATE all file_path values in one call (N+1 fix)
    with get_db(config.db_path) as conn:
        conn.executemany(
            "UPDATE chunks SET file_path = ? WHERE id = ?", update_pairs
        )
        conn.commit()

    # Step 4: Write toc.json and index.md
    with open(doc_dir / "toc.json", "w", encoding="utf-8") as f:
        json.dump(toc_entries_list, f, indent=2, ensure_ascii=False)

    # Generate index.md (backward compat: dict keyed by section_number for display)
    toc_dict_for_display: dict[str, dict[str, Any]] = {}
    for entry in toc_entries_list:
        key = str(entry["section_number"]) + "_" + str(entry.get("id", ""))
        toc_dict_for_display[key] = {
            "file": entry["file"],
            "title": entry["title"],
            "page_start": entry["page_start"],
            "images": entry.get("images", []),
        }

    generate_document_index_file(doc_dir, toc_dict_for_display)

    # Clean up temp_pdf_tmp directory if it exists
    temp_pdf_dir = Path("temp_pdf_tmp")
    if temp_pdf_dir.exists():
        try:
            shutil.rmtree(temp_pdf_dir)
        except Exception:
            pass

    # Update global catalog
    generate_global_catalog(config)

    return document_id


def generate_document_index_file(
    doc_dir: Path,
    toc: dict[str, dict[str, Any]],
) -> None:
    """Write an ``index.md`` summarising a document's chunks."""
    index_path = doc_dir / "index.md"

    def _sort_key(k: str) -> list[tuple[int, int, str]]:
        # Extract section number part before the _id suffix
        sec_num = k.rsplit('_', 1)[0]
        return section_sort_key(sec_num)

    sorted_keys = sorted(toc.keys(), key=_sort_key)

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# 📄 Document Knowledge Base\n\n")
        f.write("> 此目錄與文件區塊由自動化腳本生成，為後續 LLM 與 RAG 查詢使用。\n\n")

        f.write("## 📁 Directory Structure\n\n")
        f.write("```text\n")
        f.write("output/\n")
        f.write("└── chunks/\n")

        for i, k in enumerate(sorted_keys):
            connector = "    └── " if i == len(sorted_keys) - 1 else "    ├── "
            f.write(f"{connector}{toc[k]['file']}\n")
        f.write("```\n\n")

        f.write("## 🔗 Section Index\n\n")
        for k in sorted_keys:
            title = toc[k]['title']
            chunk_filename = toc[k]['file']
            images = toc[k].get('images', [])

            # Remove the _id suffix from the key for clean display
            display_sec = k.rsplit('_', 1)[0]

            depth = display_sec.count('.')
            indent = "  " * depth

            f.write(f"{indent}* [{display_sec} {title}](chunks/{chunk_filename})\n")
            if images:
                for img in images:
                    img_name = img.split('/')[-1]
                    f.write(f"{indent}  * [🖼️ Image: {img_name}]({img})\n")


def list_documents(config: AppConfig | None = None) -> list[dict]:
    """Retrieve list of all documents."""
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, filename, upload_time, chunk_count, status
            FROM documents
            ORDER BY upload_time DESC
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_document_toc(
    document_id: int,
    config: AppConfig | None = None,
) -> list[dict]:
    """Get the table of contents (chunk metadata without full content) for a document."""
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, section_number, title, page_start, file_path
            FROM chunks
            WHERE document_id = ?
        """, (document_id,))
        rows = [dict(row) for row in cursor.fetchall()]

    rows.sort(key=lambda r: section_sort_key(r['section_number']))
    return rows


def get_chunk(
    chunk_id: int,
    config: AppConfig | None = None,
) -> dict | None:
    """Retrieve a single chunk with its document information."""
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.document_id, c.section_number, c.title,
                   c.content, c.page_start, c.file_path,
                   d.filename AS document_name
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.id = ?
        """, (chunk_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def search_chunks(
    query: str,
    document_id: int | None = None,
    config: AppConfig | None = None,
) -> list[dict]:
    """Search for keywords in chunk titles and contents using FTS5 with LIKE fallback.

    Each keyword is sanitised via :func:`_fts5_escape_token` before being
    passed to the FTS5 MATCH clause to prevent query-syntax injection.
    """
    if config is None:
        config = AppConfig()

    init_db(config)

    # Split query by spaces, sanitise each token, and filter empties
    raw_keywords = [k.strip() for k in query.split() if k.strip()]
    if not raw_keywords:
        return []

    escaped = [_fts5_escape_token(k) for k in raw_keywords]
    safe_keywords = [t for t in escaped if t is not None]
    if not safe_keywords:
        return []

    fts_query = " AND ".join(safe_keywords)

    sql = """
        SELECT c.id, c.document_id, c.section_number, c.title,
               c.page_start, c.file_path,
               d.filename AS document_name,
               snippet(chunks_fts, 1, '==', '==', '...', 150) AS snippet
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.id
        JOIN documents d ON c.document_id = d.id
        WHERE chunks_fts MATCH ?
    """
    params: list[Any] = [fts_query]

    if document_id is not None:
        sql += " AND c.document_id = ?"
        params.append(document_id)

    sql += " ORDER BY d.upload_time DESC, c.section_number ASC LIMIT 100"

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(sql, params)
            results = [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError as exc:
            # Fallback to standard LIKE queries if FTS5 syntax parser fails
            logger.warning("FTS5 query failed: %s. Falling back to LIKE search.", exc)

            sql_fallback = """
                SELECT c.id, c.document_id, c.section_number, c.title,
                       c.page_start, c.file_path,
                       d.filename AS document_name,
                       substr(c.content, 1, 300) AS snippet
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE (c.title LIKE ? OR c.content LIKE ?)
            """
            like_param = f"%{query}%"
            params_fallback: list[Any] = [like_param, like_param]

            if document_id is not None:
                sql_fallback += " AND c.document_id = ?"
                params_fallback.append(document_id)

            sql_fallback += (
                " ORDER BY d.upload_time DESC, c.section_number ASC LIMIT 100"
            )

            cursor.execute(sql_fallback, params_fallback)
            results = [dict(row) for row in cursor.fetchall()]

    # Clean up snippet newlines
    for r in results:
        if r['snippet']:
            r['snippet'] = r['snippet'].replace('\n', ' ').strip()
            if not r['snippet'].endswith('...'):
                r['snippet'] += '...'
        else:
            r['snippet'] = ''

    return results


def delete_document(
    document_id: int,
    config: AppConfig | None = None,
) -> str | None:
    """Delete a document, its chunks, and its physical files.

    Returns the filename of the deleted document, or ``None`` if the
    document was not found.
    """
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT filename FROM documents WHERE id = ?", (document_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            filename: str = row['filename']

            # ON DELETE CASCADE handles chunk rows
            cursor.execute(
                "DELETE FROM documents WHERE id = ?", (document_id,)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # Delete physical directory
    doc_dir = config.output_dir / str(document_id)
    if doc_dir.exists():
        shutil.rmtree(doc_dir)

    # Update global catalog
    generate_global_catalog(config)

    return filename


# ---------------------------------------------------------------------------
# Document Tags & Global Catalog
# ---------------------------------------------------------------------------
def set_document_tags(
    doc_id: int,
    tags: list[str],
    config: AppConfig | None = None,
) -> None:
    """Set the tags for a document, replacing any existing tags."""
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        try:
            # Delete existing tags
            cursor.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
            # Insert new tags
            if tags:
                cursor.executemany(
                    "INSERT INTO document_tags (document_id, tag) VALUES (?, ?)",
                    [(doc_id, tag.strip()) for tag in tags if tag.strip()]
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # Regenerate global catalog
    generate_global_catalog(config)


def get_document_tags(
    doc_id: int,
    config: AppConfig | None = None,
) -> list[str]:
    """Retrieve the tags for a specific document."""
    if config is None:
        config = AppConfig()

    init_db(config)

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tag FROM document_tags WHERE document_id = ? ORDER BY tag ASC",
            (doc_id,),
        )
        return [row['tag'] for row in cursor.fetchall()]


def generate_global_catalog(config: AppConfig | None = None) -> None:
    """Generate global_catalog.md in the output directory, grouping documents by tags."""
    if config is None:
        config = AppConfig()

    init_db(config)

    # Ensure output directory exists
    config.output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = config.output_dir / "global_catalog.md"

    with get_db(config.db_path) as conn:
        cursor = conn.cursor()
        # Get all documents
        cursor.execute(
            "SELECT id, filename, upload_time, status FROM documents ORDER BY upload_time DESC"
        )
        docs = [dict(row) for row in cursor.fetchall()]

        # Get tags for each document
        cursor.execute("SELECT document_id, tag FROM document_tags")
        tags_rows = cursor.fetchall()

    # Map doc_id to tags
    doc_tags: dict[int, list[str]] = {}
    for r in tags_rows:
        doc_id = r['document_id']
        tag = r['tag']
        if doc_id not in doc_tags:
            doc_tags[doc_id] = []
        doc_tags[doc_id].append(tag)

    # Group documents by tag
    tagged_docs: dict[str, list[dict]] = {}
    untagged_docs: list[dict] = []

    for doc in docs:
        doc_id = doc['id']
        tags = doc_tags.get(doc_id, [])
        doc['tags'] = tags
        if not tags:
            untagged_docs.append(doc)
        else:
            for tag in tags:
                if tag not in tagged_docs:
                    tagged_docs[tag] = []
                tagged_docs[tag].append(doc)

    # Sort tags alphabetically
    sorted_tags = sorted(tagged_docs.keys())

    with open(catalog_path, "w", encoding="utf-8") as f:
        f.write("# 📚 全域知識庫目錄\n\n")
        f.write("> 此目錄按標籤/分類整理所有已結構化的文件，供 LLM 與 RAG 代理快速檢索目錄，節省 Token 消耗。\n\n")

        if not docs:
            f.write("*目前沒有任何已結構化的文件。*\n")
            return

        # Write tagged categories
        for tag in sorted_tags:
            f.write(f"## 🏷️ {tag}\n\n")
            for doc in tagged_docs[tag]:
                f.write(
                    f"* [{doc['filename']} (ID: {doc['id']})]({doc['id']}/index.md) - *上傳時間: {doc['upload_time']}*\n"
                )
            f.write("\n")

        # Write untagged documents
        if untagged_docs:
            f.write("## 🏷️ 未分類文件\n\n")
            for doc in untagged_docs:
                f.write(
                    f"* [{doc['filename']} (ID: {doc['id']})]({doc['id']}/index.md) - *上傳時間: {doc['upload_time']}*\n"
                )
            f.write("\n")
