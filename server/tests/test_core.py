import json
import time
import uuid

import pymupdf
import pytest

from app import documents, pdftext
from app.highlights import extract_highlights
from app.inkrender import render_strokes
from app.jobs import JobStore


@pytest.fixture
def sync_dir(tmp_path):
    """Synthetic xochitl store: one PDF document with a redirection map."""
    doc_id = str(uuid.uuid4())
    pdf = pymupdf.open()
    for text in ["Alpha page about thermodynamics.", "Beta page about entropy."]:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    pdf.save(tmp_path / f"{doc_id}.pdf")

    page_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
    (tmp_path / f"{doc_id}.metadata").write_text(
        json.dumps(
            {
                "type": "DocumentType",
                "visibleName": "Test Paper",
                "lastOpened": "1700000000000",
                "lastOpenedPage": 2,
            }
        )
    )
    # Notebook page 1 is an inserted blank page (no redir); pages 0 and 2 map to PDF 0 and 1.
    (tmp_path / f"{doc_id}.content").write_text(
        json.dumps(
            {
                "pageCount": 3,
                "fileType": "pdf",
                "cPages": {
                    "pages": [
                        {"id": page_ids[0], "redir": {"value": 0}},
                        {"id": page_ids[1]},
                        {"id": page_ids[2], "redir": {"value": 1}},
                    ]
                },
            }
        )
    )
    # An older document that must not win current_document.
    other_id = str(uuid.uuid4())
    (tmp_path / f"{other_id}.metadata").write_text(
        json.dumps({"type": "DocumentType", "visibleName": "Old", "lastOpened": "1600000000000"})
    )
    (tmp_path / f"{other_id}.content").write_text("{}")
    # A garbage .rm file: highlight extraction must not crash.
    page_dir = tmp_path / doc_id
    page_dir.mkdir()
    (page_dir / f"{page_ids[0]}.rm").write_bytes(b"not a real rm file")
    return tmp_path, doc_id


def test_current_document(sync_dir):
    path, doc_id = sync_dir
    doc = documents.current_document(path)
    assert doc is not None
    assert doc.doc_id == doc_id
    assert doc.title == "Test Paper"
    assert doc.page_index == 2
    assert doc.pdf_path is not None


def test_pdf_page_redirection(sync_dir):
    path, doc_id = sync_dir
    doc = documents.get_document(path, doc_id)
    assert documents.pdf_page_index(doc.content, 0) == 0
    assert documents.pdf_page_index(doc.content, 1) == -1  # inserted page
    assert documents.pdf_page_index(doc.content, 2) == 1


def test_page_window_text(sync_dir):
    path, doc_id = sync_dir
    doc = documents.get_document(path, doc_id)
    pdf_page = documents.pdf_page_index(doc.content, doc.page_index)
    text = pdftext.page_window(doc.pdf_path, pdf_page, radius=0)
    assert "entropy" in text
    assert "(current page)" in text


def test_full_text_truncation(sync_dir):
    path, doc_id = sync_dir
    doc = documents.get_document(path, doc_id)
    text = pdftext.full_text(doc.pdf_path, max_chars=10)
    assert "[document truncated]" in text


def test_highlights_error_isolation(sync_dir):
    path, doc_id = sync_dir
    doc = documents.get_document(path, doc_id)
    assert extract_highlights(path, doc_id, doc.content) == []


def test_render_strokes_produces_png():
    png = render_strokes([[[10, 10], [50, 60], [90, 20]], [[100, 100]]], 200, 150)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_smooth_interpolates_and_preserves_endpoints():
    from app.inkrender import _smooth

    pts = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)]
    out = _smooth(pts)
    assert out[0] == pts[0] and out[-1] == pts[-1]
    assert len(out) > len(pts)
    # Short strokes pass through untouched.
    assert _smooth(pts[:2]) == pts[:2]
    assert _smooth(pts[:1]) == pts[:1]


def test_render_strokes_rejects_empty_ink():
    with pytest.raises(ValueError):
        render_strokes([[]], 200, 150)


def test_job_worker_lifecycle():
    store = JobStore()

    def work(job):
        job.question_read = "what is entropy?"
        job.phase = "answering"
        job.append("Entropy is a measure of disorder.")
        job.usage = {"input_tokens": 10}

    job_id = store.start(work)
    for _ in range(100):
        snap = store.get(job_id).snapshot(0)
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done"
    assert snap["question_read"] == "what is entropy?"
    assert "disorder" in snap["text_so_far"]
    assert snap["usage"] == {"input_tokens": 10}


def test_job_worker_error():
    store = JobStore()

    def work(job):
        raise RuntimeError("boom")

    job_id = store.start(work)
    for _ in range(100):
        snap = store.get(job_id).snapshot(0)
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "error"
    assert "boom" in snap["error"]
