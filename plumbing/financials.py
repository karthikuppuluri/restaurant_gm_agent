"""
plumbing/financials.py — daily P&L rollups (deterministic, NO LLM).

Populates the `financials` collection: one doc per sim-day, derived entirely
from `orders` x the static BOM (menu_items -> recipes -> raw_ingredients unit
costs). Revenue is PRE-TAX (tax is a passthrough, not restaurant revenue):

  gross_revenue = sum(orders.total_money)            # list-price sales
  discount      = sum(orders.total_discount_money)
  net_revenue   = gross_revenue - discount
  cogs          = sum(line qty x item food cost)     # food cost from the BOM
  gross_margin_pct = (net_revenue - cogs) / net_revenue

Approach: RECOMPUTE, not increment (same philosophy as rollups.py). `rollup_day`
rebuilds one day's doc from the orders that exist right now, so it is idempotent
and reset-safe. `backfill` recomputes every day present in `orders` and deletes
plumbing-written docs for days that no longer have orders (the --reset/--undo
path; memory: reset-rolls-back-derived-state).

Live updates: rollups.py imports `rollup_day` and calls it for the inserted
order's day on each change-stream event, so the current sim-day's P&L stays
current without a second listener.

STARTING_CASH_CENTS seeds `live_metrics.cash_on_hand_money` (computed in
rollups.py): STARTING_CASH + all-time net revenue - committed vendor spend.
This is *operating cash* — food sales minus food purchasing; no rent/labor/tax.

Usage:
    python -m plumbing.financials           # backfill all days and exit
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Operating cash at the start of the simulation. Overridable for demos.
STARTING_CASH_CENTS = int(os.environ.get("STARTING_CASH_CENTS", 2_500_000))  # $25,000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(cents: int) -> dict:
    return {"amount": int(cents), "currency": "USD"}


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def item_maps(db):
    """Static item_id -> food-cost-cents and item_id -> category maps.

    Food cost per serving = sum(ingredient qty x ingredient unit cost) / yield_qty.
    menu_items/recipes/raw_ingredient costs are static reference data, so compute
    once at startup and reuse.
    """
    ing_cost = {
        r["ingredient_id"]: r["unit_cost_money"]["amount"]
        for r in db.raw_ingredients.find({}, {"ingredient_id": 1, "unit_cost_money": 1})
    }
    recipes = {
        r["recipe_id"]: r
        for r in db.recipes.find({}, {"recipe_id": 1, "ingredients": 1, "yield_qty": 1})
    }
    cost_map, cat_map = {}, {}
    for m in db.menu_items.find({}, {"item_id": 1, "recipe_id": 1, "category": 1}):
        rec = recipes.get(m.get("recipe_id"))
        cost = 0
        if rec:
            total = sum(i["qty"] * ing_cost.get(i["ingredient_id"], 0)
                        for i in rec.get("ingredients", []))
            cost = round(total / max(rec.get("yield_qty", 1), 1))
        cost_map[m["item_id"]] = int(cost)
        cat_map[m["item_id"]] = m.get("category", "other")
    return cost_map, cat_map


def rollup_day(db, day: str, cost_map: dict, cat_map: dict):
    """Recompute the financials doc for one sim-day (YYYY-MM-DD). Idempotent.

    Counts ALL orders that day (seed baseline + simulator). Deletes the doc if
    the day has no orders left (post-reset). Returns the computed summary or None.
    """
    start, end = f"{day}T00:00:00Z", f"{day}T23:59:59Z"
    orders = list(db.orders.find(
        {"opened_at": {"$gte": start, "$lte": end}},
        {"line_items": 1, "total_money": 1, "total_discount_money": 1},
    ))
    if not orders:
        db.financials.delete_one({"_id": day, "source": "plumbing"})
        return None

    gross = discount = cogs = 0
    by_cat = {}  # category -> [revenue_cents, cogs_cents]
    for o in orders:
        gross += o["total_money"]["amount"]
        discount += o["total_discount_money"]["amount"]
        for li in o["line_items"]:
            line_cogs = li["quantity"] * cost_map.get(li["item_id"], 0)
            cogs += line_cogs
            rev = li["gross_money"]["amount"] - li.get("applied_discount_money", _money(0))["amount"]
            cat = by_cat.setdefault(cat_map.get(li["item_id"], "other"), [0, 0])
            cat[0] += rev
            cat[1] += line_cogs

    net = gross - discount
    margin_pct = round((net - cogs) / net * 100, 1) if net else 0.0
    by_category = [
        {"category": c, "revenue_money": _money(rev),
         "margin_pct": round((rev - cg) / rev * 100, 1) if rev else 0.0}
        for c, (rev, cg) in sorted(by_cat.items())
    ]

    now = _now()
    db.financials.update_one(
        {"_id": day},
        {
            "$set": {
                "period_id": day,
                "period_start": start,
                "period_end": end,
                "gross_revenue_money": _money(gross),
                "cogs_money": _money(cogs),
                "discount_money": _money(discount),
                "net_revenue_money": _money(net),
                "gross_margin_pct": margin_pct,
                "by_category": by_category,
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
    return {"day": day, "orders": len(orders), "net": net, "margin_pct": margin_pct}


def backfill(db) -> int:
    """Recompute a financials doc for every day present in `orders`, and remove
    plumbing-written docs for days with no orders (reset/undo path)."""
    cost_map, cat_map = item_maps(db)
    days = sorted({ts[:10] for ts in db.orders.distinct("opened_at")})
    for day in days:
        r = rollup_day(db, day, cost_map, cat_map)
        if r:
            print(f"  {day}  orders={r['orders']:3d}  net=${r['net']/100:9.2f}  "
                  f"margin={r['margin_pct']}%")
    stale = db.financials.delete_many({"_id": {"$nin": days}, "source": "plumbing"})
    if stale.deleted_count:
        print(f"  removed {stale.deleted_count} stale day(s)")
    return len(days)


def rebuild() -> None:
    """Recompute all financials docs from facts (reset/undo path)."""
    client, db = _connect()
    try:
        n = backfill(db)
        print(f"Rebuilt financials: {n} day(s)")
    finally:
        client.close()


if __name__ == "__main__":
    rebuild()
