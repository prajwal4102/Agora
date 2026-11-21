"""Shop data and business rules for the shelf agent.

One SQLite file holds the supermarket's catalogue, shelf and back-room stock,
supplier price lists, purchase orders and an event log the dashboard reads.
Every function returns plain dicts so the MCP tools can hand them straight to
the model.

Ordering is deliberately two-step: `propose_order` prices an order and stores it
as a proposal, and `confirm_order` only places an order that matches a live
proposal. The model cannot place an order the supervisor has not heard priced.
"""
import difflib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import seasons

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("SHOP_DB_PATH", os.path.join(_BASE_DIR, "data", "shop.db"))

PROPOSAL_TTL_SECONDS = 10 * 60
# A proposal cannot be confirmed in the same breath it was made: the agent has
# to read it out and hear an answer first, and that takes at least this long.
MIN_READOUT_SECONDS = 4.0

# The supervisor's own words must say yes, and must not also say no.
_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure", "confirm", "confirmed", "approve",
    "approved", "proceed", "place", "correct", "right", "fine", "done", "agreed",
    "haan", "han", "theek", "thik", "kardo", "हाँ", "हां", "जी", "ठीक",
}
_YES_PHRASES = ("go ahead", "do it", "kar do", "कर दो", "sounds good")
_NO_WORDS = {
    "no", "not", "dont", "cancel", "wait", "stop", "later", "hold", "never",
    "nahi", "nahin", "mat", "नहीं", "मत", "रुको",
}


def _approval_problem(phrase: str) -> Optional[str]:
    """Why these words do not count as approval, or None if they do."""
    lowered = phrase.lower().replace("\u2019", "").replace("'", "")
    words = set("".join(ch if ch.isalnum() else " " for ch in lowered).split())
    says_yes = bool(words & _YES_WORDS) or any(p in lowered for p in _YES_PHRASES)
    says_no = bool(words & _NO_WORDS)
    if says_no:
        return "The supervisor's words include a no or a wait. Do not place the order. Ask what they want changed."
    if not says_yes:
        return "Those words are not a clear yes. Ask the supervisor plainly: shall I place this order, yes or no?"
    return None
COVER_DAYS = 3  # how many days of sales a suggested order should cover

_lock = threading.Lock()


class ShopError(Exception):
    """A business-rule failure the agent should explain and recover from."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **self.details}


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '',
    detect_label TEXT NOT NULL,
    unit TEXT NOT NULL,
    pack_size INTEGER NOT NULL,
    shelf_qty INTEGER NOT NULL,
    shelf_capacity INTEGER NOT NULL,
    shelf_threshold INTEGER NOT NULL,
    backroom_qty INTEGER NOT NULL,
    daily_sales INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    lead_time_hours INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS supplier_prices (
    supplier_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    available_qty INTEGER NOT NULL,
    PRIMARY KEY (supplier_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    total REAL NOT NULL,
    status TEXT NOT NULL,            -- proposed | placed | delivered | superseded
    approval_phrase TEXT,
    created_at REAL NOT NULL,
    placed_at REAL,
    eta_at REAL
);
CREATE TABLE IF NOT EXISTS refill_tasks (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL,            -- open | done
    created_at REAL NOT NULL,
    done_at REAL
);
CREATE TABLE IF NOT EXISTS callbacks (
    id INTEGER PRIMARY KEY,
    product_id INTEGER,
    due_at REAL NOT NULL,
    note TEXT NOT NULL,
    created_at REAL NOT NULL,
    role TEXT NOT NULL DEFAULT 'supervisor',   -- who asked to be called back
    status TEXT NOT NULL DEFAULT 'pending'     -- pending | done
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
"""

# detect_label is what the shelf camera looks for: plain descriptions of the
# shape, several per product separated by "|". "bottle" finds bottles of any
# colour; "water bottle" alone misses ones that are not clear plastic.
# A description starting with "~" marks a flat wrapped product, which the
# camera cannot name by shape: it is a sentence for the second-step reader.
# Only one wrapped product is read by camera at a time: the reader cannot tell
# a wafer chocolate bar from a biscuit packet (it called a Munch bar biscuits
# on a quarter of real frames). An empty description keeps a product off the
# camera; its count is then changed by hand on the dashboard.
# name, aliases, detect_label, unit, pack, shelf, capacity, threshold, backroom, daily_sales
SEED_PRODUCTS = [
    ("water bottle", "water,bisleri,pani,paani,bottle", "bottle|water bottle", "bottle", 12, 10, 12, 4, 24, 60),
    ("chocolate bar", "chocolate,dairy milk,choco,munch", "~a chocolate bar in its wrapper", "bar", 24, 5, 5, 2, 0, 36),
    ("cola can", "cola,coke,soft drink,cold drink,can", "soda can|tin can", "can", 24, 9, 12, 4, 0, 40),
    ("biscuit packet", "biscuit,biscuits,parle,cookies", "", "packet", 20, 10, 12, 4, 40, 50),
    ("pen", "pens,ball pen,ballpen,ballpoint,kalam", "pen@0.1|ballpoint pen@0.1", "pen", 10, 5, 5, 2, 30, 25),
]

# name, lead_time_hours
SEED_SUPPLIERS = [
    ("Bharat Wholesale", 24),
    ("Sri Lakshmi Traders", 12),
    ("QuickDrop Supply", 2),
]

# supplier, product, unit_price, available_qty
# Bharat Wholesale is always cheapest but is out of cola and short on chocolate,
# so the agent has to adapt instead of just taking the lowest price.
SEED_PRICES = [
    ("Bharat Wholesale", "water bottle", 13.0, 600),
    ("Sri Lakshmi Traders", "water bottle", 14.5, 400),
    ("QuickDrop Supply", "water bottle", 16.0, 200),
    ("Bharat Wholesale", "chocolate bar", 31.0, 48),
    ("Sri Lakshmi Traders", "chocolate bar", 33.0, 300),
    ("QuickDrop Supply", "chocolate bar", 36.0, 150),
    ("Bharat Wholesale", "cola can", 27.0, 0),
    ("Sri Lakshmi Traders", "cola can", 29.0, 480),
    ("QuickDrop Supply", "cola can", 32.0, 240),
    ("Bharat Wholesale", "biscuit packet", 8.0, 500),
    ("Sri Lakshmi Traders", "biscuit packet", 8.5, 500),
    ("QuickDrop Supply", "biscuit packet", 9.5, 200),
    ("Bharat Wholesale", "pen", 4.0, 1000),
    ("Sri Lakshmi Traders", "pen", 4.5, 500),
    ("QuickDrop Supply", "pen", 6.0, 200),
]

SEED_BUDGET = 6000.0


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_prices(conn: sqlite3.Connection) -> None:
    """Add any supplier price that is not there yet."""
    for supplier, product, price, qty in SEED_PRICES:
        conn.execute(
            "INSERT OR IGNORE INTO supplier_prices (supplier_id, product_id, unit_price, available_qty)"
            " VALUES ((SELECT id FROM suppliers WHERE name=?),"
            " (SELECT id FROM products WHERE name=?), ?, ?)",
            (supplier, product, price, qty),
        )


def init_db(reset: bool = False) -> None:
    """Create tables and seed demo data. `reset` wipes back to the seed state."""
    with _lock, _connect() as conn:
        if reset:
            for table in (
                "products", "suppliers", "supplier_prices", "orders",
                "refill_tasks", "callbacks", "settings", "events",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)
        # A database made before callbacks could ring lacks these two columns.
        have = {row[1] for row in conn.execute("PRAGMA table_info(callbacks)")}
        if "role" not in have:
            conn.execute("ALTER TABLE callbacks ADD COLUMN role TEXT NOT NULL DEFAULT 'supervisor'")
        if "status" not in have:
            conn.execute("ALTER TABLE callbacks ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
            # An existing database: add any product the seed list has gained and
            # keep the camera descriptions current. Stock levels are left alone.
            conn.executemany(
                "INSERT OR IGNORE INTO products (name, aliases, detect_label, unit, pack_size, shelf_qty,"
                " shelf_capacity, shelf_threshold, backroom_qty, daily_sales)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                SEED_PRODUCTS,
            )
            conn.executemany(
                "UPDATE products SET detect_label=? WHERE name=?",
                [(row[2], row[0]) for row in SEED_PRODUCTS],
            )
            _seed_prices(conn)
            return
        conn.executemany(
            "INSERT INTO products (name, aliases, detect_label, unit, pack_size, shelf_qty,"
            " shelf_capacity, shelf_threshold, backroom_qty, daily_sales)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            SEED_PRODUCTS,
        )
        conn.executemany(
            "INSERT INTO suppliers (name, lead_time_hours) VALUES (?,?)", SEED_SUPPLIERS
        )
        _seed_prices(conn)
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('budget_remaining', ?)",
            (str(SEED_BUDGET),),
        )


# --------------------------------------------------------------------------- #
# Event log (read by the dashboard)
# --------------------------------------------------------------------------- #
def log_event(
    kind: str,
    title: str,
    detail: Optional[Dict[str, Any]] = None,
    agent: Optional[str] = None,
    to: Optional[str] = None,
) -> None:
    """Record something that happened. `agent` is the part of the system that
    acted and `to` is who it addressed (see AGENTS in dispatch.py); the
    playground draws the hand-offs between them from these."""
    detail = {**(detail or {})}
    if agent:
        detail["agent"] = agent
    if to:
        detail["to"] = to
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, title, detail) VALUES (?,?,?,?)",
            (time.time(), kind, title, json.dumps(detail or {}, default=str)),
        )


def tool_called_since(timestamp: float) -> bool:
    """Has any tool call been logged since this moment?"""
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM events WHERE kind='tool' AND ts >= ? LIMIT 1", (timestamp,)
        ).fetchone() is not None


def last_event_id() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def list_events(after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
        ).fetchall()
    return [
        {**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in rows
    ]


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
def _normalise(text: str) -> str:
    return " ".join(str(text or "").lower().replace("-", " ").split())


def find_product(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    """Match a spoken product name. Speech-to-text is loose, so accept the
    name, any alias, a plural, or a near miss."""
    wanted = _normalise(query)
    rows = conn.execute("SELECT * FROM products").fetchall()
    if not wanted:
        raise ShopError("PRODUCT_REQUIRED", "Which product do you mean?",
                        products=[r["name"] for r in rows])
    candidates: Dict[str, sqlite3.Row] = {}
    for row in rows:
        for label in [row["name"], *row["aliases"].split(",")]:
            label = _normalise(label)
            if label:
                candidates[label] = row
    singular = wanted[:-1] if wanted.endswith("s") else wanted
    for key in (wanted, singular):
        if key in candidates:
            return candidates[key]
    for label, row in candidates.items():
        if label in wanted or singular in label:
            return row
    close = difflib.get_close_matches(wanted, list(candidates), n=1, cutoff=0.72)
    if close:
        return candidates[close[0]]
    raise ShopError(
        "UNKNOWN_PRODUCT",
        f"'{query}' is not in the catalogue.",
        products=[r["name"] for r in rows],
    )


def find_supplier(conn: sqlite3.Connection, query: str) -> sqlite3.Row:
    wanted = _normalise(query)
    rows = conn.execute("SELECT * FROM suppliers").fetchall()
    names = {_normalise(r["name"]): r for r in rows}
    if wanted in names:
        return names[wanted]
    for label, row in names.items():
        if wanted and (wanted in label or label.split()[0] in wanted):
            return row
    close = difflib.get_close_matches(wanted, list(names), n=1, cutoff=0.6)
    if close:
        return names[close[0]]
    raise ShopError(
        "UNKNOWN_SUPPLIER",
        f"'{query}' is not one of our suppliers.",
        suppliers=[r["name"] for r in rows],
    )


def catalogue() -> List[Dict[str, Any]]:
    """Every product with the text the shelf camera looks for."""
    with _connect() as conn:
        rows = conn.execute("SELECT name, unit, detect_label FROM products ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def _budget(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key='budget_remaining'").fetchone()
    return float(row["value"]) if row else 0.0


def _shelf_view(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "product": row["name"],
        "unit": row["unit"],
        "on_shelf": row["shelf_qty"],
        "shelf_capacity": row["shelf_capacity"],
        "low_threshold": row["shelf_threshold"],
        "is_low": row["shelf_qty"] <= row["shelf_threshold"],
        "in_back_room": row["backroom_qty"],
    }


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
def shelf_status(product: Optional[str] = None) -> Dict[str, Any]:
    with _connect() as conn:
        if product:
            return {"ok": True, **_shelf_view(find_product(conn, product))}
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
        return {"ok": True, "products": [_shelf_view(r) for r in rows]}


def sales_rate(product: str) -> Dict[str, Any]:
    with _connect() as conn:
        row = find_product(conn, product)
    daily = row["daily_sales"]
    in_store = row["shelf_qty"] + row["backroom_qty"]
    forecast = _forecast(row)
    return {
        "ok": True,
        "product": row["name"],
        "unit": row["unit"],
        "sold_per_day": daily,
        "total_in_store": in_store,
        "hours_until_sold_out": round(in_store / daily * 12, 1) if daily else None,
        "pack_size": row["pack_size"],
        "suggested_order_quantity": forecast["recommended_order_quantity"],
        "normal_order_quantity": forecast["normal_order_quantity"],
        "suggestion_basis": forecast["basis"],
    }


def _whole_packs(units: float, pack_size: int) -> int:
    """Round a quantity up to full packs."""
    return int(-(-units // pack_size) * pack_size)


def _forecast(row: sqlite3.Row) -> Dict[str, Any]:
    """Expected demand for a product over the days an order should cover.
    Normal sales, lifted when an occasion that moves this product is close."""
    daily, pack = row["daily_sales"], row["pack_size"]
    normal_qty = _whole_packs(daily * COVER_DAYS, pack)
    lift = seasons.uplift_for(row["name"])
    out: Dict[str, Any] = {
        "product": row["name"],
        "unit": row["unit"],
        "normal_sold_per_day": daily,
        "normal_order_quantity": normal_qty,
        "cover_days": COVER_DAYS,
        "pack_size": pack,
    }
    if not lift:
        out.update({
            "upcoming_occasion": None,
            "expected_uplift_percent": 0,
            "forecast_sold_per_day": daily,
            "recommended_order_quantity": normal_qty,
            "basis": f"{COVER_DAYS} days of normal sales, rounded up to full packs of {pack}",
            "say": f"No occasion is coming up for {row['name']}, so the normal quantity of {normal_qty} is right.",
        })
        return out
    forecast_daily = round(daily * (1 + lift["percent"] / 100))
    qty = _whole_packs(forecast_daily * COVER_DAYS, pack)
    when = "today" if lift["days_away"] == 0 else f"{lift['days_away']} days away"
    out.update({
        "upcoming_occasion": lift["name"],
        "days_until_occasion": lift["days_away"],
        "expected_uplift_percent": lift["percent"],
        "forecast_sold_per_day": forecast_daily,
        "recommended_order_quantity": qty,
        "extra_over_normal": qty - normal_qty,
        "basis": (f"{COVER_DAYS} days of sales at {lift['percent']} percent above normal for"
                  f" {lift['name']}, rounded up to full packs of {pack}"),
        "say": (f"{lift['name']} is {when} and {row['name']} sells about {lift['percent']} percent more"
                f" then, so order {qty} instead of the usual {normal_qty}."),
    })
    return out


def demand_forecast(product: str) -> Dict[str, Any]:
    with _connect() as conn:
        row = find_product(conn, product)
    return {"ok": True, **_forecast(row)}


def supplier_quotes(product: str, quantity: Optional[int] = None) -> Dict[str, Any]:
    """Everything needed to recommend an order, in one call: stock, sales, the
    suggested quantity and every supplier's quote for it. On a voice call each
    separate lookup is seconds of silence, so they are not made one by one.
    Leave `quantity` out to quote the suggested quantity."""
    sales = sales_rate(product)
    if quantity is None:
        quantity = sales["suggested_order_quantity"]
    if quantity <= 0:
        raise ShopError("BAD_QUANTITY", "Quantity must be more than zero.")
    with _connect() as conn:
        row = find_product(conn, product)
        prices = conn.execute(
            "SELECT s.name, s.lead_time_hours, p.unit_price, p.available_qty"
            " FROM supplier_prices p JOIN suppliers s ON s.id = p.supplier_id"
            " WHERE p.product_id = ? ORDER BY p.unit_price",
            (row["id"],),
        ).fetchall()
        budget = _budget(conn)
    quotes = []
    for p in prices:
        can_fill = p["available_qty"] >= quantity
        quotes.append({
            "supplier": p["name"],
            "unit_price": p["unit_price"],
            "total_for_quantity": round(p["unit_price"] * quantity, 2),
            "delivery_hours": p["lead_time_hours"],
            "available_quantity": p["available_qty"],
            "can_fill_order": can_fill,
            "within_budget": p["unit_price"] * quantity <= budget,
            "problem": None if can_fill else (
                "out of stock" if p["available_qty"] == 0
                else f"only {p['available_qty']} available"
            ),
        })
    fillable = [q for q in quotes if q["can_fill_order"]]
    return {
        "ok": True,
        "product": row["name"],
        "on_shelf": row["shelf_qty"],
        "in_back_room": row["backroom_qty"],
        "sold_per_day": sales["sold_per_day"],
        "suggested_order_quantity": sales["suggested_order_quantity"],
        "normal_order_quantity": sales["normal_order_quantity"],
        "suggestion_basis": sales["suggestion_basis"],
        "quantity": quantity,
        "budget_remaining": budget,
        "quotes": quotes,
        "cheapest_that_can_fill": min(fillable, key=lambda q: q["unit_price"])["supplier"] if fillable else None,
        "fastest_that_can_fill": min(fillable, key=lambda q: q["delivery_hours"])["supplier"] if fillable else None,
    }


def budget_status() -> Dict[str, Any]:
    with _connect() as conn:
        return {"ok": True, "currency": "INR", "budget_remaining": _budget(conn)}


# --------------------------------------------------------------------------- #
# Ordering: propose, then confirm
# --------------------------------------------------------------------------- #
def propose_order(product: str, supplier: str, quantity: int) -> Dict[str, Any]:
    if quantity <= 0:
        raise ShopError("BAD_QUANTITY", "Quantity must be more than zero.")
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        supp = find_supplier(conn, supplier)
        price = conn.execute(
            "SELECT * FROM supplier_prices WHERE supplier_id=? AND product_id=?",
            (supp["id"], prod["id"]),
        ).fetchone()
        if price is None:
            raise ShopError("NOT_SOLD", f"{supp['name']} does not sell {prod['name']}.")
        if price["available_qty"] == 0:
            raise ShopError(
                "SUPPLIER_OUT_OF_STOCK",
                f"{supp['name']} is out of {prod['name']}. Check the other suppliers.",
                supplier=supp["name"],
            )
        if price["available_qty"] < quantity:
            raise ShopError(
                "SUPPLIER_SHORT",
                f"{supp['name']} only has {price['available_qty']} {prod['unit']}s.",
                supplier=supp["name"], max_quantity=price["available_qty"],
            )
        total = round(price["unit_price"] * quantity, 2)
        budget = _budget(conn)
        if total > budget:
            raise ShopError(
                "OVER_BUDGET",
                f"That costs {total:.0f} rupees but only {budget:.0f} rupees of budget is left.",
                total=total, budget_remaining=budget,
                max_affordable_quantity=int(budget // price["unit_price"]),
            )
        on_order = conn.execute(
            "SELECT o.id, o.quantity, o.eta_at, s.name AS supplier FROM orders o"
            " JOIN suppliers s ON s.id=o.supplier_id"
            " WHERE o.product_id=? AND o.status='placed' ORDER BY o.id DESC LIMIT 1",
            (prod["id"],),
        ).fetchone()
        if on_order:
            hours = max(0, round((on_order["eta_at"] - time.time()) / 3600))
            raise ShopError(
                "ALREADY_ON_ORDER",
                f"Order number {on_order['id']} for {on_order['quantity']} {prod['name']} from"
                f" {on_order['supplier']} is already placed and arrives in about {hours} hours."
                " A second order cannot be placed until it is delivered.",
                order_number=on_order["id"],
            )
        # A new proposal for the same product replaces any earlier one.
        conn.execute(
            "UPDATE orders SET status='superseded' WHERE product_id=? AND status='proposed'",
            (prod["id"],),
        )
        cur = conn.execute(
            "INSERT INTO orders (product_id, supplier_id, quantity, unit_price, total, status, created_at)"
            " VALUES (?,?,?,?,?,'proposed',?)",
            (prod["id"], supp["id"], quantity, price["unit_price"], total, time.time()),
        )
        proposal_id = cur.lastrowid
    return {
        "ok": True,
        "proposal_id": proposal_id,
        "status": "awaiting_supervisor_approval",
        "product": prod["name"],
        "supplier": supp["name"],
        "quantity": quantity,
        "unit": prod["unit"],
        "unit_price": price["unit_price"],
        "total": total,
        "delivery_hours": supp["lead_time_hours"],
        "budget_left_after": round(budget - total, 2),
        "next_step": "Read the quantity, supplier, total and delivery time to the supervisor,"
                     " then stop and wait for their answer. Call confirm_order only in a later"
                     " turn, after they clearly say yes, and pass their exact words.",
    }


def confirm_order(proposal_id: int, approval_phrase: str) -> Dict[str, Any]:
    phrase = (approval_phrase or "").strip()
    if len(phrase) < 2:
        raise ShopError(
            "APPROVAL_REQUIRED",
            "No approval was given. Ask the supervisor to approve the order first.",
        )
    problem = _approval_problem(phrase)
    if problem:
        raise ShopError("APPROVAL_NOT_CLEAR", problem, heard=phrase[:200])
    with _lock, _connect() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=?", (proposal_id,)).fetchone()
        if order is None:
            raise ShopError("NO_SUCH_PROPOSAL", "There is no proposal with that number. Propose the order again.")
        if order["status"] == "placed":
            raise ShopError("ALREADY_PLACED", "That order has already been placed.", order_number=order["id"])
        if order["status"] != "proposed":
            raise ShopError(
                "PROPOSAL_REPLACED",
                "That proposal was replaced by a newer one. Use the latest proposal.",
            )
        age = time.time() - order["created_at"]
        if age > PROPOSAL_TTL_SECONDS:
            raise ShopError("PROPOSAL_EXPIRED", "That proposal is too old. Propose the order again.")
        if age < MIN_READOUT_SECONDS:
            raise ShopError(
                "NOT_READ_OUT",
                "This proposal was made a moment ago, so the supervisor cannot have heard it yet."
                " Read the quantity, supplier and total aloud, wait for their answer, then confirm.",
            )
        price = conn.execute(
            "SELECT * FROM supplier_prices WHERE supplier_id=? AND product_id=?",
            (order["supplier_id"], order["product_id"]),
        ).fetchone()
        if price is None or price["available_qty"] < order["quantity"]:
            raise ShopError("SUPPLIER_SHORT", "The supplier no longer has enough stock. Check quotes again.")
        if abs(price["unit_price"] - order["unit_price"]) > 0.005:
            raise ShopError(
                "PRICE_CHANGED",
                "The supplier's price changed after this was read out. Propose the order again"
                " and read the new total.",
            )
        budget = _budget(conn)
        if order["total"] > budget:
            raise ShopError("OVER_BUDGET", "The budget changed and no longer covers this order.",
                            budget_remaining=budget)
        supp = conn.execute("SELECT * FROM suppliers WHERE id=?", (order["supplier_id"],)).fetchone()
        prod = conn.execute("SELECT * FROM products WHERE id=?", (order["product_id"],)).fetchone()
        now = time.time()
        eta = now + supp["lead_time_hours"] * 3600
        conn.execute(
            "UPDATE orders SET status='placed', approval_phrase=?, placed_at=?, eta_at=? WHERE id=?",
            (phrase[:200], now, eta, proposal_id),
        )
        conn.execute(
            "UPDATE supplier_prices SET available_qty = available_qty - ?"
            " WHERE supplier_id=? AND product_id=?",
            (order["quantity"], order["supplier_id"], order["product_id"]),
        )
        conn.execute(
            "UPDATE settings SET value=? WHERE key='budget_remaining'",
            (str(round(budget - order["total"], 2)),),
        )
    return {
        "ok": True,
        "status": "placed",
        "order_number": proposal_id,
        "product": prod["name"],
        "supplier": supp["name"],
        "quantity": order["quantity"],
        "total": order["total"],
        "delivery_hours": supp["lead_time_hours"],
        "budget_remaining": round(budget - order["total"], 2),
        "approved_with": phrase[:200],
    }


def order_status(product: Optional[str] = None) -> Dict[str, Any]:
    with _connect() as conn:
        sql = (
            "SELECT o.*, p.name AS product, p.unit, s.name AS supplier FROM orders o"
            " JOIN products p ON p.id=o.product_id JOIN suppliers s ON s.id=o.supplier_id"
            " WHERE o.status IN ('placed','delivered')"
        )
        args: List[Any] = []
        if product:
            sql += " AND o.product_id=?"
            args.append(find_product(conn, product)["id"])
        rows = conn.execute(sql + " ORDER BY o.id DESC LIMIT 5", args).fetchall()
    now = time.time()
    orders = [{
        "order_number": r["id"],
        "product": r["product"],
        "supplier": r["supplier"],
        "quantity": r["quantity"],
        "total": r["total"],
        "status": r["status"],
        "hours_until_delivery": max(0, round((r["eta_at"] - now) / 3600, 1)) if r["status"] == "placed" else 0,
    } for r in rows]
    return {"ok": True, "orders": orders, "count": len(orders)}


def schedule_callback(
    product: Optional[str], in_minutes: float, note: str, role: str = "supervisor"
) -> Dict[str, Any]:
    """Promise to ring someone back. The call is placed when it falls due (see
    dispatch.fire_due_callbacks), so "later" is a real follow-up, not a note."""
    if in_minutes <= 0 or in_minutes > 60 * 24 * 14:
        raise ShopError("BAD_TIME", "Pick a callback time between now and two weeks from now.")
    text = (note or "").strip()[:200] or "Follow up"
    with _lock, _connect() as conn:
        product_id = find_product(conn, product)["id"] if product else None
        cur = conn.execute(
            "INSERT INTO callbacks (product_id, due_at, note, created_at, role, status)"
            " VALUES (?,?,?,?,?, 'pending')",
            (product_id, time.time() + in_minutes * 60, text, time.time(), role),
        )
    return {"ok": True, "callback_id": cur.lastrowid, "in_minutes": in_minutes, "note": text,
            "will_call": role}


# --------------------------------------------------------------------------- #
# Dashboard snapshot
# --------------------------------------------------------------------------- #
def snapshot() -> Dict[str, Any]:
    with _connect() as conn:
        products = [_shelf_view(r) for r in conn.execute("SELECT * FROM products ORDER BY id")]
        budget = _budget(conn)
    occasion = seasons.coming_up()
    calendar = {
        "simulated": seasons.simulated(),
        "occasions": seasons.names(),
        "coming_up": occasion["name"] if occasion else None,
        "days_away": occasion["days_away"] if occasion else None,
    }
    return {"products": products, "budget_remaining": budget, "calendar": calendar, **order_status()}
