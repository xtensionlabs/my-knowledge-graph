# Synapse

> Personal cognitive operating system. A five-layer second brain that captures, connects, and reasons about everything you learn, build, and decide — for one builder-student running across three contexts (BICS coursework, Xtension Labs startup, self-directed R&D).

[![tests](https://img.shields.io/badge/tests-214%20passed-brightgreen)](#tests)
[![license](https://img.shields.io/badge/license-proprietary-blue)](#license)
[![python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![next.js](https://img.shields.io/badge/next.js-16-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)

**Live deployment:** `https://synapse.xtensionlabs.com`

---

## What it does

Synapse turns every fragment of thought you capture — a Telegram message between lectures, a clipboard snippet mid-debug, a forwarded email, a git commit — into a connected, queryable, self-rewiring knowledge graph. Six purpose-built agents do the work of synthesis, retention, and strategic planning continuously in the background, so the user gets the value of compounding cognition without managing it.

The system is engineered around **one non-negotiable principle**: deliver value on day one, earn complexity over time. It is not a productivity tool — it is the cognitive substrate the user actually lives inside.

## The five layers

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CAPTURE        Telegram bot · clipboard daemon · browser extension      │
│                 email webhook · git post-commit hook · voice (Whisper)   │
├──────────────────────────────────────────────────────────────────────────┤
│  GRAPH          SQLite (source of truth) · ChromaDB (vector embeddings)  │
│                 NetworkX (in-memory ops) · SM-2 spaced repetition        │
│                 Hebbian edge dynamics · community detection (Louvain)    │
├──────────────────────────────────────────────────────────────────────────┤
│  CONTEXT        Foreground/Horizon/Background working memory             │
│                 Energy inference · Forgetting/freshness scoring          │
├──────────────────────────────────────────────────────────────────────────┤
│  AGENTS         Librarian (Sonnet) · Synthesizer (Opus) · Critic (Opus)  │
│                 Strategist (Opus) · Guardian (Haiku) · Scout (Sonnet)    │
├──────────────────────────────────────────────────────────────────────────┤
│  SURFACES       FastAPI gateway · Next.js dashboard · CLI · Telegram     │
│                 Obsidian vault sync · Chromium extension                 │
└──────────────────────────────────────────────────────────────────────────┘
```

Detailed rationale: [`SYNAPSE_PRD.md`](./SYNAPSE_PRD.md) (authoritative spec).

## Status

All six milestones shipped. Synapse runs 24/7 on a DigitalOcean Droplet behind a Cloudflare Tunnel, with nightly encrypted backups to DO Spaces.

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Capture pipeline + inbox + retry queue + clipboard daemon | ✅ |
| **M1** | Knowledge graph (SQLite + Chroma + NetworkX) + Librarian agent | ✅ |
| **M2** | Synthesizer + SM-2 retention + Horizon queue + APScheduler | ✅ |
| **M3** | Startup Mirror — git post-commit hook + `synapse.json` manifest + BUILD↔CONCEPT edges + INSIGHT confirmation flow | ✅ |
| **M4** | Strategist + Guardian + real Energy inference + Google Calendar OAuth + Hebbian edge dynamics + sleep consolidation + forgetting curves | ✅ |
| **M5** | Critic + Scout + Tesseract+Vision OCR + community detection + Next.js dashboard + Chromium extension | ✅ |
| **M6** | Alembic migrations + systemd deployment + Cloudflare Tunnel + nightly backups + degraded-state health checks | ✅ |

## Repository layout

```
my-knowledge-graph/
├── synapse/                  # The Python service — gateway + agents + graph + capture
│   ├── agents/               # 6 agents: librarian, synthesizer, strategist, guardian, critic, scout
│   │                         #   + consolidation pass + git ingest
│   ├── capture/              # Telegram, clipboard, email, browser, OCR, git hook
│   ├── context/              # Session state, energy, horizon
│   ├── gateway/              # FastAPI app + routes (ingest, dashboard, auth, agents, ...)
│   ├── graph/                # Models, ops, retention (SM-2), Hebbian, freshness, communities, search
│   ├── integrations/         # Google Calendar
│   ├── llm/                  # Claude client wrapper (retries, structured outputs, cost logging)
│   ├── prompts/              # Versioned agent prompts (markdown, Jinja2 templates)
│   ├── utils/                # Cross-cutting helpers (e.g., timezone)
│   ├── cli/                  # Typer CLI surface
│   ├── config.py             # Every constant + Settings (pydantic-settings)
│   └── scheduler.py          # APScheduler — 8 jobs (synthesizer, librarian, guardian, …)
│
├── dashboard/                # Next.js 16 + React 19 + Tailwind v4 — dark monospace UI
│   ├── app/                  # App Router pages: /, /graph, /communities, /agents + /api proxy
│   ├── components/           # Card, Sidebar, NodeBadge, InboxPanel, GraphCanvas (d3-force)
│   └── lib/                  # api.ts (server-only), types.ts, format.ts
│
├── extension/                # Chromium MV3 — right-click capture + popup + options
│   ├── background.js
│   ├── popup.{html,js,css}
│   ├── options.{html,js,css}
│   └── lib/api.js
│
├── alembic/                  # Database migrations (baseline + future deltas)
├── deploy/                   # M6 — systemd units, scripts, Cloudflare Tunnel config, install + migrate guides
├── tests/                    # pytest — 214 tests, ~60 s warm
├── SYNAPSE_PRD.md            # Authoritative product/architecture spec
├── CLAUDE.md                 # Build guidelines for AI-assisted development
└── pyproject.toml            # uv-managed dependencies
```

## Quick start (local dev)

For working on the code, not for daily use (that's the deployed instance).

```bash
# 1. Backend
uv sync
cp .env.example .env       # fill in ANTHROPIC_API_KEY, SYNAPSE_SECRET_KEY, etc.
uv run synapse init        # provision ./vault/ + SQLite + alembic migrations
uv run synapse start       # boot gateway + Telegram bot + scheduler
```

```bash
# 2. Dashboard (separate terminal)
cd dashboard
cp .env.local.example .env.local   # set SYNAPSE_API_KEY to match .env
npm install
npm run dev                         # http://localhost:3000
```

```bash
# 3. Browser extension (one-time)
# - chrome://extensions/ → Developer mode on → Load unpacked → select ./extension/
# - Open the extension's options, set Gateway URL + API key, click "Test connection"
```

## Daily use (production)

After M6 deployment, no commands needed. Synapse runs continuously on the VPS at `https://synapse.xtensionlabs.com`. Capture flows in from:

- **Telegram bot** — fastest mobile capture, supports voice (auto-transcribed via Whisper)
- **Browser extension** — right-click any selection / link / page → "Save to Synapse"
- **Email forwarding** — Cloudflare Email Routing → `POST /ingest/email` webhook
- **Git hook** — post-commit hook on tracked repos → BUILD node updated automatically
- **Clipboard daemon** — runs locally on each machine, dedups + filters credential-shaped content
- **Voice / OCR** — Whisper for audio, Tesseract+Claude-Vision for images
- **Manual** — `uv run synapse ingest "text…"` from any terminal

The dashboard surfaces the rest:

- **Overview** — graph counts, capture rate, agent costs, inbox panel with one-click Librarian trigger
- **Graph** — interactive d3-force layout, node color by type, edge weight by Hebbian strength, node opacity by freshness
- **Communities** — Louvain clusters with hub-concept identification
- **Agents** — per-agent 7-day cost rollup, latest run status

## Architecture decisions (locked)

These are decided. See [`CLAUDE.md`](./CLAUDE.md) for the full list and rationale.

- **FastAPI** for the gateway · **Typer** for CLI · **uv** as package manager
- **SQLModel** over raw SQLAlchemy · **alembic** for schema migrations
- **ChromaDB** for vectors (local, persistent) · **sentence-transformers/all-MiniLM-L6-v2** for embeddings (no API spend)
- **NetworkX** for in-memory graph ops (no Neo4j)
- **APScheduler** for cron + intervals · **`cryptography.fernet`** for credential encryption
- **SM-2** spaced repetition algorithm (not FSRS, not Leitner)
- **Loguru** for logging (never the stdlib `logging`)

### Model tiering

Per `model-tiers` memory and PRD §7:

| Agent | Model | Why |
|---|---|---|
| Librarian | `claude-sonnet-4-5` | High volume (2h sweeps). Throughput beats peak reasoning. |
| Scout | `claude-sonnet-4-5` | Weekly. Modest volume, modest depth. |
| Guardian | `claude-haiku-4-5` | Every 4h. ≤2-line nudges. Fast + cheap. |
| Synthesizer | `claude-opus-4-7` | Daily Delta Briefing — the artifact you actually read. Opus premium worth it. |
| Critic | `claude-opus-4-7` | Sharper-than-the-user judgment. Manual trigger. |
| Strategist | `claude-opus-4-7` | Weekly + collision-driven. Multi-source synthesis. |
| Vision OCR | `claude-sonnet-4-5` | Diagrams only; pure text OCR stays local (Tesseract, free). |

## Deployment

Full guide: [`deploy/INSTALL.md`](./deploy/INSTALL.md). End state in ~30 minutes:

- DigitalOcean Droplet ($12/mo, covered by GitHub Student Pack credit) in Bangalore (lowest Nairobi latency)
- systemd-managed `synapse-gateway` + `synapse-dashboard` services
- Cloudflare Tunnel with path-based routing — dashboard at `/`, gateway API at `/health`, `/ingest/*`, `/dashboard/*`, etc.
- Nightly backups via systemd timer to DigitalOcean Spaces
- HTTP 503 on degraded `/health` for UptimeRobot-style external monitoring

Migration from a local-only setup: [`deploy/MIGRATE.md`](./deploy/MIGRATE.md).

### Updating production after a code change

Standard workflow once Synapse is live:

```bash
# 1. On your laptop — edit, commit, push
git add . && git commit -m "feat: …" && git push

# 2. SSH to the VPS and redeploy
ssh root@<DROPLET_IP>
bash /opt/synapse/deploy/scripts/redeploy.sh
```

The script ([`deploy/scripts/redeploy.sh`](./deploy/scripts/redeploy.sh)):

- `git pull --ff-only` — bails if the VPS has uncommitted local edits (never silently overwrites)
- Detects what changed; only rebuilds affected services
- Backend changes → `uv sync` + `systemctl restart synapse-gateway` (alembic migrations auto-apply)
- Dashboard changes → `npm install` + `npm run build` + `systemctl restart synapse-dashboard`
- Extension changes → prints reminder to reload at `chrome://extensions/`
- Prints `/health` snapshot at the end

Scope it for faster redeploys:

```bash
bash /opt/synapse/deploy/scripts/redeploy.sh gateway      # backend only
bash /opt/synapse/deploy/scripts/redeploy.sh dashboard    # frontend only
```

One-liner from the laptop (no interactive SSH):

```bash
git push && ssh root@<DROPLET_IP> 'bash /opt/synapse/deploy/scripts/redeploy.sh'
```

## Testing

```bash
uv run pytest tests/ -v
```

214 tests, 1 skipped (chmod on Windows), 0 failed. ~60 s warm. Test boundaries:

- **Backend** (Python) — every agent has tests with mocked Claude calls but real DB. Hebbian, freshness, communities, retention, OAuth (with mocked Google token exchange), CORS, ingestion. The browser extension's `content` ↔ `selected_text` alias compatibility is locked by a regression test.
- **Frontend** — `npm run build` enforces TypeScript correctness; no Jest/Playwright yet (small surface, server-rendered, manual smoke checks suffice).

## Documentation index

| Doc | Purpose |
|---|---|
| [`SYNAPSE_PRD.md`](./SYNAPSE_PRD.md) | Authoritative product + architecture specification. Appendix A covers cognitive enhancements (Hebbian, sleep consolidation, forgetting). |
| [`CLAUDE.md`](./CLAUDE.md) | Build guidelines for AI-assisted development — non-negotiable rules, code style, agent prompt conventions, testing requirements. |
| [`deploy/INSTALL.md`](./deploy/INSTALL.md) | From-zero VPS deployment walkthrough (DigitalOcean + Cloudflare Tunnel + DO Spaces backups). |
| [`deploy/MIGRATE.md`](./deploy/MIGRATE.md) | One-time migration from a local install to a live VPS. |
| [`deploy/cloudflared/README.md`](./deploy/cloudflared/README.md) | Cloudflare Tunnel setup variants (quick / named / custom domain). |
| [`dashboard/README.md`](./dashboard/README.md) | Frontend stack notes + local development. |
| [`extension/README.md`](./extension/README.md) | Browser extension install + architecture + troubleshooting. |

## Engineering principles

These are real principles, enforced via memory-loaded discipline checks before each commit:

1. **Never delete, always archive.** No agent deletes a capture. Files move from `inbox/` to `archive/`. Even user-deleted INSIGHT candidates are preserved.
2. **LLM outputs validated before write.** Every Claude response that mutates the graph goes through a Pydantic schema. Failed validations log the raw output and skip the write.
3. **Credentials never logged.** All tokens are Fernet-encrypted in SQLite. Only `synapse/gateway/auth.py` sees plaintext; loguru's record patcher scrubs sensitive keys from log extras.
4. **Prompts are files, not strings.** Every agent prompt lives at `synapse/prompts/*.md` as a Jinja2 template. Prompt changes don't require code changes.
5. **One milestone at a time.** Foundations before features. M0 ships before M1 starts.
6. **No dead code, no copy-paste, no over-engineering, no AI boilerplate fluff.** Audit every diff pre-commit.

## License

Proprietary. © Wint3rX / Xtension Labs. Not open for use, redistribution, or modification without explicit permission.

---

*Built by [Wint3rX](https://github.com/xtensionlabs) at Strathmore University · Nairobi · 2026*
