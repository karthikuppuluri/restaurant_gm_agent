# CLAUDE.md — Restaurant GM

Hand-off doc for Claude Code. **Read this fully before touching the repo.**
`README.md` is the source of truth for the schema; this file is the source
of truth for how to behave and what not to break.

---

## Prime directive

**Plan before you change anything. Make the smallest change that solves the task.**

- Before any non-trivial change, state a short plan and wait if anything is ambiguous.
- Do not make "crazy changes," sweeping refactors, or architectural moves unless explicitly asked.
- Minimal diffs only. Every changed line should trace to the request. When in doubt, do less and ask.

---

## What we're building

An agentic assistant that helps an independent restaurant owner decide **when and
what flash promo to run**, grounded entirely in the restaurant's own data, with
the human approving every promo. A central orchestrator coordinates four
specialist agents. The product's core value is **transparency**: every promo
recommendation comes with real-data evidence and a prediction, and ends in a
human yes/no.

This is a hackathon project on the **MongoDB partner track** (Google Cloud Agent
hackathon). **All data is simulated.**

## Stack

- **Reasoning:** Gemini 3 via the **Agent Development Kit (ADK)**, Python.
- **Data:** MongoDB Atlas. Agents reach it through the **MongoDB MCP server**
  (`McpToolset`); deterministic code reaches it through the plain MongoDB driver.
- **Live updates:** MongoDB **Change Streams** drive the order stream and dashboard.
- **Deploy:** Cloud Run (`adk deploy cloud_run --with_ui`). Secrets in `.env`
  locally, Secret Manager when deployed.

## The agents

- **Central** — orchestrates, owns the human-approval gate, logs to `agent_events`.
- **Inventory** — monitors stock, derives the 86 list, reorders via purchase orders.
- **Order-mgmt** — a sales analyst over the order stream (velocity, top movers, pace).
- **Billing** — computes margin/feasibility, builds + justifies + predicts promo
  recommendations, configures approved promotions, rolls up financials.
- **Outreach** — targets opted-in customers, pushes the approved promo, tracks redemptions.

---

## Non-negotiable invariants

Breaking any of these is a regression, even if the code "works":

1. **`menu_items` and `recipes` are static reference data — read-only during
   service. Never write to them.**
2. **86 / availability is derived, never stored on `menu_items`.** It is computed
   from `raw_ingredients` + `recipes` and published to `live_metrics`.
3. **LLM agents never do per-order arithmetic or high-frequency state mutation.**
   Deterministic code (the plumbing) owns: order ingestion, BOM-based stock
   depletion, base-metric rollups, and redemption reconciliation. Agents own
   judgment-driven writes only.
4. **Two DB access paths, don't mix them:** agents write via the MongoDB MCP
   server; plumbing writes via the MongoDB driver / Change Streams.
5. **Human-in-the-loop is mandatory for promos.** Agents produce
   `promotion_recommendations` (with full justification); a thin trigger flips
   status on the human's yes/no and re-enters the agent loop. Agents never
   auto-execute a promo.
6. **Transparency uses real numbers.** Every recommendation's `justification`
   must be pulled from real tables with `source_table` recorded. Never fabricate
   or hardcode a statistic to fill a field.
7. **Conventions (see `DATA_MODEL.md`):** money is integer cents in a Money
   object (never floats); timestamps are RFC 3339 UTC; IDs are prefixed strings;
   foreign keys are string IDs; every doc carries audit fields.
8. **All data is simulated.** Never present seed/placeholder values as if they
   were produced by an agent.

If a task seems to require breaking one of these, **stop and ask** — it usually
means the task is underspecified.

---

## Repo layout

Status: data model is locked (`DATA_MODEL.md`); agents and plumbing are being
built. Intended structure:

```
matchday-gm/
├── CLAUDE.md              # this file
├── DATA_MODEL.md          # schema — source of truth
├── seed_data.py           # simulated data + order-stream simulator
├── requirements.txt
├── .env.example
├── plumbing/              # deterministic, NO LLM
│   ├── depletion.py       # BOM stock depletion on order insert (change stream)
│   ├── rollups.py         # base metrics + redemption reconciliation
│   └── triggers.py        # human approve/reject → wake Central
├── matchday_gm/           # the ADK agents
│   ├── __init__.py        # exposes root_agent
│   ├── agent.py           # Central orchestrator + sub-agent wiring
│   ├── tools.py           # MongoDB McpToolset config + tool_filter per agent
│   ├── inventory_agent.py
│   ├── order_mgmt_agent.py
│   ├── billing_agent.py
│   └── outreach_agent.py
└── dashboard/             # GM always-on UI (reads live_metrics + recommendations)
```

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill MONGODB_CONNECTION_STRING, GOOGLE_API_KEY
python seed_data.py           # populate Atlas with simulated data
adk web                       # local dev UI; pick "matchday_gm"
# deploy:
adk deploy cloud_run --with_ui --project=$PROJECT_ID --region=us-central1 ./matchday_gm
```

Note: the MongoDB MCP server runs via `npx` (Node), so any deploy container needs
Node installed alongside Python.

## Hackathon constraints

- Repo must be **public** with a detectable **OSS license** (Apache-2.0 or MIT)
  visible in the About section.
- Must show a **meaningful MongoDB MCP integration** and **multi-step** agentic
  planning (beyond chat).
- Deliverables: a **hosted URL**, a **public repo**, and a **~3 min demo video**.

---

## Working agreement

Behavioral guidelines (adapted from Andrej Karpathy's CLAUDE.md) to reduce common
LLM coding mistakes. These bias toward caution over speed; for trivial tasks, use
judgment.

### 1. Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs. Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused; leave
  pre-existing dead code unless asked.

The test: every changed line traces directly to the request.

### 4. Goal-driven execution
Define success criteria. Loop until verified.
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

Strong success criteria let you loop independently; weak ones ("make it work")
require constant clarification.

### Working if
Fewer unnecessary changes in diffs, fewer rewrites from overcomplication, and
clarifying questions come *before* implementation rather than after mistakes.
