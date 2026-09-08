"""Base protocol for document extractors."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, Sequence


class DocumentExtractor(Protocol):
    """Interface that all document format extractors must satisfy.

    Each extractor converts a file into a flat list of ``(page_number, line_text)``
    tuples that the parser can process into structured chunks.  The page number
    is ``None`` when the source format has no reliable pagination (DOCX,
    source code — finding F03).

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
    ) -> list[tuple[int | None, str]]:
        """Extract text lines from the given file.

        Args:
            file_path: Absolute or relative path to the document.
            temp_dir: Optional scratch directory for intermediate assets.
            ignore_patterns: Optional line filters during extraction.

        Returns:
            A list of (page number or None, stripped text line) tuples.
        """
        ...