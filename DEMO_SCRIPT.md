# Demo video script (~3:00) — Restaurant GM

## Pre-roll setup (NOT recorded, ~15 min)
1. `./start.sh reset && ./start.sh` — pristine DB, all services up
2. Run **two** sim days at "1 day ≈ 2 min" via ▶:
   - Day 1: stock dips → autonomous reorders fire; a promo rec likely appears
     (pace or surplus trigger). **Approve it** → outreach → redemptions tick.
   - Day 2: morning deliveries land (🚚), the promo collects more redemptions,
     then expires next morning → becomes a Past promo with real
     predicted-vs-actual numbers.
3. Confirm you now have: a past promo with redemptions, PO history, waste pill,
   14+ days in the P&L chart, agent activity entries.
4. Open the **hosted URL** in a clean browser window (no bookmarks bar),
   QuickTime/OBS at 1080p. Keep the chat panel visible.

## Recording flow (Day 3, knob at "1 day ≈ 2 min")

| Time | Shot | Narration (gist) |
|---|---|---|
| 0:00–0:20 | Dashboard, idle | "This is Restaurant GM — an agentic operations manager for an independent restaurant. Five Gemini agents on Google's Agent Development Kit, with MongoDB Atlas as the nervous system: every panel you see is a live change stream." |
| 0:20–0:45 | Click ▶ Run a day; clock snaps to 12 AM → 10 AM; order stream starts | "One button simulates a full service day. Orders stream in, stock depletes through recipe BOMs, the P&L updates — all deterministic plumbing reacting to MongoDB change streams." |
| 0:45–1:15 | Low-stock pills appear → ⚙️ working → 🤖 inventory explanation toast + activity entry → PO row appears | "Watch the inventory agent: plumbing detects the threshold, the AGENT decides — it reads velocity, waste history, cash on hand, and explains its reorder in its own words. It writes through the MongoDB MCP server, and unique indexes make duplicate orders physically impossible." |
| 1:15–1:30 | Chat: type "what's running low?" → 1-tool-call answer | "The GM can ask anything — one snapshot call, grounded in live data." |
| 1:30–2:15 | Promo rec card appears (or use the pending one) → PAUSE the sim → walk the card: evidence with source tables, targeting pills, prediction → click **Approve** → "configuring" → 🎉 live → 📣 notified → 🎟 redemptions in order stream | "The core: agents propose promotions with real evidence — every number cites its source collection. But customer-facing actions need a human. I approve on the dashboard… billing configures it, outreach notifies the targeted segment, and redemptions start landing — watch actual uptake track against the agent's prediction." |
| 2:15–2:40 | Expand the PAST promo: redeemed X / Y notified, actual vs predicted; point at waste pill + financials chart | "Promos are measured, not just launched — predicted versus actual, per promo. Some flop, and the system knows. Same with waste: spoilage is tracked, and the agent buys less of what we throw away." |
| 2:40–3:00 | Zoom out to full dashboard, URL visible | "MongoDB change streams drive the autonomy, the MCP server carries every agent write, ADK runs the agents, Cloud Run hosts it. It's live at this URL — press Run a Day and watch it manage the restaurant yourself." |

## Tips
- Use **⏸ Pause** while narrating the recommendation card — the gate moment
  deserves 20 unhurried seconds.
- If no promo rec fires during recording, ask in chat: "should we run a promo?"
  (Central will produce one and point you to the dashboard card).
- Worst case, record panels as separate takes and cut — Devpost allows edited
  video. Keep TOTAL ≤ 3:00.
