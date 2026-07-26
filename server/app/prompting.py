"""Prompt assembly for the two-stage flow: cheap transcription, session-based answers."""

TRANSCRIBE_SYSTEM = """The image shows pen strokes handwritten on a tablet.
The writing is usually a short question or note in English, in print or cursive, typically about a document the person is reading; expect real words and question phrasing.
Transcribe the characters. When a letter is ambiguous, prefer the reading that forms a real word.
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
    margin_note: str | None = None,
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
    if margin_note:
        parts.append(
            "Notes the user handwrote on the current page (transcribed):\n" + margin_note
        )
    if extra_text:
        parts.append(f"Additional document text:\n<document>\n{extra_text}\n</document>")
    parts.append(f"Question: {question}")
    if brief:
        parts.append("(Answer briefly: 3 to 5 sentences, only the essentials.)")
    return "\n\n".join(parts)


QUIZ_QUESTION = (
    "Quiz me: ask me exactly one exam-style question that tests real understanding "
    "of this document, preferring material I have not been quizzed on yet in this "
    "conversation. Output only the question itself, nothing else."
)


def build_quiz_question(weak_spots: list[str]) -> str:
    """The quiz-start prompt, steering toward previously missed material."""
    if not weak_spots:
        return QUIZ_QUESTION
    lines = ["I previously answered these quiz questions wrong or only partially:"]
    lines += [f"- {q}" for q in weak_spots]
    lines.append(
        "Prefer re-testing one of these areas, rephrased or from a different angle "
        "(never repeat a question verbatim). If they all seem mastered by now, pick "
        "new material instead."
    )
    return QUIZ_QUESTION + "\n\n" + "\n".join(lines)


def build_quiz_grade(question: str, answer: str) -> str:
    return (
        f'You asked me this quiz question: "{question}"\n'
        f'My handwritten answer (transcribed, may have small transcription errors): "{answer}"\n'
        "Grade my answer: start with exactly one of Correct / Partially correct / Incorrect, "
        "then briefly explain what is right or missing and give the full correct answer."
    )


COMPACT_SUMMARY = (
    "Summarize everything important from our conversation about this document so "
    "far: the questions I asked, the key explanations you gave, misconceptions "
    "you corrected, and how I did on quiz questions. Be dense and factual; this "
    "summary will seed a fresh session that replaces this one. Output only the "
    "summary."
)


def build_compact_seed(seed: str, summary: str) -> str:
    return (
        f"{seed}\n\n"
        "Summary of our earlier conversation about this document (the full "
        f"transcript was compacted):\n{summary}\n\n"
        "No question yet. Reply with just: Ready."
    )


def build_stateless_history(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = ["Previous conversation:"]
    for t in turns:
        lines.append(f'{t["role"].capitalize()}: {t["content"]}')
    return "\n".join(lines)
