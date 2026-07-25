"""Extract smart-highlight text from v6 .rm page files using rmscene."""

import logging
from pathlib import Path

from rmscene import read_blocks
from rmscene.scene_stream import SceneGlyphItemBlock

from .documents import page_ids

log = logging.getLogger(__name__)


def _page_highlights(rm_path: Path) -> list[str]:
    texts: list[str] = []
    with rm_path.open("rb") as f:
        for block in read_blocks(f):
            if isinstance(block, SceneGlyphItemBlock):
                value = getattr(block.item, "value", None)
                text = getattr(value, "text", None)
                if text:
                    texts.append(text)
    return texts


def extract_highlights(sync_dir: Path, doc_id: str, content: dict) -> list[dict]:
    """Return [{"page_index": int, "texts": [str, ...]}] for pages with highlights.

    Parsing failures are isolated per page so one bad .rm file never kills context.
    """
    results = []
    page_dir = sync_dir / doc_id
    if not page_dir.is_dir():
        return results
    for index, page_id in enumerate(page_ids(content)):
        rm_path = page_dir / f"{page_id}.rm"
        if not rm_path.exists():
            continue
        try:
            texts = _page_highlights(rm_path)
        except Exception as exc:
            log.warning("failed to parse %s: %s", rm_path.name, exc)
            continue
        if texts:
            results.append({"page_index": index, "texts": texts})
    return results
