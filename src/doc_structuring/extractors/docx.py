"""DOCX document extractor using python-docx."""

from __future__ import annotations

import re
import logging
import os
import tempfile
import json
from pathlib import Path
from typing import Sequence

from docx import Document

from . import register
from ..parser import is_ignored

logger = logging.getLogger(__name__)


def _is_bold_paragraph(para) -> bool:
    """Check whether every non-whitespace run in a paragraph is bold."""
    runs = [r for r in para.runs if r.text.strip()]
    if not runs:
        return False
    return all(run.bold for run in runs)


def _merge_split_headings(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Merge lines where a bare section number is split from its title.

    Some DOCX exports produce two consecutive paragraphs for a single
    heading — e.g. ``"1.2"`` followed by ``"Overview"``.  This function
    joins them back into ``"1.2 Overview"``.
    """
    merged: list[tuple[int, str]] = []
    i = 0

    while i < len(lines):
        page_num, line = lines[i]

        if re.match(r"^\d+(\.\d+)*$", line) and i + 1 < len(lines):
            _, next_line = lines[i + 1]
            if not re.match(r"^\d+(\.\d+)*$", next_line):
                merged.append((page_num, f"{line} {next_line}"))
                i += 2
                continue

        merged.append((page_num, line))
        i += 1

    return merged


def _render_table_markdown(table, get_drawings) -> list[str]:
    """Render a DOCX table as a Markdown table (one line per row).

    Cell text is whitespace-collapsed so multi-line cells become single
    cells.  Consecutive identical rows (an artifact of merged cells in
    python-docx's ``row.cells``) are deduplicated.  ``get_drawings``
    yields image placeholder lines for drawings nested inside the table.
    """
    lines: list[str] = []
    for r_id in get_drawings(table):
        lines.append(r_id)
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = " ".join(cell.text.split()).replace("|", "\\'")
            cells.append(text)
        rows.append(cells)
    if not rows:
        return lines
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    dedup = [rows[0]]
    for r in rows[1:]:
        if r != dedup[-1]:
            dedup.append(r)
    header, body = dedup[0], dedup[1:]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join([" --- "] * ncols) + "|")
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return lines


@register(".docx")
class DocxExtractor:
    """Extract text lines from Word DOCX files.

    Converts paragraph styles (Heading 1-9, Title, Subtitle) into
    Markdown heading markers.  Short, fully-bold paragraphs without a
    heading style are also promoted to ``## `` headings as a heuristic
    fallback.
    """

    def extract_lines(
        self,
        file_path: str,
        *,
        temp_dir: str | Path | None = None,
        ignore_patterns: Sequence[re.Pattern[str]] | None = None,
    ) -> list[tuple[int, str]]:
        """Extract text lines from a DOCX file.

        Args:
            file_path: Path to the DOCX file.
            temp_dir: Optional parent for temporary image extraction.
            ignore_patterns: Optional line filters (defaults to built-ins).

        Returns:
            A list of (1-based page number, text line) tuples with
            Markdown heading prefixes applied where appropriate.
        """
        doc = Document(file_path)
        lines: list[tuple[int, str]] = []
        # DOCX has no reliable page map; keep a constant page marker.
        page_num = 1

        work_dir_parent = Path(temp_dir) if temp_dir else Path(".doc_structuring_tmp")
        work_dir_parent.mkdir(parents=True, exist_ok=True)
        work_dir = tempfile.mkdtemp(
            prefix="doc_structuring_docx_", dir=str(work_dir_parent)
        )

        def get_drawings(el):
            """rIds of images embedded anywhere inside *el* (para or table)."""
            found = []
            for blip in el._element.xpath(".//a:blip"):
                r_id = blip.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                )
                if r_id and r_id in doc.part.related_parts and r_id not in found:
                    found.append(r_id)
            return found

        def emit_image(r_id) -> str | None:
            """Extract an embedded image blob and return an IMAGE placeholder."""
            nonlocal img_idx
            try:
                image_bytes = doc.part.related_parts[r_id].image.blob
                ext = doc.part.related_parts[r_id].image.ext or "png"
            except Exception as exc:
                logger.error("Failed to extract DOCX image with rId %s: %s", r_id, exc)
                return None
            img_filename = f"docx_img_{img_idx}.{ext}"
            temp_path = os.path.join(work_dir, img_filename)
            with open(temp_path, "wb") as img_f:
                img_f.write(image_bytes)
            meta = {
                "temp_path": temp_path,
                "caption": f"Document Image {img_idx}",
                "contained_text": [],
            }
            img_idx += 1
            return f"<!-- IMAGE: {json.dumps(meta, ensure_ascii=False)} -->"

        from docx.oxml.ns import qn
        from docx.table import Table as _Table
        from docx.text.paragraph import Paragraph as _Paragraph

        img_idx = 1
        # Walk body children in document order so tables render at their
        # true position (doc.paragraphs alone loses ~100%-table content
        # such as compliance matrices and revision tables).
        for child in doc.element.body.iterchildren():
            if child.tag == qn("w:tbl"):
                table = _Table(child, doc)
                def _tbl_images(_t=table):
                    ph = []
                    for r_id in get_drawings(_t):
                        line = emit_image(r_id)
                        if line is not None:
                            ph.append(line)
                    return ph
                for tbl_line in _render_table_markdown(table, _tbl_images):
                    if not is_ignored(tbl_line, is_markdown=True, ignore_patterns=ignore_patterns):
                        lines.append((page_num, tbl_line))
                continue
            if child.tag != qn("w:p"):
                continue
            para = _Paragraph(child, doc)
            line = para.text.strip()

            for r_id in get_drawings(para):
                placeholder = emit_image(r_id)
                if placeholder is not None:
                    lines.append((page_num, placeholder))

            if not line:
                continue

            if para.style and para.style.name:
                style_name = para.style.name
                if style_name.lower().startswith("toc"):
                    # Word-generated TOC entries ("TOC 1", "TOC 2", ...) duplicate
                    # the real Headings and carry trailing tab+page-number leaders
                    # (e.g. "1.\tPurpose\t6") which break heading detection and
                    # file writing. Skip them entirely.
                    continue
                if style_name.lower().startswith("heading"):
                    # Match "Heading 1".."Heading 9" AND custom variants such
                    # as "Heading2 - Styel2" (vendor templates often typo or
                    # style headings manually). Level = first digit found.
                    import re as _re
                    m = _re.search(r"\d+", style_name)
                    if m:
                        line = "#" * int(m.group(0)) + " " + line
                    else:
                        line = "## " + line
                elif style_name == "Title":
                    line = "# " + line
                elif style_name == "Subtitle":
                    line = "## " + line
            elif len(line) < 80 and _is_bold_paragraph(para):
                line = "## " + line

            if not is_ignored(line, is_markdown=True, ignore_patterns=ignore_patterns):
                lines.append((page_num, line))

        return _merge_split_headings(lines)
