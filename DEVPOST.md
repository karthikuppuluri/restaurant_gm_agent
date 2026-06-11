# Devpost submission draft — Restaurant GM

## Tagline (one line)
An agentic general manager for independent restaurants: five Gemini agents on
Google ADK that watch the order stream through MongoDB change streams, reorder
stock, fight food waste, and propose evidence-backed flash promos — with a
human approving every customer-facing move.

## Inspiration
Independent restaurants run on instinct: when to reorder, what to promote,
what's quietly rotting in the walk-in. Chains have analysts; the taqueria on
the corner has a tired owner at 1 AM. We wanted agents that do the analyst
work continuously — and show their evidence — while the owner keeps the final
say on anything a customer sees.

## What it does
- **Live dashboard** (one URL): order stream, shift revenue/covers, cash on
  hand, daily P&L with margins, live inventory with spoilage tracking, every
  purchase order, and a feed of agent actions *in the agents' own words*.
- **Autonomous inventory**: when stock crosses its reorder point, the inventory
  agent reviews velocity, waste history, and cash — then orders, right-sizes,
  or refuses ("we threw away $38 of this last week") and explains why.
- **Evidence-backed promotions**: triggers like sales-pace deviation or surplus
  stock wake the Central agent; the billing agent builds a recommendation where
  every number cites its source collection, picks a strategy (ride the wave /
  boost the underdog / clear the surplus) and a real audience segment
  (taco-lovers affinity, deal-seekers, open-to-walk-ins).
- **Human gate**: promos go live ONLY when the GM clicks Approve on the
  dashboard. Then billing configures it, outreach notifies the targeted
  opted-in customers, and redemptions are reconciled live — **predicted vs
  actual uptake, per promo**. Some promos flop; the system shows it honestly.
- **Self-serve demo**: a knob on the dashboard runs a full simulated service
  day in ~2 minutes. Judges can drive it themselves.

## How we built it
- **Agents**: Google **Agent Development Kit** — Central orchestrator with
  AgentTool/sub-agent routing, parallel analysis, and four specialists
  (inventory, order-mgmt, billing, outreach) on **gemini-3.5-flash** (Vertex).
- **MongoDB Atlas is the nervous system, not just storage**:
  - **Change Streams** drive everything: six deterministic plumbing listeners
    (BOM stock depletion, replenishment, metric rollups, daily P&L, redemption
    reconciliation, spoilage) plus the dashboard's SSE feed plus the autonomous
    trigger worker — all react to writes in real time.
  - **MongoDB MCP server** is the agents' write path: every purchase order,
    recommendation, promotion, and campaign send is an agent-composed MCP call.
  - **Unique indexes enforce business invariants against LLM nondeterminism**:
    one open PO per ingredient, one promo per recommendation — racing agent
    runs physically cannot double-act.
  - Star schema: `orders` fact table at line-item grain, dimensions (menu,
    recipes/BOM, customers, vendors), serving layer (`live_metrics`,
    `financials`) materialized for the always-on dashboard.
- **Division of labor** (our core design rule): *detection is plumbing,
  decisions are agents, invariants live in the database.* Deterministic code
  owns per-order math; agents own judgment; helper tools hand agents
  precomputed evidence so a model never does arithmetic.
- **Serving**: FastAPI (ADK's `get_fast_api_app`) + React dashboard on
  **Cloud Run**; a second always-on Cloud Run service runs the listeners and
  the agent-trigger worker.
- **Simulation**: a calibrated demand model (Poisson arrivals with lunch/dinner
  peaks, empirical item popularity, customer-favorite affinity, promo demand
  lift where ⅓ of promos genuinely flop) so predicted-vs-actual is a real
  measurement, not theater. All data is simulated.

## Challenges we ran into
- LLM nondeterminism meets a database: temperature-0 models copy example IDs
  instead of inventing random ones (we got colliding `po_00000001`s) and retry
  inserts. Fix: plumbing pre-generates all business IDs; unique indexes make
  duplicates impossible; the worker is idempotent with crash catch-up.
- Gemini wrapping tool calls in `print(default_api…)` Python when pipelines got
  complex — solved by moving all fixed-workflow reads into deterministic helper
  tools, cutting agent tool calls from ~8 to 1-2 per task.
- Sim-time vs wall-clock leaks everywhere a timestamp hides.
- Keeping a dashboard honest: every derived number needed exactly one writer.

## Accomplishments we're proud of
- A complete closed loop: detect → decide → act → human gate → measure →
  **learn** (the agent buys less of what gets thrown away).
- Transparency as a feature: every recommendation carries its evidence with
  source tables; every autonomous action is narrated by the agent itself.
- The whole thing is observable live, by anyone, with one button.

## What's next
- Real POS integration (the simulator is a stand-in for a Toast/Square feed)
- "✨ Analyze" retro agent on past promos (why it worked / flopped, what to
  change), financial realism (rent/labor drains making cash a real constraint),
  vector search over customer/order notes for richer targeting.

## Built with
google-agent-development-kit · gemini-3.5-flash · vertex-ai · mongodb-atlas ·
mongodb-change-streams · mongodb-mcp-server · cloud-run · fastapi · react · python

## Links
- Hosted URL: <CLOUD RUN URL>
- Repo: https://github.com/karthikuppuluri/restaurant_gm_agent
- Video: <YOUTUBE/LOOM LINK>
