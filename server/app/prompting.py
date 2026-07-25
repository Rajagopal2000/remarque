"""Prompt assembly for the two-stage flow: cheap transcription, session-based answers."""

TRANSCRIBE_SYSTEM = """The image shows pen strokes handwritten on a tablet.
Transcribe the characters.
You must always output your single best guess of the text and nothing else: no commentary, no quotes, no preamble.
Never refuse, never describe the image, never say it is illegible or blank.
A wrong guess is better than no guess.
"""

ANSWER_SYSTEM = """You are a reading assistant integrated into a reMarkable e-ink tablet.
The user is reading a document (usually an academic paper) and handwrites questions with a pen; the questions are transcribed to text before they reach you.
Questions may be about the document, about passages the user highlighted, or general questions on any topic. Use the document context when relevant; answer from your own knowledge otherwise.
The answer is displayed on a small e-ink panel. Be clear and complete but economical: prefer a few short paragraphs. Use plain text only: no markdown, no LaTeX, no tables. Use simple dashes for lists.
"""


def build_seed(title: str | None, full_text: str | None) -> str:
    """First message of a per-document session: document context sent once."""
    parts = ["This session is a conversation about one document the user is reading."]
    if title:
        parts.append(f"Document title: {title}")
    if full_text:
        parts.append(
            "Full document text below (page markers included; may be truncated). "
            "Refer back to it throughout this session.\n"
            f"<document>\n{full_text}\n</document>"
        )
    return "\n\n".join(parts)


def build_ask(
    question: str,
    page_number: int | None,
    highlights: list[dict] | None,
    extra_text: str | None,
    brief: bool = False,
) -> str:
    parts = []
    if page_number is not None:
        parts.append(f"(The user is currently on page {page_number} of the document.)")
    if highlights:
        lines = ["Text the user highlighted in the document:"]
        for h in highlights:
            for t in h["texts"]:
                lines.append(f'- (page {h["page_index"] + 1}) "{t}"')
        parts.append("\n".join(lines))
    if extra_text:
        parts.append(f"Additional document text:\n<document>\n{extra_text}\n</document>")
    parts.append(f"Question: {question}")
    if brief:
        parts.append("(Answer briefly: 3 to 5 sentences, only the essentials.)")
    return "\n\n".join(parts)


def build_stateless_history(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = ["Previous conversation:"]
    for t in turns:
        lines.append(f'{t["role"].capitalize()}: {t["content"]}')
    return "\n".join(lines)
