"""Deterministic read helpers for the Restaurant GM agents.

Each function is a pre-baked, driver-backed query workflow exposed to the agents
as a plain ADK function tool. One call returns everything a fixed workflow needs,
so the model never authors complex aggregation pipelines (the Gemini
print(default_api...) codegen bug fires exactly there) and never does arithmetic
(invariant #3 — sums, margins, qty rounding, and date math all happen here).

Division of labor (invariant #4 kept intact):
  - READS for fixed workflows  -> these helpers (MongoDB driver)
  - WRITES by agents           -> MongoDB MCP server (insert-many / update-many)
  - ad-hoc analyst reads       -> MongoDB MCP server (find / aggregate)

All money values are integer cents (suffix `_cents`); agents format Money
objects ({"amount": cents, "currency": "USD"}) only when writing.
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

_client = None


def _db():
    global _client
    if _client is None:
        uri = os.environ["MONGODB_CONNECTION_STRING"]
        _client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return _client[os.environ.get("MONGODB_DB_NAME", "restaurant_gm")]


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _sim_now(db) -> str | None:
    doc = db.orders.find_one({}, {"opened_at": 1}, sort=[("opened_at", -1)])
    return doc["opened_at"] if doc else None


def _ingredient_usage_per_hour(db, sim_now: str, hours: float = 24.0) -> dict:
    """ingredient_id -> units consumed per hour over the trailing window,
    derived from order line items x the recipe BOM."""
    start = _ts(_parse(sim_now) - timedelta(hours=hours))
    item_qty = {
        r["_id"]: r["qty"]
        for r in db.orders.aggregate([
            {"$match": {"opened_at": {"$gte": start, "$lte": sim_now}}},
            {"$unwind": "$line_items"},
            {"$group": {"_id": "$line_items.item_id",
                        "qty": {"$sum": "$line_items.quantity"}}},
        ])
    }
    usage = {}
    for rec in db.recipes.find({}, {"menu_item_id": 1, "ingredients": 1, "yield_qty": 1}):
        sold = item_qty.get(rec["menu_item_id"], 0)
        if not sold:
            continue
        servings = sold / max(rec.get("yield_qty", 1), 1)
        for ing in rec.get("ingredients", []):
            usage[ing["ingredient_id"]] = usage.get(ing["ingredient_id"], 0) + servings * ing["qty"]
    return {k: round(v / hours, 3) for k, v in usage.items()}


def get_sim_now() -> dict:
    """Current simulation time (the opened_at of the most recent order), plus
    precomputed offsets (sim_now_plus_2h, sim_now_plus_24h) for validity windows.
    Use these RFC3339 strings directly — never compute time yourself."""
    db = _db()
    now = _sim_now(db)
    if not now:
        return {"error": "no orders found"}
    dt = _parse(now)
    return {
        "sim_now": now,
        "sim_now_plus_2h": _ts(dt + timedelta(hours=2)),
        "sim_now_plus_24h": _ts(dt + timedelta(hours=24)),
        "sim_now_plus_72h": _ts(dt + timedelta(hours=72)),
    }


def stock_snapshot() -> dict:
    """Full inventory picture in one call: every ingredient's stock level with
    needs_reorder / is_zero flags and hours_of_cover, the derived 86 list,
    cash on hand, and ready-made suggested purchase orders (one per vendor,
    quantities rounded up to vendor minimums, totals and expected_delivery
    precomputed) for ingredients below reorder point with no open PO."""
    db = _db()
    sim_now = _sim_now(db)
    if not sim_now:
        return {"error": "no orders found"}
    usage = _ingredient_usage_per_hour(db, sim_now)

    lm = db.live_metrics.find_one({"_id": "current"}, {"cash_on_hand_money": 1}) or {}
    cash = lm.get("cash_on_hand_money", {}).get("amount")

    # Open POs (draft/placed) suppress re-ordering of the same ingredient.
    covered = set()
    for po in db.purchase_orders.find(
            {"status": {"$in": ["draft", "placed"]}}, {"line_items.ingredient_id": 1}):
        covered.update(li["ingredient_id"] for li in po.get("line_items", []))

    ingredients, zero_ids, to_order = [], [], []
    for r in db.raw_ingredients.find({}, {"_id": 0, "ingredient_id": 1, "name": 1, "unit": 1,
                                          "on_hand_qty": 1, "reorder_point": 1, "par_level": 1,
                                          "preferred_vendor_id": 1}):
        per_hour = usage.get(r["ingredient_id"], 0)
        is_zero = r["on_hand_qty"] <= 0
        needs = r["on_hand_qty"] < r["reorder_point"]
        ingredients.append({
            "ingredient_id": r["ingredient_id"], "name": r["name"], "unit": r["unit"],
            "on_hand_qty": r["on_hand_qty"], "reorder_point": r["reorder_point"],
            "par_level": r["par_level"], "usage_per_hour": per_hour,
            "hours_of_cover": round(r["on_hand_qty"] / per_hour, 1) if per_hour else None,
            "needs_reorder": needs, "is_zero": is_zero,
            "has_open_po": r["ingredient_id"] in covered,
        })
        if is_zero:
            zero_ids.append(r["ingredient_id"])
        if needs and r["ingredient_id"] not in covered:
            to_order.append(r)

    eighty_sixed = sorted(db.recipes.distinct(
        "menu_item_id", {"ingredients.ingredient_id": {"$in": zero_ids}})) if zero_ids else []

    suggested = _group_pos(db, [(r, r["par_level"] - r["on_hand_qty"]) for r in to_order],
                           sim_now) if to_order else []

    return {
        "sim_now": sim_now,
        "cash_on_hand_cents": cash,
        "ingredients": ingredients,
        "eighty_sixed_item_ids": eighty_sixed,
        "suggested_purchase_orders": suggested,
    }


# Vendors deliver at the start of the business day (before the 10:00 open),
# not at the same clock time the PO was placed — like real restaurants.
_DELIVERY_HOUR = 8


def _delivery_ts(now_dt: datetime, lead_time_days: int) -> str:
    d = (now_dt + timedelta(days=lead_time_days)).replace(
        hour=_DELIVERY_HOUR, minute=0, second=0)
    return _ts(d)


def _group_pos(db, wants: list, sim_now: str) -> list:
    """wants: [(raw_ingredient doc, desired_qty)] -> ready-made POs grouped by
    preferred vendor: qty rounded UP to the vendor minimum, line totals,
    total_cents, expected_delivery (morning of the delivery date, longest lead
    time in the PO)."""
    vendor_ids = sorted({r["preferred_vendor_id"] for r, _ in wants})
    vendors = {v["vendor_id"]: v for v in db.vendors.find({"vendor_id": {"$in": vendor_ids}})}
    now_dt = _parse(sim_now)
    pos = {}
    for r, want in wants:
        v = vendors.get(r["preferred_vendor_id"])
        supply = next((s for s in (v or {}).get("supplies", [])
                       if s["ingredient_id"] == r["ingredient_id"]), None)
        if not supply:
            continue
        moq = supply["min_order_qty"]
        qty = max(want, moq)
        qty = -(-qty // moq) * moq  # round UP to a multiple of min_order_qty
        unit_cost = supply["unit_cost_money"]["amount"]
        po = pos.setdefault(v["vendor_id"], {
            "vendor_id": v["vendor_id"], "vendor_name": v["name"],
            "line_items": [], "total_cents": 0,
            "expected_delivery": _delivery_ts(now_dt, supply["lead_time_days"]),
        })
        po["line_items"].append({
            "ingredient_id": r["ingredient_id"], "name": r["name"], "qty": int(qty),
            "unit_cost_cents": unit_cost, "line_total_cents": int(qty) * unit_cost,
        })
        po["total_cents"] += int(qty) * unit_cost
        eta = _delivery_ts(now_dt, supply["lead_time_days"])
        if eta > po["expected_delivery"]:
            po["expected_delivery"] = eta
    return list(pos.values())


def po_quote(items: dict) -> dict:
    """Build ready-to-insert purchase-order quotes for SPECIFIC ingredients the GM
    asked to order, regardless of current stock level. `items` maps an ingredient
    id or (partial) name to the desired quantity — e.g. {"beef patty": 200,
    "ing_salsa": 0} — where 0 means "top up to par level". Returns quotes grouped
    by vendor (qty rounded to vendor minimums, totals, expected_delivery
    precomputed), cash_on_hand_cents, sim_now for timestamps, plus any unmatched
    names and a note of requested ingredients that already have an open PO."""
    db = _db()
    sim_now = _sim_now(db)
    if not sim_now:
        return {"error": "no orders found"}

    ings = list(db.raw_ingredients.find({}, {"_id": 0, "ingredient_id": 1, "name": 1,
                                             "on_hand_qty": 1, "par_level": 1,
                                             "preferred_vendor_id": 1}))
    wants, unmatched = [], []
    for key, qty in items.items():
        k = str(key).lower().strip()
        matches = ([r for r in ings if r["ingredient_id"] == k]
                   or [r for r in ings if k in r["ingredient_id"] or k in r["name"].lower()])
        if len(matches) != 1:
            unmatched.append({"requested": key,
                              "candidates": [r["ingredient_id"] for r in matches][:5]})
            continue
        r = matches[0]
        want = int(qty) if qty else max(int(r["par_level"] - r["on_hand_qty"]), 1)
        wants.append((r, want))

    requested_ids = [r["ingredient_id"] for r, _ in wants]
    already_open = sorted(db.purchase_orders.distinct(
        "line_items.ingredient_id",
        {"status": {"$in": ["draft", "placed"]},
         "line_items.ingredient_id": {"$in": requested_ids}})) if requested_ids else []

    lm = db.live_metrics.find_one({"_id": "current"}, {"cash_on_hand_money": 1}) or {}
    return {
        "sim_now": sim_now,
        "cash_on_hand_cents": lm.get("cash_on_hand_money", {}).get("amount"),
        "quotes": _group_pos(db, wants, sim_now),
        "unmatched": unmatched,
        "already_have_open_po": already_open,
    }


def sales_snapshot(window_hours: float = 2) -> dict:
    """Sales picture for a trailing window: revenue, order count, covers, top and
    bottom sellers with velocity, and pace vs the historical baseline for the
    same hours of day. window_hours: 2 = "right now", 24 = "today", 168 = week."""
    db = _db()
    sim_now = _sim_now(db)
    if not sim_now:
        return {"error": "no orders found"}
    now_dt = _parse(sim_now)
    start = _ts(now_dt - timedelta(hours=window_hours))

    totals = list(db.orders.aggregate([
        {"$match": {"opened_at": {"$gte": start, "$lte": sim_now}}},
        {"$group": {"_id": None, "revenue": {"$sum": "$net_amount_money.amount"},
                    "orders": {"$sum": 1}, "covers": {"$sum": "$guest_count"}}},
    ]))
    t = totals[0] if totals else {"revenue": 0, "orders": 0, "covers": 0}

    movers = list(db.orders.aggregate([
        {"$match": {"opened_at": {"$gte": start, "$lte": sim_now}}},
        {"$unwind": "$line_items"},
        {"$group": {"_id": "$line_items.item_id",
                    "name": {"$first": "$line_items.name"},
                    "qty": {"$sum": "$line_items.quantity"},
                    "revenue_cents": {"$sum": "$line_items.gross_money.amount"}}},
        {"$sort": {"qty": -1}},
    ]))
    for m in movers:
        m["item_id"] = m.pop("_id")
        m["velocity_per_hour"] = round(m["qty"] / window_hours, 2)

    # Baseline: seed (non-simulator) orders in the same hour-of-day band,
    # averaged per day, scaled to the window length.
    hours = sorted({(now_dt - timedelta(hours=h)).hour for h in range(int(window_hours) + 1)}) \
        if window_hours < 24 else list(range(24))
    base = list(db.orders.aggregate([
        {"$match": {"source": {"$ne": "simulator"}}},
        {"$addFields": {"hour": {"$toInt": {"$substr": ["$opened_at", 11, 2]}},
                        "day": {"$substr": ["$opened_at", 0, 10]}}},
        {"$match": {"hour": {"$in": hours}}},
        {"$group": {"_id": "$day", "rev": {"$sum": "$net_amount_money.amount"}}},
        {"$group": {"_id": None, "avg_rev": {"$avg": "$rev"}}},
    ]))
    baseline = round(base[0]["avg_rev"]) if base else None
    if window_hours > 24 and baseline is not None:
        baseline = round(baseline * window_hours / 24)
    pace = round((t["revenue"] - baseline) / baseline * 100, 1) if baseline else None

    return {
        "sim_now": sim_now, "window_hours": window_hours, "window_start": start,
        "revenue_cents": t["revenue"], "order_count": t["orders"], "covers": t["covers"],
        "top_items": movers[:5], "bottom_items": movers[-5:][::-1] if len(movers) > 5 else [],
        "baseline_revenue_cents": baseline, "pace_vs_baseline_pct": pace,
    }


def promo_evidence() -> dict:
    """Everything needed to build a promo recommendation in one call: 24h top
    sellers, per-candidate margins (incl. precomputed margin after 10/15/20/25%
    discounts), stock cover hours, pricing-rule guardrails, blackout flags, and
    the opted-in audience size with average price sensitivity."""
    db = _db()
    sim_now = _sim_now(db)
    if not sim_now:
        return {"error": "no orders found"}
    start = _ts(_parse(sim_now) - timedelta(hours=24))

    rules = db.pricing_rules.find_one({}, {"_id": 0, "min_margin_pct": 1,
                                           "max_discount_pct": 1, "blackout_item_ids": 1}) or {}
    blackout = set(rules.get("blackout_item_ids", []))

    top = list(db.orders.aggregate([
        {"$match": {"opened_at": {"$gte": start, "$lte": sim_now}}},
        {"$unwind": "$line_items"},
        {"$group": {"_id": "$line_items.item_id",
                    "name": {"$first": "$line_items.name"},
                    "qty": {"$sum": "$line_items.quantity"},
                    "revenue_cents": {"$sum": "$line_items.gross_money.amount"}}},
        {"$sort": {"revenue_cents": -1}},
        {"$limit": 5},
    ]))

    shift = list(db.orders.aggregate([
        {"$match": {"opened_at": {"$gte": start, "$lte": sim_now}}},
        {"$group": {"_id": None, "revenue": {"$sum": "$net_amount_money.amount"},
                    "discount": {"$sum": "$total_discount_money.amount"},
                    "orders": {"$sum": 1}}},
    ]))
    s = shift[0] if shift else {"revenue": 0, "discount": 0, "orders": 0}

    usage = _ingredient_usage_per_hour(db, sim_now)
    stock = {r["ingredient_id"]: r["on_hand_qty"]
             for r in db.raw_ingredients.find({}, {"ingredient_id": 1, "on_hand_qty": 1})}
    ing_cost = {r["ingredient_id"]: r["unit_cost_money"]["amount"]
                for r in db.raw_ingredients.find({}, {"ingredient_id": 1, "unit_cost_money": 1})}
    item_ids = [c["_id"] for c in top]
    menu = {m["item_id"]: m for m in db.menu_items.find({"item_id": {"$in": item_ids}})}
    recipes = {r["menu_item_id"]: r
               for r in db.recipes.find({"menu_item_id": {"$in": item_ids}})}

    candidates = []
    for c in top:
        item_id = c["_id"]
        m, rec = menu.get(item_id), recipes.get(item_id)
        if not m or not rec:
            continue
        price = m["price_money"]["amount"]
        food_cost = round(sum(i["qty"] * ing_cost.get(i["ingredient_id"], 0)
                              for i in rec["ingredients"]) / max(rec.get("yield_qty", 1), 1))
        margin = round((price - food_cost) / price * 100, 1) if price else 0
        after = {}
        for d in (10, 15, 20, 25):
            p = price * (100 - d) / 100
            after[str(d)] = round((p - food_cost) / p * 100, 1) if p else 0
        cover = [stock[i["ingredient_id"]] / usage[i["ingredient_id"]]
                 for i in rec["ingredients"]
                 if usage.get(i["ingredient_id"]) and i["ingredient_id"] in stock]
        candidates.append({
            "item_id": item_id, "name": c["name"], "qty_24h": c["qty"],
            "revenue_24h_cents": c["revenue_cents"], "price_cents": price,
            "food_cost_cents": food_cost, "margin_pct": margin,
            "margin_after_discount_pct": after,
            "stock_cover_hours": round(min(cover), 1) if cover else None,
            "blackout": item_id in blackout,
        })

    aud = list(db.customers.aggregate([
        {"$match": {"opt_in_marketing": True, "loyalty_tier": {"$in": ["silver", "gold"]}}},
        {"$group": {"_id": None, "count": {"$sum": 1},
                    "avg_price_sensitivity": {"$avg": "$price_sensitivity"}}},
    ]))
    audience = ({"count": aud[0]["count"],
                 "avg_price_sensitivity": round(aud[0]["avg_price_sensitivity"], 2)}
                if aud else {"count": 0, "avg_price_sensitivity": None})

    return {
        "sim_now": sim_now,
        "shift_24h": {"revenue_cents": s["revenue"], "discount_cents": s["discount"],
                      "order_count": s["orders"]},
        "candidates": candidates,
        "pricing_rules": {"min_margin_pct": rules.get("min_margin_pct"),
                          "max_discount_pct": rules.get("max_discount_pct"),
                          "blackout_item_ids": sorted(blackout)},
        "audience_opted_in_silver_gold": audience,
    }


def promo_audience(promo_id: str = "") -> dict:
    """The promo to push and its eligible audience in one call. Pass a promo_id,
    or leave empty for the most recent live promo. Returns the promo details and
    the customer_ids of opted-in customers matching its target_criteria
    (matching mirrors the simulator's eligibility rules exactly)."""
    db = _db()
    q = {"promo_id": promo_id} if promo_id else {"status": "live"}
    promo = db.promotions.find_one(q, sort=[("created_at", -1)])
    if not promo:
        return {"error": f"no promotion found for {q}"}
    criteria = promo.get("target_criteria", {})

    match = {"opt_in_marketing": True}
    if "loyalty_tier" in criteria:
        match["loyalty_tier"] = {"$in": criteria["loyalty_tier"]}
    if "max_price_sensitivity" in criteria:
        match["price_sensitivity"] = {"$lte": criteria["max_price_sensitivity"]}
    if "city" in criteria:
        match["city"] = criteria["city"]
    if "dietary_flags" in criteria:
        match["dietary_flags"] = {"$in": criteria["dietary_flags"]}
    customer_ids = sorted(db.customers.distinct("customer_id", match))

    return {
        "promo": {
            "promo_id": promo["promo_id"], "title": promo.get("title"),
            "description": promo.get("description"),
            "discount_type": promo.get("discount_type"),
            "discount_value": promo.get("discount_value"),
            "applies_to_item_ids": promo.get("applies_to_item_ids", []),
            "target_criteria": criteria,
            "valid_from": promo.get("valid_from"), "valid_until": promo.get("valid_until"),
        },
        "audience_count": len(customer_ids),
        "customer_ids": customer_ids,
    }
