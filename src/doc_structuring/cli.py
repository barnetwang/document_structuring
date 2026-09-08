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
def _write_output(data: dict | str, output_path: str, format_type: str = "json") -> None:
    """Write output as JSON or raw string (XML) to *output_path*."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            if format_type == "xml" and isinstance(data, str):
                f.write(data)
            elif isinstance(data, dict):
                json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                f.write(str(data))
        print(f"Success: Output written to {output_path}")
    except Exception as exc:
        logger.error("Error writing output to %s: %s", output_path, exc)
        sys.exit(1)


def _write_json_output(data: dict, output_path: str) -> None:
    """Write *data* as pretty-printed JSON to *output_path*."""
    _write_output(data, output_path, format_type="json")



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
    """Retrieve a single chunk by its database ID with optional neighbors and XML formatting."""
    try:
        from .utils import estimate_tokens

        chunk_data = database.get_chunk_with_neighbors(
            args.chunk_id,
            include_neighbors=getattr(args, "include_neighbors", False),
            max_context_tokens=None,  # size the target before applying the budget
            config=config,
        )
        if not chunk_data or not chunk_data.get("chunk"):
            logger.error("Chunk with ID %s not found.", args.chunk_id)
            sys.exit(1)

        budget = getattr(args, "max_context_tokens", None)
        if budget is not None and estimate_tokens(chunk_data["chunk"]["content"]) > budget:
            logger.error(
                "ERROR_BUDGET_TOO_SMALL: request budget %s tokens is below "
                "the estimate %s for target chunk %s alone; the target chunk "
                "is always returned in full. Increase --max-context-tokens "
                "or drop --include-neighbors.",
                budget,
                estimate_tokens(chunk_data["chunk"]["content"]),
                args.chunk_id,
            )
            sys.exit(1)

        if budget is not None:
            # Re-run with the real budget now that the target fits.
            chunk_data = database.get_chunk_with_neighbors(
                args.chunk_id,
                include_neighbors=getattr(args, "include_neighbors", False),
                max_context_tokens=budget,
                config=config,
            )

        fmt = getattr(args, "format", "json")
        if fmt == "xml":
            from .utils import format_chunk_to_xml
            xml_str = format_chunk_to_xml(chunk_data)
            _write_output(xml_str, args.output, format_type="xml")
        else:
            _write_output(chunk_data, args.output, format_type="json")
    except Exception as exc:
        logger.error("Error fetching chunk ID %s: %s", args.chunk_id, exc)
        sys.exit(1)



def handle_embed(args: argparse.Namespace, config: AppConfig) -> None:
    """Generate and store fastembed vector embeddings for document chunks."""
    try:
        from .embeddings import generate_embeddings

        doc_id = getattr(args, "doc_id", None)
        if doc_id is not None:
            toc = database.get_document_toc(doc_id, config=config)
            chunk_ids = [item["id"] for item in toc]
        else:
            docs = database.list_documents(config=config)
            chunk_ids = []
            for d in docs:
                t = database.get_document_toc(d["id"], config=config)
                chunk_ids.extend([item["id"] for item in t])

        print(f"Generating fastembed embeddings for {len(chunk_ids)} chunks...")
        embeddings_map = {}
        batch_size = 64
        for i in range(0, len(chunk_ids), batch_size):
            batch_cids = chunk_ids[i : i + batch_size]
            batch_texts = []
            for cid in batch_cids:
                chk = database.get_chunk(cid, config=config)
                batch_texts.append(chk["content"] if chk else "")
            vecs = generate_embeddings(batch_texts, batch_size=batch_size)
            for cid, vec in zip(batch_cids, vecs):
                embeddings_map[cid] = vec

        database.save_embeddings(embeddings_map, config=config)
        _write_json_output(
            {"success": True, "embedded_chunks": len(embeddings_map)}, args.output
        )
    except Exception as exc:
        logger.error("Error generating embeddings: %s", exc)
        sys.exit(1)


def handle_parse_code(args: argparse.Namespace, config: AppConfig) -> None:
    """Parse a C/H source file or EDK2 config file and save chunks to database."""
    file_path = Path(args.file)
    if not file_path.exists():
        logger.error("Source file not found: %s", file_path)
        sys.exit(1)

    ext = file_path.suffix.lower()
    try:
        if ext in (".c", ".h"):
            from .extractors.code import parse_c_file
            raw_chunks = parse_c_file(str(file_path))
            doc_type = "code_c" if ext == ".c" else "code_h"
            chunk_dicts = [
                {
                    "symbol_name": c.symbol_name,
                    "symbol_type": c.symbol_type,
                    "content": c.content,
                    "file_path": c.file_path,
                    "line_start": c.line_start,
                    "line_end": c.line_end,
                    "cross_references": c.cross_references,
                }
                for c in raw_chunks
            ]
        elif ext in (".inf", ".dec", ".dsc", ".fdf"):
            from .extractors.edk2 import parse_edk2_file
            raw_chunks = parse_edk2_file(str(file_path))
            doc_type = f"edk2_{ext[1:]}"
            chunk_dicts = [
                {
                    "section_name": c.section_name,
                    "content": c.content,
                    "file_path": c.file_path,
                    "line_start": c.line_start,
                    "line_end": c.line_end,
                    "cross_references": c.cross_references,
                }
                for c in raw_chunks
            ]
        else:
            logger.error("Unsupported code file extension: %s", ext)
            sys.exit(1)

        doc_id = database.save_code_chunks(str(file_path), chunk_dicts, doc_type=doc_type, config=config)
        _write_json_output(
            {
                "success": True,
                "document_id": doc_id,
                "filename": file_path.name,
                "doc_type": doc_type,
                "chunk_count": len(chunk_dicts),
            },
            args.output,
        )
    except Exception as exc:
        logger.error("Error parsing code file %s: %s", file_path, exc)
        sys.exit(1)


def handle_search(args: argparse.Namespace, config: AppConfig) -> None:
    """Search chunk titles and content using hybrid, fts, or vec mode."""
    try:
        mode = getattr(args, "mode", "hybrid")
        doc_id = getattr(args, "doc_id", None)
        top_k = getattr(args, "limit", 10)
        min_fts_rank = getattr(args, "min_fts_rank", None)

        if mode == "fts":
            results = database.search_chunks(
                args.query, document_id=doc_id, limit=top_k, config=config
            )
        elif mode == "vec":
            from .embeddings import generate_embeddings
            import numpy as np

            embeddings_map = database.get_document_embeddings(
                document_id=doc_id, config=config
            )
            if not embeddings_map:
                logger.warning(
                    "No embeddings found for %s, so vector search can return "
                    "no results. Run 'doc-str embed --doc-id <id> --output "
                    "<path>.json' first.",
                    f"document {doc_id}" if doc_id is not None else "the database",
                )
                results = []
            else:
                query_vec = generate_embeddings([args.query])[0]
                cids = list(embeddings_map.keys())
                matrix = np.array([embeddings_map[cid] for cid in cids], dtype=np.float32)
                sims = np.dot(matrix, query_vec)
                top_indices = np.argsort(sims)[::-1][:top_k]
                results = []
                for rank_idx, idx in enumerate(top_indices):
                    chk = database.get_chunk(cids[idx], config=config)
                    if chk:
                        chk["vector_similarity"] = round(float(sims[idx]), 6)
                        results.append(chk)
        else:
            results = database.hybrid_search(
                args.query, document_id=doc_id, top_k=top_k, min_fts_rank=min_fts_rank, config=config
            )

        _write_json_output({"results": results, "mode": mode}, args.output)
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
        prog="doc-str",
        description=(
            "Doc-Str CLI Tool. "
            "Slices PDF/DOCX specs, C/H source code, and EDK2 build config files "
            "into searchable Markdown & AST chunks backed by SQLite FTS5 & ONNX vectors."
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
        "--include-neighbors",
        action="store_true",
        help="Include previous and next adjacent chunks if context budget permits",
    )
    p_chunk.add_argument(
        "--max-context-tokens",
        type=int,
        default=None,
        help="Maximum total token budget for target chunk and neighbors",
    )
    p_chunk.add_argument(
        "--format",
        choices=["json", "xml"],
        default="json",
        help="Output format: json (default) or xml (LLM grounding structure)",
    )
    p_chunk.add_argument(
        "--output", required=True, help="Path to write the chunk content"
    )
    p_chunk.set_defaults(func=handle_get_chunk)


    # -- parse-code ----------------------------------------------------
    p_parse_code = subparsers.add_parser(
        "parse-code", help="Parse C/H source code or EDK2 config files into DB"
    )
    p_parse_code.add_argument(
        "--file", required=True, help="Path to C/H/INF/DEC/DSC/FDF source file"
    )
    p_parse_code.add_argument(
        "--output", required=True, help="Path to write JSON parsing status"
    )
    p_parse_code.set_defaults(func=handle_parse_code)

    # -- embed ---------------------------------------------------------
    p_embed = subparsers.add_parser(
        "embed", help="Generate vector embeddings for document chunks using fastembed"
    )
    p_embed.add_argument(
        "--doc-id", type=int, help="Optional document ID to scope embedding"
    )
    p_embed.add_argument(
        "--output", required=True, help="Path to write JSON completion result"
    )
    p_embed.set_defaults(func=handle_embed)

    # -- search --------------------------------------------------------
    p_search = subparsers.add_parser(
        "search", help="Search parsed segments across documents"
    )
    p_search.add_argument(
        "--query", required=True, help="Keyword or semantic query to search"
    )
    p_search.add_argument(
        "--mode",
        choices=["hybrid", "fts", "vec"],
        default="hybrid",
        help="Search mode: hybrid (RRF default), fts (BM25 keyword), or vec (fastembed semantic)",
    )
    p_search.add_argument(
        "--limit", type=int, default=10, help="Maximum number of search results to return"
    )
    p_search.add_argument(
        "--min-fts-rank",
        type=int,
        default=None,
        help="Keep only chunks whose FTS5 rank is in the top N; "
        "chunks with no keyword match are dropped",
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
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

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
