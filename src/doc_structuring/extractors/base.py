"""Base protocol for document extractors."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, Sequence


class DocumentExtractor(Protocol):
    """Interface that all document format extractors must satisfy.

    Each extractor converts a file into a flat list of ``(page_number, line_text)``
    tuples that the parser can process into structured chunks.

    Optional keyword-only arguments (``temp_dir``, ``ignore_patterns``,
    ``batch_size`` for PDF) may be accepted by concrete implementations.
    """

    def extract_lines(
        self,
        file_path: str,
        *args,
        temp_dir: str | Path | None = None,
        ignore_patterns: Sequence[re.Pattern[str]] | None = None,
        **kwargs,
    ) -> list[tuple[int, str]]:
        """Extract text lines from the given file.

        Args:
            file_path: Absolute or relative path to the document.
            temp_dir: Optional scratch directory for intermediate assets.
            ignore_patterns: Optional line filters during extraction.

        Returns:
            A list of (1-based page number, stripped text line) tuples.
        """
        ...