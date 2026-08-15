"""C/H code extractor using tree-sitter-c for AST symbol chunking."""

from __future__ import annotations

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field

import tree_sitter_c as tsc
from tree_sitter import Language, Parser

from ..parser import extract_cross_references

logger = logging.getLogger(__name__)

# Initialize Tree-sitter C Parser
C_LANGUAGE = Language(tsc.language())
_PARSER = Parser(C_LANGUAGE)

GUID_PATTERN = re.compile(
    r"#define\s+(g[A-Za-z0-9_]+Guid|EFI_[A-Za-z0-9_]+_GUID)\s*\\\?\s*\{[^}]+\}",
    re.MULTILINE,
)


@dataclass
class CodeChunk:
    symbol_name: str
    symbol_type: str  # 'function' | 'struct' | 'enum' | 'macro' | 'typedef' | 'guid'
    content: str
    file_path: str
    line_start: int
    line_end: int
    comments: str = ""
    cross_references: list[dict] = field(default_factory=list)


def parse_c_file(file_path: str) -> list[CodeChunk]:
    """Parse a C/H file using Tree-sitter C parser and extract structured symbol chunks."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {file_path}")

    source_bytes = path.read_bytes()
    source_str = source_bytes.decode("utf-8", errors="replace")

    tree = _PARSER.parse(source_bytes)
    root_node = tree.root_node

    chunks: list[CodeChunk] = []

    # 1. AST-based symbol extraction
    for node in root_node.children:
        chunk = _extract_node(node, source_bytes, source_str, str(path))
        if chunk:
            chunks.append(chunk)

    # 2. Extract EDK2 GUID definitions via regex fallback
    for match in GUID_PATTERN.finditer(source_str):
        guid_name = match.group(1)
        content = match.group(0)
        l_start = source_str[: match.start()].count("\n") + 1
        l_end = source_str[: match.end()].count("\n") + 1
        chunks.append(
            CodeChunk(
                symbol_name=guid_name,
                symbol_type="guid",
                content=content,
                file_path=str(path),
                line_start=l_start,
                line_end=l_end,
                cross_references=extract_cross_references(content),
            )
        )

    return chunks


def _extract_node(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk | None:
    ntype = node.type

    if ntype == "function_definition":
        return _extract_function(node, source_bytes, source_str, file_path)
    elif ntype == "type_definition":
        return _extract_typedef(node, source_bytes, source_str, file_path)
    elif ntype in ("struct_specifier", "union_specifier"):
        return _extract_struct(node, source_bytes, source_str, file_path)
    elif ntype == "enum_specifier":
        return _extract_enum(node, source_bytes, source_str, file_path)
    elif ntype == "preproc_def":
        return _extract_macro(node, source_bytes, source_str, file_path)

    return None


def _get_declarator_name(declarator, source_bytes: bytes) -> str:
    if not declarator:
        return "unnamed_symbol"
    while declarator and declarator.type in (
        "pointer_declarator",
        "function_declarator",
        "parenthesized_declarator",
        "array_declarator",
    ):
        child = declarator.child_by_field_name("declarator")
        if child:
            declarator = child
        else:
            break
    if declarator:
        return source_bytes[declarator.start_byte : declarator.end_byte].decode("utf-8", errors="replace").strip()
    return "unnamed_symbol"


def _extract_preceding_comments(node, source_str: str) -> str:
    """Extract preceding // or /* */ comments before node."""
    lines = source_str.splitlines()
    start_line = node.start_point[0]  # 0-indexed
    comment_lines = []
    idx = start_line - 1
    while idx >= 0:
        line_strip = lines[idx].strip()
        if line_strip.startswith("//") or line_strip.startswith("/*") or line_strip.endswith("*/") or line_strip.startswith("*"):
            comment_lines.insert(0, lines[idx])
            idx -= 1
        else:
            break
    return "\n".join(comment_lines)


def _extract_function(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk:
    declarator = node.child_by_field_name("declarator")
    func_name = _get_declarator_name(declarator, source_bytes)
    comments = _extract_preceding_comments(node, source_str)
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    if comments:
        content = f"{comments}\n{content}"

    return CodeChunk(
        symbol_name=func_name,
        symbol_type="function",
        content=content,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        comments=comments,
        cross_references=extract_cross_references(content),
    )


def _extract_typedef(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk:
    declarator = node.child_by_field_name("declarator")
    name = _get_declarator_name(declarator, source_bytes)
    comments = _extract_preceding_comments(node, source_str)
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return CodeChunk(
        symbol_name=name,
        symbol_type="typedef",
        content=content,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        comments=comments,
        cross_references=extract_cross_references(content),
    )


def _extract_struct(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk:
    name_node = node.child_by_field_name("name")
    name = (
        source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        if name_node
        else "anonymous_struct"
    )
    comments = _extract_preceding_comments(node, source_str)
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return CodeChunk(
        symbol_name=name,
        symbol_type="struct",
        content=content,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        comments=comments,
        cross_references=extract_cross_references(content),
    )


def _extract_enum(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk:
    name_node = node.child_by_field_name("name")
    name = (
        source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        if name_node
        else "anonymous_enum"
    )
    comments = _extract_preceding_comments(node, source_str)
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    return CodeChunk(
        symbol_name=name,
        symbol_type="enum",
        content=content,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        comments=comments,
        cross_references=extract_cross_references(content),
    )


def _extract_macro(node, source_bytes: bytes, source_str: str, file_path: str) -> CodeChunk:
    name_node = node.child_by_field_name("name")
    name = (
        source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        if name_node
        else "macro"
    )
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    symbol_type = "guid" if (name.endswith("_GUID") or name.endswith("Guid") or name.startswith("gEfi")) else "macro"

    return CodeChunk(
        symbol_name=name,
        symbol_type=symbol_type,
        content=content,
        file_path=file_path,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        cross_references=extract_cross_references(content),
    )

