"""
plumbing/depletion.py — BOM stock depletion (deterministic, NO LLM).

Reacts to each simulated order the instant it lands and decrements
`raw_ingredients.on_hand_qty` by the bill-of-materials math. Live, order-by-order
via a MongoDB change stream — never batched — so the Inventory agent always reads
current stock.

Only `source: "simulator"` orders deplete stock. Seed/historical orders are
analytics history and the seeded `on_hand_qty` is "stock now," so they are
ignored.

Idempotency: each processed order is claimed with `depleted: true` via an atomic
find-and-update, so a restart (which re-scans pending orders on startup) never
double-depletes.

Usage:
    python -m plumbing.depletion              # catch up, then watch the stream
    python -m plumbing.depletion --rebuild    # recompute stock from facts (reset path)

`rebuild()` resets stock to the seed baseline and re-depletes from the simulator
orders that currently remain — this is what makes the simulator's --reset/--undo
restore ingredients (see memory: reset-rolls-back-derived-state).
"""

import argparse
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    # Fail fast on a flaky network instead of hanging silently for 30s+.
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def _load_bom(db):
    """Build the static lookup maps once: item_id → recipe_id, recipe_id → recipe."""
    recipes = {r["recipe_id"]: r for r in db.recipes.find({}, {"_id": 0})}
    item_to_recipe = {m["item_id"]: m["recipe_id"] for m in db.menu_items.find({}, {"_id": 0})}
    return item_to_recipe, recipes


def _depletion_for_order(order, item_to_recipe, recipes) -> dict:
    """ingredient_id → total qty to subtract for one order (sums across line items)."""
    totals = {}
    for li in order.get("line_items", []):
        recipe_id = item_to_recipe.get(li["item_id"])
        recipe = recipes.get(recipe_id) if recipe_id else None
        if not recipe:
            continue
        yield_qty = recipe.get("yield_qty", 1) or 1
        for ing in recipe["ingredients"]:
            amt = ing["qty"] * li["quantity"] / yield_qty
            totals[ing["ingredient_id"]] = totals.get(ing["ingredient_id"], 0) + amt
    return totals


def _apply_depletion(db, totals: dict) -> None:
    for ing_id, amt in totals.items():
        db.raw_ingredients.update_one(
            {"_id": ing_id},
            {"$inc": {"on_hand_qty": -amt}, "$set": {"updated_at": _now()}},
        )


def _process_order(db, order_id, item_to_recipe, recipes) -> bool:
    """Atomically claim an order (set depleted=true) and deplete its BOM.

    Returns False if the order was already processed or is not a simulator order —
    this is the idempotency guard that makes restarts safe.
    """
    claimed = db.orders.find_one_and_update(
        {"_id": order_id, "source": "simulator", "depleted": {"$ne": True}},
        {"$set": {"depleted": True}},
    )
    if claimed is None:
        return False
    _apply_depletion(db, _depletion_for_order(claimed, item_to_recipe, recipes))
    return True


# ── operations ────────────────────────────────────────────────────────────────

def _catch_up(db, item_to_recipe, recipes) -> int:
    """Deplete any simulator orders that landed while the listener was down."""
    pending = [o["_id"] for o in db.orders.find(
        {"source": "simulator", "depleted": {"$ne": True}}, {"_id": 1}
    )]
    n = 0
    for order_id in pending:
        if _process_order(db, order_id, item_to_recipe, recipes):
            n += 1
    return n


def rebuild() -> None:
    """Recompute stock from facts: reset to seed baseline, re-deplete remaining
    simulator orders. Used by the simulator's --reset/--undo so deleted orders
    give their ingredients back."""
    from seed_data import RAW_INGREDIENTS  # lazy: only needed on the reset path

    client, db = _connect()
    try:
        # 1. Reset every ingredient to its seeded "stock now" baseline.
        for ing in RAW_INGREDIENTS:
            db.raw_ingredients.update_one(
                {"_id": ing["ingredient_id"]},
                {"$set": {"on_hand_qty": ing["on_hand_qty"], "updated_at": _now()}},
            )

        # 2. Re-deplete from the simulator orders that currently remain.
        item_to_recipe, recipes = _load_bom(db)
        totals = {}
        for order in db.orders.find({"source": "simulator"}, {"line_items": 1}):
            for ing_id, amt in _depletion_for_order(order, item_to_recipe, recipes).items():
                totals[ing_id] = totals.get(ing_id, 0) + amt
        _apply_depletion(db, totals)

        n = db.orders.count_documents({"source": "simulator"})
        print(f"Rebuilt stock: reset to baseline, re-depleted from {n} simulator orders.")
    finally:
        client.close()


def run() -> None:
    client, db = _connect()
    item_to_recipe, recipes = _load_bom(db)
    try:
        n = _catch_up(db, item_to_recipe, recipes)
        print(f"Catch-up: depleted {n} pending simulator order(s).")
        print("Watching orders for new simulator inserts… Ctrl-C to stop.\n")

        with db.orders.watch([{"$match": {"operationType": "insert"}}]) as stream:
            for change in stream:
                doc = change["fullDocument"]
                if doc.get("source") != "simulator":
                    continue
                if _process_order(db, doc["_id"], item_to_recipe, recipes):
                    print(f"  depleted {doc['_id']}  ({len(doc.get('line_items', []))} line items)")
    except KeyboardInterrupt:
        print("\nDepletion listener stopped.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BOM stock depletion listener.")
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Recompute stock from facts (reset to baseline + re-deplete) and exit.",
    )
    args = parser.parse_args()
    if args.rebuild:
        rebuild()
    else:
        run()
