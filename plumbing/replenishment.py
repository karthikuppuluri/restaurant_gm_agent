"""
plumbing/replenishment.py — PO replenishment (deterministic, NO LLM).

Mirror of depletion: when a purchase_order's status flips to `received`, this
listener increments `raw_ingredients.on_hand_qty` by the quantities in the PO's
line_items. The Inventory agent decides *when* and *how much* to reorder; plumbing
does the arithmetic of actually restocking the shelves.

The `received` transition is driven by the simulator: each sim tick, orders whose
`expected_delivery <= sim_now` are automatically flipped to `received` (see
simulator.py `_receive_overdue_pos`). In production this would be a human action.

Idempotency: each processed PO is claimed with `replenished: true` via an atomic
find-and-update, so a restart (which re-scans pending POs on startup) never
double-replenishes.

Rebuild order: call `depletion.rebuild()` first (resets stock to baseline and
re-depletes), then `replenishment.rebuild()` (re-applies received POs on top).

Usage:
    python -m plumbing.replenishment              # catch up, then watch the stream
    python -m plumbing.replenishment --rebuild    # re-apply all received POs and exit
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

from plumbing.depletion import publish_availability

load_dotenv()


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def _apply_replenishment(db, po: dict) -> None:
    """Increment on_hand_qty for each ingredient in the PO's line_items."""
    for li in po.get("line_items", []):
        db.raw_ingredients.update_one(
            {"_id": li["ingredient_id"]},
            {"$inc": {"on_hand_qty": li["qty"]}, "$set": {"updated_at": _now()}},
        )


def _process_po(db, po_id) -> bool:
    """Atomically claim a received PO (set replenished=true) and restock its
    ingredients. Returns False if already processed — the idempotency guard."""
    claimed = db.purchase_orders.find_one_and_update(
        {"_id": po_id, "status": "received", "replenished": {"$ne": True}},
        {"$set": {"replenished": True}},
    )
    if claimed is None:
        return False
    _apply_replenishment(db, claimed)
    # Restock changes availability — republish low_stock/86 so the dashboard
    # reflects the delivery immediately (not on the next agent run).
    publish_availability(db)
    return True


# ── operations ────────────────────────────────────────────────────────────────

def _catch_up(db) -> int:
    """Restock from any received POs that landed while the listener was down."""
    pending = [p["_id"] for p in db.purchase_orders.find(
        {"status": "received", "replenished": {"$ne": True}}, {"_id": 1}
    )]
    n = 0
    for po_id in pending:
        if _process_po(db, po_id):
            n += 1
    return n


def rebuild() -> None:
    """Re-apply all received POs from scratch. Run AFTER depletion.rebuild() so
    the ingredient baseline is correct before replenishments are layered on top."""
    client, db = _connect()
    try:
        # Clear replenished flag so every received PO gets re-applied.
        db.purchase_orders.update_many(
            {"status": "received"},
            {"$unset": {"replenished": ""}},
        )
        n = _catch_up(db)
        print(f"Rebuilt replenishment: re-applied {n} received purchase order(s).")
    finally:
        client.close()


def run() -> None:
    client, db = _connect()
    try:
        n = _catch_up(db)
        print(f"Catch-up: restocked from {n} pending received PO(s).")
        print("Watching purchase_orders for status → received… Ctrl-C to stop.\n")

        # full_document="updateLookup" fetches the current doc on update events
        # so we have line_items available without a second round-trip.
        pipeline = [{"$match": {
            "operationType": "update",
            "updateDescription.updatedFields.status": "received",
        }}]
        with db.purchase_orders.watch(pipeline, full_document="updateLookup") as stream:
            for change in stream:
                doc = change.get("fullDocument")
                if not doc:
                    continue
                if _process_po(db, doc["_id"]):
                    n_items = len(doc.get("line_items", []))
                    print(f"  restocked from {doc['_id']}  ({n_items} ingredient(s))")
    except KeyboardInterrupt:
        print("\nReplenishment listener stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PO replenishment listener.")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Re-apply all received POs from facts and exit (run after depletion --rebuild).",
    )
    args = parser.parse_args()
    if args.rebuild:
        rebuild()
    else:
        run()
