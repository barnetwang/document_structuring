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

        def get_para_drawings(para, d):
            drawings = []
            for blip in para._p.xpath(".//a:blip"):
                r_id = blip.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                )
                if r_id and r_id in d.part.related_parts:
                    drawings.append(r_id)
            return drawings

        img_idx = 1
        for para in doc.paragraphs:
            line = para.text.strip()

            r_ids = get_para_drawings(para, doc)
            for r_id in r_ids:
                image_part = doc.part.related_parts[r_id]
                try:
                    image_bytes = image_part.image.blob
                    ext = image_part.image.ext or "png"
                    img_filename = f"docx_img_{img_idx}.{ext}"
                    temp_path = os.path.join(work_dir, img_filename)
                    with open(temp_path, "wb") as img_f:
                        img_f.write(image_bytes)

                    meta = {
                        "temp_path": temp_path,
                        "caption": f"Document Image {img_idx}",
                        "contained_text": [],
                    }
                    placeholder_line = (
                        f"<!-- IMAGE: {json.dumps(meta, ensure_ascii=False)} -->"
                    )
                    lines.append((page_num, placeholder_line))
                    img_idx += 1
                except Exception as exc:
                    logger.error(
                        "Failed to extract DOCX image with rId %s: %s", r_id, exc
                    )

            if not line:
                continue

            if para.style and para.style.name:
                style_name = para.style.name
                if style_name.startswith("Heading "):
                    try:
                        level = int(style_name.split(" ")[1])
                        line = "#" * level + " " + line
                    except ValueError:
                        pass
                elif style_name == "Title":
                    line = "# " + line
                elif style_name == "Subtitle":
                    line = "## " + line
            elif len(line) < 80 and _is_bold_paragraph(para):
                line = "## " + line

            if not is_ignored(line, is_markdown=True, ignore_patterns=ignore_patterns):
                lines.append((page_num, line))

        return _merge_split_headings(lines)
