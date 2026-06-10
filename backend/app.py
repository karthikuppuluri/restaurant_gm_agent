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
from datetime import datetime, timezone
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
]

_client = MongoClient(
    os.environ["MONGODB_CONNECTION_STRING"],
    serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
)
_db = _client[os.environ.get("MONGODB_DB_NAME", "restaurant_gm")]

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
    return {
        "live_metrics": _jsonable(_db.live_metrics.find_one({"_id": "current"}) or {}),
        "financials": _jsonable(list(_db.financials.find().sort("period_id", 1))),
        "recommendations": _jsonable(list(
            _db.promotion_recommendations.find().sort("created_at", -1).limit(10))),
        "promotions": _jsonable(list(_db.promotions.find().sort("created_at", -1).limit(5))),
        "purchase_orders": _jsonable(list(
            _db.purchase_orders.find().sort("placed_at", -1).limit(10))),
        "agent_events": _jsonable(list(
            _db.agent_events.find().sort("created_at", -1).limit(15))),
        "raw_ingredients": _jsonable(list(_db.raw_ingredients.find(
            {}, {"ingredient_id": 1, "name": 1, "unit": 1, "on_hand_qty": 1,
                 "reorder_point": 1, "par_level": 1}).sort("name", 1))),
        "sim_now": _sim_now(),
    }


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


# Production build of the SPA, when present (dashboard/dist). Mounted last so
# /api/* and the ADK routes win.
_dist = _REPO_ROOT / "dashboard" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
