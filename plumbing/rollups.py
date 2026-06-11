"""
plumbing/rollups.py — base-metric rollups (deterministic, NO LLM).

Keeps the singleton `live_metrics` document's BASE fields current:
  - shift_revenue_money       (sum of net_amount over the CURRENT sim-day's simulator orders)
  - covers                    (guest_count over the same current sim-day)
  - total_vendor_spend_money  (sum of placed/received purchase_orders)
  - cash_on_hand_money        (STARTING_CASH + all-time net revenue - vendor spend)
  - as_of                     (SIM time = opened_at of the latest order, NOT wall clock)

Also recomputed per order (moved here from the order_mgmt agent 2026-06-10 —
pure derivation, single writer, never stale; the agent reads the same
sales_snapshot() helper so chat and dashboard always agree):
  - sales_pace_vs_baseline_pct  (trailing 2h vs the seed baseline, same hours)
  - top_movers                  ({ item_id, qty, velocity })

Also upserts the current sim-day's `financials` doc on each order insert (the
daily P&L lives in plumbing/financials.py; this listener just keeps "today"
current). Note: a PO insert changes vendor spend / cash but is only picked up
on the next order insert — orders are frequent enough that this is fine.

Live, order-by-order via a change stream on `orders`. Only `source: "simulator"`
orders count (seed/historical orders are baseline — same rule as depletion).
Purchase orders are always agent-written so no source filter is needed there;
only `placed` and `received` statuses count (committed spend — not drafts or
canceled).

Approach: RECOMPUTE, not increment. On each insert we re-aggregate and `$set` the
base fields. This is inherently idempotent (a restart can't double-count) and makes
`rebuild()` the exact same operation — which keeps live_metrics consistent after the
simulator's --reset/--undo (memory: reset-rolls-back-derived-state).

Only the listed fields are written (via `$set` on those keys). Agent-owned fields —
gross_margin_pct, active_promo_perf — are never touched here (invariant #3);
low_stock / eighty_sixed_item_ids are owned by depletion/replenishment plumbing.

Usage:
    python -m plumbing.rollups              # sync once, then watch the stream
    python -m plumbing.rollups --rebuild    # recompute base fields from facts and exit
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

from plumbing.financials import (
    STARTING_CASH_CENTS,
    backfill as financials_backfill,
    item_maps,
    rollup_day,
)
from restaurant_gm.queries import sales_snapshot

load_dotenv()

# Singleton live_metrics doc id. Agents that write insight fields must target the
# SAME _id so plumbing and agents share one dashboard-state document.
_LIVE_ID = "current"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(cents: int) -> dict:
    return {"amount": int(cents), "currency": "USD"}


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    # Fail fast on a flaky network instead of hanging silently for 30s+.
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def _recompute(db) -> dict:
    """Re-aggregate base metrics over simulator orders and purchase_orders, then
    $set them on the singleton. Returns the computed values. Idempotent."""
    now = _now()
    # as_of is SIM time (latest order) — it also defines the current sim-day.
    latest = db.orders.find_one({}, {"opened_at": 1}, sort=[("opened_at", -1)])
    as_of = latest["opened_at"] if latest else now

    # Shift metrics cover ONLY the current sim-day: a new day starts the count
    # over (a GM's "how is tonight going" number, not an all-time total).
    day_start = f"{as_of[:10]}T00:00:00Z"
    order_agg = list(db.orders.aggregate([
        {"$match": {"source": "simulator", "opened_at": {"$gte": day_start}}},
        {"$group": {
            "_id": None,
            "revenue": {"$sum": "$net_amount_money.amount"},
            "covers": {"$sum": "$guest_count"},
        }},
    ]))
    revenue = order_agg[0]["revenue"] if order_agg else 0
    covers = order_agg[0]["covers"] if order_agg else 0

    # Committed vendor spend only: placed or received (not draft / canceled).
    po_agg = list(db.purchase_orders.aggregate([
        {"$match": {"status": {"$in": ["placed", "received"]}}},
        {"$group": {"_id": None, "spend": {"$sum": "$total_money.amount"}}},
    ]))
    vendor_spend = po_agg[0]["spend"] if po_agg else 0

    # Operating cash: all-time pre-tax net revenue (seed baseline + simulator,
    # matching financials) minus committed vendor spend, on top of starting cash.
    all_agg = list(db.orders.aggregate([
        {"$group": {
            "_id": None,
            "gross": {"$sum": "$total_money.amount"},
            "discount": {"$sum": "$total_discount_money.amount"},
        }},
    ]))
    all_net = (all_agg[0]["gross"] - all_agg[0]["discount"]) if all_agg else 0
    cash = STARTING_CASH_CENTS + all_net - vendor_spend

    # Sales pace + top movers: same derivation the order_mgmt agent reads via
    # sales_snapshot(), recomputed here per order so the dashboard is never stale.
    snap = sales_snapshot(2)
    pace = snap.get("pace_vs_baseline_pct")
    top_movers = [{"item_id": m["item_id"], "qty": m["qty"],
                   "velocity": m["velocity_per_hour"]}
                  for m in snap.get("top_items", [])]

    db.live_metrics.update_one(
        {"_id": _LIVE_ID},
        {
            "$set": {
                "shift_revenue_money": _money(revenue),
                "covers": covers,
                "total_vendor_spend_money": _money(vendor_spend),
                "cash_on_hand_money": _money(cash),
                "sales_pace_vs_baseline_pct": pace,
                "top_movers": top_movers,
                "as_of": as_of,
                "updated_at": now,
            },
            "$setOnInsert": {
                "source": "plumbing",
                "schema_version": 1,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return {"revenue": revenue, "covers": covers, "vendor_spend": vendor_spend,
            "cash": cash}


def rebuild() -> None:
    """Recompute base fields + all financials docs from facts (reset/undo path)."""
    client, db = _connect()
    try:
        r = _recompute(db)
        print(f"Rebuilt live_metrics base: "
              f"shift_revenue=${r['revenue']/100:.2f}  covers={r['covers']}  "
              f"vendor_spend=${r['vendor_spend']/100:.2f}  cash=${r['cash']/100:.2f}")
        financials_backfill(db)
    finally:
        client.close()


def run() -> None:
    client, db = _connect()
    try:
        # Static BOM cost/category maps for the daily financials upsert.
        cost_map, cat_map = item_maps(db)
        r = _recompute(db)
        print(f"Synced live_metrics: shift_revenue=${r['revenue']/100:.2f}  "
              f"covers={r['covers']}  vendor_spend=${r['vendor_spend']/100:.2f}  "
              f"cash=${r['cash']/100:.2f}")
        print("Watching orders for new simulator inserts… Ctrl-C to stop.\n")

        with db.orders.watch([{"$match": {"operationType": "insert"}}]) as stream:
            for change in stream:
                doc = change["fullDocument"]
                if doc.get("source") != "simulator":
                    continue
                r = _recompute(db)
                rollup_day(db, doc["opened_at"][:10], cost_map, cat_map)
                print(f"  rollup updated after {doc['_id']}  "
                      f"shift_revenue=${r['revenue']/100:.2f}  covers={r['covers']}  "
                      f"vendor_spend=${r['vendor_spend']/100:.2f}  cash=${r['cash']/100:.2f}")
    except KeyboardInterrupt:
        print("\nRollups listener stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Base-metric rollups listener.")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Recompute live_metrics base fields from facts and exit.",
    )
    args = parser.parse_args()
    if args.rebuild:
        rebuild()
    else:
        run()
