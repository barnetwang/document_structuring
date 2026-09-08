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

CROSS_REF_PATTERN = re.compile(
    r"(?i)\b(?:see|refer\s+to)?\s*"
    r"\b(section|chapter|figure|table|appendix)\s+"
    r"([0-9]+(?:\.[0-9]+)*)\b"
)

IMPLICIT_REF_PATTERN = re.compile(
    r"(?i)\b(?:the\s+)?(?:following|below|above|next|previous)\s+"
    r"(table|figure|section|list)\b"
)


def extract_cross_references(content: str) -> list[dict]:
    """Extract structured cross-references (explicit and implicit) from content text."""
    if not content:
        return []

    refs: list[dict] = []
    seen = set()

    # Explicit references (e.g. Section 9.2.5, Table 113, Figure 10)
    for m in CROSS_REF_PATTERN.finditer(content):
        ref_type = m.group(1).lower()
        ref_target = m.group(2).strip()
        key = (ref_type, ref_target)
        if key not in seen:
            seen.add(key)
            refs.append({"type": ref_type, "target": ref_target, "implicit": False})

    # Implicit references (e.g. following table, figure below)
    for m in IMPLICIT_REF_PATTERN.finditer(content):
        ref_type = m.group(1).lower()
        ref_target = m.group(0).strip().lower()
        key = (ref_type, ref_target)
        if key not in seen:
            seen.add(key)
            refs.append({"type": ref_type, "target": ref_target, "implicit": True})

    return refs



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
    # NOTE: bare "release" was dropped — it silently swallowed legitimate
    # numbered sections like "5.5 Release Remediation" in TREC P-022.
    # The phrase form is kept so revision tables are still skipped.
    "release history",
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
    strict_major_range: bool = True,
) -> bool:
    """Decide whether a candidate heading is genuine.

    Applies several heuristics: major-number range, length cap,
    alphanumeric / CJK check, unit-only filter, leading-zero filter, and
    bad-keyword filter.

    ``strict_major_range`` gates the VALID_MAJOR_RANGE check. It is relaxed
    for headings the extractor explicitly marked with Markdown ``#``
    (author-controlled Word styles): such titles are genuine section
    headings even when vendor templates use numbers outside 1..99.
    """
    if len(title) > 120:
        return False

    try:
        major = int(section_num.split(".")[0])
    except Exception:
        return False

    if strict_major_range and major not in VALID_MAJOR_RANGE:
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
# Heading prepass (Markdown headings only)
# ---------------------------------------------------------------------------
#: Unnumbered headings that are navigation boilerplate, not sections.
#: Checked only for unnumbered headings — an explicitly numbered
#: "2. Table of Contents" section (rare, but legitimate) keeps its heading.
_NAV_BOILERPLATE = {"table of contents"}


def _unnumbered_viable(title: str, keywords: Sequence[str]) -> bool:
    """Quick viability check for an unnumbered candidate heading."""
    if title.lower() in _NAV_BOILERPLATE:
        return False
    return is_valid_heading("0", title, bad_keywords=keywords, strict_major_range=False)


def _markdown_heading_prepass(
    lines: list[tuple[int, str]],
    bad_keywords: Sequence[str] | None = None,
) -> dict[int, tuple[bool, str, str]]:
    """Validate Markdown-marked headings and assign section numbers.

    Runs in a dedicated pass (own tracker) BEFORE the main chunking loop.
    Explicitly numbered headings are authoritative and re-synchronize the
    tracker absolutely, which self-heals drift everywhere else.

    Unnumbered headings (author-chosen Word styles / bold fallback — common
    in vendor compliance templates whose front-matter H1s "Purpose",
    "Scope" etc. carry no visible number) receive positionally generated
    numbers in the document's own sequence, with a sub-series anchor:

      * run  = maximal consecutive run of viable unnumbered headings at the
               same heading level;
      * anchor: if the FIRST explicitly numbered heading after the run sits
               one level deeper (e.g. "3.1 Abbreviation" follows the run
               [Purpose, Scope, Abbreviations and Definitions]), the run is
               numbered so its LAST member becomes that parent major ("3"),
               i.e. [1, 2, 3].  This recovers the template's implied
               front-matter numbering without hardcoding it.

    Navigation boilerplate (unnumbered "Table of Contents") and keyword/
    length-invalid titles are rejected and never number anything.

    Returns a plan keyed by line index: ``plan[idx] = (is_heading, number, title)``.
    """
    keywords = bad_keywords if bad_keywords is not None else DEFAULT_BAD_KEYWORDS

    # --- phase 1: collect Markdown headings --------------------------------
    entries: list[dict] = []
    for idx, (_, raw) in enumerate(lines):
        m = MD_HEADING_REGEX.match(raw.strip())
        if not m:
            continue
        level = len(m.group(1))
        title_text = m.group(2).strip()
        title_text = re.sub(r"^[\*_#\s]+|[\*_#\s]+$", "", title_text).strip()
        num_match = EXPLICIT_NUM_REGEX.match(title_text)
        if num_match:
            title = re.sub(
                r"^[\*_#\s]+|[\*_#\s]+$", "", num_match.group(2) or "Overview"
            ).strip()
            entries.append({"idx": idx, "level": level,
                            "explicit": num_match.group(1), "title": title})
        else:
            entries.append({"idx": idx, "level": level, "explicit": None,
                            "title": title_text})

    def _major(num: str) -> int:
        try:
            return int(num.split(".")[0])
        except ValueError:
            return 0

    # --- phase 2: number in document order ---------------------------------
    tracker = SectionNumberTracker()
    last_explicit_major = 0
    i, total = 0, len(entries)
    while i < total:
        e = entries[i]
        if e["explicit"] is not None:
            e["number"] = e["explicit"]
            tracker.sync(e["explicit"])
            last_explicit_major = max(last_explicit_major, _major(e["explicit"]))
            i += 1
            continue
        if not _unnumbered_viable(e["title"], keywords):
            e["rejected"] = True
            i += 1
            continue
        # Maximal run of consecutive viable unnumbered headings, same level.
        j = i
        while (j + 1 < total and entries[j + 1]["explicit"] is None
               and entries[j + 1]["level"] == e["level"]
               and _unnumbered_viable(entries[j + 1]["title"], keywords)):
            j += 1
        run = entries[i:j + 1]

        # Look ahead for the first explicit heading to derive an anchor.
        anchor_parent: int | None = None
        for g in entries[j + 1:]:
            if g["explicit"] is not None:
                if g["level"] == e["level"] + 1:
                    p = _major(g["explicit"])
                    if p > last_explicit_major and p >= len(run):
                        anchor_parent = p
                break
            if g["level"] <= e["level"]:
                break  # next same-level run starts; no anchor for this run

        if anchor_parent is not None:
            for k, g in enumerate(run):
                g["number"] = str(anchor_parent - (len(run) - 1 - k))
                tracker.sync(g["number"])
        else:
            for g in run:
                g["number"] = tracker.generate(g["level"])
                tracker.sync(g["number"])
        i = j + 1

    # --- phase 3: final validity + plan ------------------------------------
    plan: dict[int, tuple[bool, str, str]] = {}
    seen_major = 0
    for e in entries:
        number = e.get("number")
        ok = False
        if number is not None and not e.get("rejected"):
            ok = (is_valid_heading(number, e["title"], bad_keywords=keywords,
                                   strict_major_range=False)
                  and _major(number) >= seen_major)
            if ok:
                seen_major = max(seen_major, _major(number))
        plan[e["idx"]] = (ok, number or "0", e["title"])
    return plan


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
    ``content``, ``page_start``, ``page_end``, and ``source``.

    Page provenance (finding F03): for line-sourced documents the
    extractor supplies physical page numbers and ``page_start`` /
    ``page_end`` span the chunk's first to last physical page.  For
    documents without reliable pagination (DOCX, source code) both
    keys are ``None`` — never a fabricated "page 1".
    """
    chunks: list[dict] = []

    current: dict = {
        "number": "0",
        "title": "Introduction",
        "content": [],
        "page_start": None,
        "page_end": None,
    }

    tracker = SectionNumberTracker()
    keywords = bad_keywords if bad_keywords is not None else DEFAULT_BAD_KEYWORDS

    # One-pass prevalidation of Markdown headings (handles unnumbered
    # vendor-template headings and Annex number drift — see
    # _markdown_heading_prepass).
    md_plan = _markdown_heading_prepass(lines, bad_keywords=bad_keywords)

    for line_idx, (page_num, line) in enumerate(lines):
        clean_line = line.strip()

        is_heading = False
        section_num = ""
        title = ""

        md_match = MD_HEADING_REGEX.match(clean_line)
        if md_match:
            entry = md_plan.get(line_idx)
            if entry is not None and entry[0]:
                is_heading = True
                section_num = entry[1]
                title = entry[2]
                tracker.sync(section_num)
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
                chunk_text = "\n".join(current["content"]).strip()
                chunks.append({
                    "number": current["number"],
                    "title": current["title"],
                    "content": chunk_text,
                    "page_start": current["page_start"],
                    "page_end": current["page_end"],
                    "source": source_file,
                    "cross_references": extract_cross_references(chunk_text),
                })

            current = {
                "number": section_num,
                "title": title,
                "content": [],
                "page_start": page_num,
                "page_end": page_num,
            }
            continue

        current["content"].append(line)
        if page_num is not None:
            if current["page_start"] is None:
                current["page_start"] = page_num
            if current["page_end"] is None or page_num > current["page_end"]:
                current["page_end"] = page_num

    final_chunk_text = "\n".join(current["content"]).strip()
    chunks.append({
        "number": current["number"],
        "title": current["title"],
        "content": final_chunk_text,
        "page_start": current["page_start"],
        "page_end": current["page_end"],
        "source": source_file,
        "cross_references": extract_cross_references(final_chunk_text),
    })

    return chunks

