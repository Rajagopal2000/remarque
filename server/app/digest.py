"""Deterministic reading digest: what was read, asked, quizzed, and noted.

No LLM involved; everything comes straight from the stores, so the digest is
free, reproducible, and testable. Rendered as an Obsidian-ready markdown note.
"""

from datetime import datetime

VERDICT_LABELS = {"correct": "correct", "partial": "partially correct", "incorrect": "incorrect"}


def build_digest_markdown(
    days: float,
    now: float,
    docs_opened: list[str],
    turns: list[dict],
    quiz_results: list[dict],
    margin_notes: list[dict],
    anki_updates: list[dict],
    titles: dict[str, str],
) -> str:
    """turns/quiz_results/margin_notes/anki_updates carry doc_id; titles maps them."""

    def title(doc_id: str) -> str:
        return titles.get(doc_id, "General questions")

    end = datetime.fromtimestamp(now)
    start = datetime.fromtimestamp(now - days * 86400)
    lines = [
        "---",
        f'title: "Remarque digest {end.strftime("%Y-%m-%d")}"',
        f"date: {end.strftime('%Y-%m-%d')}",
        "tags: [remarque, digest]",
        "---",
        "",
        f"# Reading digest: {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}",
    ]

    lines += ["", "## Documents opened", ""]
    if docs_opened:
        lines += [f"- [[{t}]]" for t in docs_opened]
    else:
        lines.append("- none")

    questions = [t for t in turns if t["role"] == "user"]
    lines += ["", f"## Questions asked ({len(questions)})"]
    by_doc: dict[str, list[dict]] = {}
    for q in questions:
        by_doc.setdefault(q["doc_id"], []).append(q)
    for doc_id, doc_questions in by_doc.items():
        lines += ["", f"### {title(doc_id)}", ""]
        for q in doc_questions:
            page = f" (p. {q['page']})" if q.get("page") else ""
            lines.append(f"- {q['content']}{page}")

    lines += ["", "## Quiz performance", ""]
    if quiz_results:
        counts = {v: sum(1 for r in quiz_results if r["verdict"] == v) for v in VERDICT_LABELS}
        summary = ", ".join(f"{n} {VERDICT_LABELS[v]}" for v, n in counts.items() if n)
        lines.append(f"{len(quiz_results)} answered: {summary or 'no graded answers'}.")
        missed = [r for r in quiz_results if r["verdict"] in ("incorrect", "partial")]
        if missed:
            lines += ["", "Missed:", ""]
            lines += [
                f"- [{title(r['doc_id'])}] {r['question']} ({VERDICT_LABELS[r['verdict']]})"
                for r in missed
            ]
    else:
        lines.append("No quizzes taken.")

    if margin_notes:
        lines += ["", "## Margin notes added", ""]
        lines += [f"- [{title(n['doc_id'])}] {n['text']}" for n in margin_notes]

    if anki_updates:
        lines += ["", "## Anki decks updated", ""]
        lines += [f"- {title(u['doc_id'])}: {u['n_cards']} cards total" for u in anki_updates]

    return "\n".join(lines) + "\n"
