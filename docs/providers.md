# Providers, sessions, and transcription

## Choosing a provider

Set `PROVIDER` in `server/.env`:

- `claude-code` (default) - headless Claude Code under your claude.ai subscription; no API key as long as Claude Code is installed and logged in.
- `codex` - same idea with the codex CLI and a ChatGPT subscription.
- `gemini` - experimental, stateless.
- `claude` - Anthropic API with `ANTHROPIC_API_KEY`.
- `openai` - OpenAI API; with `OPENAI_BASE_URL` this also covers gateways like LiteLLM or OpenRouter.

## Two-stage asks

Every ask runs in two stages: one model transcribes the handwriting (sonnet by default; haiku or a local Ollama vision model are cheaper options), and the answer model responds.
Transcription and answering are independent providers (`TRANSCRIBE_PROVIDER`), so a cheap or local model can transcribe while a strong model answers.

## Persistent sessions

For `claude-code` and `codex`, answers run inside a persistent per-document session: the full document text is sent once when the session starts, follow-up questions send only the question plus page and highlight hints, and resumed turns are served mostly from prompt cache (which counts far less against subscription limits).
Sessions expire after `SESSION_TTL_DAYS` (730, i.e. 2 years) idle days, and a matching `cleanupPeriodDays` is passed to Claude Code so it does not prune session transcripts earlier.
The panel's warm-up, "New session", and "Compact" behaviors are described in [features.md](features.md#sessions).

## Local transcription (no cloud call for handwriting)

No code changes needed; any OpenAI-compatible server works (e.g. Ollama in the cluster):

```bash
TRANSCRIBE_PROVIDER=openai
OPENAI_BASE_URL=http://ollama:11434/v1
OPENAI_API_KEY=ollama
OPENAI_TRANSCRIBE_MODEL=qwen2.5vl:7b
```
