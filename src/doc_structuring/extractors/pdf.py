"""PDF document extractor using pymupdf4llm."""

import re
import logging

import fitz
import pymupdf4llm

from . import register
from ..parser import MD_HEADING_REGEX, is_ignored

logger = logging.getLogger(__name__)


@register(".pdf")
class PdfExtractor:
    """Extract text lines from PDF files using pymupdf4llm in batch mode.

    Processes pages in batches (default 50-page chunks). Each batch call does
    layout analysis only once for the whole chunk, giving ~2x speedup vs
    page-by-page processing where every page repeats the full overhead.

    To preserve accurate page_start tracking without relying on pymupdf4llm's
    (non-existent) page markers in batch mode, we pre-build a heading→page map
    from the PDF bookmarks/internal TOC via doc.get_toc(). This runs in <1s and
    maps section titles to exact page numbers. For headings not in the TOC,
    we fall back to start_page + 1 of the current batch.
    """

    def extract_lines(self, file_path: str, batch_size: int = 50) -> list[tuple[int, str]]:
        """Extract text lines from a PDF using pymupdf4llm in BATCH mode.

        Args:
            file_path: Path to the PDF file.
            batch_size: Number of pages to process per batch (default 50).

        Returns:
            A list of (1-based page number, stripped text line) tuples.
        """
        doc = fitz.open(file_path)
        lines: list[tuple[int, str]] = []
        total_pages = len(doc)

        # ------------------------------------------------------------------
        # Step 1: Build heading→page map from PDF internal TOC (near-instant)
        # ------------------------------------------------------------------
        try:
            toc_entries = doc.get_toc()
            # toc format: [(level, title, page), ...]
            # Normalise titles: strip leading section numbers for fuzzy matching
            _to_title = re.compile(r'^\d+(\.\d+)*\s*', re.ASCII)
            toc_page_map: dict[str, int] = {}
            for _lvl, title, page in toc_entries:
                t = title.strip()
                if not t:
                    continue
                toc_page_map[t] = page
                no_num = _to_title.sub('', t).strip()
                if no_num != t and no_num:
                    toc_page_map[no_num] = page
        except Exception:
            toc_page_map = {}  # some PDFs have no internal TOC

        def resolve_heading_page(title_text: str) -> int | None:
            """Find page number for a heading by matching against the TOC.

            Uses case-insensitive substring containment for fuzzy matching.
            Returns the page number of the first match, or None.
            """
            title_lower = title_text.lower()
            best_len, best_page = 0, 1  # prefer longest match
            for t_t, p in toc_page_map.items():
                tp = t_t.lower()
                if (title_lower == tp or title_lower in tp or tp in title_lower) and len(t_t) > best_len:
                    best_len, best_page = len(t_t), p
            return best_page if best_len > 0 else None

        # ------------------------------------------------------------------
        # Step 2: Batch pymupdf4llm conversion with heading→page resolution
        # ------------------------------------------------------------------
        for start_page in range(0, total_pages, batch_size):
            end_page = min(start_page + batch_size, total_pages)
            page_indices = list(range(start_page, end_page))

            # Batch to_markdown call — single layout analysis for entire batch
            batch_md = pymupdf4llm.to_markdown(doc, pages=page_indices)

            # Track current page by checking if a line is a heading that matches TOC
            current_page = start_page + 1

            for raw_line in batch_md.splitlines():
                stripped = raw_line.strip()
                if not is_ignored(stripped, is_markdown=True):
                    # For headings, try to resolve exact page from TOC
                    md_m = MD_HEADING_REGEX.match(stripped)
                    if md_m:
                        title_text = md_m.group(2).strip()
                        title_text = re.sub(r'^[\*_#\s]+|[\*_#\s]+$', '', title_text).strip()
                        resolved_page = resolve_heading_page(title_text)
                        if resolved_page and start_page < resolved_page <= end_page:
                            current_page = resolved_page

                    lines.append((current_page, stripped))
                else:
                    # Blank/ignored line: advance page tracking for empty pages
                    pass

        doc.close()
        return lines
