# Restaurant GM — Devpost Submission

## Tagline
An agentic general manager for independent restaurants: five Gemini agents on Google ADK that watch the order stream through MongoDB Change Streams, manage inventory, fight food waste, and propose data-backed flash promos — with a human approving every customer-facing move.

---

## Inspiration

Independent restaurants run on instinct. When to reorder, what to promote, what's quietly rotting in the walk-in — most of those calls happen on gut feel at the end of a 14-hour shift. Chains have analysts and ops teams. The taqueria on the corner has a tired owner.

We wanted to build something that does the analyst work continuously and shows its evidence, while keeping the owner in charge of anything a customer actually sees. Not a chatbot. A genuine second pair of eyes that never sleeps.

---

## What it does

**Live dashboard** — one URL, always on. Order stream ticking in real time, shift revenue and covers, cash on hand, daily P&L with margins, live inventory with spoilage tracking, every purchase order, and a feed of agent actions in the agents' own words.

**Autonomous inventory** — when stock drops below its reorder point, the inventory agent reviews velocity, waste history, and available cash before acting. It orders, right-sizes, or holds back ("we threw away $38 of this last week, holding the reorder"). It also flags dead stock: if an ingredient has near-zero sales velocity or weeks of cover sitting on the shelf, it won't pile on more.

**Evidence-backed promotions** — triggers like a sales-pace deviation or a surplus ingredient wake up the central orchestrator. The billing agent builds a recommendation where every number cites its source collection in MongoDB, picks a strategy (ride the wave / boost the underdog / clear the surplus), and selects a real audience segment based on customer affinity, deal-seeking behavior, or open-to-walk-ins.

**Human gate** — promos go live only when the GM clicks Approve on the dashboard. Then billing configures the promotion, outreach targets the right opted-in customers, and redemptions are reconciled live with predicted vs. actual uptake shown per promo. Some promos flop. The system shows it honestly.

**Self-serve demo** — a simulator knob on the dashboard runs a full service day in about two minutes. Anyone can drive it.

---

## How we built it

### Agents

Five agents on Google ADK, all running `gemini-3.5-flash` on Vertex AI:

- **Central** — orchestrator. Coordinates the four specialists, validates that every recommendation is grounded in real data, and owns the human approval gate.
- **Inventory** — monitors stock, derives the 86 list, evaluates reorder decisions with full financial context, and places purchase orders via MCP.
- **Order-mgmt** — a sales analyst over the live order stream: velocity, top movers, pace vs. baseline.
- **Billing** — computes margins, builds promo recommendations with a strategy, justification, and predicted lift, and configures approved promotions.
- **Outreach** — targets opted-in customers by criteria (affinity, segment, open-to-all), sends campaign notifications, and the reconciliation is handled by deterministic plumbing.

We chose the agent framework per task rather than one-size-fits-all: ReAct for Central and Billing (where the next action depends on what just came back), Evaluator–Optimizer for Billing's guardrail validation, and fixed workflow for Inventory, Order-mgmt, and Outreach where a loop adds no real value.

### MongoDB as the nervous system

MongoDB Atlas isn't just storage here — it's the coordination layer.

**Change Streams drive everything.** Six deterministic plumbing listeners react to writes in real time: BOM-based stock depletion, replenishment on PO delivery, metric rollups, daily P&L, redemption reconciliation, and spoilage. The dashboard's SSE feed and the autonomous trigger worker are also change stream listeners.

**MongoDB MCP server is the agents' write path.** Every purchase order, recommendation, promotion, and campaign send is an agent-composed MCP call. Agents never write directly — they go through the MCP server, and we set a per-agent `tool_filter` so each agent can only touch the collections it's supposed to touch.

**Unique indexes enforce invariants against LLM nondeterminism.** One open PO per ingredient. One promo per recommendation. Racing concurrent agent runs physically cannot double-act, because the database won't allow it.

**Star schema for the dashboard.** `orders` is the fact table at line-item grain, surrounded by dimension collections. A serving layer (`live_metrics`, `financials`) is materialized from the facts so the always-on dashboard reads cheap summaries instead of re-aggregating everything on every refresh.

### The core design rule

*Detection is plumbing. Decisions are agents. Invariants live in the database.*

Deterministic code owns the high-frequency, per-order work: order ingestion, BOM-based stock depletion, base metric rollups, daily P&L, and redemption reconciliation. LLM agents own judgment. We also pre-compute all the arithmetic (sums, margins, PO quantities, date math) in deterministic helper tools so the model never does the math itself — it just reads the result and decides what to do with it.

### Serving

FastAPI (ADK's `get_fast_api_app`) serves the React dashboard statically and exposes the ADK chat endpoint over SSE. A second Cloud Run service runs always-on with CPU always allocated — that's the plumbing listeners and the agent trigger worker. Two services: one handles traffic, one handles autonomous work.

### Simulation

A calibrated demand model: Poisson arrivals with lunch and dinner peaks, item popularity learned from historical baseline data, customer affinity (regulars order their favorites ~3x more often), and a promo demand lift where roughly a third of promos genuinely flop. Predicted-vs-actual is a real measurement, not theater.

---

## Challenges we ran into

**LLM nondeterminism meets a database.** Temperature-0 models copy example IDs from the prompt instead of generating random ones — we ended up with colliding `po_00000001`s causing duplicate key errors and broken retry loops. The fix: plumbing pre-generates all business IDs before handing them to the agent; unique indexes make duplicates physically impossible.

**Gemini writing Python instead of making tool calls.** When pipelines got complex enough, Gemini started wrapping MCP calls in `print(default_api.aggregate(...))` — generating code strings instead of calling the tool. The fix: move all fixed-workflow reads into deterministic helper tools that take trivial or no arguments. The model passes a flag, gets back a fully precomputed result, and never has to author a complex aggregation pipeline.

**Sim-time vs wall-clock leaks everywhere a timestamp hides.** Nearly every datetime comparison in the codebase had to be audited to make sure it was using the simulated clock, not the system clock.

**Keeping the dashboard honest.** Every derived number needed exactly one writer, one source of truth. The moment two processes could write the same field, they'd race and the UI would flicker or show stale data.

---

## What we're proud of

A complete closed loop — detect, decide, act, human gate, measure, learn — that's observable live by anyone with one URL. Transparency isn't a feature we added on top; it's the core design. Every recommendation shows its evidence with source tables. Every autonomous action is narrated by the agent itself. The dashboard is the product, not a debug view.

---

## What's next

- Real POS integration (the simulator is a stand-in for a Toast or Square feed)
- Agentic post-mortems on past promos: why it worked, why it flopped, what to change next time
- Financial realism: rent and labor drains making cash a real constraint so the agents have to negotiate priorities
- Vector search over customer and order notes for richer targeting

---

## Built with

`google-agent-development-kit` · `gemini-3.5-flash` · `vertex-ai` · `mongodb-atlas` · `mongodb-change-streams` · `mongodb-mcp-server` · `cloud-run` · `fastapi` · `react` · `python`

---

## Links

- **Hosted URL**: https://restaurant-gm-app-827886453598.us-central1.run.app
- **Repo**: https://github.com/karthikuppuluri/restaurant_gm_agent
- **Video**: https://youtu.be/XJsRg_W5fQs
