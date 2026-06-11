"""FastAPI backend — single origin for the whole product.

Three kinds of routes on one app:
  - ADK chat API     -> provided by get_fast_api_app() (/run_sse, session routes);
                        the chat panel talks to Central through these.
  - dashboard routes -> /api/dashboard (one-shot snapshot) and /api/feed (SSE that
                        tails a MongoDB Change Stream over the serving collections).
  - approval gate    -> POST /api/recommendations/{rec_id}/decision flips
                        promotion_recommendations.status via the driver (plumbing
                        write path — thin, deterministic, no LLM). The status change
                        fires a change stream that plumbing/worker.py picks up to
                        re-enter the Central agent loop.

Run locally:
    uvicorn backend.app:app --reload --port 8000
The React SPA (dashboard/) talks to this origin; a production build in
dashboard/dist is served statically at / when present.
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app
from pydantic import BaseModel
from pymongo import MongoClient
from starlette.concurrency import run_in_threadpool

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Collections the dashboard cares about; the SSE feed tails these.
_FEED_COLLECTIONS = [
    "live_metrics",
    "financials",
    "promotion_recommendations",
    "promotions",
    "purchase_orders",
    "campaign_sends",
    "agent_events",
    "raw_ingredients",
    "orders",
    "waste_events",
]

_client = MongoClient(
    os.environ["MONGODB_CONNECTION_STRING"],
    serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
)
_db = _client[os.environ.get("MONGODB_DB_NAME", "restaurant_gm")]


def _ensure_indexes() -> None:
    """Unique indexes = the DB-level guard against agent retries inserting the
    same business entity twice (LLM idempotency cannot be trusted). Idempotent;
    recreated on every backend start since resets drop these collections."""
    try:
        _db.promotions.create_index("promo_id", unique=True)
        _db.promotions.create_index("recommendation_id", unique=True)
        _db.promotion_recommendations.create_index("recommendation_id", unique=True)
        _db.purchase_orders.create_index("po_id", unique=True)
        # Business rule as a DB constraint: at most ONE open (placed) PO per
        # ingredient — racing agent runs physically cannot double-order.
        _db.purchase_orders.create_index(
            "line_items.ingredient_id", unique=True,
            partialFilterExpression={"status": "placed"},
            name="one_open_po_per_ingredient",
        )
    except Exception as e:  # duplicate legacy data — surface it, don't crash
        print(f"WARNING: could not create unique indexes: {e}")


_ensure_indexes()

app = get_fast_api_app(
    agents_dir=str(_REPO_ROOT),
    web=False,
    allow_origins=["*"],
)


def _jsonable(doc):
    """Mongo doc -> JSON-safe (stringify ObjectId and anything non-serializable)."""
    return json.loads(json.dumps(doc, default=str))


def _sim_now() -> str:
    doc = _db.orders.find_one({}, {"opened_at": 1}, sort=[("opened_at", -1)])
    return doc["opened_at"] if doc else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot() -> dict:
    # notified_count per promo (how many customers Outreach pushed) — the
    # denominator for predicted-vs-actual uptake on the dashboard.
    sends = {s["_id"]: s["n"] for s in _db.campaign_sends.aggregate(
        [{"$group": {"_id": "$promo_id", "n": {"$sum": 1}}}])}
    promos = list(_db.promotions.find().sort("created_at", -1).limit(10))
    for p in promos:
        p["notified_count"] = sends.get(p.get("promo_id"), 0)
    return {
        "live_metrics": _jsonable(_db.live_metrics.find_one({"_id": "current"}) or {}),
        "financials": _jsonable(list(_db.financials.find().sort("period_id", 1))),
        "recommendations": _jsonable(list(
            _db.promotion_recommendations.find().sort("created_at", -1).limit(10))),
        "promotions": _jsonable(promos),
        "purchase_orders": _jsonable(list(
            _db.purchase_orders.find().sort("placed_at", -1).limit(10))),
        "agent_events": _jsonable(list(
            _db.agent_events.find().sort("created_at", -1).limit(15))),
        "raw_ingredients": _jsonable(list(_db.raw_ingredients.find(
            {}, {"ingredient_id": 1, "name": 1, "unit": 1, "on_hand_qty": 1,
                 "reorder_point": 1, "par_level": 1}).sort("name", 1))),
        "orders": _jsonable(list(_db.orders.find(
            {"source": "simulator"},
            {"order_id": 1, "opened_at": 1, "channel": 1, "guest_count": 1,
             "net_amount_money": 1, "line_items": 1, "created_at": 1},
        ).sort("opened_at", -1).limit(5))),
        "waste_7d_cents": _waste_7d(),
        "sim_now": _sim_now(),
    }


def _waste_7d() -> int:
    """Spoilage cost over the trailing 7 sim-days (waste_events)."""
    now = _sim_now()
    cut = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
           - timedelta(days=7)).strftime("%Y-%m-%d")
    w = list(_db.waste_events.aggregate([
        {"$match": {"day": {"$gte": cut}}},
        {"$group": {"_id": None, "c": {"$sum": "$cost_cents"}}},
    ]))
    return w[0]["c"] if w else 0


@app.get("/api/dashboard")
def dashboard_snapshot():
    return _snapshot()


@app.get("/api/feed")
async def feed():
    """SSE: one `snapshot` event, then a `change` event per write to any serving
    collection (MongoDB Change Stream, full documents)."""

    async def event_stream():
        pipeline = [{"$match": {"ns.coll": {"$in": _FEED_COLLECTIONS}}}]
        with _db.watch(pipeline, full_document="updateLookup",
                       max_await_time_ms=1000) as stream:
            snap = await run_in_threadpool(_snapshot)
            yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
            while True:
                change = await run_in_threadpool(stream.try_next)
                if change is None:
                    # keep-alive comment so proxies don't drop the connection
                    yield ": ping\n\n"
                    continue
                payload = {
                    "collection": change["ns"]["coll"],
                    "operation": change["operationType"],
                    "document": _jsonable(change.get("fullDocument")),
                }
                yield f"event: change\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


class Decision(BaseModel):
    decision: str  # "approved" | "rejected"


@app.post("/api/recommendations/{rec_id}/decision")
def decide(rec_id: str, body: Decision):
    """The human approval gate. Thin status flip via the driver (plumbing);
    the resulting change-stream event re-enters the agent loop (worker)."""
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be 'approved' or 'rejected'")
    rec = _db.promotion_recommendations.find_one({"recommendation_id": rec_id})
    if not rec:
        raise HTTPException(404, f"recommendation {rec_id} not found")
    if rec.get("status") != "pending":
        raise HTTPException(409, f"recommendation {rec_id} is already {rec.get('status')}")

    now = _sim_now()
    _db.promotion_recommendations.update_one(
        {"recommendation_id": rec_id},
        {"$set": {"status": body.decision, "decided_by": "gm",
                  "decided_at": now, "updated_at": now}},
    )
    return {"recommendation_id": rec_id, "status": body.decision, "decided_at": now}


# ── simulator control (the demo knob) ────────────────────────────────────────
# Lets a (possibly remote) user run one simulated day at a chosen pace. The knob
# is "how many real minutes should one day take"; we translate that to SIM_SPEED
# over the ~13h service window (closed hours fast-forward and cost ~0 real time).
_SERVICE_WINDOW_SIM_SECONDS = 13 * 3600  # 10:00–23:00
_sim_proc: subprocess.Popen | None = None


class SimStart(BaseModel):
    day_minutes: float = 5


@app.post("/api/sim/start")
def sim_start(body: SimStart):
    global _sim_proc
    if _sim_proc and _sim_proc.poll() is None:
        raise HTTPException(409, "a simulated day is already running")
    if not 0.5 <= body.day_minutes <= 60:
        raise HTTPException(422, "day_minutes must be between 0.5 and 60")
    speed = max(1, round(_SERVICE_WINDOW_SIM_SECONDS / (body.day_minutes * 60)))
    env = {**os.environ, "SIM_SPEED": str(speed)}
    _sim_proc = subprocess.Popen(
        [sys.executable, "-m", "plumbing.simulator"],
        cwd=str(_REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # the day about to be simulated = day after the latest order, starting 12 AM
    day_start = (datetime.strptime(_sim_now(), "%Y-%m-%dT%H:%M:%SZ")
                 + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    return {"running": True, "sim_speed": speed, "day_minutes": body.day_minutes,
            "sim_day_start": day_start}


@app.post("/api/sim/stop")
def sim_stop():
    global _sim_proc
    if _sim_proc and _sim_proc.poll() is None:
        _sim_proc.terminate()
    # clear a lingering pause so the next run starts cleanly
    _db.sim_control.update_one({"_id": "sim"}, {"$set": {"paused": False}}, upsert=True)
    return {"running": False}


@app.post("/api/sim/pause")
def sim_pause():
    """Flip the sim_control flag the simulator polls — it holds at the current
    sim-time until resumed (same mechanism as the CLI --pause)."""
    _db.sim_control.update_one({"_id": "sim"}, {"$set": {"paused": True}}, upsert=True)
    return {"paused": True}


@app.post("/api/sim/resume")
def sim_resume():
    _db.sim_control.update_one({"_id": "sim"}, {"$set": {"paused": False}}, upsert=True)
    return {"paused": False}


@app.get("/api/sim/status")
def sim_status():
    ctl = _db.sim_control.find_one({"_id": "sim"}, {"paused": 1}) or {}
    return {"running": bool(_sim_proc and _sim_proc.poll() is None),
            "paused": bool(ctl.get("paused"))}


def _promo_insights(promo_id: str) -> dict:
    """Deterministic promo post-mortem data (driver, no LLM): conversion of
    notified customers, actual vs predicted uptake, redeemer demographics, and
    demand lift on the promoted items during the window vs the window before."""
    promo = _db.promotions.find_one({"promo_id": promo_id})
    if not promo:
        raise HTTPException(404, f"promotion {promo_id} not found")

    notified = _db.campaign_sends.count_documents({"promo_id": promo_id})
    notified_redeemed = _db.campaign_sends.count_documents(
        {"promo_id": promo_id, "redeemed": True})
    total_redemptions = promo.get("redemption_count", 0)

    # redeeming orders: known customers + walk-ins
    redeemer_ids = [c for c in _db.orders.distinct(
        "customer_id", {"line_items.promo_id": promo_id}) if c]
    walk_in_redemptions = _db.orders.count_documents(
        {"line_items.promo_id": promo_id, "customer_id": None})

    # demographics of known redeemers
    demo = {"loyalty_tiers": {}, "age_bands": {}, "cities": {},
            "avg_price_sensitivity": None}
    if redeemer_ids:
        custs = list(_db.customers.find({"customer_id": {"$in": redeemer_ids}},
                                        {"loyalty_tier": 1, "age": 1, "city": 1,
                                         "price_sensitivity": 1}))
        ps = []
        for c in custs:
            demo["loyalty_tiers"][c.get("loyalty_tier", "none")] = \
                demo["loyalty_tiers"].get(c.get("loyalty_tier", "none"), 0) + 1
            age = c.get("age") or 0
            band = ("18-25" if age <= 25 else "26-35" if age <= 35
                    else "36-50" if age <= 50 else "50+")
            demo["age_bands"][band] = demo["age_bands"].get(band, 0) + 1
            demo["cities"][c.get("city", "?")] = demo["cities"].get(c.get("city", "?"), 0) + 1
            if c.get("price_sensitivity") is not None:
                ps.append(c["price_sensitivity"])
        if ps:
            demo["avg_price_sensitivity"] = round(sum(ps) / len(ps), 2)

    # demand lift: promoted-item units during the window vs equal window before
    vf, vu = promo.get("valid_from"), promo.get("valid_until")
    lift = None
    if vf and vu:
        f = datetime.strptime(vf, "%Y-%m-%dT%H:%M:%SZ")
        u = datetime.strptime(vu, "%Y-%m-%dT%H:%M:%SZ")
        before_start = (f - (u - f)).strftime("%Y-%m-%dT%H:%M:%SZ")
        item_ids = promo.get("applies_to_item_ids", [])

        def units(start, end):
            r = list(_db.orders.aggregate([
                {"$match": {"opened_at": {"$gte": start, "$lt": end}}},
                {"$unwind": "$line_items"},
                {"$match": {"line_items.item_id": {"$in": item_ids}}},
                {"$group": {"_id": None, "qty": {"$sum": "$line_items.quantity"}}},
            ]))
            return r[0]["qty"] if r else 0

        during, before = units(vf, vu), units(before_start, vf)
        lift = {"units_during": during, "units_before": before,
                "lift_pct": round((during - before) / before * 100, 1) if before else None}

    discount_given = list(_db.orders.aggregate([
        {"$match": {"line_items.promo_id": promo_id}},
        {"$unwind": "$line_items"},
        {"$match": {"line_items.promo_id": promo_id}},
        {"$group": {"_id": None,
                    "discount_cents": {"$sum": "$line_items.applied_discount_money.amount"},
                    "revenue_cents": {"$sum": "$line_items.gross_money.amount"}}},
    ]))
    money = discount_given[0] if discount_given else {"discount_cents": 0, "revenue_cents": 0}

    return {
        "promo": _jsonable({k: promo.get(k) for k in (
            "promo_id", "title", "discount_value", "status", "valid_from",
            "valid_until", "predicted_uptake", "target_criteria",
            "applies_to_item_ids")}),
        "notified": notified,
        "notified_redeemed": notified_redeemed,
        "conversion_of_notified_pct": round(notified_redeemed / notified * 100, 1)
        if notified else None,
        "total_redemptions": total_redemptions,
        "walk_in_redemptions": walk_in_redemptions,
        "predicted_uptake_pct": round(promo.get("predicted_uptake", 0) * 100, 1)
        if promo.get("predicted_uptake") is not None else None,
        "actual_uptake_pct": round(total_redemptions / notified * 100, 1)
        if notified else None,
        "redeemer_demographics": demo,
        "demand_lift": lift,
        "promo_line_revenue_cents": money["revenue_cents"],
        "discount_given_cents": money["discount_cents"],
    }


@app.get("/api/promos/{promo_id}/insights")
def promo_insights(promo_id: str):
    return _promo_insights(promo_id)


@app.post("/api/promos/{promo_id}/analyze")
def promo_analyze(promo_id: str):
    """✨ Agentic retro: a Gemini post-mortem grounded EXCLUSIVELY in the
    deterministic insights above — did it work, why, what to do differently."""
    insights = _promo_insights(promo_id)
    try:
        from google import genai
        client = genai.Client()
        r = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=(
                "You are the promotions analyst for an independent restaurant. "
                "Here is the complete measured data for a finished promotion, as JSON:\n"
                f"{json.dumps(insights)}\n\n"
                "Write a sharp post-mortem in markdown (max ~150 words): "
                "1) Verdict — success, partial, or flop, with the one number that "
                "decides it; 2) WHY — compare actual vs predicted uptake, conversion "
                "of notified customers, walk-in share, and demand lift; 3) Who "
                "actually redeemed (demographics); 4) One concrete change for the "
                "next promo. Use ONLY the numbers provided — never invent any."),
        )
        return {"analysis": r.text or "", "insights": insights}
    except Exception as e:
        raise HTTPException(502, f"analysis failed: {str(e)[:200]}")


@app.get("/api/debug/llm")
def debug_llm():
    """Deploy diagnostics: is the model reachable from inside this container,
    independent of the agent stack? Harmless read-only probe."""
    try:
        from google import genai
        client = genai.Client()
        r = client.models.generate_content(model="gemini-3.5-flash",
                                           contents="reply with exactly: ok")
        return {"ok": True, "reply": (r.text or "")[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


# Production build of the SPA, when present (dashboard/dist). Mounted last so
# /api/* and the ADK routes win.
_dist = _REPO_ROOT / "dashboard" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
