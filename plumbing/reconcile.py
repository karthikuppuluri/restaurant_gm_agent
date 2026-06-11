"""
plumbing/reconcile.py — redemption reconciliation (deterministic, NO LLM).

Closes the promo measurement loop. The simulator already *creates* redemptions
(eligible customers get line items with a promo_id + discount); this listener
*counts* them so the dashboard's predicted-vs-actual numbers are real:

  - promotions.redemption_count        += 1 per redeeming order (per promo)
  - campaign_sends.redeemed            = true (+ redeemed_order_id) when the
    redeeming customer was one we proactively pushed — distinguishing
    outreach-driven redemptions from organic ones (eligibility is
    target_criteria, not the push; see README design note)
  - live_metrics.active_promo_perf[].actual_redemptions kept in sync

Live, order-by-order via a change stream on `orders`. Pure per-order
bookkeeping — no judgment — so it is plumbing (invariant #3).

Idempotency: each processed order is claimed with `reconciled: true` via an
atomic find-and-update, so restarts (which re-scan pending orders on startup)
never double-count.

Usage:
    python -m plumbing.reconcile              # catch up, then watch the stream
    python -m plumbing.reconcile --rebuild    # recount everything from facts (reset path)
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def _apply(db, order: dict) -> list:
    """Count one order's redemptions. Returns the promo_ids it redeemed."""
    promo_ids = sorted({li["promo_id"] for li in order.get("line_items", [])
                        if li.get("promo_id")})
    now = _now()
    for pid in promo_ids:
        db.promotions.update_one(
            {"promo_id": pid},
            {"$inc": {"redemption_count": 1}, "$set": {"updated_at": now}},
        )
        db.live_metrics.update_one(
            {"_id": "current", "active_promo_perf.promo_id": pid},
            {"$inc": {"active_promo_perf.$.actual_redemptions": 1},
             "$set": {"updated_at": now}},
        )
        if order.get("customer_id"):
            db.campaign_sends.update_one(
                {"promo_id": pid, "customer_id": order["customer_id"],
                 "redeemed": {"$ne": True}},
                {"$set": {"redeemed": True,
                          "redeemed_order_id": order["order_id"]}},
            )
    return promo_ids


def _process_order(db, order_id) -> list:
    """Atomically claim an order (set reconciled=true) and count its redemptions.
    Returns [] if already processed — the idempotency guard."""
    claimed = db.orders.find_one_and_update(
        {"_id": order_id, "reconciled": {"$ne": True}},
        {"$set": {"reconciled": True}},
    )
    if claimed is None:
        return []
    return _apply(db, claimed)


def _catch_up(db) -> int:
    """Reconcile any promo orders that landed while the listener was down."""
    pending = [o["_id"] for o in db.orders.find(
        {"line_items.promo_id": {"$ne": None}, "reconciled": {"$ne": True}},
        {"_id": 1},
    )]
    n = 0
    for order_id in pending:
        if _process_order(db, order_id):
            n += 1
    return n


def rebuild() -> None:
    """Recount everything from facts (reset/undo path): zero out redemption
    counts and send flags, then re-apply from the orders that currently exist."""
    client, db = _connect()
    try:
        db.promotions.update_many({}, {"$set": {"redemption_count": 0}})
        db.live_metrics.update_many(
            {"active_promo_perf": {"$exists": True}},
            {"$set": {"active_promo_perf.$[].actual_redemptions": 0}},
        )
        db.campaign_sends.update_many(
            {}, {"$set": {"redeemed": False, "redeemed_order_id": None}})
        db.orders.update_many({"reconciled": True}, {"$unset": {"reconciled": ""}})
        n = _catch_up(db)
        print(f"Rebuilt reconciliation: recounted {n} redeeming order(s).")
    finally:
        client.close()


def run() -> None:
    client, db = _connect()
    try:
        n = _catch_up(db)
        print(f"Catch-up: reconciled {n} pending promo order(s).")
        print("Watching orders for redemptions… Ctrl-C to stop.\n")

        with db.orders.watch([{"$match": {"operationType": "insert"}}]) as stream:
            for change in stream:
                doc = change["fullDocument"]
                if not any(li.get("promo_id") for li in doc.get("line_items", [])):
                    continue
                promos = _process_order(db, doc["_id"])
                if promos:
                    print(f"  redemption {doc['_id']}  cust={doc.get('customer_id')}  "
                          f"promo={','.join(promos)}")
    except KeyboardInterrupt:
        print("\nReconcile listener stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redemption reconciliation listener.")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Recount redemptions from facts and exit.",
    )
    args = parser.parse_args()
    if args.rebuild:
        rebuild()
    else:
        run()
