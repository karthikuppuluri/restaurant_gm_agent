"""
Reproducible seed for matchday-gm reference collections.

Run:
    python seed_data.py

Requires MONGODB_CONNECTION_STRING in .env (copy from .env.example).
Drops and repopulates: raw_ingredients, vendors, recipes, menu_items,
pricing_rules, customers (150), orders (~600 over 14 days).
Insert order is dependency-safe: raw_ingredients → recipes → menu_items.
"""

import os
import random
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pymongo import MongoClient

SEED = 42
random.seed(SEED)

CREATED = "2026-01-04T15:00:00Z"
UPDATED = "2026-01-04T15:00:00Z"
SV = 1


def money(cents: int) -> dict:
    return {"amount": cents, "currency": "USD"}


def audit() -> dict:
    return {
        "source": "seed",
        "schema_version": SV,
        "created_at": CREATED,
        "updated_at": UPDATED,
    }


# ──────────────────────────────────────────────────────────────────────────────
# RAW INGREDIENTS
# 28 items. on_hand_qty sized for ~2 days of service at ~300 covers/day.
# par_level = ~3 days; reorder_point = ~1 day.
# Vendor IDs are forward references — vendors collection seeded separately.
# ──────────────────────────────────────────────────────────────────────────────
RAW_INGREDIENTS = [
    # ── produce ──────────────────────────────────────────────────────────────
    {
        "_id": "ing_tortilla_chips",
        "ingredient_id": "ing_tortilla_chips",
        "name": "Tortilla Chips",
        "unit": "kg",
        "on_hand_qty": 25.0,
        "par_level": 40.0,
        "reorder_point": 12.0,
        "unit_cost_money": money(200),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_guacamole",
        "ingredient_id": "ing_guacamole",
        "name": "Guacamole",
        "unit": "kg",
        "on_hand_qty": 10.0,
        "par_level": 16.0,
        "reorder_point": 5.0,
        "unit_cost_money": money(800),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    {
        "_id": "ing_salsa",
        "ingredient_id": "ing_salsa",
        "name": "Salsa (chunky)",
        "unit": "kg",
        "on_hand_qty": 9.0,
        "par_level": 14.0,
        "reorder_point": 4.0,
        "unit_cost_money": money(300),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    {
        "_id": "ing_shredded_lettuce",
        "ingredient_id": "ing_shredded_lettuce",
        "name": "Shredded Iceberg Lettuce",
        "unit": "kg",
        "on_hand_qty": 9.0,
        "par_level": 14.0,
        "reorder_point": 4.0,
        "unit_cost_money": money(200),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    {
        "_id": "ing_tomato_diced",
        "ingredient_id": "ing_tomato_diced",
        "name": "Diced Tomato",
        "unit": "kg",
        "on_hand_qty": 7.0,
        "par_level": 11.0,
        "reorder_point": 3.0,
        "unit_cost_money": money(200),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    {
        "_id": "ing_pico_de_gallo",
        "ingredient_id": "ing_pico_de_gallo",
        "name": "Pico de Gallo",
        "unit": "kg",
        "on_hand_qty": 8.0,
        "par_level": 13.0,
        "reorder_point": 4.0,
        "unit_cost_money": money(300),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    {
        "_id": "ing_jalapenos",
        "ingredient_id": "ing_jalapenos",
        "name": "Jalapeños (sliced)",
        "unit": "kg",
        "on_hand_qty": 3.0,
        "par_level": 5.0,
        "reorder_point": 1.5,
        "unit_cost_money": money(300),
        "preferred_vendor_id": "ven_fresh_produce",
        **audit(),
    },
    # ── proteins ─────────────────────────────────────────────────────────────
    {
        "_id": "ing_seasoned_beef",
        "ingredient_id": "ing_seasoned_beef",
        "name": "Seasoned Ground Beef",
        "unit": "kg",
        "on_hand_qty": 52.0,
        "par_level": 80.0,
        "reorder_point": 26.0,
        "unit_cost_money": money(900),
        "preferred_vendor_id": "ven_meat_co",
        **audit(),
    },
    {
        "_id": "ing_grilled_chicken",
        "ingredient_id": "ing_grilled_chicken",
        "name": "Grilled Chicken (strips)",
        "unit": "kg",
        "on_hand_qty": 32.0,
        "par_level": 50.0,
        "reorder_point": 16.0,
        "unit_cost_money": money(770),
        "preferred_vendor_id": "ven_meat_co",
        **audit(),
    },
    # ── shells & tortillas ───────────────────────────────────────────────────
    {
        "_id": "ing_taco_shell",
        "ingredient_id": "ing_taco_shell",
        "name": "Crunchy Taco Shell",
        "unit": "each",
        "on_hand_qty": 250,
        "par_level": 400,
        "reorder_point": 120,
        "unit_cost_money": money(15),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_flour_tortilla_sm",
        "ingredient_id": "ing_flour_tortilla_sm",
        "name": "Flour Tortilla 6-in (small)",
        "unit": "each",
        "on_hand_qty": 180,
        "par_level": 280,
        "reorder_point": 90,
        "unit_cost_money": money(10),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_flour_tortilla_lg",
        "ingredient_id": "ing_flour_tortilla_lg",
        "name": "Flour Tortilla 12-in (large)",
        "unit": "each",
        "on_hand_qty": 420,
        "par_level": 650,
        "reorder_point": 210,
        "unit_cost_money": money(20),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_chalupa_shell",
        "ingredient_id": "ing_chalupa_shell",
        "name": "Chalupa Shell",
        "unit": "each",
        "on_hand_qty": 130,
        "par_level": 200,
        "reorder_point": 65,
        "unit_cost_money": money(25),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_tostada_shell",
        "ingredient_id": "ing_tostada_shell",
        "name": "Tostada Shell",
        "unit": "each",
        "on_hand_qty": 90,
        "par_level": 140,
        "reorder_point": 45,
        "unit_cost_money": money(12),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    # ── dairy & sauces ───────────────────────────────────────────────────────
    {
        "_id": "ing_nacho_cheese",
        "ingredient_id": "ing_nacho_cheese",
        "name": "Nacho Cheese Sauce",
        "unit": "liter",
        "on_hand_qty": 22.0,
        "par_level": 35.0,
        "reorder_point": 11.0,
        "unit_cost_money": money(300),
        "preferred_vendor_id": "ven_dairy_cold",
        **audit(),
    },
    {
        "_id": "ing_shredded_cheese",
        "ingredient_id": "ing_shredded_cheese",
        "name": "Shredded Mexican Cheese Blend",
        "unit": "kg",
        "on_hand_qty": 13.0,
        "par_level": 20.0,
        "reorder_point": 7.0,
        "unit_cost_money": money(600),
        "preferred_vendor_id": "ven_dairy_cold",
        **audit(),
    },
    {
        "_id": "ing_sour_cream",
        "ingredient_id": "ing_sour_cream",
        "name": "Sour Cream",
        "unit": "kg",
        "on_hand_qty": 9.0,
        "par_level": 14.0,
        "reorder_point": 4.0,
        "unit_cost_money": money(400),
        "preferred_vendor_id": "ven_dairy_cold",
        **audit(),
    },
    {
        "_id": "ing_jalapeno_sauce",
        "ingredient_id": "ing_jalapeno_sauce",
        "name": "Creamy Jalapeño Sauce",
        "unit": "liter",
        "on_hand_qty": 4.0,
        "par_level": 6.0,
        "reorder_point": 2.0,
        "unit_cost_money": money(400),
        "preferred_vendor_id": "ven_dairy_cold",
        **audit(),
    },
    {
        "_id": "ing_chocolate_sauce",
        "ingredient_id": "ing_chocolate_sauce",
        "name": "Chocolate Dipping Sauce",
        "unit": "liter",
        "on_hand_qty": 4.0,
        "par_level": 7.0,
        "reorder_point": 2.0,
        "unit_cost_money": money(500),
        "preferred_vendor_id": "ven_dairy_cold",
        **audit(),
    },
    # ── grains & legumes ─────────────────────────────────────────────────────
    {
        "_id": "ing_black_beans",
        "ingredient_id": "ing_black_beans",
        "name": "Black Beans (cooked)",
        "unit": "kg",
        "on_hand_qty": 30.0,
        "par_level": 48.0,
        "reorder_point": 15.0,
        "unit_cost_money": money(200),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_mexican_rice",
        "ingredient_id": "ing_mexican_rice",
        "name": "Mexican Rice (cooked)",
        "unit": "kg",
        "on_hand_qty": 28.0,
        "par_level": 44.0,
        "reorder_point": 14.0,
        "unit_cost_money": money(150),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    # ── beverages ─────────────────────────────────────────────────────────────
    {
        "_id": "ing_soda_syrup",
        "ingredient_id": "ing_soda_syrup",
        "name": "Fountain Soda Syrup",
        "unit": "liter",
        "on_hand_qty": 13.0,
        "par_level": 20.0,
        "reorder_point": 6.0,
        "unit_cost_money": money(500),
        "preferred_vendor_id": "ven_beverage_co",
        **audit(),
    },
    {
        "_id": "ing_horchata_mix",
        "ingredient_id": "ing_horchata_mix",
        "name": "Horchata Mix (powder)",
        "unit": "kg",
        "on_hand_qty": 5.0,
        "par_level": 8.0,
        "reorder_point": 2.0,
        "unit_cost_money": money(500),
        "preferred_vendor_id": "ven_beverage_co",
        **audit(),
    },
    {
        "_id": "ing_agua_fresca_mix",
        "ingredient_id": "ing_agua_fresca_mix",
        "name": "Agua Fresca Mix (powder)",
        "unit": "kg",
        "on_hand_qty": 5.0,
        "par_level": 8.0,
        "reorder_point": 2.0,
        "unit_cost_money": money(400),
        "preferred_vendor_id": "ven_beverage_co",
        **audit(),
    },
    {
        "_id": "ing_iced_tea_conc",
        "ingredient_id": "ing_iced_tea_conc",
        "name": "Iced Tea Concentrate",
        "unit": "liter",
        "on_hand_qty": 8.0,
        "par_level": 12.0,
        "reorder_point": 4.0,
        "unit_cost_money": money(300),
        "preferred_vendor_id": "ven_beverage_co",
        **audit(),
    },
    {
        "_id": "ing_cup_large",
        "ingredient_id": "ing_cup_large",
        "name": "Large Drink Cup (32oz)",
        "unit": "each",
        "on_hand_qty": 450,
        "par_level": 700,
        "reorder_point": 210,
        "unit_cost_money": money(12),
        "preferred_vendor_id": "ven_beverage_co",
        **audit(),
    },
    # ── dessert ───────────────────────────────────────────────────────────────
    {
        "_id": "ing_cinnamon_twists",
        "ingredient_id": "ing_cinnamon_twists",
        "name": "Cinnamon Twists (serving bag)",
        "unit": "each",
        "on_hand_qty": 180,
        "par_level": 280,
        "reorder_point": 85,
        "unit_cost_money": money(30),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
    {
        "_id": "ing_churros",
        "ingredient_id": "ing_churros",
        "name": "Churros",
        "unit": "each",
        "on_hand_qty": 400,
        "par_level": 600,
        "reorder_point": 190,
        "unit_cost_money": money(50),
        "preferred_vendor_id": "ven_dry_goods",
        **audit(),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# RECIPES  (BOM — one per menu item, 1:1)
# ──────────────────────────────────────────────────────────────────────────────
def ing(ingredient_id: str, qty: float, unit: str) -> dict:
    return {"ingredient_id": ingredient_id, "qty": qty, "unit": unit}


RECIPES = [
    # ── starters ─────────────────────────────────────────────────────────────
    {
        "_id": "rec_nachos_cheese_01",
        "recipe_id": "rec_nachos_cheese_01",
        "menu_item_id": "cat_item_nachos_cheese_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_tortilla_chips", 0.08, "kg"),
            ing("ing_nacho_cheese", 0.07, "liter"),
        ],
        **audit(),
    },
    {
        "_id": "rec_chips_guac_01",
        "recipe_id": "rec_chips_guac_01",
        "menu_item_id": "cat_item_chips_guac_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_tortilla_chips", 0.08, "kg"),
            ing("ing_guacamole", 0.06, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_chips_salsa_01",
        "recipe_id": "rec_chips_salsa_01",
        "menu_item_id": "cat_item_chips_salsa_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_tortilla_chips", 0.08, "kg"),
            ing("ing_salsa", 0.05, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_nachos_supreme_01",
        "recipe_id": "rec_nachos_supreme_01",
        "menu_item_id": "cat_item_nachos_supreme_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_tortilla_chips", 0.15, "kg"),
            ing("ing_nacho_cheese", 0.10, "liter"),
            ing("ing_seasoned_beef", 0.09, "kg"),
            ing("ing_sour_cream", 0.03, "kg"),
            ing("ing_pico_de_gallo", 0.04, "kg"),
            ing("ing_jalapenos", 0.02, "kg"),
            ing("ing_black_beans", 0.05, "kg"),
        ],
        **audit(),
    },
    # ── mains ────────────────────────────────────────────────────────────────
    {
        "_id": "rec_crunchy_taco_01",
        "recipe_id": "rec_crunchy_taco_01",
        "menu_item_id": "cat_item_crunchy_taco_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_taco_shell", 1, "each"),
            ing("ing_seasoned_beef", 0.085, "kg"),
            ing("ing_shredded_lettuce", 0.020, "kg"),
            ing("ing_shredded_cheese", 0.020, "kg"),
            ing("ing_tomato_diced", 0.015, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_soft_beef_taco_01",
        "recipe_id": "rec_soft_beef_taco_01",
        "menu_item_id": "cat_item_soft_beef_taco_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_sm", 1, "each"),
            ing("ing_seasoned_beef", 0.085, "kg"),
            ing("ing_shredded_lettuce", 0.020, "kg"),
            ing("ing_shredded_cheese", 0.020, "kg"),
            ing("ing_tomato_diced", 0.015, "kg"),
            ing("ing_sour_cream", 0.015, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_chicken_taco_01",
        "recipe_id": "rec_chicken_taco_01",
        "menu_item_id": "cat_item_chicken_taco_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_sm", 1, "each"),
            ing("ing_grilled_chicken", 0.085, "kg"),
            ing("ing_shredded_lettuce", 0.020, "kg"),
            ing("ing_shredded_cheese", 0.020, "kg"),
            ing("ing_tomato_diced", 0.015, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_beef_burrito_01",
        "recipe_id": "rec_beef_burrito_01",
        "menu_item_id": "cat_item_beef_burrito_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_lg", 1, "each"),
            ing("ing_seasoned_beef", 0.120, "kg"),
            ing("ing_mexican_rice", 0.080, "kg"),
            ing("ing_black_beans", 0.060, "kg"),
            ing("ing_shredded_cheese", 0.030, "kg"),
            ing("ing_sour_cream", 0.025, "kg"),
            ing("ing_pico_de_gallo", 0.030, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_chicken_burrito_01",
        "recipe_id": "rec_chicken_burrito_01",
        "menu_item_id": "cat_item_chicken_burrito_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_lg", 1, "each"),
            ing("ing_grilled_chicken", 0.120, "kg"),
            ing("ing_mexican_rice", 0.080, "kg"),
            ing("ing_black_beans", 0.060, "kg"),
            ing("ing_shredded_cheese", 0.030, "kg"),
            ing("ing_sour_cream", 0.025, "kg"),
            ing("ing_pico_de_gallo", 0.030, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_bean_burrito_01",
        "recipe_id": "rec_bean_burrito_01",
        "menu_item_id": "cat_item_bean_burrito_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_lg", 1, "each"),
            ing("ing_black_beans", 0.100, "kg"),
            ing("ing_shredded_cheese", 0.040, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_chicken_quesadilla_01",
        "recipe_id": "rec_chicken_quesadilla_01",
        "menu_item_id": "cat_item_chicken_quesadilla_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_flour_tortilla_lg", 2, "each"),  # folded — 2 tortillas
            ing("ing_grilled_chicken", 0.100, "kg"),
            ing("ing_shredded_cheese", 0.060, "kg"),
            ing("ing_jalapeno_sauce", 0.030, "liter"),
        ],
        **audit(),
    },
    {
        "_id": "rec_beef_chalupa_01",
        "recipe_id": "rec_beef_chalupa_01",
        "menu_item_id": "cat_item_beef_chalupa_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_chalupa_shell", 1, "each"),
            ing("ing_seasoned_beef", 0.085, "kg"),
            ing("ing_shredded_lettuce", 0.020, "kg"),
            ing("ing_shredded_cheese", 0.025, "kg"),
            ing("ing_tomato_diced", 0.020, "kg"),
            ing("ing_sour_cream", 0.020, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_bean_tostada_01",
        "recipe_id": "rec_bean_tostada_01",
        "menu_item_id": "cat_item_bean_tostada_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_tostada_shell", 1, "each"),
            ing("ing_black_beans", 0.070, "kg"),
            ing("ing_shredded_lettuce", 0.020, "kg"),
            ing("ing_shredded_cheese", 0.025, "kg"),
            ing("ing_tomato_diced", 0.020, "kg"),
            ing("ing_sour_cream", 0.020, "kg"),
        ],
        **audit(),
    },
    {
        "_id": "rec_combo_box_01",
        "recipe_id": "rec_combo_box_01",
        "menu_item_id": "cat_item_combo_box_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_taco_shell", 2, "each"),
            ing("ing_flour_tortilla_lg", 1, "each"),
            ing("ing_seasoned_beef", 0.290, "kg"),  # 2×0.085 tacos + 0.12 burrito
            ing("ing_mexican_rice", 0.080, "kg"),
            ing("ing_black_beans", 0.060, "kg"),
            ing("ing_shredded_lettuce", 0.040, "kg"),
            ing("ing_shredded_cheese", 0.050, "kg"),
            ing("ing_tomato_diced", 0.030, "kg"),
            ing("ing_sour_cream", 0.025, "kg"),
            ing("ing_pico_de_gallo", 0.030, "kg"),
        ],
        **audit(),
    },
    # ── drinks ───────────────────────────────────────────────────────────────
    {
        "_id": "rec_soda_01",
        "recipe_id": "rec_soda_01",
        "menu_item_id": "cat_item_soda_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_soda_syrup", 0.060, "liter"),
            ing("ing_cup_large", 1, "each"),
        ],
        **audit(),
    },
    {
        "_id": "rec_horchata_01",
        "recipe_id": "rec_horchata_01",
        "menu_item_id": "cat_item_horchata_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_horchata_mix", 0.040, "kg"),
            ing("ing_cup_large", 1, "each"),
        ],
        **audit(),
    },
    {
        "_id": "rec_agua_fresca_01",
        "recipe_id": "rec_agua_fresca_01",
        "menu_item_id": "cat_item_agua_fresca_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_agua_fresca_mix", 0.040, "kg"),
            ing("ing_cup_large", 1, "each"),
        ],
        **audit(),
    },
    {
        "_id": "rec_iced_tea_01",
        "recipe_id": "rec_iced_tea_01",
        "menu_item_id": "cat_item_iced_tea_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_iced_tea_conc", 0.050, "liter"),
            ing("ing_cup_large", 1, "each"),
        ],
        **audit(),
    },
    # ── desserts ─────────────────────────────────────────────────────────────
    {
        "_id": "rec_cinnamon_twists_01",
        "recipe_id": "rec_cinnamon_twists_01",
        "menu_item_id": "cat_item_cinnamon_twists_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_cinnamon_twists", 1, "each"),
        ],
        **audit(),
    },
    {
        "_id": "rec_churros_01",
        "recipe_id": "rec_churros_01",
        "menu_item_id": "cat_item_churros_01",
        "yield_qty": 1,
        "ingredients": [
            ing("ing_churros", 3, "each"),
            ing("ing_chocolate_sauce", 0.030, "liter"),
        ],
        **audit(),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# MENU ITEMS
# ──────────────────────────────────────────────────────────────────────────────
MENU_ITEMS = [
    # ── starters ─────────────────────────────────────────────────────────────
    {
        "_id": "cat_item_nachos_cheese_01",
        "item_id": "cat_item_nachos_cheese_01",
        "name": "Chips & Nacho Cheese",
        "category": "starters",
        "price_money": money(399),
        "recipe_id": "rec_nachos_cheese_01",
        "tags": ["vegetarian"],
        **audit(),
    },
    {
        "_id": "cat_item_chips_guac_01",
        "item_id": "cat_item_chips_guac_01",
        "name": "Chips & Guacamole",
        "category": "starters",
        "price_money": money(449),
        "recipe_id": "rec_chips_guac_01",
        "tags": ["vegetarian", "high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_chips_salsa_01",
        "item_id": "cat_item_chips_salsa_01",
        "name": "Chips & Salsa",
        "category": "starters",
        "price_money": money(249),
        "recipe_id": "rec_chips_salsa_01",
        "tags": ["vegetarian", "high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_nachos_supreme_01",
        "item_id": "cat_item_nachos_supreme_01",
        "name": "Loaded Nachos Supreme",
        "category": "starters",
        "price_money": money(799),
        "recipe_id": "rec_nachos_supreme_01",
        "tags": ["shareable"],
        **audit(),
    },
    # ── mains ────────────────────────────────────────────────────────────────
    {
        "_id": "cat_item_crunchy_taco_01",
        "item_id": "cat_item_crunchy_taco_01",
        "name": "Crunchy Beef Taco",
        "category": "mains",
        "price_money": money(249),
        "recipe_id": "rec_crunchy_taco_01",
        "tags": [],
        **audit(),
    },
    {
        "_id": "cat_item_soft_beef_taco_01",
        "item_id": "cat_item_soft_beef_taco_01",
        "name": "Soft Beef Taco",
        "category": "mains",
        "price_money": money(269),
        "recipe_id": "rec_soft_beef_taco_01",
        "tags": [],
        **audit(),
    },
    {
        "_id": "cat_item_chicken_taco_01",
        "item_id": "cat_item_chicken_taco_01",
        "name": "Chicken Soft Taco",
        "category": "mains",
        "price_money": money(289),
        "recipe_id": "rec_chicken_taco_01",
        "tags": [],
        **audit(),
    },
    {
        "_id": "cat_item_beef_burrito_01",
        "item_id": "cat_item_beef_burrito_01",
        "name": "Beef Burrito",
        "category": "mains",
        "price_money": money(649),
        "recipe_id": "rec_beef_burrito_01",
        "tags": [],
        **audit(),
    },
    {
        "_id": "cat_item_chicken_burrito_01",
        "item_id": "cat_item_chicken_burrito_01",
        "name": "Chicken Burrito",
        "category": "mains",
        "price_money": money(699),
        "recipe_id": "rec_chicken_burrito_01",
        "tags": ["high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_bean_burrito_01",
        "item_id": "cat_item_bean_burrito_01",
        "name": "Bean & Cheese Burrito",
        "category": "mains",
        "price_money": money(499),
        "recipe_id": "rec_bean_burrito_01",
        "tags": ["vegetarian", "high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_chicken_quesadilla_01",
        "item_id": "cat_item_chicken_quesadilla_01",
        "name": "Chicken Quesadilla",
        "category": "mains",
        "price_money": money(749),
        "recipe_id": "rec_chicken_quesadilla_01",
        "tags": ["high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_beef_chalupa_01",
        "item_id": "cat_item_beef_chalupa_01",
        "name": "Beef Chalupa",
        "category": "mains",
        "price_money": money(579),
        "recipe_id": "rec_beef_chalupa_01",
        "tags": [],
        **audit(),
    },
    {
        "_id": "cat_item_bean_tostada_01",
        "item_id": "cat_item_bean_tostada_01",
        "name": "Bean Tostada",
        "category": "mains",
        "price_money": money(449),
        "recipe_id": "rec_bean_tostada_01",
        "tags": ["vegetarian"],
        **audit(),
    },
    {
        "_id": "cat_item_combo_box_01",
        "item_id": "cat_item_combo_box_01",
        "name": "Combo Box (2 Tacos + Burrito)",
        "category": "mains",
        "price_money": money(999),
        "recipe_id": "rec_combo_box_01",
        "tags": ["combo"],
        **audit(),
    },
    # ── drinks ───────────────────────────────────────────────────────────────
    {
        "_id": "cat_item_soda_01",
        "item_id": "cat_item_soda_01",
        "name": "Fountain Soda (Large)",
        "category": "drinks",
        "price_money": money(249),
        "recipe_id": "rec_soda_01",
        "tags": ["high_margin"],
        **audit(),
    },
    {
        "_id": "cat_item_horchata_01",
        "item_id": "cat_item_horchata_01",
        "name": "Horchata",
        "category": "drinks",
        "price_money": money(349),
        "recipe_id": "rec_horchata_01",
        "tags": ["high_margin", "vegetarian"],
        **audit(),
    },
    {
        "_id": "cat_item_agua_fresca_01",
        "item_id": "cat_item_agua_fresca_01",
        "name": "Agua Fresca",
        "category": "drinks",
        "price_money": money(349),
        "recipe_id": "rec_agua_fresca_01",
        "tags": ["high_margin", "vegetarian"],
        **audit(),
    },
    {
        "_id": "cat_item_iced_tea_01",
        "item_id": "cat_item_iced_tea_01",
        "name": "Iced Tea",
        "category": "drinks",
        "price_money": money(199),
        "recipe_id": "rec_iced_tea_01",
        "tags": ["high_margin", "vegetarian"],
        **audit(),
    },
    # ── desserts ─────────────────────────────────────────────────────────────
    {
        "_id": "cat_item_cinnamon_twists_01",
        "item_id": "cat_item_cinnamon_twists_01",
        "name": "Cinnamon Twists",
        "category": "desserts",
        "price_money": money(199),
        "recipe_id": "rec_cinnamon_twists_01",
        "tags": ["high_margin", "vegetarian"],
        **audit(),
    },
    {
        "_id": "cat_item_churros_01",
        "item_id": "cat_item_churros_01",
        "name": "Churros & Dipping Sauce",
        "category": "desserts",
        "price_money": money(299),
        "recipe_id": "rec_churros_01",
        "tags": ["vegetarian"],
        **audit(),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# VENDORS  (5 suppliers — one per ingredient group)
# ──────────────────────────────────────────────────────────────────────────────
def _supply(ingredient_id, unit_cost_cents, min_order_qty, lead_time_days):
    return {
        "ingredient_id": ingredient_id,
        "unit_cost_money": money(unit_cost_cents),
        "min_order_qty": min_order_qty,
        "lead_time_days": lead_time_days,
    }

VENDORS = [
    {
        "_id": "ven_fresh_produce",
        "vendor_id": "ven_fresh_produce",
        "name": "Fresh Valley Produce",
        "contact_masked": "orders@•••.com",
        "supplies": [
            _supply("ing_guacamole",       800,  5,   1),
            _supply("ing_salsa",           300,  5,   1),
            _supply("ing_shredded_lettuce",200,  5,   1),
            _supply("ing_tomato_diced",    200,  5,   1),
            _supply("ing_pico_de_gallo",   300,  5,   1),
            _supply("ing_jalapenos",       300,  2,   1),
        ],
        "rating": 4.7,
        **audit(),
    },
    {
        "_id": "ven_meat_co",
        "vendor_id": "ven_meat_co",
        "name": "Southwest Meat Co.",
        "contact_masked": "orders@•••.com",
        "supplies": [
            _supply("ing_seasoned_beef",   900, 10,   1),
            _supply("ing_grilled_chicken", 770, 10,   1),
        ],
        "rating": 4.5,
        **audit(),
    },
    {
        "_id": "ven_dry_goods",
        "vendor_id": "ven_dry_goods",
        "name": "Mesa Dry Goods",
        "contact_masked": "orders@•••.com",
        "supplies": [
            _supply("ing_tortilla_chips",  200, 10,   2),
            _supply("ing_taco_shell",       15, 200,  2),
            _supply("ing_flour_tortilla_sm",10, 200,  2),
            _supply("ing_flour_tortilla_lg",20, 200,  2),
            _supply("ing_chalupa_shell",    25, 100,  3),
            _supply("ing_tostada_shell",    12, 100,  3),
            _supply("ing_black_beans",     200,  10,  2),
            _supply("ing_mexican_rice",    150,  10,  2),
            _supply("ing_cinnamon_twists",  30, 100,  3),
            _supply("ing_churros",          50, 100,  2),
        ],
        "rating": 4.3,
        **audit(),
    },
    {
        "_id": "ven_dairy_cold",
        "vendor_id": "ven_dairy_cold",
        "name": "Dairy Fresh Distributors",
        "contact_masked": "orders@•••.com",
        "supplies": [
            _supply("ing_nacho_cheese",    300,  5,   1),
            _supply("ing_shredded_cheese", 600,  5,   1),
            _supply("ing_sour_cream",      400,  5,   1),
            _supply("ing_jalapeno_sauce",  400,  3,   2),
            _supply("ing_chocolate_sauce", 500,  2,   2),
        ],
        "rating": 4.6,
        **audit(),
    },
    {
        "_id": "ven_beverage_co",
        "vendor_id": "ven_beverage_co",
        "name": "SunBev Supplies",
        "contact_masked": "orders@•••.com",
        "supplies": [
            _supply("ing_soda_syrup",      500, 10,   2),
            _supply("ing_horchata_mix",    500,  5,   3),
            _supply("ing_agua_fresca_mix", 400,  5,   3),
            _supply("ing_iced_tea_conc",   300,  5,   2),
            _supply("ing_cup_large",        12, 500,  3),
        ],
        "rating": 4.4,
        **audit(),
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# PRICING RULES  (singleton config)
# ──────────────────────────────────────────────────────────────────────────────
PRICING_RULES = [
    {
        "_id": "cfg_default",
        "rule_id": "cfg_default",
        "min_margin_pct": 30,
        "max_discount_pct": 25,
        "blackout_item_ids": [],
        "tax_rate_bps": 875,
        "currency": "USD",
        **audit(),
    }
]


# ──────────────────────────────────────────────────────────────────────────────
# CUSTOMERS  (50 simulated guests with demographics + RFM)
# ──────────────────────────────────────────────────────────────────────────────
_FIRST = [
    "Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery",
    "Dakota", "Quinn", "Jamie", "Reese", "Skyler", "Blake", "Cameron", "Jesse",
    "Drew", "Peyton", "Hayden", "Rowan", "Kai", "Finley", "Phoenix", "Sage",
    "River", "Emery", "Remy", "Lennon", "Shawn", "Nico",
]
_LAST = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor", "Thomas",
    "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris",
    "Sanchez", "Clark", "Lewis", "Robinson", "Walker", "Hall", "Young", "Allen",
]

# (city, state, zip)
_LOCATIONS = [
    ("Chicago", "IL", "60601"),
    ("Chicago", "IL", "60614"),
    ("Chicago", "IL", "60622"),
    ("Chicago", "IL", "60647"),
    ("Chicago", "IL", "60657"),
    ("Evanston", "IL", "60201"),
    ("Oak Park", "IL", "60301"),
    ("Naperville", "IL", "60540"),
]

_GENDERS     = ["M", "F", "other", "prefer_not_to_say"]
_GENDER_W    = [45, 45, 5, 5]
_TIER_POOL   = ["none"] * 75 + ["silver"] * 51 + ["gold"] * 24
_DIET_OPTIONS = ["vegetarian", "gluten_free", "vegan", "halal", "none"]
_ITEM_IDS    = [m["item_id"] for m in MENU_ITEMS]


def _generate_customers():
    random.seed(SEED + 1)
    customers = []
    tier_pool = _TIER_POOL[:]
    random.shuffle(tier_pool)

    for i in range(150):
        hex_id = format(random.randint(0, 0xFFFFFF), "06x")
        cust_id = f"cust_{hex_id}"
        first = _FIRST[i % len(_FIRST)]
        last  = _LAST[i % len(_LAST)]
        city, state, zip_code = random.choice(_LOCATIONS)

        # dietary flags: most have "none", some have 1 real flag
        if random.random() < 0.25:
            dietary_flags = [random.choice(_DIET_OPTIONS[:-1])]  # exclude "none"
        else:
            dietary_flags = ["none"]

        customers.append({
            "_id": cust_id,
            "customer_id": cust_id,
            "name": f"{first} {last}",
            "email_masked": f"{first.lower()}.{last.lower()}@•••.com",
            "age": random.randint(18, 65),
            "gender": random.choices(_GENDERS, weights=_GENDER_W)[0],
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "dietary_flags": dietary_flags,
            "recency_days": random.randint(1, 60),
            "lifetime_value_money": money(random.randint(1000, 80000)),
            "avg_check_money": money(random.randint(600, 5000)),
            "price_sensitivity": round(random.uniform(0.1, 0.9), 2),
            "loyalty_tier": tier_pool[i],
            "opt_in_marketing": random.random() < 0.65,
            "favorite_item_ids": random.sample(_ITEM_IDS, k=random.randint(1, 3)),
            **audit(),
        })
    return customers


CUSTOMERS = _generate_customers()


# ──────────────────────────────────────────────────────────────────────────────
# ORDERS  (~600 historical orders, 14 days, no promos)
# Spread across 2026-05-26 → 2026-06-08. Arrival times follow a lunch/dinner
# distribution (same shape as the simulator) so the order-mgmt agent has
# a meaningful baseline to compare against. ~40 orders/weekday, ~50/weekend.
# Totals use integer BPS arithmetic to stay in sync with pricing_rules.
# ──────────────────────────────────────────────────────────────────────────────
_TAX_BPS = 875  # mirrors pricing_rules.tax_rate_bps

_ITEM_WEIGHTS = {
    "cat_item_crunchy_taco_01":      15,
    "cat_item_soft_beef_taco_01":    12,
    "cat_item_chicken_taco_01":      10,
    "cat_item_soda_01":              12,
    "cat_item_beef_burrito_01":      10,
    "cat_item_chicken_burrito_01":    8,
    "cat_item_nachos_cheese_01":      6,
    "cat_item_chips_salsa_01":        5,
    "cat_item_chicken_quesadilla_01": 6,
    "cat_item_bean_burrito_01":       4,
    "cat_item_beef_chalupa_01":       4,
    "cat_item_nachos_supreme_01":     3,
    "cat_item_horchata_01":           2,
    "cat_item_combo_box_01":          2,
    "cat_item_chips_guac_01":         2,
    "cat_item_bean_tostada_01":       1,
    "cat_item_agua_fresca_01":        1,
    "cat_item_iced_tea_01":           1,
    "cat_item_cinnamon_twists_01":    1,
    "cat_item_churros_01":            1,
}
_ITEM_POOL = list(_ITEM_WEIGHTS.keys())
_ITEM_CUM_WEIGHTS = list(_ITEM_WEIGHTS.values())

_PRICE_MAP = {m["item_id"]: m["price_money"]["amount"] for m in MENU_ITEMS}
_NAME_MAP  = {m["item_id"]: m["name"]                  for m in MENU_ITEMS}

# Hour-of-day weights for realistic arrival distribution (lunch + dinner peaks).
# Matches the simulator's HOURLY_RATE shape so the baseline is comparable.
_HOUR_POOL    = list(range(10, 23))
_HOUR_WEIGHTS = [1, 4, 8, 6, 2, 1, 2, 5, 9, 12, 8, 4, 1]  # hours 10-22


def _generate_orders():
    random.seed(SEED + 2)
    cust_ids = [c["customer_id"] for c in CUSTOMERS]
    base = datetime(2026, 5, 26, tzinfo=timezone.utc)
    orders = []
    order_counter = 1

    for day_offset in range(14):
        day = base + timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        n_orders = random.randint(45, 55) if is_weekend else random.randint(37, 45)

        for _ in range(n_orders):
            hour   = random.choices(_HOUR_POOL, weights=_HOUR_WEIGHTS)[0]
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            opened = day.replace(hour=hour, minute=minute, second=second)

            channel = random.choices(["DINE_IN", "TAKEOUT"], weights=[60, 40])[0]
            guest_count = random.randint(1, 6) if channel == "DINE_IN" else 1
            customer_id = random.choice(cust_ids) if random.random() < 0.60 else None

            n_items = random.randint(1, 4)
            chosen_items = random.choices(_ITEM_POOL, weights=_ITEM_CUM_WEIGHTS, k=n_items)
            line_items = []
            total_gross = 0
            for j, item_id in enumerate(chosen_items):
                qty = random.randint(1, 6)
                price = _PRICE_MAP[item_id]
                gross = price * qty
                total_gross += gross
                line_items.append({
                    "uid": f"li_{j + 1}",
                    "item_id": item_id,
                    "name": _NAME_MAP[item_id],
                    "quantity": qty,
                    "unit_price_money": money(price),
                    "gross_money": money(gross),
                    "applied_discount_money": money(0),
                    "promo_id": None,
                })

            tax = total_gross * _TAX_BPS // 10000
            net = total_gross + tax

            duration = random.randint(10, 20) if channel == "TAKEOUT" else random.randint(20, 55)
            closed = opened + timedelta(minutes=duration)

            opened_ts = opened.strftime("%Y-%m-%dT%H:%M:%SZ")
            closed_ts = closed.strftime("%Y-%m-%dT%H:%M:%SZ")
            order_id = f"ord_{opened.strftime('%Y%m%d')}_{order_counter:04d}"
            order_counter += 1

            orders.append({
                "_id": order_id,
                "order_id": order_id,
                "state": "COMPLETED",
                "channel": channel,
                "customer_id": customer_id,
                "guest_count": guest_count,
                "opened_at": opened_ts,
                "closed_at": closed_ts,
                "line_items": line_items,
                "total_money": money(total_gross),
                "total_tax_money": money(tax),
                "total_discount_money": money(0),
                "net_amount_money": money(net),
                "source": "seed",
                "schema_version": SV,
                "created_at": opened_ts,
                "updated_at": closed_ts,
            })

    return orders


ORDERS = _generate_orders()


# ──────────────────────────────────────────────────────────────────────────────
# INTEGRITY CHECK  (runs before any DB write)
# ──────────────────────────────────────────────────────────────────────────────
def validate() -> None:
    ing_ids   = {r["ingredient_id"] for r in RAW_INGREDIENTS}
    rec_ids   = {r["recipe_id"]     for r in RECIPES}
    item_ids  = {m["item_id"]       for m in MENU_ITEMS}
    ven_ids   = {v["vendor_id"]     for v in VENDORS}
    cust_ids  = {c["customer_id"]   for c in CUSTOMERS}

    # recipes → ingredients + menu items
    for recipe in RECIPES:
        assert recipe["menu_item_id"] in item_ids, (
            f"Recipe {recipe['recipe_id']} → missing menu_item_id {recipe['menu_item_id']}"
        )
        for line in recipe["ingredients"]:
            assert line["ingredient_id"] in ing_ids, (
                f"Recipe {recipe['recipe_id']} → missing ingredient {line['ingredient_id']}"
            )

    # menu items → recipes
    for item in MENU_ITEMS:
        assert item["recipe_id"] in rec_ids, (
            f"Item {item['item_id']} → missing recipe_id {item['recipe_id']}"
        )

    claimed = {item["recipe_id"] for item in MENU_ITEMS}
    orphans = rec_ids - claimed
    assert not orphans, f"Orphan recipes (no menu item): {orphans}"

    # ingredients → vendors
    for ing in RAW_INGREDIENTS:
        assert ing["preferred_vendor_id"] in ven_ids, (
            f"Ingredient {ing['ingredient_id']} → missing vendor {ing['preferred_vendor_id']}"
        )

    # vendors.supplies → ingredients
    for vendor in VENDORS:
        for s in vendor["supplies"]:
            assert s["ingredient_id"] in ing_ids, (
                f"Vendor {vendor['vendor_id']} → missing ingredient {s['ingredient_id']}"
            )

    # orders → customers + menu items
    for order in ORDERS:
        if order["customer_id"] is not None:
            assert order["customer_id"] in cust_ids, (
                f"Order {order['order_id']} → missing customer {order['customer_id']}"
            )
        for li in order["line_items"]:
            assert li["item_id"] in item_ids, (
                f"Order {order['order_id']} → missing item {li['item_id']}"
            )

    print(
        f"  FK integrity OK — "
        f"{len(RAW_INGREDIENTS)} ingredients, {len(RECIPES)} recipes, "
        f"{len(MENU_ITEMS)} menu items, {len(VENDORS)} vendors, "
        f"{len(CUSTOMERS)} customers, {len(ORDERS)} orders"
    )


# ──────────────────────────────────────────────────────────────────────────────
# SEED
# ──────────────────────────────────────────────────────────────────────────────
def seed(db) -> None:
    validate()

    # Drop → insert in dependency order
    for collection_name, docs in [
        ("raw_ingredients", RAW_INGREDIENTS),
        ("vendors", VENDORS),
        ("recipes", RECIPES),
        ("menu_items", MENU_ITEMS),
        ("pricing_rules", PRICING_RULES),
        ("customers", CUSTOMERS),
        ("orders", ORDERS),
    ]:
        col = db[collection_name]
        col.drop()
        result = col.insert_many(docs)
        print(f"  {collection_name}: inserted {len(result.inserted_ids)} documents")


def main() -> None:
    load_dotenv()
    uri = os.environ.get("MONGODB_CONNECTION_STRING")
    if not uri:
        raise RuntimeError("MONGODB_CONNECTION_STRING not set in environment / .env")

    db_name = os.environ.get("MONGODB_DB_NAME", "restaurant_gm")
    client = MongoClient(uri)
    db = client[db_name]

    print(f"Seeding '{db_name}' …")
    seed(db)
    print("Done.")
    client.close()


if __name__ == "__main__":
    main()
