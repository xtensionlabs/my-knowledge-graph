# Synapse Dashboard

The web UI for Synapse — a read-only window onto the graph, communities, and agent activity.

Stack: **Next.js 16** App Router, **React 19**, **Tailwind v4**, **@xyflow/react** with **d3-force** physics for the graph view. Server components fetch from the gateway server-side so the API key never reaches the browser bundle.

## Prerequisites

1. The Synapse gateway running locally:
   ```bash
   uv run synapse start --no-telegram --no-clipboard
   ```
2. Node 20+ (verified on Node 26).

## Setup

```bash
cd dashboard
cp .env.local.example .env.local
# Edit .env.local — set SYNAPSE_API_KEY to match SYNAPSE_BROWSER_API_KEY
# in the project root .env file.
npm install
npm run dev
```

Then open http://localhost:3000.

## Pages

| Route | Endpoint consumed | What it shows |
|---|---|---|
| `/` | `/dashboard/overview` | Graph counts, 24h capture rate, recent agent runs |
| `/graph` | `/dashboard/graph` | Interactive node graph (d3-force layout) |
| `/communities` | `/dashboard/communities` | Louvain clusters with hub concepts |
| `/agents` | `/dashboard/agents` | Per-agent 7-day cost + run history |

## Architecture notes

- **Server-side fetching.** All data is fetched in async Server Components via `lib/api.ts`. The `SYNAPSE_API_KEY` is read from the Node process env, sent as `x-synapse-api-key` to the gateway, and never bundled into client JS.
- **No client-side query layer.** No TanStack Query, no SWR — server components re-fetch on every navigation. Add polling later if needed.
- **Graph layout precomputation.** `app/graph/GraphCanvas.tsx` runs d3-force simulation synchronously (300 ticks) on render to compute organic node positions, then hands the result to React Flow for pan/zoom/drag. Layout is memoized so it only re-runs when the input data changes.
- **Theme.** Dark default, Inter + JetBrains Mono, electric violet accent. All design tokens live as CSS variables in `app/globals.css`.

## Production build

```bash
npm run build
npm run start
```

The dashboard binds to `0.0.0.0:3000` by default. Keep it behind a reverse proxy / firewall — the `SYNAPSE_API_KEY` check provides only basic auth.
