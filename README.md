# Synapse

Personal cognitive operating system. Five layers: Capture · Knowledge Graph · Context · Agents · Surfaces.

**Authoritative specification:** [`SYNAPSE_PRD.md`](./SYNAPSE_PRD.md)
**Build guidelines for Claude Code:** [`CLAUDE.md`](./CLAUDE.md)

## Quick start

```bash
uv sync
cp .env.example .env
# Fill .env, then:
uv run synapse init           # provision vault + database
uv run synapse start          # boot gateway + telegram bot
```

## Status

| Milestone | State |
|---|---|
| M0 Foundation (capture) | completed |
| M1 The Graph Lives | pending |
| M2 Daily Ritual | pending |
| M3 Startup Mirror | pending |
| M4 Strategic Intelligence | pending |
| M5 Full Surface Layer | pending |
| M6 Production Hardening | pending |

See `SYNAPSE_PRD.md §11` for the full milestone roadmap.
