"""
plumbing/worker.py — autonomous trigger worker (deterministic detection, agent decision).

A single-flight change-stream watcher that invokes Central programmatically via the
ADK Runner with a targeted prompt. The LLM only runs when there is something to
decide; everything else here is plumbing-grade Python.

Three triggers, each with suppression so one condition can't spam LLM runs:

  1. HUMAN DECISION  promotion_recommendations.status -> "approved"
       (flipped by the dashboard's approve button via backend/app.py)
       -> invoke Central: billing Mode B (configure the promo) + outreach (push it).
       "rejected" is just logged.

  2. LOW STOCK       raw_ingredients.on_hand_qty falls below reorder_point
       (depletion plumbing decrements stock on every order)
       suppression: per-ingredient cooldown + stock_snapshot()'s open-PO check —
       if the snapshot suggests no POs, no agent run happens at all.
       -> invoke Central: transfer to inventory_agent to review and place POs.
       (Decided 2026-06-10: reorders are autonomous; the human gate is promos only.)

  3. PACE SIGNAL     live_metrics.sales_pace_vs_baseline_pct beyond +/-15%
       suppression: global cooldown + skip if a pending recommendation or live
       promo already exists.
       -> invoke Central: run the promo evaluation flow (ends at the human gate).

  4. SURPLUS STOCK   raw_ingredients.on_hand_qty >= par_level * SURPLUS_FACTOR
       (overstock = future waste; the classic restaurant promo trigger)
       suppression: same as the pace signal + per-ingredient cooldown.
       -> invoke Central: evaluate a surplus-clearing promo (billing's evidence
          includes surplus_ingredients + surplus_mover candidates).

Single-flight by construction: one process, one loop, triggers handled sequentially.

Usage:
    python -m plumbing.worker
"""

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import config_agent_utils
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pymongo import MongoClient

from restaurant_gm.queries import stock_snapshot

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP = "restaurant_gm"

_WATCHED = ["promotion_recommendations", "raw_ingredients", "live_metrics"]

LOW_STOCK_COOLDOWN_S = 900   # per ingredient
PROMO_COOLDOWN_S = 1800      # global (pace) / per ingredient (surplus)
PACE_TRIGGER_PCT = 15.0
SURPLUS_FACTOR = 1.4         # on_hand >= par * this → surplus promo trigger

_cooldowns: dict[str, float] = {}


def _cooled(key: str, secs: float) -> bool:
    """True (and arms the cooldown) if `key` hasn't fired in the last `secs`."""
    now = time.monotonic()
    if now - _cooldowns.get(key, float("-inf")) < secs:
        return False
    _cooldowns[key] = now
    return True


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[os.environ.get("MONGODB_DB_NAME", "restaurant_gm")]


async def _invoke(runner: Runner, sessions: InMemorySessionService, prompt: str) -> tuple[str, str]:
    """Run Central once with a targeted prompt in a fresh session.
    Returns (author, final_text) of the last agent response."""
    sid = f"worker_{uuid.uuid4().hex[:8]}"
    await sessions.create_session(app_name=_APP, user_id="worker", session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text=prompt)])
    final, author = "", "central"
    t0 = time.monotonic()
    async for ev in runner.run_async(user_id="worker", session_id=sid, new_message=msg):
        parts = (ev.content.parts if ev.content and ev.content.parts else [])
        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc:
                print(f"    [{ev.author}] → {fc.name}")
            if p.text and not getattr(p, "thought", False):
                final, author = p.text, ev.author
    print(f"  agent done in {time.monotonic() - t0:.1f}s: {final.strip()[:300]}")
    return author, final.strip()


def _sim_ts(db) -> str:
    latest = db.orders.find_one({}, {"opened_at": 1}, sort=[("opened_at", -1)])
    return (latest["opened_at"] if latest
            else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _log_start(db, action: str, summary: str, related_ids: dict | None = None) -> str:
    """Two-phase transparency log, phase 1: insert the moment a trigger fires so
    the dashboard reacts INSTANTLY (an agent run takes 30-75s; without this the
    🤖 narration lags the recommendation/promo by that much). Returns event_id."""
    eid = f"evt_{uuid.uuid4().hex[:8]}"
    db.agent_events.insert_one({
        "event_id": eid,
        "agent": "central",
        "action": action,
        "summary": summary,
        "reasoning": "",
        "phase": "started",
        "related_ids": related_ids or {},
        "created_at": _sim_ts(db),
        "source": "worker", "schema_version": 1,
    })
    return eid


def _log_done(db, event_id: str, agent: str, reasoning: str) -> None:
    """Phase 2: fill in the agent's own final response when the run completes."""
    db.agent_events.update_one(
        {"event_id": event_id},
        {"$set": {
            "agent": agent.replace("_agent", "").replace("central_orchestrator", "central"),
            "summary": (reasoning or "(no response)").split("\n")[0][:200],
            "reasoning": reasoning or "(no response)",
            "phase": "done",
        }},
    )


async def _configure_approved(db, runner, sessions, rid: str, decided_at) -> None:
    """Run the post-approval flow (billing Mode B + outreach) for one recommendation.

    Idempotency lives HERE, not in the agent: if a promotions doc already exists
    for this recommendation (a previous run partially succeeded), skip the agent
    entirely and just repair the resulting_promo_id link."""
    existing = db.promotions.find_one({"recommendation_id": rid}, {"promo_id": 1})
    if existing:
        db.promotion_recommendations.update_one(
            {"recommendation_id": rid},
            {"$set": {"resulting_promo_id": existing["promo_id"]}},
        )
        print(f"  {rid} already has promo {existing['promo_id']} — skipped, link repaired")
        return

    eid = _log_start(db, "configure_promo",
                     "Approval received — configuring the promotion and notifying "
                     "customers (≈1 min)…", {"recommendation_id": rid})
    author, text = await _invoke(runner, sessions, (
        f"The GM APPROVED promotion recommendation {rid} "
        f"(its status is already 'approved', decided_at {decided_at}; "
        "do NOT update it again and do NOT ask for approval). "
        "Call billing_agent with: 'Mode B: recommendation "
        f"{rid} was approved — configure the promotions document.' "
        "After billing confirms the promo is live, call outreach_agent with: "
        "'Push the new live promo to eligible customers.' "
        "Then report in 1-2 sentences what was configured and how many "
        "customers were notified."
    ))
    # Repair the rec→promo link if billing skipped its update step — the
    # catch-up keys on resulting_promo_id, so a missing link means a duplicate
    # configure on the next restart.
    promo = db.promotions.find_one({"recommendation_id": rid}, {"promo_id": 1})
    if promo:
        db.promotion_recommendations.update_one(
            {"recommendation_id": rid},
            {"$set": {"resulting_promo_id": promo["promo_id"]}},
        )
    _log_done(db, eid, author, text)


async def _catch_up(db, runner, sessions) -> None:
    """Configure approvals that happened while the worker was DOWN. A change
    stream only sees events while listening — without this, an approval during
    a worker outage silently never becomes a promo."""
    missed = list(db.promotion_recommendations.find(
        {"status": "approved", "resulting_promo_id": None},
        {"recommendation_id": 1, "decided_at": 1},
    ))
    for rec in missed:
        rid = rec["recommendation_id"]
        print(f"▶ catch-up: recommendation {rid} was approved but never configured")
        await _configure_approved(db, runner, sessions, rid, rec.get("decided_at"))


async def _handle(db, runner, sessions, change) -> None:
    coll = change["ns"]["coll"]
    op = change["operationType"]
    doc = change.get("fullDocument") or {}

    if coll == "promotion_recommendations" and op == "update":
        updated = change.get("updateDescription", {}).get("updatedFields", {})
        if "status" not in updated:
            return
        rid = doc.get("recommendation_id")
        status = doc.get("status")
        if status == "approved":
            print(f"▶ trigger: recommendation {rid} APPROVED — configuring promo")
            await _configure_approved(db, runner, sessions, rid, doc.get("decided_at"))
        elif status == "rejected":
            print(f"▶ recommendation {rid} rejected — logged, going idle")

    elif coll == "raw_ingredients":
        ing_id = doc.get("ingredient_id")
        if not ing_id:
            return
        on_hand = doc.get("on_hand_qty", 0)

        if on_hand < doc.get("reorder_point", 0):
            # ── LOW STOCK → autonomous reorder ────────────────────────────────
            if not _cooled(f"low:{ing_id}", LOW_STOCK_COOLDOWN_S):
                return
            # Deterministic pre-check: anything actually orderable (no open PO)?
            snap = stock_snapshot()
            pos = snap.get("suggested_purchase_orders") or []
            if not pos:
                print(f"  low stock ({ing_id}) but all needs covered by open POs — no agent run")
                return
            names = sorted({li["name"] for po in pos for li in po["line_items"]})
            print(f"▶ trigger: low stock, orderable: {', '.join(names)}")
            eid = _log_start(db, "place_po",
                             f"Low stock detected: {', '.join(names)} — the inventory "
                             "agent is reviewing whether to reorder (≈30s)…")
            author, text = await _invoke(runner, sessions, (
                "Stock alert: these ingredients are below their reorder point with no "
                f"open purchase order: {', '.join(names)}. "
                "Transfer to inventory_agent to review the situation and place purchase "
                "orders if appropriate (it is authorized to order without human approval "
                "when funds allow, and to HOLD BACK dead-stock lines it judges not "
                "worth reordering). After acting, explain in 1-2 sentences what was "
                "ordered or held back and WHY, citing the stock numbers and sales "
                "velocity (hours_of_cover) from the snapshot."
            ))
            _log_done(db, eid, author, text)

        elif doc.get("par_level") and on_hand >= doc["par_level"] * SURPLUS_FACTOR:
            # ── SURPLUS STOCK → promo opportunity (move it before it's waste) ─
            if db.promotion_recommendations.count_documents({"status": "pending"}, limit=1):
                return
            if db.promotions.count_documents({"status": "live"}, limit=1):
                return
            if not _cooled(f"surplus:{ing_id}", PROMO_COOLDOWN_S):
                return
            print(f"▶ trigger: surplus stock of {ing_id} "
                  f"({on_hand} vs par {doc['par_level']}) — evaluating promo")
            eid = _log_start(db, "recommend_promo",
                             f"Surplus stock of {doc.get('name', ing_id)} detected — "
                             "evaluating a promo to move it before it spoils (≈1 min)…")
            author, text = await _invoke(runner, sessions, (
                f"Autonomous check: we are overstocked on {doc.get('name', ing_id)} "
                f"({on_hand} {doc.get('unit', '')} on hand vs a par level of "
                f"{doc['par_level']}) — excess inventory becomes waste if it doesn't "
                "move. Run the promo evaluation flow: billing_agent's evidence includes "
                "surplus_ingredients and surplus_mover candidates (menu items that "
                "consume the overstocked ingredient). STOP after the recommendation is "
                "inserted as 'pending' — the GM decides on the dashboard. Summarize in "
                "1-2 sentences what was recommended and the key evidence."
            ))
            _log_done(db, eid, author, text)

    elif coll == "live_metrics":
        pace = doc.get("sales_pace_vs_baseline_pct")
        if pace is None or abs(pace) < PACE_TRIGGER_PCT:
            return
        if db.promotion_recommendations.count_documents({"status": "pending"}, limit=1):
            return
        if db.promotions.count_documents({"status": "live"}, limit=1):
            return
        if not _cooled("promo", PROMO_COOLDOWN_S):
            return
        print(f"▶ trigger: sales pace {pace:+.1f}% vs baseline — evaluating promo")
        eid = _log_start(db, "recommend_promo",
                         f"Sales pace is {pace:+.1f}% vs baseline — evaluating a "
                         "promo opportunity (≈1 min)…")
        author, text = await _invoke(runner, sessions, (
            f"Autonomous check: sales pace is {pace:+.1f}% vs baseline. "
            "Run the promo evaluation flow: parallel analysis if needed, then "
            "billing_agent to build a recommendation. STOP after the recommendation "
            "is inserted as 'pending' — the GM approves it on the dashboard, not here. "
            "Summarize in 1-2 sentences what was recommended and the key evidence."
        ))
        _log_done(db, eid, author, text)


def _refuse_twin_workers() -> None:
    """Two workers = duplicate agent actions (cooldowns are per-process memory).
    Refuse to start if another plumbing.worker process is already running."""
    import subprocess
    try:
        out = subprocess.run(["pgrep", "-f", "plumbing.worker"],
                             capture_output=True, text=True).stdout.split()
        others = [p for p in out if p and int(p) != os.getpid()]
        if others:
            raise SystemExit(
                f"Another worker is already running (pid {', '.join(others)}) — "
                "exiting. Use ./start.sh stop first.")
    except FileNotFoundError:
        pass  # no pgrep (unlikely) — proceed


async def run() -> None:
    _refuse_twin_workers()
    client, db = _connect()
    root = config_agent_utils.from_config(str(_REPO_ROOT / "restaurant_gm" / "root_agent.yaml"))
    sessions = InMemorySessionService()
    runner = Runner(agent=root, app_name=_APP, session_service=sessions)

    pipeline = [{"$match": {"ns.coll": {"$in": _WATCHED}}}]
    loop = asyncio.get_event_loop()
    try:
        with db.watch(pipeline, full_document="updateLookup", max_await_time_ms=1000) as stream:
            await _catch_up(db, runner, sessions)
            print("Worker watching: " + ", ".join(_WATCHED) + " … Ctrl-C to stop.\n")
            while True:
                change = await loop.run_in_executor(None, stream.try_next)
                if change is None:
                    continue
                try:
                    await _handle(db, runner, sessions, change)
                except Exception as e:  # keep the watcher alive across bad events
                    print(f"  ! handler error: {type(e).__name__}: {e}")
    except KeyboardInterrupt:
        print("\nWorker stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run())
