# Panel features

What the tablet panel can do, and the server behavior behind each feature.

## Asking

Every ask runs in two stages: a cheap model transcribes the handwriting, then the answer model responds inside a persistent per-document session (see [providers.md](providers.md)).
Answers stream back to the panel as paginated text.

- Ask becomes Stop while busy: cancelling stops the job server-side and the exchange is not recorded.
- The scratchpad auto-clears once transcription succeeds, ready for the follow-up; Undo removes the last stroke.
- A Retry button appears after failures and re-submits the same strokes or action.
- Re-read button: if the cheap model misread your handwriting, one tap re-submits the same strokes to the answer model for transcription.
- "Brief: on" answers in 3-5 sentences; A-/A+ on the answer view adjust the font (repagination is automatic).
- "HL: on" includes the text of your smart highlights as context for the question.
- "Attach" cycles extra per-ask context: none, current page text, full document text, or a rendered image of the current page ("image" sends the page to the vision model, for questions about figures and equations).
- The status line shows elapsed seconds while thinking, per-ask token usage, and a warning when the answer used stale synced data.

## Page ask (native ink)

Write the question directly on the PDF page with the pen, circle it, flip the page and back (the tablet only saves ink on page events), then tap Page ask.
The server reads the fresh page ink and transcribes the newest un-asked circle as the question; each ask consumes its circle, so several circled questions are answered one tap each, in drawing order.
A circle that encloses nothing is ignored, and with no circle at all, every stroke written since the last page ask becomes the question.
This exists because the panel scratchpad cannot match native inking latency, and the selection tool's state never reaches disk.

## Quick actions

One tap, no handwriting, no transcription call: summarize page, summarize document, explain highlights, define the latest highlight.

## Margin notes

Handwritten pen notes on the current document page are transcribed (cached by ink hash, so unchanged pages cost nothing) and included as context for every ask, and as a priority source for Anki cards.

## Search

Handwrite a query and tap Search: the server transcribes it and full-text searches (sqlite FTS5) both your past Q&A and the text of every synced document, showing ranked snippets with page numbers.

## Quiz

The Quiz button asks you one exam-style question about the document; handwrite your answer and tap Answer (the Ask button while a quiz is pending) to get it graded and explained.
Quiz exchanges land in the history, so they feed later Anki decks.
Grades are tracked per question (Correct / Partially correct / Incorrect), and the next quiz steers toward questions you last got wrong, rephrased, until they are mastered.

## History and export

- History records the page you were on with each question; the History view and exports show "(p. N)" markers.
- History button: paginated view of all past Q&A for the document.
- Export button: generates a "Notes - <title>" PDF of the Q&A history and pushes it onto the tablet (appears after a device restart).
- With `OBSIDIAN_DIR` set, the same export also writes a markdown note (highlights + Q&A, YAML frontmatter) into your Obsidian vault; `GET /api/export/<doc_id>.md` downloads it anywhere.

## Reading digest

`POST /api/digest?days=7` builds a markdown summary of the period with no LLM call: documents opened, questions asked with pages, quiz performance with missed questions, margin notes, and Anki deck updates.
With `DIGEST_EVERY_DAYS` set, the digest lands in the Obsidian vault on that schedule automatically.

## Anki decks

The Anki button generates a spaced-repetition deck for the whole document with mixed card types: basic for concepts, basic-and-reversed for definitions, cloze for facts and formulas.

- The chat history, highlights, and margin notes feed the same generation pass as priority sources: exchanges are distilled into atomic self-contained cards (conversational questions rewritten to stand alone, junk exchanges skipped, overlap with document cards deduplicated).
- Decks are incremental: the server tracks what each deck already covers (chat timestamps, highlight set, margin notes, document text hash), and pressing Anki again adds cards only for new material; unchanged documents short-circuit without an LLM call.
- The `.apkg` always contains the full merged deck with stable note ids (safe to re-import); download at `/api/anki/<doc_id>.apkg`.
- `POST /api/anki/<doc_id>?mode=full` discards the tracking state and regenerates from scratch.
- With `ANKI_CONNECT_URL` set, new notes are also pushed via AnkiConnect and synced to AnkiWeb (AnkiWeb has no official API; AnkiConnect is the sanctioned bridge).
- With `ANKI_AUTO_HOURS` set (the Kubernetes chart defaults to 24), decks you created once are refreshed incrementally on that schedule, so new reading and conversations become cards without pressing the button; unchanged documents skip the LLM entirely.
- In the cluster the AnkiConnect path is automatic via a sidecar Anki service; see [deployment.md](deployment.md#kubernetes).

## Sessions

The tablet panel shows the current session (turns and age) and has a "New session" button to clear the context.
Opening the panel warms the session in the background: the document seed is uploaded while you are still writing, so the first question of a new document answers as fast as a follow-up.
When a long-lived session grows old, the "Compact" button summarizes it, clears it, and reseeds a fresh session with the document plus that summary, so the accumulated understanding survives without the ever-growing transcript.
Session lifetime and caching details: [providers.md](providers.md).

## Operational

- Document syncs are throttled (`SYNC_MAX_AGE`, default 60s) so rsync stays out of the ask path; `/api/refresh` always syncs.
  A failed sync degrades to cached data and the panel says so.
- `/metrics` exposes Prometheus metrics (asks, per-phase latency, tokens by provider, sync failures); the pod carries scrape annotations.
- Auth: set `API_TOKEN` on the server and bake the same value into the app (`API_TOKEN=... ./scripts/deploy-app.sh device-app`); requests without the token get 401.
