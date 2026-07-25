"""PDF text extraction with PyMuPDF."""

from pathlib import Path

import pymupdf


def full_text(pdf_path: Path, max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if not text:
                continue
            chunk = f"[page {i + 1}]\n{text}"
            parts.append(chunk)
            total += len(chunk)
            if total >= max_chars:
                parts.append("[document truncated]")
                break
    return "\n\n".join(parts)


def render_page_png(pdf_path: Path, page_index: int, out_path: Path, width: int = 1500) -> Path:
    """Render one PDF page to a PNG (for vision questions about figures/equations)."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out_path)
    return out_path


def page_window(pdf_path: Path, center_page: int, radius: int = 1) -> str:
    parts: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        lo = max(0, center_page - radius)
        hi = min(len(doc) - 1, center_page + radius)
        for i in range(lo, hi + 1):
            text = doc[i].get_text().strip()
            marker = " (current page)" if i == center_page else ""
            parts.append(f"[page {i + 1}{marker}]\n{text}")
    return "\n\n".join(parts)
