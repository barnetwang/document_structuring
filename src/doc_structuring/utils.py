"""Shared utility functions used across the package."""

import re
import logging

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Convert a heading title into a safe filename component.

    Strips Markdown decorators and replaces unsafe characters.
    """
    clean_name = name.replace('*', '').replace('#', '')
    clean_name = re.sub(r'[\x00-\x1f\x7f]', '', clean_name)  # control chars (tab leaders) crash Windows file I/O
    return re.sub(r'[\\/*?:"<>|]', '', clean_name).replace(' ', '_')


def section_sort_key(section_number: str) -> list[tuple[int, int, str]]:
    """Generate a sort key for dotted section numbers (e.g. '1.2.3').

    Handles mixed numeric/non-numeric parts correctly so that
    '1.2' sorts before '1.10'.
    """
    result = []
    for part in section_number.split('.'):
        if part.isdigit():
            result.append((0, int(part), ''))
        else:
            result.append((1, 0, part))
    return result


def estimate_tokens(text: str) -> int:
    """Estimate token count without heavy external dependencies.

    Roughly assumes ~4 characters per token for ASCII/English words,
    and ~1.5 tokens per CJK character.
    """
    if not text:
        return 0

    cjk_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    non_cjk_len = len(text) - cjk_count

    estimated = int(cjk_count * 1.5 + (non_cjk_len / 4.0))
    return max(1, estimated)


def truncate_chunk_content(content: str, max_tokens: int, position: str = "previous") -> str:
    """Truncate chunk content to fit within max_tokens token budget.

    If position == 'previous', keeps trailing content closest to main chunk.
    If position == 'next', keeps leading content closest to main chunk.
    """
    if max_tokens <= 15:
        return ""

    banner_reserve = 15
    budget_for_text = max_tokens - banner_reserve

    curr_tokens = estimate_tokens(content)
    if curr_tokens <= budget_for_text:
        return content

    char_ratio = budget_for_text / float(curr_tokens)
    target_char_len = int(len(content) * char_ratio)

    if position == "previous":
        trimmed = content[-target_char_len:]
        first_nl = trimmed.find("\n")
        if first_nl != -1 and first_nl < len(trimmed) // 3:
            trimmed = trimmed[first_nl + 1:]
        return "...\n[Previous section content truncated due to token budget]\n" + trimmed
    else:
        trimmed = content[:target_char_len]
        last_nl = trimmed.rfind("\n")
        if last_nl != -1 and (len(trimmed) - last_nl) < len(trimmed) // 3:
            trimmed = trimmed[:last_nl]
        return trimmed + "\n...\n[Next section content truncated due to token budget]"




def _xml_escape(val: str | int | None) -> str:
    """Escape special characters (&, <, >) for valid XML output."""
    if val is None:
        return ""
    text = str(val)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_chunk_to_xml(chunk_data: dict) -> str:
    """Format a chunk dictionary (with optional neighbors) into LLM-ready XML grounding structure."""
    chunk = chunk_data.get("chunk", chunk_data)
    doc_title = _xml_escape(chunk.get("document_name") or chunk.get("source", "Document"))
    sec_num = _xml_escape(chunk.get("section_number", "0"))
    sec_title = _xml_escape(chunk.get("title", ""))
    page_num = _xml_escape(chunk.get("page_start", 1))
    content = _xml_escape(chunk.get("content", ""))

    doc_type = _xml_escape(chunk.get("doc_type", "spec"))
    symbol_name = _xml_escape(chunk.get("symbol_name", ""))
    file_path = _xml_escape(chunk.get("file_path", ""))
    line_start = chunk.get("line_start")
    line_end = chunk.get("line_end")

    xml_lines = [
        "<document_context>",
        f'  <metadata doc_type="{doc_type}">',
        f"    <document_title>{doc_title}</document_title>",
        f"    <section_number>{sec_num}</section_number>",
        f"    <section_title>{sec_title}</section_title>",
        f"    <page_number>{page_num}</page_number>",
    ]

    if symbol_name:
        xml_lines.append(f"    <symbol_name>{symbol_name}</symbol_name>")
    if file_path:
        xml_lines.append(f"    <file_path>{file_path}</file_path>")
    if line_start is not None and line_end is not None:
        xml_lines.append(f'    <line_range start="{line_start}" end="{line_end}" />')

    xml_lines.append("  </metadata>")


    refs = chunk.get("cross_references", [])
    if refs:
        xml_lines.append("  <references>")
        for ref in refs:
            if isinstance(ref, dict):
                r_type = _xml_escape(ref.get("type", "reference"))
                r_target = _xml_escape(ref.get("target", ""))
                r_imp = ' implicit="true"' if ref.get("implicit") else ""
                xml_lines.append(f'    <ref type="{r_type}" target="{r_target}"{r_imp} />')
            else:
                xml_lines.append(f'    <ref target="{_xml_escape(ref)}" />')
        xml_lines.append("  </references>")


    prev_chunk = chunk_data.get("previous_chunk")
    if prev_chunk:
        cs_attr = ' cross_section="true"' if prev_chunk.get("cross_section") else ""
        xml_lines.extend([
            f'  <neighbor_context position="previous"{cs_attr}>',
            f'    <section_number>{_xml_escape(prev_chunk.get("section_number", ""))}</section_number>',
            f'    <section_title>{_xml_escape(prev_chunk.get("title", ""))}</section_title>',
            f'    <page_number>{_xml_escape(prev_chunk.get("page_start", 1))}</page_number>',
            '    <content>',
            f'{_xml_escape(prev_chunk.get("content", "").strip())}',
            '    </content>',
            '  </neighbor_context>',
        ])

    xml_lines.extend([
        "  <content>",
        f"{content.strip()}",
        "  </content>",
    ])

    next_chunk = chunk_data.get("next_chunk")
    if next_chunk:
        cs_attr = ' cross_section="true"' if next_chunk.get("cross_section") else ""
        xml_lines.extend([
            f'  <neighbor_context position="next"{cs_attr}>',
            f'    <section_number>{_xml_escape(next_chunk.get("section_number", ""))}</section_number>',
            f'    <section_title>{_xml_escape(next_chunk.get("title", ""))}</section_title>',
            f'    <page_number>{_xml_escape(next_chunk.get("page_start", 1))}</page_number>',
            '    <content>',
            f'{_xml_escape(next_chunk.get("content", "").strip())}',
            '    </content>',
            '  </neighbor_context>',
        ])

    xml_lines.append("</document_context>")
    return "\n".join(xml_lines)



