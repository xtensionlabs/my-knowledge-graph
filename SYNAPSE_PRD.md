# SYNAPSE v2
## The Living Operating System for a Builder-Student
*Wint3rX / Xtension Labs — Internal Architecture Document*

---

> **Core Thesis:** Most second brain systems fail not because they lack features, but because they demand behavior change before they deliver value. Synapse is designed around the opposite principle: *deliver value on day one, earn complexity over time.* It is not a productivity tool. It is a cognitive exoskeleton that compounds.

---

## 0. The Problem, Precisely Stated

You are operating across three simultaneous contexts that create constant cognitive debt:

1. **BICS Semester 1** — Differential Calculus, Discrete Mathematics, and three other units demanding retention and structured thinking
2. **Xtension / SIGNAL** — Early-stage startup requiring architectural decisions, investor-facing communication, and shipping
3. **Self-directed R&D** — IoA positioning, bio-inspired systems, agent architecture — learning that doesn't fit neatly into either box

The real cost isn't time. It's **context-switching tax** — the 15–25 minutes of recovery time every time your brain shifts frames. A system that eliminates this tax and turns cross-context connections into *compounding value* is worth more than any individual productivity tool.

Synapse's job: make the three contexts feed each other instead of competing.

---

## 1. Design Principles (Non-Negotiable)

These govern every architectural decision below.

**1.1 Zero Willpower at Capture**
The system must work when you're exhausted, in a matatu, between lectures, or mid-debugging session. Every capture path must be ≤2 taps or ≤3 seconds. If it requires sitting down to use, it won't be used.

**1.2 Build With Intention, Not Inheritance**
Synapse is being built clean — no legacy architecture to accommodate, no technical debt to route around. This is an advantage. Every component is designed for its actual purpose from day one, and Claude Code will construct the agent gateway layer with the right primitives rather than retrofitting an existing one. Greenfield is a gift. Treat it like one.

**1.3 Cloud-First, Resilient by Design**
Synapse assumes a reliable internet connection as the default. Cloud APIs, real-time sync, and live integrations are first-class — not fallbacks. Where offline capability exists (e.g., the gateway's job queue), it functions as a resilience layer for brief interruptions, not as the primary operating mode.

**1.4 Earn Complexity**
Week 1 Synapse should be almost embarrassingly simple. Complexity is added only when a simpler layer has been used consistently for 2+ weeks. A system you actually use at 30% of its potential beats a system you abandon at 100%.

**1.5 The Bridge is the Product**
Every CS concept must be forced through the question: *"Where does this live in Xtension's architecture?"* Every startup decision must be traceable to a CS primitive. This bidirectional pressure is not a feature — it is the entire point.

---

## 2. Architecture: The Five-Layer Stack

Synapse is not a swarm of agents. It is a **layered stack** where each layer has a single responsibility and a clean interface to the layer above and below.

```
┌─────────────────────────────────────────────┐
│          LAYER 5: SURFACE (Interfaces)       │
│     Obsidian · Dashboard · Mobile Widget     │
├─────────────────────────────────────────────┤
│          LAYER 4: REASONING (Agents)         │
│   Synthesizer · Strategist · Critic · Scout  │
├─────────────────────────────────────────────┤
│          LAYER 3: CONTEXT (Working Memory)   │
│      Active projects · Session state ·       │
│      Current focus · Energy model            │
├─────────────────────────────────────────────┤
│          LAYER 2: GRAPH (Knowledge Store)    │
│   Concept nodes · Relations · Evidence ·     │
│   Spaced repetition state · Startup links    │
├─────────────────────────────────────────────┤
│          LAYER 1: CAPTURE (Ingestion)        │
│   Voice · OCR · Clipboard · Git hooks ·      │
│   Browser extension · Email forward          │
└─────────────────────────────────────────────┘
```

Each layer can be built and used independently. You start at Layer 1. You earn access to Layer 4.

---

## 3. Layer 1: Capture — The Only Layer That Must Be Perfect

**The brutal truth about second brains:** 90% of them fail at Layer 1. Not because the tools are bad, but because the capture friction is just high enough that busy moments — the exact moments when valuable insights occur — produce nothing.

### 3.1 The Capture Contracts

Every input channel must honor exactly one contract: *accept anything, judge nothing, process asynchronously.*

The system must never block you to ask "what folder does this go in?" or "add tags?" Those decisions happen later, in batch, by an agent. Your job at capture time is exactly one thing: get it in.

### 3.2 Capture Channels

**Text (Primary)**
- The default and preferred capture mode. Fast, precise, searchable from the moment it lands.
- Primary paths: a dedicated Telegram bot (send a message → instant inbox entry), the Obsidian mobile quick-capture shortcut, and a minimal PWA with a single text field — open, type, done.
- Supports markdown natively: bullet a rough outline, paste a code snippet, drop a URL — all valid. No formatting required.
- The Telegram bot is particularly powerful: it's already open on your phone, requires zero additional app switching, and works on any connection speed including 2G.

**Voice (Secondary)**
- For moments where typing is genuinely not possible: walking between buildings, mid-commute, hands occupied during a lab.
- Mobile widget (one tap) → Whisper transcription → raw text dropped into `inbox/`
- Transcripts are treated as drafts — the Librarian flags them for a quick human scan before full ingestion, since voice captures tend to be more fragmented than text ones.
- No hotword trigger by default. One tap is enough.

**Visual / OCR**
- Phone camera → Automatic OCR → Text + image stored together
- Covers: whiteboards, lecture slides, textbook pages, handwritten notes, physical receipts for expense tracking
- Diagrams are stored as images AND converted to Mermaid/PlantUML where structure is detectable

**Clipboard Daemon (Desktop)**
- Background process monitors clipboard
- Anything you copy that's >50 chars and looks like content (not a file path) → queued for review
- Especially powerful for code snippets, quotes from papers, Stack Overflow solutions

**Git Hooks**
- Post-commit hook on all Xtension repos
- Extracts: what changed, what problem it solved, what CS concept it demonstrates
- Auto-creates a "Build Log" entry linked to relevant course concepts
- This is the bridge made automatic

**Email Forward**
- Dedicated address (synapse@xtensionlabs.com via Cloudflare Email Routing → free)
- Forward anything → instant ingestion
- Handles: newsletter articles, professor emails, investor replies, research papers

**Browser Extension (Minimal)**
- Single purpose: highlight + send to inbox
- No "save to folder," no tagging at time of capture
- Works offline and syncs when connected

### 3.3 The Inbox

Everything goes to one place first: `SYNAPSE/inbox/`. No exceptions.

The inbox is not where things live. It is a buffer. The Librarian agent (Layer 2) processes it asynchronously. Your only discipline: don't let the inbox exceed 48 hours of unprocessed items. The Librarian runs automatically, but you review its categorizations in a 10-minute daily triage.

---

## 4. Layer 2: The Knowledge Graph — Concepts, Not Documents

### 4.1 The Fundamental Shift

Most note systems store *documents*. Synapse stores *concepts* and uses documents as evidence.

A document about Dijkstra's algorithm is not a node in your graph. The concept **"shortest path under non-negative weights"** is a node. The document, your lecture notes, the implementation in NEXUS's agent routing layer, and the exam question are all *edges* pointing to that concept node.

This means when you search for something, you find *all contexts where it appears*, not just the document you happened to store it in.

### 4.2 Node Types

```
CONCEPT     → A CS/math/domain idea (e.g., "graph traversal", "consent flow")
FACT        → A specific assertion (e.g., "Dijkstra fails on negative edges")
BUILD       → A concrete thing you made (code, design, pitch)
PERSON      → Lecturer, investor, collaborator, author
EVENT       → Lecture, meeting, deadline, hackathon
QUESTION    → Open problems, both academic and startup
INSIGHT     → Cross-context connections (the highest-value nodes)
```

**INSIGHT nodes are the crown jewels.** An example:

> *"The proof structure for induction in ICS 1104 (Discrete Math) is structurally identical to the invariant proof needed for Synapse's consent layer — if the base state is valid and each transition preserves validity, the system is provably safe."*

This is an INSIGHT node. It has edges to: `graph theory lecture notes`, `Synapse agent gateway spec`, `ICS 1104 assignment 2`, `IoA trust layer concept`. No other system surfaces this. Synapse is built to manufacture these.

### 4.3 Retention Layer (Spaced Repetition, Correctly)

The Anki integration in the original proposal treats spaced repetition as an add-on. It is not. It is the primary mechanism by which the graph becomes durable.

Every CONCEPT node has a retention state: `{last_reviewed, interval, ease_factor, scheduled_next}`.

The Synthesizer agent (Layer 4) generates review questions — but not generic flashcard questions. It generates questions that force you to apply the concept in a *new context*, usually the startup one:

- Instead of: *"What is time complexity of Dijkstra?"*
- Synapse asks: *"Your agent gateway needs to find the cheapest API call path between services. Model this as a graph problem and name the algorithm. What breaks if one API occasionally returns negative latency values?"*

This is not harder to answer. It is *harder to forget*.

### 4.4 The Startup Mirror

Every Xtension repo has a `synapse.json` manifest (auto-maintained by the Git hook) that maps:

```json
{
  "module": "synapse-consent-layer",
  "cs_concepts": ["graph theory", "state machines", "OAuth flows"],
  "open_questions": ["how to handle revoked tokens mid-session?"],
  "last_updated": "2026-05-06"
}
```

This manifest is how Synapse knows to surface *"you have a lecture on state machines tomorrow — it directly applies to your open question in the agent consent layer"*.

---

## 5. Layer 3: Context — Working Memory

This layer is the most underspecified in the original proposal and the most important to get right.

### 5.1 The Session State

Every time you open Synapse, it reconstructs a **session context**: what you were last doing, what's due, what's decaying in retention, what's overdue in the startup.

This is not a to-do list. It is an answer to the question: *"Given everything I know about you, what should your brain be loaded with right now?"*

The session state has three components:

**Foreground** — The one thing you are doing right now. Synapse protects this. While foreground is active, it suppresses all non-critical notifications and queues everything else.

**Background** — The 2-3 things your brain should be passively processing. These are served during low-effort moments: commute, waiting, transition time. Synapse surfaces these via the mobile widget.

**Horizon** — Upcoming events within 72 hours that require preparation. Deadlines, meetings, lectures. Synapse pre-loads relevant concepts into your review queue 48 hours before.

### 5.2 Energy Modeling (The Part Others Get Wrong)

The original proposal mentions "energy patterns from wearable data." This is over-engineered. You don't need a wearable to build a useful energy model.

Synapse infers energy state from behavioral signals:
- **Typing velocity** in active sessions (slow = tired or thinking hard)
- **Task switching frequency** (high switching = fragmented, low energy)
- **Capture quality** (terse, fragmented captures = low energy)
- **Time of day + day of week** (build a model from 2 weeks of data)

This inferred energy state determines *what kind of work* Synapse suggests, not whether you work:
- **High energy** → New material, hard problems, architectural decisions
- **Medium energy** → Review, writing, synthesis
- **Low energy** → Passive recall, reading, inbox triage

---

## 6. Layer 4: Reasoning — The Agents

Now we get to agents. But notice: we are at Layer 4. The agents are not the foundation. They are the intelligence layer sitting on top of a robust capture, storage, and context system. This is why most AI-powered second brains fail — they build agents before they have good data.

### 6.1 The Librarian (Ingestion + Graph Builder)

**Input:** Raw inbox items
**Output:** Structured nodes + edges in the knowledge graph

The Librarian runs on a schedule (every 2 hours, or triggered manually). It:
1. Reads all items in `inbox/`
2. Extracts concepts, facts, and questions
3. Finds existing graph nodes to connect to (or creates new ones)
4. Tags with source, timestamp, context (was this during a lecture? a build session?)
5. Proposes Startup Mirror links for human confirmation
6. Archives the raw item

The Librarian never deletes. It archives. The raw item is always retrievable.

### 6.2 The Synthesizer (Recall + Connection Engine)

**Runs:** Daily (morning, 5 minutes) + on-demand
**Output:** Delta Briefing + review queue + INSIGHT candidates

The Delta Briefing is the most important daily ritual in Synapse. It is 5 minutes, delivered as voice (text-to-speech during morning routine) or text. It covers:

1. **Retention alerts** — 3 concepts that are predicted to decay in the next 48 hours based on Ebbinghaus curve + your personal forgetting rate
2. **Upcoming lecture prep** — What concepts you already know that connect to today's lecture topic
3. **Startup-academic bridge** — One specific connection between current coursework and an open Xtension question
4. **One open question** — A question from the graph that has been unanswered for >3 days, surfaced for focused thought

The Delta Briefing is not a summary. It is a *cognitive priming mechanism*. It loads your brain with the right pointers before the day starts.

### 6.3 The Strategist (Planning + Collision Detection)

**Runs:** Weekly (Sunday, 15 minutes) + on deadline collision
**Output:** Weekly plan + tradeoff analysis

The Strategist's primary value is not scheduling. It is **collision detection and honest tradeoff analysis**.

When a CAT and a startup deadline land in the same week (and they will), the Strategist does not pretend there's a clever hack. It presents:

- **Minimum Viable Grade** path: what is the least preparation that still passes this CAT, and what grade does that likely produce?
- **Startup cost** of deprioritizing: what features slip, what is the real impact?
- **Recommended tradeoff** with explicit reasoning

It does not make the decision. You make the decision. But it makes the decision *legible* instead of anxiety-inducing.

The Strategist also identifies **Synergy Windows** — blocks where academic and startup work are genuinely the same work. Example: "Your Discrete Math assignment on graph connectivity is identical to the routing problem your agent gateway faces in mesh topology. Submit your gateway solution as the assignment basis." This is not cheating. This is the entire point of studying at a university while building.

### 6.4 The Critic (Quality Assurance)

**Runs:** On demand, pre-submission, pre-publish
**Output:** Structured feedback with specific improvements

The Critic reviews outputs across three domains:

**Academic outputs** — Notes, assignments, exam prep. Reviews for: conceptual completeness, common exam traps, connection to adjacent concepts you've studied.

**Startup outputs** — Architecture docs, pitch decks, PRDs, code. Reviews for: internal consistency, missing edge cases, how well the stated approach actually solves the stated problem.

**Communication** — Emails to professors, investor outreach, GitHub READMEs. Reviews for: clarity of ask, whether the framing serves your goal.

The Critic has one rule: it must always identify *the single most important thing to fix*. A list of 20 suggestions is not feedback. It is noise.

### 6.5 The Scout (Research + Horizon Scanning)

**Runs:** Weekly background task
**Output:** Curated additions to graph + competitive intelligence

The Scout is the only agent that looks *outward* rather than at your existing knowledge. Its job:

- Monitor arXiv, GitHub trending, Hacker News for concepts that connect to nodes in your graph
- Track competitor movements relevant to Xtension (agent frameworks, IoA papers, African fintech API changes)
- Surface one "unknown unknown" per week — something you didn't know you needed to know

The Scout's output is always *proposed additions*, never automatic. You confirm or reject in the weekly Alignment Review.

### 6.6 The Guardian (Wellbeing + Early Warning)

**Runs:** Continuous background monitoring
**Output:** Alerts + micro-interventions

The Guardian watches for two failure modes:

**Burnout trajectory** — Detected by: declining capture quality, increasing task-switching, falling retention scores, reduced Delta Briefing engagement. Response: mandatory 48-hour scope reduction + explicit workload shedding suggestions.

**Academic crisis** — Detected by: concept decay without review, missed lectures without makeup capture, assignment deadline proximity with no recorded preparation. Response: triage plan, not panic.

The Guardian has a hard rule: it can *suggest* scope reduction, but it cannot *schedule* anything. It flags. You decide.

---

## 7. Layer 5: Surfaces — Where You Actually Touch It

### 7.1 Obsidian (Primary Knowledge Interface)

Obsidian remains the right choice, but for a specific reason: the graph view maps directly onto Synapse's concept graph, and the plugin ecosystem means you can build custom surfaces without leaving the tool.

Required plugins: Dataview (for dynamic queries), Templater (for node creation templates), Canvas (for visual synthesis sessions), Periodic Notes (for Delta Briefing delivery).

Folder structure:
```
SYNAPSE/
├── inbox/          ← everything lands here first
├── concepts/       ← CONCEPT and FACT nodes
├── builds/         ← BUILD nodes (code, designs, pitches)
├── bridge/         ← INSIGHT nodes (cross-context connections)
├── courses/
│   ├── ICS1103-calc/
│   ├── ICS1104-discrete/
│   └── [other units]/
├── xtension/
│   ├── nexus/
│   ├── signal/
│   └── strategy/
├── daily/          ← Delta Briefings + daily notes
└── archive/        ← processed raw captures
```

### 7.2 Mobile Widget (Capture-First Interface)

The mobile surface has exactly one primary function: capture. Everything else is secondary.

The widget home screen shows:
- One-tap text capture (opens single text field — type, send, done)
- Voice capture as secondary option (one tap below)
- Current Foreground task
- Today's 3 retention review cards (spaced repetition)
- One open question from the graph

It deliberately shows nothing else. No full inbox view, no calendar, no notifications. The phone is for input and brief review. Deep work happens at the desk.

### 7.3 The Weekly Alignment Review (30 Minutes, Non-Negotiable)

This is the human layer that prevents the system from becoming a sophisticated way to feel productive while achieving nothing.

Every Sunday, 30 minutes:

1. **Scout review** (5 min) — Accept/reject proposed graph additions
2. **Bridge audit** (10 min) — Review INSIGHT nodes generated this week. Are they real connections or false pattern-matching? Rate them.
3. **Retention check** (5 min) — Which concepts are you actually retaining vs. just reviewing?
4. **Strategist planning** (10 min) — Next week's collision detection and Synergy Window identification

The Alignment Review generates a `week_N_review.md` that becomes a node in the graph itself. Over a semester, this becomes a high-fidelity record of how your thinking evolved.

---

## 8. The Agent Gateway (The Infrastructure Layer)

This is the section that turns Synapse from a collection of scripts into a coherent system — and the section where building clean from scratch pays off.

Synapse needs an agent gateway: a layer that handles tool registration, auth, orchestration, and inter-agent communication. Rather than bolting this onto an existing architecture, Claude Code will build it purpose-first with the following design:

### 8.1 What the Gateway Does

Every Synapse agent (Librarian, Synthesizer, Strategist, Critic, Scout, Guardian) exposes a clean API endpoint. The gateway sits in front of all of them and handles:

- **Auth** — OAuth 2.0 via a lightweight wrapper. Connects to Gmail (professor emails), Google Calendar (deadlines), and GitHub (build log) without ever storing raw credentials
- **Tool routing** — An agent can call another agent or an external service through the gateway, not directly. This means every inter-agent call is logged, rate-limited, and auditable
- **Context injection** — Before routing a request to any agent, the gateway injects the current session state (Layer 3) so agents always have working memory without each one maintaining its own state
- **Resilience queuing** — Requests that fail due to transient connectivity issues are queued locally and replayed automatically. The gateway is the single point where retry logic lives, so individual agents don't need to implement it

### 8.2 Why Claude Code Builds This

Building the gateway spec-first with Claude Code means it is designed for its actual purpose from day one — not adapted from something else, not over-engineered for hypothetical futures. Clean architecture, clean codebase.

Specifically: the gateway is built as a FastAPI service with a SQLite-backed job queue, OAuth token storage encrypted at rest, and a simple tool registry where each agent registers its capabilities on startup. No vendor lock-in. No mandatory cloud dependency for core orchestration. Runs locally. Deploys to a VPS when ready.

### 8.3 The SIGNAL Connection

Concrete example of the gateway in action: The Librarian ingests a forwarded email from Dr. Chepkorir about an upcoming CAT. The gateway routes this through the Gmail OAuth integration, the Librarian creates an EVENT node linked to the ICS1103 concept cluster, and the Strategist automatically flags a pre-CAT review block in the next 72-hour Horizon. Zero manual steps.

This is not just convenient. It is the proof-of-concept for SIGNAL — the exact email triage and context-surfacing flow you are building for lecturers is the same architecture you use for yourself. **Synapse is SIGNAL's internal prototype.** Every week you use it, SIGNAL's design gets sharper.

### 8.4 The Xtension Moat

The gateway is also Xtension's first real infrastructure asset. A purpose-built agent gateway with clean tool registration patterns, encrypted credential management, and offline-capable queuing is exactly the kind of primitive the IoA layer needs. What gets built for Synapse gets extracted for Xtension. The personal system and the product are the same system at different scales.

---

## 9. Implementation Roadmap (Constraint-Honest)

No Monte Carlo simulations. No theoretical architectures that require 6 months to build before delivering value. This roadmap is built around the constraint that you have lectures, startup commitments, and finite energy.

### Phase 0: The Minimal Viable Brain (Week 1-2)
*Goal: Capture without friction. Nothing else.*

- Create Obsidian vault with the folder structure above
- Install: Dataview, Templater, Periodic Notes
- Set up text capture: Telegram bot → auto-post to `inbox/` (one evening, ~30 lines of Python)
- Set up email forward address (Cloudflare Email Routing → free)
- Set up clipboard daemon (simple Python script, 50 lines)
- One daily habit: 10-minute inbox triage before sleep

Deliverable: You never lose an idea again. That's it. That's enough for Week 1.

### Phase 1: The Graph Comes Alive (Weeks 3-6)
*Goal: Concepts, not documents. The Librarian.*

- Build the Librarian as a Python script (not a full agent yet): reads inbox, uses Claude API to extract concepts, creates Obsidian notes with proper links
- Create your first 20 CONCEPT nodes manually (core ICS1103 + ICS1104 concepts)
- Create your first 5 BUILD nodes (Strathmore Maps, SIGNAL draft, Synapse itself)
- Link them. Find your first real INSIGHT node.
- Begin sketching the agent gateway spec (the architecture, not the code yet)

Deliverable: A knowledge graph with 50+ nodes and 100+ edges. Your first cross-context insight documented.

### Phase 2: The Retention Engine (Weeks 7-10)
*Goal: Actually remember what you learn. The Synthesizer.*

- Build Delta Briefing: daily cron job, Claude API call with your graph as context, outputs to `daily/` and reads aloud via TTS on mobile
- Add spaced repetition state to CONCEPT nodes
- Build the review card generator (application-style questions, not rote recall)
- Measure: take ICS1104 practice exam before and after 2 weeks of Synapse reviews

Deliverable: Measurable retention improvement. You have data, not faith.

### Phase 3: The Strategic Layer (Weeks 11-14, end of Semester 1)
*Goal: Navigate exam season without burning out. The Strategist + Guardian.*

- Build Strategist: weekly planning script, pulls from Google Calendar via the gateway's OAuth layer, generates tradeoff analysis
- Build Guardian: simple burnout heuristics on interaction logs
- Conduct a full Synapse-assisted CAT preparation cycle

Deliverable: You finish Semester 1 with grades you can be proud of AND Xtension still moving. That's the proof of concept.

### Phase 4: The Full Stack (Semester 2+)
*Goal: The system you described in this document, fully operational.*

- Critic + Scout agents
- Full agent gateway operational (Claude Code build)
- Startup Mirror automation via Git hooks
- Mobile widget (React Native or PWA)
- Begin extracting gateway + agent patterns as Xtension infrastructure products

---

## 10. What Synapse Is Actually Building Toward

By the end of Year 1 at Strathmore, Synapse should be two things simultaneously:

**For you:** A cognitive system that made your BICS degree and Xtension's founding year genuinely compound on each other — where studying Discrete Math made your agent architecture better, and building Synapse gave you intuitions no textbook could provide.

**For Xtension:** A live prototype of the "personal agent layer" that sits above IoA infrastructure. The consent patterns in the gateway, the context management in Synapse's working memory layer, the agent orchestration in the Reasoning layer — these are not student projects. They are the foundations of a defensible product.

The path from "personal second brain" to "institutional intelligence product" is direct. SIGNAL for lecturers is Synapse without the startup layer. The same architecture. The same agent patterns. Your own experience using it is your most credible case study.

---

*Last updated: May 2026 | Wint3rX / Xtension Labs*
*Architecture version: 2.0*
*Next review: End of Semester 1*
