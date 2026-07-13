"""Command-line interface for doc-structuring."""

import sys
import json
import logging
import argparse
from pathlib import Path

from .config import AppConfig
from . import database
from .extractors import get_extractor, supported_extensions
from .parser import parse_into_chunks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_json_output(data: dict, output_path: str) -> None:
    """Write *data* as pretty-printed JSON to *output_path*."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Success: Output written to {output_path}")
    except Exception as exc:
        logger.error("Error writing output to %s: %s", output_path, exc)
        sys.exit(1)


def _extract_kwargs(config: AppConfig) -> dict:
    """Common kwargs passed into format extractors."""
    return {
        "temp_dir": str(config.temp_dir),
        "ignore_patterns": config.compiled_ignore_patterns(),
    }


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------
def handle_parse(args: argparse.Namespace, config: AppConfig) -> None:
    """Parse a document file, store chunks in the database."""
    file_path = Path(args.file)
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        sys.exit(1)

    ext = file_path.suffix.lower()
    filename = file_path.name

    try:
        extractor = get_extractor(ext)
    except ValueError:
        allowed = ", ".join(sorted(supported_extensions()))
        logger.error(
            "Unsupported file type '%s'. Supported: %s", ext, allowed
        )
        sys.exit(1)

    try:
        logger.info("Parsing document '%s'...", filename)
        extract_kw = _extract_kwargs(config)
        if ext == ".pdf":
            lines = extractor.extract_lines(
                str(file_path),
                batch_size=config.pdf_batch_size,
                **extract_kw,
            )
        else:
            lines = extractor.extract_lines(str(file_path), **extract_kw)

        chunks = parse_into_chunks(
            lines,
            filename,
            bad_keywords=config.bad_heading_keywords,
        )

        if not chunks:
            logger.error("No content could be extracted or parsed.")
            sys.exit(1)

        tags_list = None
        if args.tags:
            tags_list = [t.strip() for t in args.tags.split(",") if t.strip()]

        doc_id = database.save_document(
            filename, chunks, config=config, tags=tags_list
        )

        result = {
            "success": True,
            "document_id": doc_id,
            "filename": filename,
            "chunk_count": len(chunks),
        }
        _write_json_output(result, args.output)
    except Exception as exc:
        logger.error("Error processing document: %s", exc)
        sys.exit(1)


def handle_tag(args: argparse.Namespace, config: AppConfig) -> None:
    """Set tags for a document, updating global_catalog.md."""
    try:
        tags_list = [t.strip() for t in args.tags.split(",") if t.strip()]
        database.set_document_tags(args.doc_id, tags_list, config=config)
        current_tags = database.get_document_tags(args.doc_id, config=config)
        print(
            f"Success: Tags for document ID {args.doc_id} set to: "
            f"{', '.join(current_tags)}"
        )
    except Exception as exc:
        logger.error(
            "Error setting tags for document ID %s: %s", args.doc_id, exc
        )
        sys.exit(1)


def handle_list(args: argparse.Namespace, config: AppConfig) -> None:
    """List all parsed documents in the database."""
    try:
        docs = database.list_documents(config=config)
        _write_json_output({"documents": docs}, args.output)
    except Exception as exc:
        logger.error("Error listing documents: %s", exc)
        sys.exit(1)


def handle_toc(args: argparse.Namespace, config: AppConfig) -> None:
    """Display the table of contents for a specific document."""
    try:
        toc = database.get_document_toc(args.doc_id, config=config)
        _write_json_output({"toc": toc}, args.output)
    except Exception as exc:
        logger.error("Error fetching TOC for doc ID %s: %s", args.doc_id, exc)
        sys.exit(1)


def handle_get_chunk(args: argparse.Namespace, config: AppConfig) -> None:
    """Retrieve a single chunk by its database ID."""
    try:
        chunk = database.get_chunk(args.chunk_id, config=config)
        if not chunk:
            logger.error("Chunk with ID %s not found.", args.chunk_id)
            sys.exit(1)
        _write_json_output({"chunk": chunk}, args.output)
    except Exception as exc:
        logger.error("Error fetching chunk ID %s: %s", args.chunk_id, exc)
        sys.exit(1)


def handle_search(args: argparse.Namespace, config: AppConfig) -> None:
    """Search chunk titles and content for keywords."""
    try:
        results = database.search_chunks(
            args.query, args.doc_id, config=config
        )
        _write_json_output({"results": results}, args.output)
    except Exception as exc:
        logger.error("Error searching for '%s': %s", args.query, exc)
        sys.exit(1)


def handle_delete(args: argparse.Namespace, config: AppConfig) -> None:
    """Delete a document and all its associated chunks."""
    try:
        filename = database.delete_document(args.doc_id, config=config)
        if not filename:
            logger.error(
                "Document with ID %s not found in database.", args.doc_id
            )
            sys.exit(1)
        print(
            f"Success: Document '{filename}' (ID: {args.doc_id}) "
            "and all its associated chunks have been deleted."
        )
    except Exception as exc:
        logger.error("Error deleting document ID %s: %s", args.doc_id, exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Parse arguments and dispatch to the appropriate handler."""
    parser = argparse.ArgumentParser(
        description=(
            "Document Structuring CLI Tool. "
            "Slices PDF/DOCX files into searchable Markdown chunks "
            "backed by SQLite FTS5."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase output verbosity (-v for INFO, -vv for DEBUG).",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help=(
            "Workspace root for documents.db and output/ "
            "(default: DOC_STRUCTURING_BASE_DIR or CWD)."
        ),
    )
    parser.add_argument(
        "--locale",
        default=None,
        choices=["en", "zh"],
        help="Language for generated catalog/index labels (default: en).",
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Subcommands"
    )

    # -- parse ---------------------------------------------------------
    p_parse = subparsers.add_parser(
        "parse", help="Parse PDF/DOCX file and structure into segments"
    )
    p_parse.add_argument(
        "--file", required=True, help="Path to input document file"
    )
    p_parse.add_argument(
        "--tags", help="Optional comma-separated tags to assign to the document"
    )
    p_parse.add_argument(
        "--output", required=True, help="Path to write the JSON operation summary"
    )
    p_parse.set_defaults(func=handle_parse)

    # -- list ----------------------------------------------------------
    p_list = subparsers.add_parser(
        "list", help="List all parsed documents in the database"
    )
    p_list.add_argument(
        "--output", required=True, help="Path to write the JSON documents list"
    )
    p_list.set_defaults(func=handle_list)

    # -- toc -----------------------------------------------------------
    p_toc = subparsers.add_parser(
        "toc", help="Get table of contents (chunk metadata) for a document"
    )
    p_toc.add_argument(
        "--doc-id", type=int, required=True, help="Database ID of the document"
    )
    p_toc.add_argument(
        "--output", required=True, help="Path to write the JSON TOC data"
    )
    p_toc.set_defaults(func=handle_toc)

    # -- get-chunk -----------------------------------------------------
    p_chunk = subparsers.add_parser(
        "get-chunk", help="Retrieve content of a specific chunk by ID"
    )
    p_chunk.add_argument(
        "--chunk-id", type=int, required=True, help="Database ID of the chunk"
    )
    p_chunk.add_argument(
        "--output", required=True, help="Path to write the JSON chunk content"
    )
    p_chunk.set_defaults(func=handle_get_chunk)

    # -- search --------------------------------------------------------
    p_search = subparsers.add_parser(
        "search", help="Search parsed segments across documents"
    )
    p_search.add_argument(
        "--query", required=True, help="Keyword query to search"
    )
    p_search.add_argument(
        "--doc-id", type=int, help="Optional document ID to scope search"
    )
    p_search.add_argument(
        "--output", required=True, help="Path to write the JSON search results"
    )
    p_search.set_defaults(func=handle_search)

    # -- delete --------------------------------------------------------
    p_delete = subparsers.add_parser(
        "delete", help="Delete a document and its segments from DB and disk"
    )
    p_delete.add_argument(
        "--doc-id",
        type=int,
        required=True,
        help="Database ID of the document to delete",
    )
    p_delete.set_defaults(func=handle_delete)

    # -- tag -----------------------------------------------------------
    p_tag = subparsers.add_parser(
        "tag", help="Assign or update tags for a specific document"
    )
    p_tag.add_argument(
        "--doc-id", type=int, required=True, help="Database ID of the document"
    )
    p_tag.add_argument(
        "--tags", required=True, help="Comma-separated list of tags to assign"
    )
    p_tag.set_defaults(func=handle_tag)

    # -- dispatch ------------------------------------------------------
    args = parser.parse_args()

    level = {0: logging.WARNING, 1: logging.INFO}.get(
        args.verbose, logging.DEBUG
    )
    logging.basicConfig(
        level=level, format="%(levelname)s: %(name)s: %(message)s"
    )

    config_kwargs: dict = {}
    if args.base_dir:
        config_kwargs["base_dir"] = Path(args.base_dir)
    if args.locale:
        config_kwargs["locale"] = args.locale
    config = AppConfig(**config_kwargs) if config_kwargs else AppConfig()
    args.func(args, config)


if __name__ == "__main__":
    main()
