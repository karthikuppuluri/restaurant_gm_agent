"""
plumbing/simulator.py — order stream generator.

Each run simulates exactly ONE service day, then exits. The day simulated is the
day AFTER the latest order already in `orders` — so the first run continues from
the seeded history (ends 2026-06-08 → simulates 2026-06-09), and each subsequent
run advances one more day. Re-runnable and idempotent: order IDs are UUID-based,
and the next day is always derived from the DB, never from wall-clock time.

Orders arrive on a Poisson clock with lunch (~12:00) and dinner (~19:00) peaks.
Closed hours (rate 0) are fast-forwarded so real time is spent only on the
service window. Reference data is loaded once at startup; this script writes
only to `orders`. Stock depletion, metric rollups, and redemption reconciliation
react to these inserts via change streams (Tasks 4, 5, 12).

Usage:
    python -m plumbing.simulator
    SIM_SPEED=300 python -m plumbing.simulator

SIM_SPEED: sim-seconds that elapse per real second (default 150 → a full service
day streams in ~5 real minutes).
"""

import argparse
import hashlib
import math
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# ── constants ─────────────────────────────────────────────────────────────────

SIM_SPEED = int(os.environ.get("SIM_SPEED", 150))

# Fixed sim-seconds advanced per tick. Decouples arrival granularity from speed:
# every sim-minute is sampled regardless of SIM_SPEED, so cranking the speed up
# never skips hours. SIM_SPEED only controls how long each step takes in real time.
SIM_STEP = 60

# Expected orders per sim-hour by hour-of-day.
# Lunch peak at 12:00, dinner peak at 19:00.
HOURLY_RATE = {
    0: 0,  1: 0,  2: 0,  3: 0,  4: 0,  5: 0,
    6: 0,  7: 0,  8: 0,  9: 0,  10: 1,
    11: 4, 12: 8, 13: 6, 14: 2, 15: 1,
    16: 2, 17: 5, 18: 9, 19: 12, 20: 8,
    21: 4, 22: 1, 23: 0,
}

_TAX_BPS_FALLBACK = 875  # used if pricing_rules unavailable

# Singleton control doc in `sim_control` that a frontend (or --pause/--resume)
# flips to pause/resume a running simulation. Coordinated through Mongo so the
# controller and the sim process need not share a machine.
_CONTROL_ID = "sim"


# ── helpers ───────────────────────────────────────────────────────────────────

def _poisson(lam: float) -> int:
    """Knuth algorithm — sample Poisson(lam) without numpy."""
    if lam <= 0:
        return 0
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(cents: int) -> dict:
    return {"amount": cents, "currency": "USD"}


def _matches_criteria(customer: dict, criteria: dict) -> bool:
    """True if customer satisfies every field present in target_criteria."""
    if "loyalty_tier" in criteria:
        if customer.get("loyalty_tier") not in criteria["loyalty_tier"]:
            return False
    if "max_price_sensitivity" in criteria:
        if customer.get("price_sensitivity", 1.0) > criteria["max_price_sensitivity"]:
            return False
    if "city" in criteria:
        if customer.get("city") != criteria["city"]:
            return False
    if "dietary_flags" in criteria:
        required = set(criteria["dietary_flags"])
        if not required.intersection(set(customer.get("dietary_flags", []))):
            return False
    if "favorite_item_ids" in criteria:
        wanted = set(criteria["favorite_item_ids"])
        if not wanted.intersection(set(customer.get("favorite_item_ids", []))):
            return False
    return True


# ── order builder ─────────────────────────────────────────────────────────────

def _build_order(
    sim_now: datetime,
    customers: list,
    items: list,
    item_weights: list,
    price_map: dict,
    name_map: dict,
    tax_rate_bps: int,
    db,
    bom: dict,
) -> dict | None:
    # Customer: 60% known, 40% walk-in
    if customers and random.random() < 0.60:
        customer = random.choice(customers)
        customer_id = customer["customer_id"]
    else:
        customer = None
        customer_id = None

    channel = random.choices(["DINE_IN", "TAKEOUT"], weights=[60, 40])[0]
    guest_count = random.randint(1, 6) if channel == "DINE_IN" else 1

    # One live-promo lookup per order: it drives BOTH the demand lift on promoted
    # items (below) and redemption eligibility (after the lines are built).
    promo = db.promotions.find_one(
        {"status": "live",
         "valid_from": {"$lte": _ts(sim_now)},
         "valid_until": {"$gte": _ts(sim_now)}},
        {"_id": 0},
    )

    # Demand lift: a promo SOMETIMES makes its items more popular — and sometimes
    # does nothing (real promos flop too). The factor is derived from a hash of
    # the promo_id so it is stable for the promo's whole life and across ticks:
    # one third of promos get no lift, the rest get 1.75x or 2.5x pick weight.
    # This is what makes predicted vs actual uptake genuinely diverge.
    lift, lifted_ids = 1.0, set()
    if promo:
        h = int(hashlib.md5(promo["promo_id"].encode()).hexdigest(), 16)
        lift = 1.0 + 0.75 * (h % 3)
        lifted_ids = set(promo.get("applies_to_item_ids", []))

    # Availability gate: a customer cannot order an item whose ingredients are
    # out (86 is derived from stock — invariant #2 — and the simulator must
    # respect it, otherwise depletion drives on_hand_qty negative). `stock` is a
    # local view, decremented per line so one order can't oversell itself either.
    stock = {r["_id"]: r["on_hand_qty"] for r in db.raw_ingredients.find({}, {"on_hand_qty": 1})}

    def _can_make(item_id: str) -> int:
        needs = bom.get(item_id)
        if not needs:
            return 0
        return int(min(stock.get(i, 0) / q for i, q in needs.items() if q > 0))

    available = [(it, w) for it, w in zip(items, item_weights)
                 if _can_make(it["item_id"]) >= 1]
    if not available:
        return None  # everything is 86'd — customer turned away
    avail_items = [x[0] for x in available]
    avail_weights = [w * lift if it["item_id"] in lifted_ids else w
                     for it, w in available]

    # Personal taste: a known customer orders their favorites ~3x as often —
    # this is what makes affinity-targeted promos measurably real (taco fans
    # actually buy tacos).
    if customer:
        favs = set(customer.get("favorite_item_ids", []))
        if favs:
            avail_weights = [w * 3 if it["item_id"] in favs else w
                             for it, w in zip(avail_items, avail_weights)]

    chosen_items = random.choices(avail_items, weights=avail_weights, k=random.randint(1, 4))
    line_items = []
    for j, item in enumerate(chosen_items):
        cap = _can_make(item["item_id"])
        if cap < 1:
            continue  # an earlier line in this order used up the stock
        # Quantity skews low: mostly 1-2 of an item, big multiples are rare.
        qty = min(random.choices([1, 2, 3, 4, 5, 6],
                                 weights=[45, 28, 14, 7, 4, 2])[0], cap)
        for ing_id, need in bom[item["item_id"]].items():
            stock[ing_id] = stock.get(ing_id, 0) - need * qty
        price = price_map[item["item_id"]]
        gross = price * qty
        line_items.append({
            "uid": f"li_{j + 1}",
            "item_id": item["item_id"],
            "name": name_map[item["item_id"]],
            "quantity": qty,
            "unit_price_money": _money(price),
            "gross_money": _money(gross),
            "applied_discount_money": _money(0),
            "promo_id": None,
        })
    if not line_items:
        return None

    # Redemption — eligibility is promotions.target_criteria, not campaign_sends
    # (the push is only a probability boost). Walk-ins have no profile, so they
    # can redeem ONLY open promos (empty criteria — _matches_criteria({}, {}) is
    # True, while any profile-keyed criteria correctly excludes them).
    if promo and _matches_criteria(customer or {}, promo.get("target_criteria", {})):
        if customer:
            p_redeem = customer.get("price_sensitivity", 0.5)
            has_send = db.campaign_sends.find_one(
                {"promo_id": promo["promo_id"], "customer_id": customer_id},
                {"_id": 1},
            )
            if has_send:
                p_redeem = min(1.0, p_redeem * 1.4)
            # Affinity: a promo on an item this customer already loves converts
            # better — this is what makes WELL-TARGETED promos systematically
            # beat mistargeted ones (on top of the random per-promo lift).
            if set(promo.get("applies_to_item_ids", [])) & set(customer.get("favorite_item_ids", [])):
                p_redeem = min(1.0, p_redeem * 1.5)
        else:
            p_redeem = 0.4  # walk-ins notice in-store signage, no push boost

        if random.random() < p_redeem:
            eligible_ids = set(promo.get("applies_to_item_ids", []))
            dtype = promo.get("discount_type", "PERCENTAGE")
            dvalue = promo.get("discount_value", 0)
            for li in line_items:
                if li["item_id"] in eligible_ids:
                    if dtype == "PERCENTAGE":
                        disc = li["unit_price_money"]["amount"] * li["quantity"] * dvalue // 100
                    else:
                        disc = dvalue * li["quantity"]
                    li["applied_discount_money"] = _money(disc)
                    li["promo_id"] = promo["promo_id"]

    total_gross = sum(li["gross_money"]["amount"] for li in line_items)
    total_discount = sum(li["applied_discount_money"]["amount"] for li in line_items)
    taxable = total_gross - total_discount
    tax = taxable * tax_rate_bps // 10000
    net = taxable + tax

    duration_mins = random.randint(10, 20) if channel == "TAKEOUT" else random.randint(20, 55)
    closed = sim_now + timedelta(minutes=duration_mins)
    order_id = f"ord_sim_{sim_now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

    return {
        "_id": order_id,
        "order_id": order_id,
        "state": "COMPLETED",
        "channel": channel,
        "customer_id": customer_id,
        "guest_count": guest_count,
        "opened_at": _ts(sim_now),
        "closed_at": _ts(closed),
        "line_items": line_items,
        "total_money": _money(total_gross),
        "total_tax_money": _money(tax),
        "total_discount_money": _money(total_discount),
        "net_amount_money": _money(net),
        "source": "simulator",
        "schema_version": 1,
        "created_at": _ts(sim_now),
        "updated_at": _ts(closed),
    }


# ── spoilage ──────────────────────────────────────────────────────────────────

def _spoilage_pass(db, sim_now: datetime) -> int:
    """Daily spoilage, run once at the start of each simulated day (BEFORE the
    morning deliveries land). No-lot-tracking approximation of FIFO expiry:
    stock beyond what current demand can consume within an ingredient's
    shelf_life_days is "old", and roughly 1/shelf_life of that excess expires
    each day (gradual decay, not a cliff). Every toss is logged to waste_events
    — the recurring cost signal that lets the Inventory agent learn what not to
    keep buying. Deterministic plumbing (invariant #3)."""
    from restaurant_gm.queries import _ingredient_usage_per_hour
    usage = _ingredient_usage_per_hour(db, _ts(sim_now))
    n, total_cents = 0, 0
    for r in db.raw_ingredients.find({}, {"ingredient_id": 1, "name": 1, "unit": 1,
                                          "on_hand_qty": 1, "reorder_point": 1,
                                          "shelf_life_days": 1, "unit_cost_money": 1}):
        shelf = r.get("shelf_life_days")
        if not shelf:
            continue
        daily = usage.get(r["ingredient_id"], 0) * 24
        # what demand can consume before it expires (floored at the reorder point
        # so normal working stock isn't tossed)
        keep = max(daily * shelf, r.get("reorder_point", 0))
        excess = r["on_hand_qty"] - keep
        waste = round(excess / shelf, 3) if excess > 0 else 0
        if waste < 0.05:
            continue
        cost = round(waste * r["unit_cost_money"]["amount"])
        db.raw_ingredients.update_one(
            {"_id": r["ingredient_id"]},
            [{"$set": {"on_hand_qty": {"$max": [0, {"$round": [
                {"$subtract": ["$on_hand_qty", waste]}, 3]}]},
                "updated_at": _ts(sim_now)}}],
        )
        db.waste_events.insert_one({
            "waste_id": f"waste_{uuid.uuid4().hex[:8]}",
            "ingredient_id": r["ingredient_id"], "name": r["name"], "unit": r["unit"],
            "qty": waste, "cost_cents": cost,
            "day": _ts(sim_now)[:10],
            "reason": "past shelf life",
            "created_at": _ts(sim_now),
            "source": "simulator", "schema_version": 1,
        })
        n += 1
        total_cents += cost
    if n:
        print(f"  [WASTE] start of day: tossed {n} ingredient(s) past shelf life — "
              f"${total_cents / 100:.2f}")
    return n


# ── PO receipt ────────────────────────────────────────────────────────────────

def _receive_overdue_pos(db, sim_now: datetime) -> int:
    """Flip placed POs whose expected_delivery has passed to received.

    The Inventory agent writes expected_delivery as an ISO 8601 string
    (e.g. "2026-06-10T00:00:00Z"). ISO strings sort lexicographically, so a
    string comparison against _ts(sim_now) is correct and avoids datetime parsing.
    """
    result = db.purchase_orders.update_many(
        {"status": "placed", "expected_delivery": {"$lte": _ts(sim_now)}},
        {"$set": {"status": "received", "received_at": _ts(sim_now), "updated_at": _ts(sim_now)}},
    )
    return result.modified_count


def _expire_promos(db, sim_now: datetime) -> int:
    """Flip live promotions whose valid_until has passed (in SIM time) to expired.
    Keeps the dashboard's live/past split honest and re-arms the worker's
    promo-opportunity suppression (which skips while a promo is live)."""
    result = db.promotions.update_many(
        {"status": "live", "valid_until": {"$lte": _ts(sim_now)}},
        {"$set": {"status": "expired", "updated_at": _ts(sim_now)}},
    )
    return result.modified_count


# ── main loop ─────────────────────────────────────────────────────────────────

def _next_sim_day(db) -> datetime:
    """The day to simulate = (latest order date in `orders`) + 1 day.

    ISO 8601 timestamps sort lexicographically, so a string sort gives the
    newest order. Derived from the DB — never from wall-clock — so repeated
    runs advance one day at a time with no gaps or overlaps.
    """
    latest = db.orders.find_one(sort=[("opened_at", -1)], projection={"opened_at": 1})
    if not latest or "opened_at" not in latest:
        # Empty DB — start today.
        today = datetime.now(tz=timezone.utc)
        return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    latest_date = datetime.strptime(latest["opened_at"][:10], "%Y-%m-%d")
    return latest_date.replace(tzinfo=timezone.utc) + timedelta(days=1)


def _connect():
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")
    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    # Fail fast on a flaky network instead of hanging silently for 30s+.
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
    return client, client[db_name]


def _rebuild_derived() -> None:
    """Restore all derived state after orders are deleted.
    Order matters: depletion resets stock to baseline first, then replenishment
    layers received POs on top, then rollups recomputes metrics, then reconcile
    recounts redemptions from the orders that remain."""
    from plumbing.depletion import rebuild as depletion_rebuild
    from plumbing.reconcile import rebuild as reconcile_rebuild
    from plumbing.replenishment import rebuild as replenishment_rebuild
    from plumbing.rollups import rebuild as rollups_rebuild
    depletion_rebuild()
    replenishment_rebuild()
    rollups_rebuild()
    reconcile_rebuild()


def reset() -> None:
    """Full reset to seed state: delete every simulator order AND all agent-written
    / downstream collections (POs, recommendations, promos, sends, events), then
    rebuild derived state. Seed orders and dimensions are untouched."""
    client, db = _connect()
    try:
        res = db.orders.delete_many({"source": "simulator"})
        cleared = []
        for coll in ("purchase_orders", "promotion_recommendations", "promotions",
                     "campaign_sends", "agent_events", "waste_events"):
            n = db[coll].delete_many({}).deleted_count
            if n:
                cleared.append(f"{coll}={n}")
        # Clear live_metrics insight fields so nothing stale survives the reset;
        # the rebuild below recomputes the base fields.
        db.live_metrics.update_one(
            {"_id": "current"},
            {"$unset": {"as_of": "", "sales_pace_vs_baseline_pct": "",
                        "top_movers": "", "low_stock": "", "eighty_sixed_item_ids": "",
                        "gross_margin_pct": "", "active_promo_perf": ""}},
        )
        latest = db.orders.find_one(sort=[("opened_at", -1)], projection={"opened_at": 1})
        latest_day = latest["opened_at"][:10] if latest else "(none)"
        print(f"Deleted {res.deleted_count} simulator orders. "
              f"Cleared: {', '.join(cleared) or 'nothing else'}. "
              f"Latest order now {latest_day} — next run resumes from there.")
    finally:
        client.close()
    _rebuild_derived()


def undo_last_day() -> None:
    """Delete only the most recent simulated day and restore derived state."""
    client, db = _connect()
    try:
        latest = db.orders.find_one(
            {"source": "simulator"},
            sort=[("opened_at", -1)],
            projection={"opened_at": 1},
        )
        if not latest:
            print("No simulator orders to undo.")
            return
        day = latest["opened_at"][:10]
        next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        res = db.orders.delete_many({
            "source": "simulator",
            "opened_at": {"$gte": day, "$lt": next_day},
        })
        new_latest = db.orders.find_one(sort=[("opened_at", -1)], projection={"opened_at": 1})
        new_day = new_latest["opened_at"][:10] if new_latest else "(none)"
        print(f"Undid {day}: deleted {res.deleted_count} simulator orders. "
              f"Latest order now {new_day} — next run resumes from there.")
    finally:
        client.close()
    _rebuild_derived()


def _is_paused(db) -> bool:
    """True if the control doc requests a pause. Absent doc → not paused."""
    doc = db.sim_control.find_one({"_id": _CONTROL_ID}, {"paused": 1})
    return bool(doc and doc.get("paused"))


def _set_paused(db, paused: bool) -> None:
    db.sim_control.update_one(
        {"_id": _CONTROL_ID}, {"$set": {"paused": paused}}, upsert=True
    )


def pause() -> None:
    """Flip the control flag so a running sim holds at the current sim-time."""
    client, db = _connect()
    try:
        _set_paused(db, True)
        print("Paused. A running simulation will hold until --resume.")
    finally:
        client.close()


def resume() -> None:
    """Clear the control flag so a paused sim continues."""
    client, db = _connect()
    try:
        _set_paused(db, False)
        print("Resumed.")
    finally:
        client.close()


def run() -> None:
    print("Connecting to MongoDB and loading menu + customers…", flush=True)
    client, db = _connect()

    # Load reference data once at startup
    items = list(db.menu_items.find({}, {"_id": 0}))
    customers = list(db.customers.find({}, {"_id": 0}))
    pricing = db.pricing_rules.find_one({"rule_id": "cfg_default"})
    tax_rate_bps = pricing["tax_rate_bps"] if pricing else _TAX_BPS_FALLBACK

    if not items:
        raise RuntimeError("No menu_items found — run seed_data.py first")

    # Item popularity learned FROM THE DATA: weight = units sold in the
    # historical baseline. Keeps the live order mix consistent with the seed
    # history (same margins, same top sellers) instead of an arbitrary tag rule.
    hist = {h["_id"]: h["qty"] for h in db.orders.aggregate([
        {"$match": {"source": {"$ne": "simulator"}}},
        {"$unwind": "$line_items"},
        {"$group": {"_id": "$line_items.item_id",
                    "qty": {"$sum": "$line_items.quantity"}}},
    ])}
    item_weights = [max(hist.get(item["item_id"], 1), 1) for item in items]
    price_map = {item["item_id"]: item["price_money"]["amount"] for item in items}
    name_map = {item["item_id"]: item["name"] for item in items}

    # Static per-serving BOM: item_id -> { ingredient_id: qty per serving }.
    # Used by the availability gate in _build_order (86'd items can't be ordered).
    recipes = {r["recipe_id"]: r for r in db.recipes.find({}, {"_id": 0})}
    bom = {}
    for item in items:
        rec = recipes.get(item.get("recipe_id"))
        if rec:
            y = rec.get("yield_qty", 1) or 1
            bom[item["item_id"]] = {i["ingredient_id"]: i["qty"] / y for i in rec["ingredients"]}

    # A fresh run always starts in the running state, clearing any stale pause.
    _set_paused(db, False)

    # Simulate exactly one day: the day after the latest order in the DB.
    sim_now = _next_sim_day(db)
    day_end = sim_now + timedelta(days=1)
    day_label = sim_now.strftime("%Y-%m-%d")

    print(f"Simulating {day_label}  SIM_SPEED={SIM_SPEED}x")
    print(f"  {len(items)} items  {len(customers)} customers  tax={tax_rate_bps}bps")
    print("  Closed hours fast-forward; orders stream during service. Ctrl-C to stop early.\n")

    # Start-of-day spoilage (before the morning deliveries land at the open jump).
    _spoilage_pass(db, sim_now)

    n_orders = 0
    announced_pause = False
    last_pause_poll = 0.0
    try:
        while sim_now < day_end:
            tick_start = time.monotonic()
            sim_now += timedelta(seconds=SIM_STEP)

            # Closed hours: JUMP straight to the next open hour instead of grinding
            # through 60 no-op ticks per hour — each tick costs two Atlas round-trips
            # (PO receive + promo expiry), which made the "fast-forward" take a
            # minute-plus of dead silence and look like the sim was hung. The PO/promo
            # checks below run at the jump destination, so anything due during the
            # closed stretch (e.g. the 08:00 morning delivery) is still picked up.
            if HOURLY_RATE.get(sim_now.hour, 0) == 0:
                nxt = sim_now.replace(minute=0, second=0)
                while nxt < day_end and HOURLY_RATE.get(nxt.hour, 0) == 0:
                    nxt += timedelta(hours=1)
                nxt = min(nxt, day_end)
                if nxt > sim_now:
                    print(f"  [closed] {_ts(sim_now)} → jumping to {_ts(nxt)}")
                    sim_now = nxt

            # Auto-receive any POs whose expected_delivery has passed.
            received = _receive_overdue_pos(db, sim_now)
            if received:
                print(f"  [PO] {received} purchase order(s) marked received at {_ts(sim_now)}")

            # Expire any live promos whose window has closed (sim time).
            expired = _expire_promos(db, sim_now)
            if expired:
                print(f"  [PROMO] {expired} promotion(s) expired at {_ts(sim_now)}")

            # Poisson arrivals: expected orders in this SIM_STEP-second sim window
            rate = HOURLY_RATE.get(sim_now.hour, 0)

            # Pause gate — poll the control flag at most ~once per real second, not
            # every tick. A per-tick DB read would dominate runtime on a slow cluster
            # and cap the effective SIM_SPEED. Sub-second pause response is plenty.
            if rate > 0 and tick_start - last_pause_poll >= 1.0:
                last_pause_poll = tick_start
                if _is_paused(db):
                    if not announced_pause:
                        print(f"  [paused at {_ts(sim_now)} — waiting for --resume]")
                        announced_pause = True
                    while _is_paused(db):
                        time.sleep(1.0)
                    print(f"  [resumed at {_ts(sim_now)}]")
                    announced_pause = False
                    tick_start = time.monotonic()  # don't count paused time against pacing

            n = _poisson(rate * SIM_STEP / 3600)

            for _ in range(n):
                # Spread arrivals randomly across the sim window
                jitter = random.randint(0, SIM_STEP - 1)
                order_time = sim_now - timedelta(seconds=SIM_STEP) + timedelta(seconds=jitter)
                try:
                    doc = _build_order(
                        order_time, customers, items, item_weights,
                        price_map, name_map, tax_rate_bps, db, bom,
                    )
                    if doc is None:
                        print(f"[{_ts(order_time)}] customer turned away — items unavailable (86)")
                        continue
                    db.orders.insert_one(doc)
                    cust_label = doc["customer_id"] or "walk-in"
                    net_dollars = doc["net_amount_money"]["amount"] / 100
                    n_li = len(doc["line_items"])
                    print(
                        f"[{_ts(order_time)}] {doc['order_id']}"
                        f"  cust={cust_label}  items={n_li}  net=${net_dollars:.2f}"
                    )
                    n_orders += 1
                except Exception as exc:
                    print(f"  ERROR: {exc}")

            # Pace in real time only during open hours; fast-forward closed hours.
            if rate > 0:
                target = SIM_STEP / SIM_SPEED  # real seconds this step should take
                elapsed = time.monotonic() - tick_start
                time.sleep(max(0.0, target - elapsed))

        print(f"\nDay complete — {day_label}: inserted {n_orders} orders. "
              f"Run again to simulate the next day.")
    except KeyboardInterrupt:
        print(f"\nStopped early — {day_label}: inserted {n_orders} orders.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Order stream simulator.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--reset", action="store_true",
        help="Delete ALL simulator-generated orders and exit (seed data untouched).",
    )
    group.add_argument(
        "--undo", action="store_true",
        help="Delete only the most recent simulated day and exit.",
    )
    group.add_argument(
        "--pause", action="store_true",
        help="Pause a running simulation (sets the control flag) and exit.",
    )
    group.add_argument(
        "--resume", action="store_true",
        help="Resume a paused simulation (clears the control flag) and exit.",
    )
    args = parser.parse_args()
    if args.reset:
        reset()
    elif args.undo:
        undo_last_day()
    elif args.pause:
        pause()
    elif args.resume:
        resume()
    else:
        run()
