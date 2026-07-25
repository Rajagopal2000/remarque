"""Locate the currently open document in a synced xochitl store."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Document:
    doc_id: str
    title: str
    last_opened_ms: int
    page_index: int
    n_pages: int
    pdf_path: Path | None
    content: dict


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def list_documents(sync_dir: Path) -> list[Document]:
    docs = []
    for meta_path in sync_dir.glob("*.metadata"):
        meta = _load_json(meta_path)
        if not meta or meta.get("type") != "DocumentType" or meta.get("deleted"):
            continue
        doc_id = meta_path.stem
        content = _load_json(sync_dir / f"{doc_id}.content") or {}
        pdf_path = sync_dir / f"{doc_id}.pdf"
        try:
            last_opened = int(meta.get("lastOpened", 0) or 0)
        except ValueError:
            last_opened = 0
        docs.append(
            Document(
                doc_id=doc_id,
                title=meta.get("visibleName", doc_id),
                last_opened_ms=last_opened,
                page_index=int(meta.get("lastOpenedPage", 0) or 0),
                n_pages=int(content.get("pageCount", 0) or 0),
                pdf_path=pdf_path if pdf_path.exists() else None,
                content=content,
            )
        )
    return docs


def current_document(sync_dir: Path) -> Document | None:
    docs = [d for d in list_documents(sync_dir) if d.last_opened_ms > 0]
    return max(docs, key=lambda d: d.last_opened_ms, default=None)


def get_document(sync_dir: Path, doc_id: str) -> Document | None:
    return next((d for d in list_documents(sync_dir) if d.doc_id == doc_id), None)


def page_ids(content: dict) -> list[str]:
    """Ordered notebook page ids from a .content file (new cPages or legacy pages)."""
    cpages = content.get("cPages")
    if isinstance(cpages, dict) and isinstance(cpages.get("pages"), list):
        return [
            p["id"]
            for p in cpages["pages"]
            if isinstance(p, dict) and "id" in p and "deleted" not in p
        ]
    pages = content.get("pages")
    if isinstance(pages, list):
        return [p for p in pages if isinstance(p, str)]
    return []


def pdf_page_index(content: dict, notebook_page_index: int) -> int:
    """Map a notebook page index to the underlying PDF page index.

    Pages inserted between PDF pages have no redirection entry (value -1).
    """
    cpages = content.get("cPages")
    if isinstance(cpages, dict) and isinstance(cpages.get("pages"), list):
        pages = cpages["pages"]
        if 0 <= notebook_page_index < len(pages):
            redir = pages[notebook_page_index].get("redir")
            if isinstance(redir, dict) and isinstance(redir.get("value"), int):
                return redir["value"]
            return -1
    redirection = content.get("redirectionPageMap")
    if isinstance(redirection, list) and 0 <= notebook_page_index < len(redirection):
        return redirection[notebook_page_index]
    return notebook_page_index
