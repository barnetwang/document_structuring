"""Application configuration with environment variable fallback."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def _default_ignore_patterns() -> list[re.Pattern[str]]:
    """Generic line filters for headers/footers common across manuals.

    Vendor-specific banners are intentionally omitted so the tool works for
    arbitrary documents. Extend via ``AppConfig.extra_ignore_patterns``.
    """
    return [
        re.compile(r"^Page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
        re.compile(r"^Table of Contents$", re.IGNORECASE),
        re.compile(r"^Contents$", re.IGNORECASE),
        # Short header/footer banners only — not long body sentences
        re.compile(
            r"^(?=.{0,70}$).*\b(Confidential|Proprietary|Internal Use Only)\b.*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^Copyright\s*(©|\(c\))?.*All rights reserved\.?$",
            re.IGNORECASE,
        ),
    ]


def _default_bad_heading_keywords() -> list[str]:
    """Title substrings that usually mark revision / changelog noise, not sections."""
    return [
        "updated",
        "corrected",
        "release",
        "initial nda",
        "revision history",
        "document revision",
        "change history",
        "revision record",
    ]


@dataclass
class AppConfig:
    """Centralised configuration for all doc_structuring modules.

    Attributes:
        base_dir: Root directory for output and database files.
                  Defaults to ``DOC_STRUCTURING_BASE_DIR``, else CWD.
        extra_ignore_patterns: Additional regex strings applied when filtering
            extracted lines (compiled lazily).
        bad_heading_keywords: Title substrings rejected as section headings.
        pdf_batch_size: Pages per pymupdf4llm batch when extracting PDFs.
        locale: Language for generated catalog/index labels (``en`` or ``zh``).
        search_limit: Max rows returned by FTS/LIKE search.
    """

    base_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("DOC_STRUCTURING_BASE_DIR", str(Path.cwd()))
        )
    )
    extra_ignore_patterns: list[str] = field(default_factory=list)
    bad_heading_keywords: list[str] = field(
        default_factory=_default_bad_heading_keywords
    )
    pdf_batch_size: int = 50
    locale: str = field(
        default_factory=lambda: os.getenv("DOC_STRUCTURING_LOCALE", "en")
    )
    search_limit: int = 100

    @property
    def output_dir(self) -> Path:
        """Directory for generated document output trees."""
        return self.base_dir / "output"

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.base_dir / "documents.db"

    @property
    def chunks_subdir(self) -> str:
        """Subdirectory name for chunk files within each document output."""
        return "chunks"

    @property
    def temp_dir(self) -> Path:
        """Scratch directory for intermediate image extraction."""
        return self.base_dir / ".doc_structuring_tmp"

    def compiled_ignore_patterns(self) -> list[re.Pattern[str]]:
        """Built-in ignore patterns plus any user-supplied extras."""
        patterns = list(_default_ignore_patterns())
        for raw in self.extra_ignore_patterns:
            try:
                patterns.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue
        return patterns
