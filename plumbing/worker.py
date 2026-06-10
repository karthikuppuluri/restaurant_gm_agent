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

  3. PROMO SIGNAL    live_metrics.sales_pace_vs_baseline_pct beyond +/-15%
       (written by order_mgmt / rollups)
       suppression: global cooldown + skip if a pending recommendation or live
       promo already exists.
       -> invoke Central: run the promo evaluation flow (ends at the human gate).

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
PROMO_COOLDOWN_S = 1800      # global
PACE_TRIGGER_PCT = 15.0

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


def _log_event(db, agent: str, action: str, reasoning: str, related_ids: dict | None = None) -> None:
    """Record an autonomous agent action in agent_events (the transparency log).
    Plumbing writes the record; the words are the agent's own final response —
    this is what the dashboard's Agent activity feed and toasts display."""
    if not reasoning:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.agent_events.insert_one({
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "agent": agent.replace("_agent", "").replace("central_orchestrator", "central"),
        "action": action,
        "summary": reasoning.split("\n")[0][:200],
        "reasoning": reasoning,
        "related_ids": related_ids or {},
        "created_at": now,
        "source": "worker", "schema_version": 1,
    })


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
            author, text = await _invoke(runner, sessions, (
                f"The GM just APPROVED promotion recommendation {rid} "
                f"(its status is already 'approved', decided_at {doc.get('decided_at')}; "
                "do NOT update it again and do NOT ask for approval). "
                "Call billing_agent with: 'Mode B: recommendation "
                f"{rid} was approved — configure the promotions document.' "
                "After billing confirms the promo is live, call outreach_agent with: "
                "'Push the new live promo to eligible customers.' "
                "Then report in 1-2 sentences what was configured and how many "
                "customers were notified."
            ))
            _log_event(db, author, "configure_promo", text, {"recommendation_id": rid})
        elif status == "rejected":
            print(f"▶ recommendation {rid} rejected — logged, going idle")

    elif coll == "raw_ingredients":
        ing_id = doc.get("ingredient_id")
        if not ing_id or doc.get("on_hand_qty", 0) >= doc.get("reorder_point", 0):
            return
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
        author, text = await _invoke(runner, sessions, (
            "Stock alert: these ingredients are below their reorder point with no "
            f"open purchase order: {', '.join(names)}. "
            "Transfer to inventory_agent to review the situation and place purchase "
            "orders if appropriate (it is authorized to order without human approval "
            "when funds allow). After ordering, explain in 1-2 sentences what was "
            "ordered and WHY, citing the stock numbers and sales velocity "
            "(hours_of_cover) from the snapshot."
        ))
        _log_event(db, author, "place_po", text)

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
        author, text = await _invoke(runner, sessions, (
            f"Autonomous check: sales pace is {pace:+.1f}% vs baseline. "
            "Run the promo evaluation flow: parallel analysis if needed, then "
            "billing_agent to build a recommendation. STOP after the recommendation "
            "is inserted as 'pending' — the GM approves it on the dashboard, not here. "
            "Summarize in 1-2 sentences what was recommended and the key evidence."
        ))
        _log_event(db, author, "recommend_promo", text)


async def run() -> None:
    client, db = _connect()
    root = config_agent_utils.from_config(str(_REPO_ROOT / "restaurant_gm" / "root_agent.yaml"))
    sessions = InMemorySessionService()
    runner = Runner(agent=root, app_name=_APP, session_service=sessions)

    pipeline = [{"$match": {"ns.coll": {"$in": _WATCHED}}}]
    loop = asyncio.get_event_loop()
    try:
        with db.watch(pipeline, full_document="updateLookup", max_await_time_ms=1000) as stream:
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
