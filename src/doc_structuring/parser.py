"""Document parsing logic: heading detection, section numbering, and chunk generation."""

from __future__ import annotations

import re
import logging
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

VALID_MAJOR_RANGE = range(1, 100)

MD_HEADING_REGEX = re.compile(
    r"^(#+)\s*(?:\*\*\s*)?(.*?)(?:\s*\*\*)?$"
)

EXPLICIT_NUM_REGEX = re.compile(
    r"^(?:Chapter|Section|第)?\s*(\d+(?:\.\d+)*)\.?(?:[\s:-]+(.*))?$",
    re.IGNORECASE,
)

TOC_IGNORE_REGEX = re.compile(r"\.{3,}\s*\d+$")

UNIT_ONLY_REGEX = re.compile(
    r"^\d+(?:\.\d+)?\s*(MHz|GHz|W|V|A|mV|mA|s|ns|ms|us|bytes|KB|MB|GB|TB)$",
    re.IGNORECASE,
)

# Built-in defaults (kept for callers that do not pass a config).
# Prefer AppConfig.compiled_ignore_patterns() for full control.
DEFAULT_IGNORE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^Table of Contents$", re.IGNORECASE),
    re.compile(r"^Contents$", re.IGNORECASE),
    # Short header/footer banners only (max ~70 chars) — not body sentences
    re.compile(
        r"^(?=.{0,70}$).*\b(Confidential|Proprietary|Internal Use Only)\b.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^Copyright\s*(©|\(c\))?.*All rights reserved\.?$",
        re.IGNORECASE,
    ),
]

DEFAULT_BAD_KEYWORDS = [
    "updated",
    "corrected",
    "release",
    "initial nda",
    "revision history",
    "document revision",
    "change history",
    "revision record",
]


# ---------------------------------------------------------------------------
# Heading & Section Number Tracker
# ---------------------------------------------------------------------------
class SectionNumberTracker:
    """Track and auto-generate hierarchical section numbers.

    Keeps an internal counter array (one slot per depth level).  Callers can
    either *sync* to an explicitly-numbered heading or *generate* a number
    for a Markdown-only heading that lacks an explicit section number.
    """

    def __init__(self, max_depth: int = 10) -> None:
        self.current_nums: list[int] = [0] * max_depth

    def sync(self, section_num: str) -> None:
        """Synchronize tracker with an explicit section number (e.g., ``'1.2.3'``)."""
        parts = section_num.split(".")
        for i, part in enumerate(parts):
            if i < len(self.current_nums):
                try:
                    self.current_nums[i] = int(part)
                except ValueError:
                    self.current_nums[i] = 1  # Fallback
        for i in range(len(parts), len(self.current_nums)):
            self.current_nums[i] = 0

    def generate(self, level: int) -> str:
        """Generate a pseudo-section number for a given *level* (1-indexed)."""
        idx = level - 1
        if idx >= len(self.current_nums):
            idx = len(self.current_nums) - 1

        self.current_nums[idx] += 1

        for i in range(idx + 1, len(self.current_nums)):
            self.current_nums[i] = 0

        parts: list[str] = []
        for i in range(level):
            val = max(self.current_nums[i], 1)
            parts.append(str(val))

        return ".".join(parts)


# ---------------------------------------------------------------------------
# Filtering / validation helpers
# ---------------------------------------------------------------------------
def is_ignored(
    line: str,
    is_markdown: bool = False,
    ignore_patterns: Sequence[re.Pattern[str]] | None = None,
) -> bool:
    """Return ``True`` if *line* should be discarded during extraction.

    For Markdown sources blank lines are preserved (they carry formatting
    meaning); for non-Markdown sources they are dropped.
    """
    clean_line = line.strip()

    if not clean_line:
        return not is_markdown

    if TOC_IGNORE_REGEX.search(clean_line):
        return True

    patterns = ignore_patterns if ignore_patterns is not None else DEFAULT_IGNORE_PATTERNS
    for pattern in patterns:
        if pattern.match(clean_line):
            return True

    if not is_markdown and re.match(r"^\d+$", clean_line):
        return True

    return False


def is_valid_heading(
    section_num: str,
    title: str,
    bad_keywords: Sequence[str] | None = None,
) -> bool:
    """Decide whether a candidate heading is genuine.

    Applies several heuristics: major-number range, length cap,
    alphanumeric / CJK check, unit-only filter, leading-zero filter, and
    bad-keyword filter.
    """
    try:
        major = int(section_num.split(".")[0])
    except Exception:
        return False

    if major not in VALID_MAJOR_RANGE:
        return False

    if len(title) > 120:
        return False

    # Letters (Latin), digits, or CJK ideographs
    if not re.search(r"[a-zA-Z0-9\u4e00-\u9fff]", title):
        return False

    full_line = f"{section_num} {title}".strip()
    if UNIT_ONLY_REGEX.match(full_line):
        return False

    if section_num.count(".") == 1:
        minor = section_num.split(".")[1]
        if len(minor) >= 2 and minor.startswith("0"):
            return False

    keywords = bad_keywords if bad_keywords is not None else DEFAULT_BAD_KEYWORDS
    lowered = title.lower()
    for kw in keywords:
        if kw in lowered:
            return False

    return True


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------
def parse_into_chunks(
    lines: list[tuple[int, str]],
    source_file: str,
    *,
    bad_keywords: Sequence[str] | None = None,
) -> list[dict]:
    """Split extracted lines into structured section chunks.

    Each returned dict contains the keys ``number``, ``title``,
    ``content``, ``page_start``, and ``source``.
    """
    chunks: list[dict] = []

    current: dict = {
        "number": "0",
        "title": "Introduction",
        "content": [],
        "page_start": 1,
    }

    tracker = SectionNumberTracker()
    keywords = bad_keywords if bad_keywords is not None else DEFAULT_BAD_KEYWORDS

    for page_num, line in lines:
        clean_line = line.strip()

        is_heading = False
        section_num = ""
        title = ""

        md_match = MD_HEADING_REGEX.match(clean_line)
        if md_match:
            level = len(md_match.group(1))
            title_text = md_match.group(2).strip()
            title_text = re.sub(r"^[\*_#\s]+|[\*_#\s]+$", "", title_text).strip()

            num_match = EXPLICIT_NUM_REGEX.match(title_text)
            if num_match:
                section_num = num_match.group(1)
                title = num_match.group(2) or "Overview"
                title = re.sub(r"^[\*_#\s]+|[\*_#\s]+$", "", title).strip()
                if is_valid_heading(section_num, title, bad_keywords=keywords):
                    tracker.sync(section_num)
                    is_heading = True
            else:
                title = title_text
                section_num = tracker.generate(level)
                if is_valid_heading(section_num, title, bad_keywords=keywords):
                    is_heading = True
        else:
            num_match = EXPLICIT_NUM_REGEX.match(clean_line)
            if num_match:
                section_num = num_match.group(1)
                title = num_match.group(2) or "Overview"
                title = re.sub(r"^[\*_#\s]+|[\*_#\s]+$", "", title).strip()
                if is_valid_heading(section_num, title, bad_keywords=keywords):
                    tracker.sync(section_num)
                    is_heading = True

        if is_heading:
            if current["content"] or current["number"] != "0":
                chunks.append({
                    "number": current["number"],
                    "title": current["title"],
                    "content": "\n".join(current["content"]).strip(),
                    "page_start": current["page_start"],
                    "source": source_file,
                })

            current = {
                "number": section_num,
                "title": title,
                "content": [],
                "page_start": page_num,
            }
            continue

        current["content"].append(line)

    chunks.append({
        "number": current["number"],
        "title": current["title"],
        "content": "\n".join(current["content"]).strip(),
        "page_start": current["page_start"],
        "source": source_file,
    })

    return chunks
