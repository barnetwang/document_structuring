"""PDF document extractor using pymupdf4llm."""

import re
import logging
import os
import tempfile
import base64
import json

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
        image_metadata_map = {}

        # Create a temp directory for extracted images
        temp_dir = "temp_pdf_tmp"
        os.makedirs(temp_dir, exist_ok=True)

        # Helper: cluster rects
        def cluster_rects(rects: list[fitz.Rect], threshold: float = 25.0) -> list[fitz.Rect]:
            if not rects:
                return []
            n = len(rects)
            parent = list(range(n))
            def find(i):
                if parent[i] == i:
                    return i
                parent[i] = find(parent[i])
                return parent[i]
            def union(i, j):
                root_i = find(i)
                root_j = find(j)
                if root_i != root_j:
                    parent[root_i] = root_j
            for i in range(n):
                for j in range(i + 1, n):
                    r1 = rects[i]
                    r2 = rects[j]
                    dx = max(0, r1.x0 - r2.x1, r2.x0 - r1.x1)
                    dy = max(0, r1.y0 - r2.y1, r2.y0 - r1.y1)
                    if dx <= threshold and dy <= threshold:
                        union(i, j)
            groups = {}
            for i in range(n):
                root = find(i)
                if root not in groups:
                    groups[root] = []
                groups[root].append(rects[i])
            union_rects = []
            for g_rects in groups.values():
                union_r = fitz.Rect(g_rects[0])
                for r in g_rects[1:]:
                    union_r.include_rect(r)
                union_rects.append(union_r)
            return union_rects

        caption_regex = re.compile(r'^(Figure|Table|圖|表)\s*\d+[-.]\d+.*$', re.IGNORECASE)

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
            """Find page number for a heading by matching against the TOC."""
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

            # Pre-process pages to cluster graphics, extract images, redact text salad, and insert anchors
            for pno in page_indices:
                page = doc[pno]
                page_rect = page.rect
                page_width = page_rect.width
                page_height = page_rect.height

                # Gather all graphic bounding boxes
                rects = []
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

                clusters = cluster_rects(rects, threshold=25.0)
                clusters = [c for c in clusters if c.width >= 30 and c.height >= 30]

                if not clusters:
                    continue

                try:
                    words = page.get_text("words")
                except Exception:
                    words = []

                for idx, c_rect in enumerate(clusters):
                    c_rect = c_rect & page_rect
                    if c_rect.is_empty:
                        continue

                    # Crop and render high-res image
                    try:
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=c_rect)
                        img_filename = f"temp_page_{pno + 1}_{idx}.png"
                        temp_path = os.path.join(temp_dir, img_filename)
                        pix.save(temp_path)
                    except Exception as exc:
                        logger.error("Failed to render image for page %d cluster %d: %s", pno + 1, idx, exc)
                        continue

                    # Extract contained text words inside c_rect
                    contained_words = []
                    for w in words:
                        w_rect = fitz.Rect(w[0], w[1], w[2], w[3])
                        w_area = w_rect.get_area()
                        if w_area > 0:
                            intersect = w_rect & c_rect
                            if (intersect.get_area() / w_area) >= 0.8:
                                contained_words.append({
                                    "text": w[4],
                                    "bbox": [w[0], w[1], w[2], w[3]]
                                })

                    # Detect caption
                    caption = f"Figure Page {pno + 1} Graphics {idx + 1}"
                    try:
                        blocks = page.get_text("blocks")
                    except Exception:
                        blocks = []
                    for blk in blocks:
                        bx0, by0, bx1, by1, btext, bno, btype = blk
                        if btype == 0:
                            btext_clean = btext.strip()
                            if caption_regex.match(btext_clean):
                                is_near = False
                                if (by0 >= c_rect.y1 and by0 <= c_rect.y1 + 35) or (by1 >= c_rect.y0 - 35 and by1 <= c_rect.y0):
                                    is_near = True
                                if is_near:
                                    caption = btext_clean.replace('\n', ' ')
                                    break

                    # Store contained words in local map
                    image_metadata_map[temp_path] = contained_words

                    # Create urlsafe base64 anchor with ONLY temp_path and caption to keep it short
                    mini_meta = {
                        "temp_path": temp_path,
                        "caption": caption
                    }
                    meta_bytes = json.dumps(mini_meta, ensure_ascii=False).encode('utf-8')
                    b64_str = base64.urlsafe_b64encode(meta_bytes).decode('ascii').rstrip('=')
                    anchor_text = f"GRAPHICANCHOR{b64_str}ENDANCHOR"

                    # Add redact annotation
                    page.add_redact_annot(c_rect)
                    page.apply_redactions()

                    # Insert the anchor text in the left margin, font size 5 to prevent layout filtering and clipping
                    page.insert_text(fitz.Point(50, c_rect.y0), anchor_text, fontsize=5)

            # Force serialization of redactions and text insertions by writing and reloading doc
            doc_bytes = doc.write()
            doc_reloaded = fitz.open("pdf", doc_bytes)

            # Batch to_markdown call — single layout analysis for entire batch using reloaded document
            batch_md = pymupdf4llm.to_markdown(doc_reloaded, pages=page_indices)
            doc_reloaded.close()

            # Track current page by checking if a line is a heading that matches TOC
            current_page = start_page + 1

            for raw_line in batch_md.splitlines():
                stripped = raw_line.strip()
                if not is_ignored(stripped, is_markdown=True):
                    # Replace graphic anchors with standard HTML placeholder comment
                    anchor_pattern = re.compile(r'GRAPHICANCHOR([A-Za-z0-9_-]+)ENDANCHOR')

                    def replace_anchor(match):
                        b64_str = match.group(1)
                        try:
                            # Restore base64 padding
                            missing_padding = len(b64_str) % 4
                            if missing_padding:
                                b64_str += '=' * (4 - missing_padding)
                            meta_bytes = base64.urlsafe_b64decode(b64_str)
                            meta_str = meta_bytes.decode('utf-8')
                            mini_meta = json.loads(meta_str)

                            # Reconstruct full meta with contained words
                            t_path = mini_meta.get("temp_path")
                            capt = mini_meta.get("caption")
                            meta = {
                                "temp_path": t_path,
                                "caption": capt,
                                "contained_text": image_metadata_map.get(t_path, [])
                            }
                            meta_str = json.dumps(meta, ensure_ascii=False)
                            return f"<!-- IMAGE: {meta_str} -->"
                        except Exception as e:
                            logger.error("Failed to decode anchor base64: %s", e)
                            return match.group(0)

                    new_line = anchor_pattern.sub(replace_anchor, raw_line)

                    # For headings, try to resolve exact page from TOC
                    md_m = MD_HEADING_REGEX.match(new_line.strip())
                    if md_m:
                        title_text = md_m.group(2).strip()
                        title_text = re.sub(r'^[\*_#\s]+|[\*_#\s]+$', '', title_text).strip()
                        resolved_page = resolve_heading_page(title_text)
                        if resolved_page and start_page < resolved_page <= end_page:
                            current_page = resolved_page

                    lines.append((current_page, new_line))
                else:
                    # Blank/ignored line: advance page tracking for empty pages
                    pass

        doc.close()
        return lines
