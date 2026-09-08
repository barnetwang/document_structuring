"""PDF document extractor using pymupdf4llm."""

from __future__ import annotations

import re
import logging
import json
import base64
from pathlib import Path
from typing import Sequence

import fitz
import pymupdf4llm

from . import register
from ..parser import is_ignored

logger = logging.getLogger(__name__)

# Compiled once — used on every batch line
_ANCHOR_PATTERN = re.compile(r"GRAPHICANCHOR([A-Za-z0-9_-]+)ENDANCHOR")
_CAPTION_REGEX = re.compile(
    r"^(Figure|Table|圖|表|Fig\.?)\s*\d+([.-]\d+)?.*$", re.IGNORECASE
)
_MD_HEADER_SEP_PATTERN = re.compile(r"\|(?:\s*:?-+:?\s*\|)+")


def _has_valid_markdown_table(text: str) -> bool:
    """Check if text contains valid Markdown table header separator syntax."""
    return bool(_MD_HEADER_SEP_PATTERN.search(text))


def _merge_adjacent_tables(tabs: list, gap_threshold: float = 10.0) -> list[list[list[str]]]:
    """Merge vertically adjacent tables that overlap horizontally (handles find_tables splitting borderless tables)."""
    if not tabs:
        return []

    extracted = []
    for tab in tabs:
        rows = tab.extract()
        if rows and len(rows) >= 1:
            extracted.append({"bbox": tab.bbox, "rows": rows})

    if not extracted:
        return []

    extracted.sort(key=lambda item: item["bbox"][1])

    merged = [extracted[0]]
    for cur in extracted[1:]:
        prev = merged[-1]
        p_bbox = prev["bbox"]
        c_bbox = cur["bbox"]

        vertical_gap = c_bbox[1] - p_bbox[3]
        h_overlap = min(p_bbox[2], c_bbox[2]) - max(p_bbox[0], c_bbox[0])

        if vertical_gap <= gap_threshold and h_overlap > 0:
            prev["rows"].extend(cur["rows"])
            prev["bbox"] = (
                min(p_bbox[0], c_bbox[0]),
                p_bbox[1],
                max(p_bbox[2], c_bbox[2]),
                c_bbox[3],
            )
        else:
            merged.append(cur)

    return [item["rows"] for item in merged]


def _extract_page_tables_fallback(page: fitz.Page) -> list[str]:
    """Fallback table extraction using PyMuPDF find_tables strategy='text' for borderless tables."""
    extracted_tables: list[str] = []
    try:
        tabs = page.find_tables(
            strategy="text", min_words_vertical=3, min_words_horizontal=3
        )
        merged_tables = _merge_adjacent_tables(list(tabs))
        for df_rows in merged_tables:
            if not df_rows or len(df_rows) < 2:
                continue

            md_lines = []
            header = [
                str(cell).strip().replace("\n", "<br>") if cell is not None else ""
                for cell in df_rows[0]
            ]
            md_lines.append("| " + " | ".join(header) + " |")
            md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")

            for row in df_rows[1:]:
                clean_row = [
                    str(cell).strip().replace("\n", "<br>") if cell is not None else ""
                    for cell in row
                ]
                md_lines.append("| " + " | ".join(clean_row) + " |")

            extracted_tables.append("\n".join(md_lines))
    except Exception as exc:
        logger.debug("find_tables fallback exception: %s", exc)

    return extracted_tables




def _cluster_rects(rects: list[fitz.Rect], threshold: float = 25.0) -> list[fitz.Rect]:
    """Union-find clustering of nearby rectangles (axis-aligned proximity)."""
    if not rects:
        return []
    n = len(rects)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    for i in range(n):
        r1 = rects[i]
        for j in range(i + 1, n):
            r2 = rects[j]
            dx = max(0, r1.x0 - r2.x1, r2.x0 - r1.x1)
            dy = max(0, r1.y0 - r2.y1, r2.y0 - r1.y1)
            if dx <= threshold and dy <= threshold:
                union(i, j)

    groups: dict[int, list[fitz.Rect]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(rects[i])

    union_rects: list[fitz.Rect] = []
    for g_rects in groups.values():
        union_r = fitz.Rect(g_rects[0])
        for r in g_rects[1:]:
            union_r.include_rect(r)
        union_rects.append(union_r)
    return union_rects


@register(".pdf")
class PdfExtractor:
    """Extract text lines from PDF files using pymupdf4llm.

    Pipeline (optimised for large manuals):
      1. Pre-process *all* pages once: cluster drawings/images, crop PNGs,
         redact overlapping text, insert margin anchors.
      2. Serialise the modified document **once**, then convert to
         Markdown with ``page_chunks=True`` so **every physical page
         produces exactly one chunk** — line→page attribution is exact
         and never depends on PDF bookmarks (finding F03: the previous
         batch mode attributed all lines of a 50-page batch to the
         batch's first page unless a heading happened to match a TOC
         entry).
    """

    def extract_lines(
        self,
        file_path: str,
        batch_size: int = 50,
        *,
        temp_dir: str | Path | None = None,
        ignore_patterns: Sequence[re.Pattern[str]] | None = None,
    ) -> list[tuple[int, str]]:
        """Extract text lines from a PDF.

        Args:
            file_path: Path to the PDF file.
            batch_size: Accepted for backward compatibility; the
                page-chunked conversion path no longer batches pages.
            temp_dir: Directory for intermediate cropped images.
            ignore_patterns: Optional line filters (defaults to built-ins).

        Returns:
            A list of (1-based physical page number, text line) tuples.
            Page attribution is by physical page span — no bookmark or
            printed-label guessing is involved.
        """
        doc = fitz.open(file_path)
        lines: list[tuple[int, str]] = []
        total_pages = len(doc)
        image_metadata_map: dict[str, list[dict]] = {}

        work_dir = Path(temp_dir) if temp_dir else Path(".doc_structuring_tmp")
        work_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # Step 1: Pre-process ALL pages once (graphics cluster + anchors)
        # ------------------------------------------------------------------
        for pno in range(total_pages):
            page = doc[pno]
            page_rect = page.rect
            page_width = page_rect.width
            page_height = page_rect.height

            rects: list[fitz.Rect] = []
            try:
                drawings = page.get_drawings()
            except Exception:
                drawings = []
            for d in drawings:
                r = fitz.Rect(d["rect"])
                if r.width > page_width * 0.85 and r.height < 5:
                    continue
                if r.y1 < 45 or r.y0 > page_height - 45:
                    continue
                if r.width < 5 and r.height < 5:
                    continue
                rects.append(r)

            try:
                img_info = page.get_image_info(rects=True)
            except Exception:
                img_info = []
            for img in img_info:
                r = fitz.Rect(img["bbox"])
                if r.y1 < 45 or r.y0 > page_height - 45:
                    continue
                rects.append(r)

            clusters = [
                c
                for c in _cluster_rects(rects, threshold=25.0)
                if c.width >= 30 and c.height >= 30
            ]
            if not clusters:
                continue

            try:
                words = page.get_text("words")
            except Exception:
                words = []

            try:
                blocks = page.get_text("blocks")
            except Exception:
                blocks = []

            for idx, c_rect in enumerate(clusters):
                c_rect = c_rect & page_rect
                if c_rect.is_empty:
                    continue

                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=c_rect)
                    img_filename = f"temp_page_{pno + 1}_{idx}.png"
                    temp_path = str(work_dir / img_filename)
                    pix.save(temp_path)
                except Exception as exc:
                    logger.error(
                        "Failed to render image for page %d cluster %d: %s",
                        pno + 1,
                        idx,
                        exc,
                    )
                    continue

                contained_words = []
                for w in words:
                    w_rect = fitz.Rect(w[0], w[1], w[2], w[3])
                    w_area = w_rect.get_area()
                    if w_area > 0:
                        intersect = w_rect & c_rect
                        if (intersect.get_area() / w_area) >= 0.8:
                            contained_words.append({
                                "text": w[4],
                                "bbox": [w[0], w[1], w[2], w[3]],
                            })

                caption = f"Figure Page {pno + 1} Graphics {idx + 1}"
                for blk in blocks:
                    bx0, by0, bx1, by1, btext, bno, btype = blk
                    if btype != 0:
                        continue
                    btext_clean = btext.strip()
                    if not _CAPTION_REGEX.match(btext_clean):
                        continue
                    near = (by0 >= c_rect.y1 and by0 <= c_rect.y1 + 35) or (
                        by1 >= c_rect.y0 - 35 and by1 <= c_rect.y0
                    )
                    if near:
                        caption = btext_clean.replace("\n", " ")
                        break

                image_metadata_map[temp_path] = contained_words

                mini_meta = {"temp_path": temp_path, "caption": caption}
                meta_bytes = json.dumps(mini_meta, ensure_ascii=False).encode("utf-8")
                b64_str = base64.urlsafe_b64encode(meta_bytes).decode("ascii").rstrip("=")
                anchor_text = f"GRAPHICANCHOR{b64_str}ENDANCHOR"

                page.add_redact_annot(c_rect)
                page.apply_redactions()
                page.insert_text(
                    fitz.Point(50, c_rect.y0), anchor_text, fontsize=5
                )

        # Single serialisation after all page edits (major speed-up vs per-batch write)
        doc_bytes = doc.write()
        doc.close()
        doc_reloaded = fitz.open("pdf", doc_bytes)

        # ------------------------------------------------------------------
        # Step 3: Batch markdown conversion on the modified document
        # ------------------------------------------------------------------
        def replace_anchor(match: re.Match[str]) -> str:
            b64_str = match.group(1)
            try:
                missing_padding = len(b64_str) % 4
                if missing_padding:
                    b64_str += "=" * (4 - missing_padding)
                mini_meta = json.loads(
                    base64.urlsafe_b64decode(b64_str).decode("utf-8")
                )
                t_path = mini_meta.get("temp_path")
                meta = {
                    "temp_path": t_path,
                    "caption": mini_meta.get("caption"),
                    "contained_text": image_metadata_map.get(t_path, []),
                }
                return f"<!-- IMAGE: {json.dumps(meta, ensure_ascii=False)} -->"
            except Exception as e:
                logger.error("Failed to decode anchor base64: %s", e)
                return match.group(0)

        # ------------------------------------------------------------------
        # Step 2: Page-chunked markdown conversion.
        #
        # page_chunks=True yields exactly one entry per physical page, in
        # page order.  Every text line is therefore attributed to its true
        # physical page with no bookmark lookup (finding F03).  For a 73-page
        # manual this single call takes ~10 s — competitive with the old
        # batched path and correct by construction.
        # ------------------------------------------------------------------
        page_chunks = pymupdf4llm.to_markdown(
            doc_reloaded, pages=list(range(total_pages)), page_chunks=True
        )
        if len(page_chunks) != total_pages:
            raise RuntimeError(
                "Page-chunked conversion returned "
                f"{len(page_chunks)} chunks for {total_pages} pages; "
                "cannot attribute lines to physical pages reliably."
            )

        for idx, page_chunk in enumerate(page_chunks):
            page_num = idx + 1
            for raw_line in (page_chunk.get("text") or "").splitlines():
                stripped = raw_line.strip()
                if is_ignored(
                    stripped, is_markdown=True, ignore_patterns=ignore_patterns
                ):
                    continue

                new_line = _ANCHOR_PATTERN.sub(replace_anchor, raw_line)
                lines.append((page_num, new_line))

        doc_reloaded.close()
        return lines
