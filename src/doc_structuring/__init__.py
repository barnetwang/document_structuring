"""doc_structuring — RAG document parser for PDF/DOCX files.

Slice documents into structured, searchable Markdown chunks backed by SQLite FTS5.
"""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"

from .config import AppConfig
from .parser import parse_into_chunks
from .extractors import get_extractor


def parse_file(
    file_path: str,
    *,
    config: AppConfig | None = None,
    save_to_db: bool = True,
) -> dict:
    """High-level API: parse a document file and optionally persist to database.

    Args:
        file_path: Path to a PDF or DOCX file.
        config: Optional AppConfig; defaults to AppConfig() if not provided.
        save_to_db: If True, save chunks to SQLite and write physical files.

    Returns:
        dict with keys: document_id (if saved), filename, chunks, chunk_count.
    """
    from pathlib import Path as _Path
    from .database import save_document

    if config is None:
        config = AppConfig()

    p = _Path(file_path)
    ext = p.suffix.lower()
    filename = p.name

    extractor = get_extractor(ext)
    lines = extractor.extract_lines(str(p))
    chunks = parse_into_chunks(lines, filename)

    result = {
        "filename": filename,
        "chunks": chunks,
        "chunk_count": len(chunks),
    }

    if save_to_db:
        doc_id = save_document(filename, chunks, config=config)
        result["document_id"] = doc_id

    return result
