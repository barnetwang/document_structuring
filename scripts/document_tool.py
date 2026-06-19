import sys
import os
import json
import argparse
from pathlib import Path

# Add this script's directory to python path to resolve imports
script_dir = Path(__file__).resolve().parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

import parse_document
import database

def write_json_output(data, output_path):
    """Utility to write JSON data to the specified output file."""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Success: Output written to {output_path}")
    except Exception as e:
        print(f"Error writing output to {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

def handle_parse(args):
    file_path = args.file
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
        
    ext = Path(file_path).suffix.lower()
    filename = Path(file_path).name
    
    if ext not in [".pdf", ".docx"]:
        print(f"Error: Unsupported file type '{ext}'. Only PDF and DOCX are supported.", file=sys.stderr)
        sys.exit(1)
        
    try:
        print(f"Parsing document '{filename}'...")
        if ext == ".pdf":
            lines = parse_document.extract_pdf_lines(file_path)
        elif ext == ".docx":
            lines = parse_document.extract_docx_lines(file_path)
            
        chunks = parse_document.parse_into_chunks(lines, filename)
        
        if not chunks:
            print("Error: No content could be extracted or parsed.", file=sys.stderr)
            sys.exit(1)
            
        # Save to SQLite database and write physical chunk markdown files
        # database.DATABASE_FILE will resolve in the workspace root
        doc_id = database.save_document(filename, chunks)
        
        result = {
            "success": True,
            "document_id": doc_id,
            "filename": filename,
            "chunk_count": len(chunks)
        }
        write_json_output(result, args.output)
    except Exception as e:
        print(f"Error processing document: {e}", file=sys.stderr)
        sys.exit(1)

def handle_list(args):
    try:
        docs = database.list_documents()
        write_json_output({"documents": docs}, args.output)
    except Exception as e:
        print(f"Error listing documents: {e}", file=sys.stderr)
        sys.exit(1)

def handle_toc(args):
    try:
        toc = database.get_document_toc(args.doc_id)
        write_json_output({"toc": toc}, args.output)
    except Exception as e:
        print(f"Error fetching TOC for doc ID {args.doc_id}: {e}", file=sys.stderr)
        sys.exit(1)

def handle_get_chunk(args):
    try:
        chunk = database.get_chunk(args.chunk_id)
        if not chunk:
            print(f"Error: Chunk with ID {args.chunk_id} not found.", file=sys.stderr)
            sys.exit(1)
        write_json_output({"chunk": chunk}, args.output)
    except Exception as e:
        print(f"Error fetching chunk ID {args.chunk_id}: {e}", file=sys.stderr)
        sys.exit(1)

def handle_search(args):
    try:
        results = database.search_chunks(args.query, args.doc_id)
        write_json_output({"results": results}, args.output)
    except Exception as e:
        print(f"Error searching for '{args.query}': {e}", file=sys.stderr)
        sys.exit(1)

def handle_delete(args):
    try:
        filename = database.delete_document(args.doc_id)
        if not filename:
            print(f"Error: Document with ID {args.doc_id} not found in database.", file=sys.stderr)
            sys.exit(1)
        print(f"Success: Document '{filename}' (ID: {args.doc_id}) and all its associated chunks have been deleted.")
    except Exception as e:
        print(f"Error deleting document ID {args.doc_id}: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="Document Structuring CLI Tool for Hermes Agent. Slices files and manages SQLite database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # Parse command
    p_parse = subparsers.add_parser("parse", help="Parse PDF/DOCX file and structure into segments")
    p_parse.add_argument("--file", required=True, help="Path to input PDF or DOCX file")
    p_parse.add_argument("--output", required=True, help="Path to write the JSON operation summary")
    p_parse.set_defaults(func=handle_parse)

    # List command
    p_list = subparsers.add_parser("list", help="List all parsed documents in the database")
    p_list.add_argument("--output", required=True, help="Path to write the JSON documents list")
    p_list.set_defaults(func=handle_list)

    # TOC command
    p_toc = subparsers.add_parser("toc", help="Get table of contents (chunk metadata) for a document")
    p_toc.add_argument("--doc-id", type=int, required=True, help="Database ID of the document")
    p_toc.add_argument("--output", required=True, help="Path to write the JSON TOC data")
    p_toc.set_defaults(func=handle_toc)

    # Get Chunk command
    p_chunk = subparsers.add_parser("get-chunk", help="Retrieve content of a specific chunk by ID")
    p_chunk.add_argument("--chunk-id", type=int, required=True, help="Database ID of the chunk")
    p_chunk.add_argument("--output", required=True, help="Path to write the JSON chunk content")
    p_chunk.set_defaults(func=handle_get_chunk)

    # Search command
    p_search = subparsers.add_parser("search", help="Search parsed segments across documents")
    p_search.add_argument("--query", required=True, help="Keyword query to search")
    p_search.add_argument("--doc-id", type=int, help="Optional document ID to scope search")
    p_search.add_argument("--output", required=True, help="Path to write the JSON search results")
    p_search.set_defaults(func=handle_search)

    # Delete command
    p_delete = subparsers.add_parser("delete", help="Delete a document and its segments from DB and disk")
    p_delete.add_argument("--doc-id", type=int, required=True, help="Database ID of the document to delete")
    p_delete.set_defaults(func=handle_delete)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
