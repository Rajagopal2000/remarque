"""Anki deck generation: LLM-made cards from the document + deterministic cards
from the chat history, packaged as .apkg and optionally pushed via AnkiConnect
(which then syncs to AnkiWeb - there is no official AnkiWeb API).
"""

import hashlib
import json
import re
import sqlite3
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import genanki

from .config import settings

_lock = threading.Lock()


@dataclass
class DeckState:
    cards: list[dict]
    last_history_ts: float
    highlight_texts: list[str]
    doc_text_hash: str
    # None marks a deck saved before margin tracking existed: those notes are
    # treated as already covered, not as new material to generate cards for.
    margin_texts: list[str] | None = field(default_factory=list)


class AnkiStateStore:
    """Tracks, per document, what a generated deck already covers."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS anki_decks ("
                "doc_id TEXT PRIMARY KEY, updated_at REAL NOT NULL,"
                "last_history_ts REAL NOT NULL, highlight_texts TEXT NOT NULL,"
                "doc_text_hash TEXT NOT NULL, cards TEXT NOT NULL)"
            )
            cols = [row[1] for row in conn.execute("PRAGMA table_info(anki_decks)")]
            if "margin_texts" not in cols:
                # Nullable on purpose: existing rows get NULL ("unknown"), not
                # an empty list that would count every old note as new.
                conn.execute("ALTER TABLE anki_decks ADD COLUMN margin_texts TEXT")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, doc_id: str) -> DeckState | None:
        with _lock, self._connect() as conn:
            row = conn.execute(
                "SELECT cards, last_history_ts, highlight_texts, doc_text_hash, margin_texts "
                "FROM anki_decks WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            return None
        return DeckState(
            json.loads(row[0]),
            row[1],
            json.loads(row[2]),
            row[3],
            json.loads(row[4]) if row[4] is not None else None,
        )

    def save(self, doc_id: str, state: DeckState) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_decks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    time.time(),
                    state.last_history_ts,
                    json.dumps(state.highlight_texts),
                    state.doc_text_hash,
                    json.dumps(state.cards),
                    json.dumps(state.margin_texts),
                ),
            )

    def doc_ids(self) -> list[str]:
        with _lock, self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT doc_id FROM anki_decks")]

    def updated_since(self, ts: float) -> list[dict]:
        """Decks touched from ts onward, with their total size (for the digest)."""
        with _lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, cards FROM anki_decks WHERE updated_at >= ?", (ts,)
            ).fetchall()
        return [{"doc_id": d, "n_cards": len(json.loads(cards))} for d, cards in rows]

    def clear(self, doc_id: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute("DELETE FROM anki_decks WHERE doc_id = ?", (doc_id,))


def text_hash(text: str | None) -> str:
    return hashlib.md5((text or "").encode()).hexdigest()


def card_front(card: dict) -> str:
    return card.get("front") or card.get("text") or ""

CARD_SYSTEM = """You create Anki flashcards from a document.
Output ONLY a JSON array, no markdown fences, no commentary.
Each element is one card, one of:
  {"type": "basic", "front": "...", "back": "..."}         conceptual questions ("Why/How...")
  {"type": "reversed", "front": "...", "back": "..."}      terms and definitions (asked both ways)
  {"type": "cloze", "text": "... {{c1::hidden part}} ..."} concrete facts, numbers, formulas, named results

Rules:
- Cover the important content of the whole document evenly; skip boilerplate and references.
- Aim for 25 to 60 cards depending on how much substantive content there is.
- Mix the three types according to what fits each piece of knowledge.
- Cloze cards must contain at least one {{c1::...}} marker; use {{c2::...}} for a second blank in the same card.
- Fronts must be answerable without seeing the document; include enough context.
- Plain text only inside fields.

If a conversation the user had about the document is provided, treat it as the
highest-priority source: those questions mark what the user personally found
unclear. Distill each exchange into atomic, self-contained cards (an exchange
may yield zero, one, or several cards). Rewrite conversational questions to
stand alone; never copy long answers verbatim. Skip exchanges with no durable
knowledge (greetings, tests, meta-questions about the assistant). If an
exchange covers the same fact as a document card, make the card once.
"""

MAX_CHAT_CHARS = 30000


def _chat_section(turns: list[dict]) -> str:
    lines = []
    for turn in turns:
        prefix = "User asked" if turn["role"] == "user" else "Assistant answered"
        lines.append(f"{prefix}: {turn['content']}")
    text = "\n\n".join(lines)
    return text[-MAX_CHAT_CHARS:]


def _clean_json_array(text: str) -> list[dict]:
    text = re.sub(r"```(json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in model output")
    cards = json.loads(text[start : end + 1])
    if not isinstance(cards, list):
        raise ValueError("model output is not a list")
    return cards


MAX_EXISTING_FRONTS = 400


def generate_cards(
    provider,
    title: str,
    doc_text: str | None,
    turns: list[dict],
    highlights: list[str] | None = None,
    margin_notes: list[str] | None = None,
    existing_fronts: list[str] | None = None,
    update_only: bool = False,
) -> list[dict]:
    parts = [f"Create Anki cards.\n\nTitle: {title}"]
    if doc_text:
        parts.append(f"<document>\n{doc_text}\n</document>")
    if highlights:
        parts.append(
            "Passages the user highlighted (important to them; make sure these are covered):\n"
            + "\n".join(f"- {h}" for h in highlights)
        )
    if margin_notes:
        parts.append(
            "Notes the user handwrote in the margins (transcribed; they mark what "
            "mattered to the user, make sure it is covered):\n"
            + "\n".join(f"- {m}" for m in margin_notes)
        )
    if turns:
        parts.append(
            "The user's conversation about this document:\n"
            f"<conversation>\n{_chat_section(turns)}\n</conversation>"
        )
    if existing_fronts:
        fronts = existing_fronts[:MAX_EXISTING_FRONTS]
        parts.append(
            "Cards that ALREADY EXIST for this document (do not recreate these or near-duplicates):\n"
            + "\n".join(f"- {f[:120]}" for f in fronts)
        )
    if update_only:
        parts.append(
            "This is an UPDATE run: create cards ONLY for the new material shown above "
            "that the existing cards do not cover. If nothing card-worthy is new, "
            "output an empty JSON array []."
        )
    prompt = "\n\n".join(parts)
    chunks = [
        str(payload)
        for kind, payload in provider.answer_events(prompt, CARD_SYSTEM, None)
        if kind == "text"
    ]
    return validate_cards(_clean_json_array("".join(chunks)))


def validate_cards(cards: list[dict]) -> list[dict]:
    valid = []
    for card in cards:
        ctype = card.get("type")
        if ctype == "cloze" and "{{c" in card.get("text", ""):
            valid.append({"type": "cloze", "text": card["text"]})
        elif ctype in ("basic", "reversed") and card.get("front") and card.get("back"):
            valid.append({"type": ctype, "front": card["front"], "back": card["back"]})
    return valid


def _field(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def build_apkg(title: str, cards: list[dict]) -> bytes:
    deck_id = int(hashlib.md5(title.encode()).hexdigest()[:8], 16) + (1 << 30)
    deck = genanki.Deck(deck_id, f"reMarkable::{title}")
    for card in cards:
        if card["type"] == "cloze":
            # cloze markers must survive; escape everything else around them
            deck.add_note(genanki.Note(model=genanki.CLOZE_MODEL, fields=[card["text"], ""]))
        else:
            model = (
                genanki.BASIC_AND_REVERSED_CARD_MODEL
                if card["type"] == "reversed"
                else genanki.BASIC_MODEL
            )
            deck.add_note(
                genanki.Note(model=model, fields=[_field(card["front"]), _field(card["back"])])
            )
    with tempfile.NamedTemporaryFile(suffix=".apkg") as tmp:
        genanki.Package(deck).write_to_file(tmp.name)
        return Path(tmp.name).read_bytes()


ANKI_MODEL_NAMES = {"basic": "Basic", "reversed": "Basic (and reversed card)", "cloze": "Cloze"}


def _anki_connect(action: str, **params) -> object:
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        settings.anki_connect_url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {result['error']}")
    return result.get("result")


def push_via_ankiconnect(title: str, cards: list[dict]) -> int:
    """Add notes to the running Anki instance and sync them to AnkiWeb.

    Sync down first so we add on top of the latest collection state (reduces
    conflicts with the phone/desktop), then add, then sync up.
    """
    deck_name = f"reMarkable::{title}"
    _anki_connect("sync")
    _anki_connect("createDeck", deck=deck_name)
    notes = []
    for card in cards:
        if card["type"] == "cloze":
            fields = {"Text": card["text"], "Back Extra": ""}
        else:
            fields = {"Front": _field(card["front"]), "Back": _field(card["back"])}
        notes.append(
            {
                "deckName": deck_name,
                "modelName": ANKI_MODEL_NAMES[card["type"]],
                "fields": fields,
                "tags": ["remarque"],
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            }
        )
    results = _anki_connect("addNotes", notes=notes)
    added = sum(1 for r in results if r)
    _anki_connect("sync")
    return added
