"""Document parsing logic: heading detection, section numbering, and chunk generation."""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

VALID_MAJOR_RANGE = range(1, 100)

MD_HEADING_REGEX = re.compile(
    r'^(#+)\s*(?:\*\*\s*)?(.*?)(?:\s*\*\*)?$'
)

EXPLICIT_NUM_REGEX = re.compile(
    r'^(?:Chapter|Section)?\s*(\d+(?:\.\d+)*)\.?(?:[\s:-]+(.*))?$',
    re.IGNORECASE,
)

TOC_IGNORE_REGEX = re.compile(r'\.{3,}\s*\d+$')

UNIT_ONLY_REGEX = re.compile(
    r'^\d+(?:\.\d+)?\s*(MHz|GHz|W|V|A|mV|mA|s|ns|ms|us|bytes|KB|MB|GB)$',
    re.IGNORECASE,
)

IGNORE_PATTERNS = [
    re.compile(r'^AMD Confidential.*$', re.IGNORECASE),
    re.compile(r'^Page\s+\d+.*$', re.IGNORECASE),
    re.compile(r'^Overclocking Guidance for AMD Family.*$', re.IGNORECASE),
    re.compile(r'^Table of Contents$', re.IGNORECASE),
]

BAD_KEYWORDS = [
    "updated",
    "corrected",
    "release",
    "initial nda",
    "revision history",
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
        parts = section_num.split('.')
        for i, part in enumerate(parts):
            if i < len(self.current_nums):
                try:
                    self.current_nums[i] = int(part)
                except ValueError:
                    self.current_nums[i] = 1  # Fallback
        # Reset subsequent levels
        for i in range(len(parts), len(self.current_nums)):
            self.current_nums[i] = 0

    def generate(self, level: int) -> str:
        """Generate a pseudo-section number for a given *level* (1-indexed)."""
        idx = level - 1
        if idx >= len(self.current_nums):
            idx = len(self.current_nums) - 1

        # Increment the target level
        self.current_nums[idx] += 1

        # Reset all sub-levels
        for i in range(idx + 1, len(self.current_nums)):
            self.current_nums[i] = 0

        # Construct the section number string — READ-ONLY, do NOT mutate state
        parts: list[str] = []
        for i in range(level):
            val = max(self.current_nums[i], 1)  # treat zero as 1 without writing back
            parts.append(str(val))

        return ".".join(parts)


# ---------------------------------------------------------------------------
# Filtering / validation helpers
# ---------------------------------------------------------------------------
def is_ignored(line: str, is_markdown: bool = False) -> bool:
    """Return ``True`` if *line* should be discarded during extraction.

    For Markdown sources blank lines are preserved (they carry formatting
    meaning); for non-Markdown sources they are dropped.
    """
    clean_line = line.strip()

    if not clean_line:
        return not is_markdown

    if TOC_IGNORE_REGEX.search(clean_line):
        return True

    for pattern in IGNORE_PATTERNS:
        if pattern.match(clean_line):
            return True

    if not is_markdown and re.match(r'^\d+$', clean_line):
        return True

    return False


def is_valid_heading(section_num: str, title: str) -> bool:
    """Decide whether a candidate heading is genuine.

    Applies several heuristics: major-number range, length cap,
    alphanumeric check, unit-only filter, leading-zero filter, and
    bad-keyword filter.
    """
    try:
        major = int(section_num.split('.')[0])
    except Exception:
        return False

    if major not in VALID_MAJOR_RANGE:
        return False

    # Heuristic: Real headings are rarely excessively long
    if len(title) > 120:
        return False

    # Heuristic: Real headings must contain some letters/numbers
    if not re.search(r'[a-zA-Z0-9\u4e00-\u9fa5]', title):
        return False

    full_line = f"{section_num} {title}".strip()
    if UNIT_ONLY_REGEX.match(full_line):
        return False

    if section_num.count('.') == 1:
        minor = section_num.split('.')[1]
        if len(minor) >= 2 and minor.startswith('0'):
            return False

    lowered = title.lower()
    for kw in BAD_KEYWORDS:
        if kw in lowered:
            return False

    return True


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------
def parse_into_chunks(
    lines: list[tuple[int, str]],
    source_file: str,
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

    for page_num, line in lines:
        clean_line = line.strip()

        is_heading = False
        section_num = ""
        title = ""

        # First try to match markdown headings
        md_match = MD_HEADING_REGEX.match(clean_line)
        if md_match:
            level = len(md_match.group(1))
            title_text = md_match.group(2).strip()

            # Clean title from any markdown decorators (e.g. bold/italic)
            title_text = re.sub(r'^[\*_#\s]+|[\*_#\s]+$', '', title_text).strip()

            # Check if title starts with explicit section number
            num_match = EXPLICIT_NUM_REGEX.match(title_text)
            if num_match:
                section_num = num_match.group(1)
                title = num_match.group(2) or "Overview"
                title = re.sub(r'^[\*_#\s]+|[\*_#\s]+$', '', title).strip()
                if is_valid_heading(section_num, title):
                    tracker.sync(section_num)
                    is_heading = True
            else:
                title = title_text
                section_num = tracker.generate(level)
                if is_valid_heading(section_num, title):
                    is_heading = True
        else:
            # Check if it has an explicit section number without markdown prefix
            num_match = EXPLICIT_NUM_REGEX.match(clean_line)
            if num_match:
                section_num = num_match.group(1)
                title = num_match.group(2) or "Overview"
                title = re.sub(r'^[\*_#\s]+|[\*_#\s]+$', '', title).strip()
                if is_valid_heading(section_num, title):
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
