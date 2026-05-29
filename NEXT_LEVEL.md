# Synapse — The Next Level

*Bridging personal knowledge to public knowledge.*

---

## 0. Why this document exists

Synapse v2 (M0–M6) shipped a personal cognitive substrate: capture → graph → agents → surfaces, deployed 24/7 with backups and OAuth integrations. It works. The graph contains *what you know, in your words*.

The next leap is making Synapse aware of **what humanity knows about the same things**. When you capture "BFS," the graph shouldn't just store "BFS" — it should know that's [Q302414](https://www.wikidata.org/wiki/Q302414), an algorithm in graph theory, with canonical relations to "graph traversal," "Dijkstra's algorithm," and "Edsger W. Dijkstra." It should be able to *answer questions you haven't yet asked*: "What other graph algorithms should you know if you know BFS and DFS?"

This document outlines that bridge. Three tiers, honest scoping, concrete first steps, and an explicit anti-vision so we don't build everything because we can.

---

## 1. What we already have (don't re-build)

| Capability | Implementation | State |
|---|---|---|
| Local embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim) | ✅ M1 |
| Vector index | ChromaDB persistent client | ✅ M1 |
| In-memory graph ops | NetworkX | ✅ M1 |
| Semantic search | Cosine distance + centrality + freshness ranker | ✅ M5 |
| Community detection | Louvain via NetworkX | ✅ M5 |
| Hebbian edge dynamics | Custom strengthen + decay | ✅ M4 |
| Multi-modal capture | Whisper (voice) + Tesseract + Claude Vision (images) | ✅ M5 |
| Agent reasoning | Librarian / Synthesizer / Strategist / Guardian / Critic / Scout | ✅ M1–M5 |

Vector embeddings + a vector index already exist. The user request to "add vector embeddings / vector database" is technically already done — the next move is *what we feed the index and how we rank against it*, not the index itself.

---

## 2. The thesis

> A second brain that only sees what *you* captured plateaus at the limits of your attention. A second brain wired into the structured knowledge graph of humanity compounds against the world's curiosity, not just yours.

Three concrete benefits:

1. **Canonical dedup.** "BFS" and "breadth-first search" both map to Wikidata Q302414. The Librarian's title-match dedup becomes entity-match dedup. No more duplicate concepts in different phrasing.
2. **Latent neighbors.** The graph can suggest concepts you haven't captured: "you have Dijkstra + BFS, you don't have A*, here's why it matters." This is curriculum-aware.
3. **Fact retrieval.** SPARQL against Wikidata can answer factual questions the agents currently hallucinate or skip: "When did Strathmore start its BICS program?" → real answer, sourced.

---

## 3. Proposed layers, tiered

### Tier 1 — Wikidata entity linking (one focused session)

**Goal:** every new CONCEPT / PERSON / EVENT node gets a `wikidata_id` field populated when a clear match exists.

**Why Wikidata over Google Knowledge Graph:**
- Open license (CC0); no API key, no rate limit at our usage
- Same underlying data as the GKG (Wikidata is the source)
- SPARQL endpoint gives structured queries Google's API can't

**Implementation:**

- `synapse/external/wikidata.py` — wraps the [`wbsearchentities`](https://www.wikidata.org/w/api.php?action=help&modules=wbsearchentities) endpoint for title→entity lookup, and the SPARQL endpoint for structured queries. ~100 lines.
- Schema delta: `Node.external_ids: JSON` (already supports arbitrary tags; we use a sentinel `_wikidata=Q302414`).
- Librarian prompt update: include `existing_wikidata_ids` in the context so the LLM can dedup against canonical IDs, not just titles.
- New CLI: `synapse link wikidata --all` for backfill on existing nodes.

**First commit pattern:**

```python
# synapse/external/wikidata.py
def search_entity(query: str, lang: str = "en", limit: int = 5) -> list[Match]:
    """Returns top-k candidate Wikidata entities for a title."""
    # GET https://www.wikidata.org/w/api.php?action=wbsearchentities&search=...

def get_entity_description(qid: str) -> EntityDescription:
    """Returns label + description + instance_of + Wikipedia URL."""
```

**Honest cost:**
- Latency: ~150–300ms per lookup (Wikidata is in EU). Acceptable for batch Librarian runs.
- Failure modes: ambiguity (multiple Q-IDs match), no match (user-coined term). Both must degrade gracefully — store `unlinked: true` and don't block the node.

**Gate:** at the end of Tier 1, ≥ 80% of your existing CONCEPT nodes have a Wikidata ID, and the Librarian no longer creates duplicate CONCEPTs for "BFS" vs "Breadth-First Search."

---

### Tier 2 — SPARQL-driven curriculum + suggestions (one milestone, ~3 sessions)

**Goal:** the Strategist and Scout can ask Wikidata questions and surface "what should I know next."

**New capabilities:**

1. **Curriculum prerequisites.** For each CONCEPT with a Wikidata ID, query the [`P527 (has part)`](https://www.wikidata.org/wiki/Property:P527), [`P279 (subclass of)`](https://www.wikidata.org/wiki/Property:P279), and [`P361 (part of)`](https://www.wikidata.org/wiki/Property:P361) properties. Build a prerequisite map.
2. **Latent neighbor suggestions.** For each Louvain community (via M5), query Wikidata for canonical entities in the same domain that the user hasn't captured. Surface as INSIGHT candidates (user confirms via existing flow).
3. **Domain bridges.** "You have CS BUILDs and Discrete Math CONCEPTs — Wikidata says these connect via 'graph theory' (Q131476). Want a bridge node?"

**New module:** `synapse/external/sparql.py` — small SPARQL client (just `httpx` + JSON parsing).

**New agent capability:** Scout, when running weekly, queries Wikidata for canonical neighbors of every active community's hub concepts. Filters by existing graph state (don't suggest things you already have).

**Schema delta:** new edge relation `canonical_neighbor` (different from `bridges` — that's user-confirmed; `canonical_neighbor` is Wikidata-asserted).

**Honest cost:**
- SPARQL queries are slow (1–10s for complex ones). Batch + cache aggressively.
- Adding curriculum suggestions can be noisy — strict confidence gating required, or the Synthesizer's daily briefing becomes a wall of "have you considered…"

**Gate:** Scout's weekly digest contains ≥ 1 high-confidence "you should know X" item that the user agrees with after 4 weeks of usage. Negative gate: user doesn't disable Scout because it became noise.

---

### Tier 3 — Hybrid retrieval + reranking + multi-modal (multi-milestone, post-Year-1)

This tier is the upgrade path *if* you outgrow what we already have. **None of it is a blocker** for the personal-OS use case. Listed in priority order:

#### 3.1 Hybrid retrieval (BM25 + dense)

Current: pure cosine similarity over MiniLM embeddings.
Upgrade: BM25 keyword scoring + dense vector + Reciprocal Rank Fusion. Catches exact-term matches the current vector ranker can fumble (acronyms, code symbols).

**When to do this:** if `synapse ask` ever fails to find a node you know exists. Until then, premature.

#### 3.2 Cross-encoder reranker

Current: vector distance is the final score.
Upgrade: top-50 candidates re-ranked by a small cross-encoder (e.g., `cross-encoder/ms-marco-MiniLM-L-6-v2`). Adds ~200ms but typically reduces "wrong-but-similar" results by 30%.

**When to do this:** if hybrid retrieval still leaves search feeling fuzzy. Pure quality improvement, no architectural risk.

#### 3.3 Upgrade embedding model

Current: MiniLM, 384-dim, ~100MB.
Upgrade options:
- `all-mpnet-base-v2` (768-dim, ~450MB, ~25% better STS scores)
- `BAAI/bge-large-en-v1.5` (1024-dim, ~1.3GB, near-SOTA for English)

**When to do this:** if retrieval quality plateaus. Cost: re-embed entire graph; one-time ~2hr CPU run for a few thousand nodes.

#### 3.4 Multi-modal CLIP embeddings

Current: text embeds with MiniLM, images are OCR'd to text then embedded as text.
Upgrade: CLIP (`open_clip` library) gives a shared text↔image embedding space. Paste an image of a graph → search for similar diagrams across all captures. Capture a photo of a whiteboard → semantic match against your text-based notes.

**When to do this:** when you regularly capture diagrams + photos that aren't well-served by OCR-to-text. Requires re-embedding the graph in a new space.


#### 3.5 Replace ChromaDB with Qdrant or LanceDB

Current: ChromaDB, persistent local store.
Why you might switch:
- Qdrant: better filtering DSL, scales to millions of vectors
- LanceDB: zero-copy reads, embedded mode, smaller footprint

**When to do this:** never, unless ChromaDB specifically breaks at your scale. At personal use (10K–100K nodes) it's fine. This is the kind of "swap the database" change that costs a session and gains nothing observable.

---

## 4. Architecture: the External Knowledge Layer

The cleanest way to add this without polluting existing layers:

```
┌────────────────────────────────────────────────────────────────┐
│  CAPTURE     Telegram · clipboard · browser · email · git · OCR │
├────────────────────────────────────────────────────────────────┤
│  GRAPH       SQLite + ChromaDB + NetworkX + Hebbian + Louvain   │
├────────────────────────────────────────────────────────────────┤
│  CONTEXT     Foreground / Horizon / Background · Energy         │
├────────────────────────────────────────────────────────────────┤
│  AGENTS      Librarian · Synthesizer · Strategist · …           │
├────────────────────────────────────────────────────────────────┤
│  EXTERNAL    Wikidata · Google KG · ConceptNet · WordNet  ← NEW │
│  KNOWLEDGE   (read-only; cached; query-able by agents)          │
├────────────────────────────────────────────────────────────────┤
│  SURFACES    FastAPI · Dashboard · CLI · Telegram · Vault       │
└────────────────────────────────────────────────────────────────┘
```

The External layer is **read-only from Synapse's perspective**. Synapse never writes to Wikidata. The layer caches responses aggressively (rate-limit friendly, fast retries on agent runs).

New module structure:

```
synapse/
├── external/                  # ← new
│   ├── __init__.py
│   ├── wikidata.py           # entity search + SPARQL
│   ├── google_kg.py          # fallback / complement
│   ├── conceptnet.py         # common-sense relations (Tier 2+)
│   ├── cache.py              # SQLite-backed response cache
│   └── linking.py            # title → entity_id resolver
│
├── graph/
│   └── models.py              # adds Node.external_ids JSON field
```

---

## 5. Schema deltas

Minimal — most additions ride on existing JSON fields.

```python
class Node(SQLModel, table=True):
    # ...existing fields...

    # New: canonical identifiers from external knowledge bases.
    # JSON object: {"wikidata": "Q302414", "google_kg": "kg:/m/0123"}
    external_ids: str = "{}"

    # New: timestamp of last entity-linking attempt. NULL = never tried.
    # Used so re-linking can re-try after improvements without re-trying
    # everything every Librarian sweep.
    external_linked_at: datetime | None = None
```

New edge relation (no schema change, just a new value in the enum):

```python
class RelationType(str, Enum):
    # ...existing...
    EQUIVALENT_TO = "equivalent_to"      # this node IS the same as another (via shared QID)
    CANONICAL_NEIGHBOR = "canonical_neighbor"  # Wikidata says these are connected
```

Alembic migration: one additive change, generated via `alembic revision --autogenerate`. ~30 seconds to apply.

---

## 6. Cost analysis (honest)

| Layer | Cost | Notes |
|---|---|---|
| Wikidata API + SPARQL | **$0** | CC0, no key, no published rate limit; we cache responses |
| Google Knowledge Graph API | **$0** (≤100K queries/day) | Fallback only; needed if Wikidata coverage gaps appear |
| ConceptNet API | **$0** | MIT-licensed, no key |
| Tier 1 implementation | **~1 session** | Single source file + Librarian prompt update + tests |
| Tier 2 implementation | **~1 milestone** | 3 sessions; involves Scout + Strategist prompt updates |
| Tier 3 cross-encoder | **~0.5 session** | Drop-in upgrade if needed; CPU inference, no API |
| Tier 3 CLIP embeddings | **~1 milestone** | Re-embed graph; bigger model on disk; choose carefully |

No new monthly costs. Tier 3 multi-modal adds ~1.5 GB to the VPS disk for CLIP weights — fits comfortably in the 50 GB Droplet.

---

## 7. Honest tradeoffs (what could go wrong)

**Entity linking adds latency to the Librarian.** Wikidata round-trip is 150–300ms. With ~50 captures/day, that's ~15s/day of extra time. Acceptable, but means the Librarian's 2hr sweep gets ~15s longer. Cache aggressively.

**Wikidata coverage isn't 100% for personal concepts.** Your "Xtension Signal" build node has no canonical entity. The flow must gracefully accept unlinked nodes — they're still valid first-class graph members. Don't block on missing matches.

**Curriculum suggestions can become noise.** Scout pulling in "related concepts" without strict confidence gating drowns the user in low-value content. The existing INSIGHT candidate flow (user confirms before write) is the right gate to reuse.

**Multi-modal CLIP changes the embedding space.** Re-embedding the entire graph is irreversible without a backup. Plan it as a milestone, not a tweak.

**Domain drift.** Wikidata is English-centric and Western-academic-canon-biased. For Synapse — used in Nairobi for a CS+startup workload — that's mostly fine, but be aware: "harambee" or "M-Pesa" may not have rich enough Wikidata coverage to drive curriculum suggestions.

---

## 8. Anti-vision (what we're NOT doing)

These are the temptations to refuse:

- **Building our own knowledge graph from scratch.** Wikidata exists. Anything we'd build would be worse and unmaintained.
- **Writing data back to Wikidata or DBPedia.** We're a consumer. Round-tripping our personal notes upstream is out of scope (and probably wrong — they're not encyclopedic).
- **Replacing ChromaDB without a measured reason.** It works. Until it doesn't.
- **Real-time agent SPARQL queries during user interaction.** SPARQL is slow. Agents query in batch during scheduled runs, never in the user's hot path.
- **A "universal knowledge graph" pitch.** Synapse is a *personal* cognitive OS. The External layer makes it smarter, not generic.

---

## 9. Tier 1 — concrete first session plan

When you come back to start this, the first session should:

1. Add `synapse/external/wikidata.py` with `search_entity()` + `get_entity_description()` + tests (mocked HTTP responses, same pattern as `tests/test_github_integration.py`).
2. Add the SQLite-backed cache (`synapse/external/cache.py`) — 30 lines. Wikidata responses are immutable for our purposes; cache forever, manual invalidation only.
3. Add `Node.external_ids` + `Node.external_linked_at` via alembic migration.
4. Extend the Librarian prompt to include `linked_concept_titles_with_qid` in the context, and add a JSON field `wikidata_id` to its output schema for new CONCEPTs.
5. Add CLI: `synapse link wikidata` (no args) backfills existing CONCEPT nodes; `--all` includes PERSON and EVENT too.
6. Update the dashboard's NodeDetailPanel to show the Wikidata link if present, with the external icon affordance.

**Success gate:** after running `synapse link wikidata` on the current production graph, ≥ 80% of CONCEPT nodes have a non-null `wikidata_id`. The Librarian's next sweep doesn't re-create "Graph theory" when it already exists as "graph theory."

---

## 10. Open questions to resolve before starting

Don't start until these have answers — they're scope decisions, not implementation details.

1. **Linking confidence threshold.** Wikidata returns ranked candidates. Top match? Top match if score > X? Top match if disambiguator (the description) syntactically matches the node's content?
2. **Disambiguation UI.** When linking is ambiguous (e.g., "Mercury" → planet vs element vs band), should we:
   - Pick the highest-scored automatically?
   - Surface a `pending_links.md` for the user (mirrors `pending_insights.md`)?
   - Both, with a config toggle?
3. **Caching strategy.** Wikidata entities can update (rarely). Refresh policy: never? Every 30 days? On user demand?
4. **Privacy boundary.** Sending node titles to Wikidata's API is a privacy decision. For personal CS coursework it's fine. For sensitive captures (medical, financial)? Add a `external_lookup: false` tag the user can apply to opt out per-node?
5. **Backfill scope.** Run linking on existing 100 nodes vs only new captures going forward? Backfill is one-time but each lookup is ~250ms, so ~25s for the existing graph. Trivial.

These are honest decisions. Resolve them before code, not during.

---

## 11. Where this fits in the roadmap

Synapse milestones so far:

- M0 Capture · M1 Graph · M2 Synthesizer · M3 Startup Mirror
- M4 Strategic + Self-Rewiring · M5 Critic + Scout + OCR + Dashboard
- M6 Production Hardening

Proposed:

- **M7 — External Knowledge: Tier 1 (Wikidata linking)**
- **M8 — External Knowledge: Tier 2 (SPARQL curriculum + suggestions)**
- **M9 — Retrieval polish (Tier 3.1, 3.2 if needed)**
- (Tier 3.3, 3.4, 3.5 land as warranted, not as a planned milestone)

M7 is the next session-worth of work after the breather. M8 onwards depends on whether M7 actually compounds the way we expect.

---

*Drafted 2026-05-29 · Wint3rX / Xtension Labs · Synapse v2 → v3 outline*
