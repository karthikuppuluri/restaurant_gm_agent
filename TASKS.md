# TASKS.md — Claude Code prompt briefs

One brief per session. Paste the brief as the task prompt. Each assumes
CLAUDE.md and README.md are in the repo root and have been read.

Status: seed data done (task 2). Briefs start at task 3.

---

## Task 3 — Order simulator

**Goal:** A standalone script `plumbing/simulator.py` that inserts realistic
order documents into `orders` on a statistical clock.

**Requirements:**
- Plain Python + pymongo. No LLM, no MCP, no ADK.
- Arrival model: Poisson-style arrivals with a time-of-day rate curve having
  lunch (~12:00) and dinner (~19:00) peaks. Hourly rate table is fine.
- `SIM_SPEED` env var: clock multiplier (60 = one sim-hour per real minute).
- Order builder: ~60% orders linked to a random `customers` doc, 40%
  `customer_id: null`. 1–4 line items, item choice weighted by `tags`
  (`popular` weighs more). Quantities 1–6.
- Money: integer cents only. Tax from `pricing_rules.tax_rate_bps`. Totals
  precomputed per the README `orders` schema. State `COMPLETED`.
- Promo awareness: before building each order, check for a `promotions` doc
  with `status: "live"` and valid time window. If the chosen customer has a
  `campaign_sends` doc for that promo, apply the discount to matching line
  items with probability `(1 - price_sensitivity)` inverted appropriately —
  i.e., more price-sensitive customers redeem MORE. Set `promo_id` and
  `applied_discount_money` on those line items and reflect it in totals.
- Log one line per inserted order to stdout.

**Out of scope:** stock depletion, live_metrics, any reads/writes beyond
`orders` (read-only: customers, menu_items, pricing_rules, promotions,
campaign_sends).

**Verify:**
- Run 5 sim-hours; a quick matplotlib or text histogram of orders/hour shows
  two peaks.
- Every `line_items[].item_id` exists in `menu_items`.
- All money fields are ints; totals recompute correctly for 3 sampled orders.

---

## Task 4 — Depletion plumbing

**Goal:** `plumbing/depletion.py` — a change-stream listener that depletes
stock when an order is inserted.

**Requirements:**
- pymongo change stream on `orders` (insert events only).
- For each line item: look up the menu item's `recipe_id`, read the recipe's
  `ingredients`, decrement `raw_ingredients.on_hand_qty` by
  `qty_per_serving * line_item.quantity` using atomic `$inc`.
- Update `updated_at` on touched ingredient docs.
- Idempotency guard: mark processed orders (e.g. set `depleted: true` on the
  order) and skip already-marked ones, so a restart doesn't double-deplete.

**Out of scope:** metrics, alerts, reordering, any LLM.

**Verify:**
- Hand-insert one order with known quantities; on_hand_qty drops by exactly
  the BOM math.
- Restart the listener and re-run; no double depletion.

---

## Task 5 — Base metric rollups

**Goal:** `plumbing/rollups.py` — keep `live_metrics` base fields current.

**Requirements:**
- Same change stream (can share the listener process with depletion or run
  separately — keep whichever is simpler).
- On each order insert, update the singleton `live_metrics` doc:
  `shift_revenue_money` (+= order total), `covers` (+= guest_count),
  `as_of` (now). Upsert if missing.
- Shift boundary: a `--reset` flag that zeroes the base fields.

**Out of scope:** agent-written fields (margins, pace, top_movers, low_stock,
86 list, promo perf). Plumbing never writes those.

**Verify:**
- Run simulator 5 sim-hours; `live_metrics` totals equal an independent
  aggregation query over the same window.

---

## Task 6 — MCP wiring

**Goal:** `matchday_gm/tools.py` — one factory that returns a MongoDB
`McpToolset` for a given agent, with per-agent tool filters.

**Requirements:**
- `McpToolset` launching `npx -y mongodb-mcp-server --connectionString $MONGODB_CONNECTION_STRING`
  via `StdioConnectionParams` (sync, module top-level pattern).
- First: run the server once and LIST its actual tool names; do not trust
  guessed names. Record the real names in a comment.
- A `TOOL_FILTERS` dict keyed by agent name implementing the README's
  per-agent read/write summary (e.g. order_mgmt gets find/aggregate +
  update for live_metrics only — if per-collection filtering isn't supported
  by tool_filter, enforce collection boundaries in the agent instruction and
  note this limitation in a comment).
- Connection string from env. Never hardcode.

**Verify:**
- A throwaway ADK agent using the factory can `find` one document from
  `menu_items` via `adk web`.

---

## Task 7 — Order-mgmt agent

**Goal:** `matchday_gm/order_mgmt_agent.py` — the sales analyst. Simplest
agent; proves the whole stack.

**Requirements:**
- ADK `LlmAgent`, workflow style (single pass, no loop). Gemini flash-tier
  model.
- Instruction: aggregate the last N sim-minutes of `orders`; compute sales
  pace vs the same window on prior days (the seeded history is the
  baseline); identify top 3 movers.
- Writes: `live_metrics.sales_pace_vs_baseline_pct` and `top_movers`;
  one `agent_events` doc (action `analyze_sales`, summary, reasoning).
- Tools: via task 6 factory, filter `order_mgmt`.

**Out of scope:** recommendations, stock, anything Billing/Inventory owns.

**Verify:**
- In `adk web`, ask "what's selling right now?" — numbers match a manual
  aggregate run side by side.
- Exactly one agent_events doc per invocation.

---

## Task 8 — Inventory agent

**Goal:** `matchday_gm/inventory_agent.py` — stock monitor + reorder
judgment.

**Requirements:**
- Workflow + one judgment call.
- Reads raw_ingredients, recipes, vendors, recent orders (velocity).
- Derives: low-stock list (on_hand vs reorder_point, plus hours-of-cover
  from current velocity) and the 86 list (menu items whose recipe needs a
  depleted ingredient). Writes both to `live_metrics`.
- Judgment: for ingredients below reorder_point, choose a vendor from
  `vendors.supplies` (price vs lead time) and insert ONE well-formed
  `purchase_orders` doc (status `placed`, money in cents, FKs valid).
- Guard: do not create a duplicate PO if an open one already exists for the
  same ingredient.
- Logs to agent_events (`flag_86`, `place_po`).

**Verify:**
- Manually set one ingredient below reorder_point → correct 86 items derived
  through recipes; one PO inserted; second run creates no duplicate PO.
- With healthy stock → no PO, empty 86 list.

---

## Task 9 — Billing agent (centerpiece — budget 2x time)

**Goal:** `matchday_gm/billing_agent.py` — margin analysis + the justified
promo recommendation, with self-check.

**Requirements:**
- ReAct + Evaluator–Optimizer: draft → validate against `pricing_rules` →
  refine if violating → only then insert.
- Reads: orders, menu_items, recipes, raw_ingredients, pricing_rules,
  customers.
- Produces ONE `promotion_recommendations` doc per invocation matching the
  README schema exactly:
  - `proposal` with `target_criteria` (loyalty_tier / max_price_sensitivity /
    city / dietary_flags as appropriate — agent-chosen).
  - `justification.analytical`: 3+ metrics, each with real `value` computed
    from the DB and its `source_table`. NEVER fabricated (CLAUDE.md
    invariant 6).
  - `justification.predictive`: predicted_uptake (scaled by target group's
    price_sensitivity distribution), predicted_incremental_revenue_money,
    predicted_margin_after_pct, confidence, model_note.
- Guardrails enforced: margin-after >= min_margin_pct, discount <=
  max_discount_pct, no blackout items.
- Also writes `live_metrics.gross_margin_pct` and an agent_events doc.

**Verify:**
- Each analytical metric independently recomputable from its source_table.
- Force a violation (set max_discount_pct = 10, prompt for 20% off) → agent
  refines to a compliant proposal rather than surfacing the violation.
- Recommendation status is `pending`; decided_by/resulting_promo_id null.

---

## Task 10 — Central orchestrator + approval glue

**Goal:** `matchday_gm/agent.py` (root_agent) + `plumbing/triggers.py`.

**Requirements:**
- Central: ADK manager agent with sub_agents = [order_mgmt, inventory,
  billing, outreach]. ReAct instruction: decide whether a promo evaluation
  is warranted (from live_metrics + recent agent_events), delegate in
  sequence (context from order-mgmt/inventory feeds billing), quality-gate
  the recommendation (justification complete, source_table present) and
  send back to billing if not.
- Approval glue (plumbing): a small function/endpoint that takes
  (recommendation_id, yes/no, decided_by), flips status to
  approved/rejected with decided_at, and on approval re-invokes Central
  with an "execute approved promo" message.
- Central never executes a promo without an approved status (invariant 5).

**Verify:**
- End-to-end in adk web: one invocation produces a pending recommendation;
  calling the trigger with yes flips it and wakes Central, which routes to
  Billing (configure promotions) — even if Outreach (task 11) is stubbed.

---

## Task 11 — Outreach agent

**Goal:** `matchday_gm/outreach_agent.py` — targeted push on approval.

**Requirements:**
- Workflow style. Triggered by Central after approval.
- Billing has created the `promotions` doc; Outreach reads it +
  `promotion_recommendations.target_criteria`.
- Finds customers matching target_criteria AND `opt_in_marketing: true`.
- Inserts one `campaign_sends` doc per recipient (channel sms/push/email,
  status `sent`, redeemed false, redeemed_order_id null).
- Logs agent_events (`push_campaign`, includes recipient count).

**Verify:**
- Zero sends to opt_in_marketing: false (query to prove).
- All recipients match every criterion in target_criteria.
- Send count == matching customer count.

---

## Task 12 — Redemption reconciliation

**Goal:** `plumbing/reconcile.py` — close the predicted-vs-actual loop.

**Requirements:**
- Change-stream (or short-interval poll) over `orders` inserts carrying a
  `promo_id` in any line item.
- For each: increment `promotions.redemption_count`; if the order's
  customer has a campaign_sends doc for that promo, set redeemed: true and
  redeemed_order_id.
- Maintain `live_metrics.active_promo_perf` = [{promo_id, predicted_uptake,
  actual_redemptions}] for live promos.
- Idempotent (same guard pattern as task 4).

**Verify:**
- With the simulator's promo-awareness on, run 10+ redemptions:
  redemption_count exact, campaign_sends flags consistent, dashboard doc
  shows predicted vs actual side by side.

---

## Task 13 — GM dashboard

**Goal:** `dashboard/` — the always-on view.

**Requirements:**
- Simple web app (keep the stack minimal; reading Mongo + polling
  live_metrics every few seconds is fine — no websockets needed for the
  demo).
- Panels: shift revenue + covers + gross margin; live order feed (latest
  orders); sales pace + top movers; low stock + 86 list; recommendation
  cards; active promo strip (predicted vs actual).
- Recommendation card renders the FULL justification: every analytical
  metric with its source_table, the predictive block, and Approve/Reject
  buttons wired to task 10's trigger.
- No writes from the dashboard except via the approval trigger.

**Verify:**
- With simulator + plumbing + agents running, numbers visibly update.
- Clicking Approve drives the real task-10 flow; card status changes;
  campaign sends appear.

---

## Task 14 — Financials rollup

**Goal:** Shift-end P&L written by Billing.

**Requirements:**
- Extend Billing (or a Central-invoked step) to write one `financials` doc
  per period per the README schema: gross revenue, COGS (orders x recipes
  ingredient costs), discounts, net, margin pct, by_category breakdown.
- Money in cents; period_id like `2026-06-14:dinner`.

**Verify:**
- COGS recomputed independently (script) matches the doc to the cent.

---

## Task 15 — Deploy + demo

**Goal:** Public Cloud Run URL + 3-minute demo recording.

**Requirements:**
- `adk deploy cloud_run --with_ui` for the agents. The container MUST
  include Node.js (the MongoDB MCP server runs via npx) — custom Dockerfile
  if the autogenerated image is Python-only.
- Dashboard deployed as its own Cloud Run service.
- Secrets via Secret Manager, not baked into images.
- Demo path: simulator on (sped up) → dashboard fills → recommendation
  appears with justification → human approves → outreach fires → redemptions
  tick up against the prediction.

**Verify:**
- Full demo path runs on the DEPLOYED instance, not just locally.
- Repo public, MIT license visible in About, Devpost form complete.
