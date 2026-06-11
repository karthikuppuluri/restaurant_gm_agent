"""
plumbing/factory_reset.py — return MongoDB to a pristine seeded state.

One command does everything, in order:
  1. drop every non-seed collection (agent writes, derived state, sim control)
  2. re-seed the dimensions + historical orders (seed_data.seed drops + inserts)
  3. rebuild the serving layer (live_metrics base + financials backfill)
  4. republish derived availability (low_stock / 86 list)
  5. recreate the unique indexes (the DB-level idempotency guards)

Stop all services first (./start.sh stop) — or use ./start.sh reset, which does.

Usage:
    python -m plumbing.factory_reset
"""

import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Everything that is NOT seeded reference data. seed_data.seed() handles the
# seven seed collections itself (drop + insert).
_NON_SEED = [
    "purchase_orders", "promotion_recommendations", "promotions",
    "campaign_sends", "agent_events", "waste_events",
    "live_metrics", "financials", "sim_control",
]


def ensure_indexes(db) -> None:
    """Unique indexes = DB-level guards against duplicate agent writes.
    Keep in sync with backend.app._ensure_indexes (backend re-ensures on boot)."""
    db.promotions.create_index("promo_id", unique=True)
    db.promotions.create_index("recommendation_id", unique=True)
    db.promotion_recommendations.create_index("recommendation_id", unique=True)
    db.purchase_orders.create_index("po_id", unique=True)
    db.purchase_orders.create_index(
        "line_items.ingredient_id", unique=True,
        partialFilterExpression={"status": "placed"},
        name="one_open_po_per_ingredient",
    )


def main() -> None:
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[os.environ.get("MONGODB_DB_NAME", "restaurant_gm")]

    print("1/5 dropping non-seed collections…")
    for coll in _NON_SEED:
        db[coll].drop()

    print("2/5 re-seeding dimensions + historical orders…")
    from seed_data import seed
    seed(db)

    print("3/5 rebuilding serving layer (live_metrics + financials)…")
    from plumbing.rollups import rebuild as rollups_rebuild
    rollups_rebuild()

    print("4/5 republishing availability (low_stock / 86)…")
    from plumbing.depletion import publish_availability
    publish_availability(db)

    print("5/5 recreating unique indexes…")
    ensure_indexes(db)

    lm = db.live_metrics.find_one({"_id": "current"})
    print(f"\nFactory reset complete — sim resumes after {lm['as_of'][:10]}, "
          f"cash ${lm['cash_on_hand_money']['amount'] / 100:,.2f}, "
          f"{db.orders.count_documents({})} seed orders, "
          f"{db.financials.count_documents({})} financial days.")
    client.close()


if __name__ == "__main__":
    main()
