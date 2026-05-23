# CLAUDE.md
## Synapse — Claude Code Instructions

---

## What You Are Building

Synapse is a personal cognitive operating system. It is not a CRUD app. It is a system where every component has a reason for existing and a defined relationship to every other component. Read `SYNAPSE_PRD.md` in full before writing any code.

You are building this for one user: a CS student and startup founder who will use this system daily. Correctness and reliability matter more than feature velocity. A system that loses a capture or corrupts a graph node is worse than a slower build.

---

## Non-Negotiable Rules

**1. Read the PRD before starting any milestone.**
The PRD defines the schema, the agent interfaces, the capture contracts, and the success gates. These are not suggestions. If you believe a PRD decision is wrong, flag it explicitly before proceeding — do not silently deviate.

**2. Never delete. Always archive.**
The Librarian and all ingestion pipelines must archive processed items, not delete them. The raw capture record is always recoverable. This rule has no exceptions.

**3. LLM outputs that modify the graph must be validated before write.**
Every Claude API call that returns data to be written to the knowledge graph must return structured JSON. Validate the JSON against the expected schema before any database write. If validation fails: log the raw output, skip the write, flag for user review. Never write unvalidated LLM output to the graph.

**4. Prompts are files, not strings.**
All agent prompts live in `synapse/prompts/*.md`. They are loaded at runtime, not hardcoded. Changes to prompts do not require code changes. This is by design — the user will iterate on prompts without your involvement.

**5. Credentials are never logged.**
The `synapse/gateway/auth.py` module handles all credentials. No other module ever sees a raw token. If a module needs to make an authenticated API call, it calls the gateway's integration proxy (`/integrations/gmail/messages`, etc.) — never directly with a stored credential.

**6. One milestone at a time.**
Complete the current milestone's success gate before moving to the next. Do not partially implement Milestone 2 while Milestone 1's success gate is unmet. If blocked, say so explicitly.

---

## Architecture Decisions (Already Made — Do Not Revisit Without Flag)

These decisions are final unless the user explicitly reopens them:

- **FastAPI** for the gateway (not Flask, not Django)
- **SQLite via SQLModel** as the primary database (not Postgres, not MongoDB)
- **ChromaDB** for vector storage (not Pinecone, not Weaviate)
- **NetworkX** for in-memory graph operations (not Neo4j)
- **APScheduler** for job scheduling (not Celery, not Redis Queue)
- **`cryptography.fernet`** for credential encryption
- **`python-telegram-bot`** for the Telegram bot
- **`typer`** for the CLI
- **`uv`** as the package manager (not pip, not poetry)
- **`claude-sonnet-4-20250514`** for all agent reasoning
- **SM-2 algorithm** for spaced repetition (not FSRS, not Leitner)

If you believe one of these is genuinely wrong for a specific use case, flag it with reasoning before the relevant milestone. Do not swap silently.

---

## Code Style

**Python:**
- Type hints everywhere. No untyped functions.
- Async functions for all I/O (FastAPI routes, database queries, API calls)
- Pydantic models for all request/response schemas
- SQLModel for all database models
- `loguru` for logging (not Python's built-in `logging`)
- Docstrings on all public functions: one-line summary + params + returns
- Error handling: specific exceptions, never bare `except:`
- Constants in `synapse/config.py` loaded from environment via `pydantic-settings`

**File naming:** snake_case for Python files. kebab-case for markdown files.

**Imports:** Standard library → third-party → internal. Grouped with blank lines between.

**No magic numbers.** All thresholds, intervals, and limits are named constants in `synapse/config.py`.

---

## Agent Prompt Engineering Guidelines

When writing or updating agent prompts in `synapse/prompts/`:

**Structure every prompt with:**
1. Role definition (what the agent is)
2. Input description (what it receives)
3. Output format specification (exact JSON schema or markdown structure)
4. Constraints (what it must never do)
5. Examples (at least one good example, one edge case)

**For prompts that output JSON:**
- Specify the exact schema in the prompt
- Include: "Return only valid JSON. No preamble. No markdown code fences. No explanation."
- Always include a `confidence` field (0.0–1.0) in the output so the gateway can gate low-confidence writes

**For the Librarian specifically:**
- Include the current list of existing node titles and types in the prompt context (prevents duplicate concept creation)
- The instruction "never create an INSIGHT node without user confirmation" must appear verbatim in the prompt

**For the Critic specifically:**
- The instruction "identify exactly one 'most important fix' — not two, not a list" must appear verbatim
- Include: "If you cannot identify a single most important fix, that means the output is good. Say so."

---

## Testing Requirements

Every module must have a corresponding test file in `tests/`. Minimum coverage requirements:

| Module | Required Tests |
|---|---|
| `graph/models.py` | Node creation, edge creation, duplicate prevention |
| `graph/operations.py` | CRUD operations, graph traversal correctness |
| `graph/retention.py` | SM-2 interval calculation, ease factor updates |
| `capture/telegram_bot.py` | Message routing, voice queuing, error handling |
| `gateway/auth.py` | Credential encryption/decryption, token refresh |
| `agents/librarian.py` | JSON validation, archive behavior, no-delete guarantee |

Use `pytest` with `pytest-asyncio` for async tests. Use `httpx.AsyncClient` for FastAPI endpoint tests.

Run tests before declaring any milestone complete: `uv run pytest tests/ -v`

---

## Database Migrations

Use `alembic` for schema migrations. Every schema change requires a migration file. Never modify the SQLite database directly in production code — always through SQLModel + alembic.

Migration naming: `YYYYMMDD_HHMM_short_description.py`

---

## How to Work with the Obsidian Vault

The vault is a mirror of the graph, not the source of truth. Rules:

- The gateway writes to the vault via the Obsidian Local REST API plugin (port 27123)
- Never write vault files directly from agent code — always via the vault service in `synapse/graph/vault_sync.py`
- If the vault is unavailable (Obsidian not running), queue the sync and retry — do not fail the operation
- Vault file frontmatter must stay in sync with the SQLite node record. The `id` field is the link between them.

---

## Handling Claude API Calls

All Claude API calls go through `synapse/llm/client.py`. This module:
- Handles retries (3 attempts, exponential backoff)
- Logs token usage per call to the `api_usage` table
- Validates structured outputs before returning
- Never exposes the raw API key to calling code

Example call pattern:
```python
from synapse.llm.client import claude

result = await claude.structured(
    prompt_file="librarian.md",
    context={"inbox_items": items, "existing_nodes": node_list},
    schema=LibrarianOutput,  # Pydantic model
    temperature=0.3
)
```

---

## Milestone Completion Checklist

Before declaring a milestone complete:

- [ ] All deliverables in the PRD checklist are implemented
- [ ] The success gate scenario has been manually tested and passed
- [ ] All new modules have corresponding tests
- [ ] `uv run pytest tests/ -v` passes with zero failures
- [ ] No hardcoded credentials, paths, or magic numbers in new code
- [ ] `synapse/prompts/` files updated if any agent prompt changed
- [ ] `.env.example` updated if any new environment variables added

---

## When You Are Uncertain

If the PRD is ambiguous on a specific behavior: implement the most conservative interpretation (the one that preserves data integrity and user control) and flag the ambiguity in a comment with `# CLARIFY: [question]`. Do not make silent assumptions about behavior that affects data.

If a dependency is unavailable or has changed its API: do not work around it silently. Flag it. The user may want to make a different dependency choice.

If a milestone's success gate reveals a flaw in an earlier milestone: stop, fix the earlier milestone, re-run its success gate, then continue. Foundations before features.

---

*CLAUDE.md Version 1.0 — Synapse Project*
*Keep this file updated as architecture evolves.*
