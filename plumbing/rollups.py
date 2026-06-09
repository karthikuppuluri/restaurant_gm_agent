"""
plumbing/rollups.py — base-metric rollups (deterministic, NO LLM).

Keeps the singleton `live_metrics` document's BASE fields current:
  - shift_revenue_money       (sum of net_amount over simulator orders)
  - covers                    (sum of guest_count)
  - total_vendor_spend_money  (sum of placed/received purchase_orders)
  - as_of                     (now)

Live, order-by-order via a change stream on `orders`. Only `source: "simulator"`
orders count (seed/historical orders are baseline — same rule as depletion).
Purchase orders are always agent-written so no source filter is needed there;
only `placed` and `received` statuses count (committed spend — not drafts or
canceled).

Approach: RECOMPUTE, not increment. On each insert we re-aggregate and `$set` the
base fields. This is inherently idempotent (a restart can't double-count) and makes
`rebuild()` the exact same operation — which keeps live_metrics consistent after the
simulator's --reset/--undo (memory: reset-rolls-back-derived-state).

Only the base fields are written (via `$set` on those keys). Agent-owned fields —
gross_margin_pct, sales_pace, top_movers, low_stock, eighty_sixed_item_ids,
active_promo_perf — are never touched here (invariant #3).

Usage:
    python -m plumbing.rollups              # sync once, then watch the stream
    python -m plumbing.rollups --rebuild    # recompute base fields from facts and exit
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

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
    order_agg = list(db.orders.aggregate([
        {"$match": {"source": "simulator"}},
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

    now = _now()
    db.live_metrics.update_one(
        {"_id": _LIVE_ID},
        {
            "$set": {
                "shift_revenue_money": _money(revenue),
                "covers": covers,
                "total_vendor_spend_money": _money(vendor_spend),
                "as_of": now,
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
    return {"revenue": revenue, "covers": covers, "vendor_spend": vendor_spend}


def rebuild() -> None:
    """Recompute base fields from facts (reset/undo path)."""
    client, db = _connect()
    try:
        r = _recompute(db)
        print(f"Rebuilt live_metrics base: "
              f"shift_revenue=${r['revenue']/100:.2f}  covers={r['covers']}  "
              f"vendor_spend=${r['vendor_spend']/100:.2f}")
    finally:
        client.close()


def run() -> None:
    client, db = _connect()
    try:
        r = _recompute(db)
        print(f"Synced live_metrics: shift_revenue=${r['revenue']/100:.2f}  "
              f"covers={r['covers']}  vendor_spend=${r['vendor_spend']/100:.2f}")
        print("Watching orders for new simulator inserts… Ctrl-C to stop.\n")

        with db.orders.watch([{"$match": {"operationType": "insert"}}]) as stream:
            for change in stream:
                doc = change["fullDocument"]
                if doc.get("source") != "simulator":
                    continue
                r = _recompute(db)
                print(f"  rollup updated after {doc['_id']}  "
                      f"shift_revenue=${r['revenue']/100:.2f}  covers={r['covers']}  "
                      f"vendor_spend=${r['vendor_spend']/100:.2f}")
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
