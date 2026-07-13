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
from ..parser import MD_HEADING_REGEX, is_ignored

logger = logging.getLogger(__name__)

# Compiled once — used on every batch line
_ANCHOR_PATTERN = re.compile(r"GRAPHICANCHOR([A-Za-z0-9_-]+)ENDANCHOR")
_CAPTION_REGEX = re.compile(
    r"^(Figure|Table|圖|表|Fig\.?)\s*\d+([.-]\d+)?.*$", re.IGNORECASE
)
_STRIP_SECTION_NUM = re.compile(r"^\d+(\.\d+)*\s*", re.ASCII)


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
    """Extract text lines from PDF files using pymupdf4llm in batch mode.

    Pipeline (optimised for large manuals):
      1. Build heading→page map from PDF bookmarks (TOC).
      2. Pre-process *all* pages once: cluster drawings/images, crop PNGs,
         redact overlapping text, insert margin anchors.
      3. Serialise the modified document **once**, then batch-convert to
         Markdown (avoids rewriting the full PDF on every batch).
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
            batch_size: Pages per pymupdf4llm batch (default 50).
            temp_dir: Directory for intermediate cropped images.
            ignore_patterns: Optional line filters (defaults to built-ins).

        Returns:
            A list of (1-based page number, text line) tuples.
        """
        doc = fitz.open(file_path)
        lines: list[tuple[int, str]] = []
        total_pages = len(doc)
        image_metadata_map: dict[str, list[dict]] = {}

        work_dir = Path(temp_dir) if temp_dir else Path(".doc_structuring_tmp")
        work_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # Step 1: heading→page map from PDF internal TOC
        # ------------------------------------------------------------------
        toc_page_map: dict[str, int] = {}
        try:
            for _lvl, title, page in doc.get_toc():
                t = title.strip()
                if not t:
                    continue
                toc_page_map[t] = page
                no_num = _STRIP_SECTION_NUM.sub("", t).strip()
                if no_num and no_num != t:
                    toc_page_map[no_num] = page
        except Exception:
            toc_page_map = {}

        def resolve_heading_page(title_text: str) -> int | None:
            title_lower = title_text.lower()
            best_len, best_page = 0, 1
            for t_t, p in toc_page_map.items():
                tp = t_t.lower()
                if (
                    title_lower == tp or title_lower in tp or tp in title_lower
                ) and len(t_t) > best_len:
                    best_len, best_page = len(t_t), p
            return best_page if best_len > 0 else None

        # ------------------------------------------------------------------
        # Step 2: Pre-process ALL pages once (graphics cluster + anchors)
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

        for start_page in range(0, total_pages, batch_size):
            end_page = min(start_page + batch_size, total_pages)
            page_indices = list(range(start_page, end_page))

            batch_md = pymupdf4llm.to_markdown(doc_reloaded, pages=page_indices)
            current_page = start_page + 1

            for raw_line in batch_md.splitlines():
                stripped = raw_line.strip()
                if is_ignored(
                    stripped, is_markdown=True, ignore_patterns=ignore_patterns
                ):
                    continue

                new_line = _ANCHOR_PATTERN.sub(replace_anchor, raw_line)

                md_m = MD_HEADING_REGEX.match(new_line.strip())
                if md_m:
                    title_text = md_m.group(2).strip()
                    title_text = re.sub(
                        r"^[\*_#\s]+|[\*_#\s]+$", "", title_text
                    ).strip()
                    resolved_page = resolve_heading_page(title_text)
                    if resolved_page and start_page < resolved_page <= end_page:
                        current_page = resolved_page

                lines.append((current_page, new_line))

        doc_reloaded.close()
        return lines
