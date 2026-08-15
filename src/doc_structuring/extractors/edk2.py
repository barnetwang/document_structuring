"""EDK2 build configuration file parser (.inf, .dec, .dsc, .fdf)."""

from __future__ import annotations

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field

from ..parser import extract_cross_references

logger = logging.getLogger(__name__)


@dataclass
class Edk2Chunk:
    section_name: str  # e.g. [Defines], [Protocols], [Guids], [Pcds]
    content: str
    file_path: str
    line_start: int
    line_end: int
    cross_references: list[dict] = field(default_factory=list)


def parse_edk2_file(file_path: str) -> list[Edk2Chunk]:
    """Parse an EDK2 configuration file (.inf, .dec, .dsc, .fdf) into structured section chunks."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"EDK2 config file not found: {file_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    chunks: list[Edk2Chunk] = []
    current_section = None
    current_lines: list[str] = []
    section_start = 1

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Detect Section Header: [Defines], [Protocols], [Guids], [Pcd], etc.
        sec_match = re.match(r"^\[([^\]]+)\]", stripped)
        if sec_match:
            if current_section and current_lines:
                sec_text = "\n".join(current_lines).strip()
                chunks.append(
                    Edk2Chunk(
                        section_name=current_section,
                        content=sec_text,
                        file_path=str(path),
                        line_start=section_start,
                        line_end=i - 1,
                        cross_references=extract_cross_references(sec_text),
                    )
                )
            current_section = sec_match.group(1)
            current_lines = [line]
            section_start = i
        elif current_section:
            current_lines.append(line)

    if current_section and current_lines:
        sec_text = "\n".join(current_lines).strip()
        chunks.append(
            Edk2Chunk(
                section_name=current_section,
                content=sec_text,
                file_path=str(path),
                line_start=section_start,
                line_end=len(lines),
                cross_references=extract_cross_references(sec_text),
            )
        )

    return chunks
