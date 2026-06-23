"""Base protocol for document extractors."""

from __future__ import annotations

from typing import Protocol


class DocumentExtractor(Protocol):
    """Interface that all document format extractors must satisfy.

    Each extractor converts a file into a flat list of ``(page_number, line_text)``
    tuples that the parser can process into structured chunks.
    """

    def extract_lines(self, file_path: str) -> list[tuple[int, str]]:
        """Extract text lines from the given file.

        Args:
            file_path: Absolute or relative path to the document.

        Returns:
            A list of (1-based page number, stripped text line) tuples.
        """
        ...
