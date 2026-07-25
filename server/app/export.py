"""Export a document's Q&A history as a reading-notes PDF, optionally pushed
to the tablet as a new xochitl document.

Push caveat (verify on device): xochitl only indexes new documents on restart,
so the pushed notes appear after the tablet is restarted. We deliberately do
not restart xochitl ourselves: the assistant panel runs inside it.
"""

import html
import io
import json
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import pymupdf

from .config import settings
from .sync import ssh_command

PAGE_RECT = pymupdf.paper_rect("a4")


def build_notes_pdf(title: str, turns: list[dict]) -> bytes:
    parts = [
        "<h1>Reading notes</h1>",
        f"<h2>{html.escape(title)}</h2>",
        f"<p><i>Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}</i></p>",
    ]
    last_day = ""
    for turn in turns:
        if turn.get("ts"):
            day = datetime.fromtimestamp(turn["ts"]).strftime("%A, %d %B %Y")
            if day != last_day:
                parts.append(f"<h3>{day}</h3>")
                last_day = day
        content = html.escape(turn["content"]).replace("\n", "<br>")
        if turn["role"] == "user":
            parts.append(f"<p><b>Q: {content}</b></p>")
        else:
            parts.append(f"<p>{content}</p>")

    story = pymupdf.Story(html="".join(parts))
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more = True
    while more:
        device = writer.begin_page(PAGE_RECT)
        more, _ = story.place(PAGE_RECT + (36, 36, -36, -36))
        story.draw(device)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def build_notes_markdown(title: str, turns: list[dict], highlights: list[dict] | None = None) -> str:
    """Markdown reading notes: frontmatter, highlights, then the Q&A history."""
    lines = [
        "---",
        f'title: "Reading notes: {title.replace(chr(34), chr(39))}"',
        f"date: {datetime.now().strftime('%Y-%m-%d')}",
        "tags: [remarque]",
        "---",
        "",
        f"# {title}",
    ]
    if highlights:
        lines += ["", "## Highlights", ""]
        for h in highlights:
            for t in h["texts"]:
                lines.append(f"- (p. {h['page_index'] + 1}) {t}")
    lines += ["", "## Questions and answers"]
    last_day = ""
    for turn in turns:
        if turn.get("ts"):
            day = datetime.fromtimestamp(turn["ts"]).strftime("%Y-%m-%d")
            if day != last_day:
                lines += ["", f"### {day}"]
                last_day = day
        if turn["role"] == "user":
            page = f" *(p. {turn['page']})*" if turn.get("page") else ""
            lines += ["", f"**Q: {turn['content']}**{page}", ""]
        else:
            lines.append(turn["content"])
    return "\n".join(lines) + "\n"


def _safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "-", title).strip(" -.")
    return name or "untitled"


def write_to_obsidian(title: str, markdown: str) -> Path:
    """Write (or overwrite) the note in the configured vault folder."""
    vault = Path(settings.obsidian_dir).expanduser()
    vault.mkdir(parents=True, exist_ok=True)
    path = vault / f"{_safe_name(title)}.md"
    path.write_text(markdown)
    return path


def push_to_tablet(pdf_bytes: bytes, visible_name: str) -> str:
    """Copy the PDF onto the tablet as a new xochitl document. Returns the doc uuid."""
    doc_id = str(uuid.uuid4())
    now_ms = str(int(time.time() * 1000))
    metadata = {
        "visibleName": visible_name,
        "type": "DocumentType",
        "parent": "",
        "lastModified": now_ms,
        "lastOpened": "0",
        "lastOpenedPage": 0,
        "version": 0,
        "pinned": False,
        "deleted": False,
        "modified": False,
        "metadatamodified": False,
        "synced": False,
    }
    content = {"fileType": "pdf", "coverPageNumber": 0}

    ssh_parts = ssh_command().split()
    remote = f"{settings.rm_user}@{settings.rm_host}"
    with tempfile.TemporaryDirectory(prefix="remarque-export-") as tmpdir:
        tmp = Path(tmpdir)
        (tmp / f"{doc_id}.pdf").write_bytes(pdf_bytes)
        (tmp / f"{doc_id}.metadata").write_text(json.dumps(metadata))
        (tmp / f"{doc_id}.content").write_text(json.dumps(content))
        scp = ["scp", *ssh_parts[1:]] + [
            str(tmp / f"{doc_id}.pdf"),
            str(tmp / f"{doc_id}.metadata"),
            str(tmp / f"{doc_id}.content"),
            f"{remote}:{settings.xochitl_remote_dir}/",
        ]
        proc = subprocess.run(scp, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"scp failed: {proc.stderr.strip()[:300]}")
    return doc_id
