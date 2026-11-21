"""Who the agent calls, and why.

A shelf count arrives (from the camera or the dashboard slider). If a product is
low, `triage` checks the back room and either sends the floor employee to
refill the shelf or rings the supervisor about a reorder. Calls sit in a queue
that each phone polls; answering one starts a voice agent briefed for that
person and that product.
"""
import time
from typing import Any, Dict, List, Optional

import prompts
import shop_db
from shop_db import ShopError, _connect, _lock, find_product, log_event, near_low_mark, refill_target

RING_TIMEOUT_SECONDS = 45

# The parts of the system, as the playground shows them. Only the two voice
# agents are language models; the rest are vision models and plain rules, and
# are labelled as such on screen.
#   shelf_watcher      camera: detector + reader, steady counts
#   dispatcher         triage rules: who to call and why, the call queue
#   floor_agent        voice agent for the employee (Hinglish)
#   procurement_agent  voice agent for the supervisor (English)
#   order_gate         propose-then-confirm rule on purchases
#   shop_tools         stock, sales, supplier and budget lookups over MCP
#   employee, supervisor   the two people
VOICE_AGENT = {"employee": "floor_agent", "supervisor": "procurement_agent"}

# Who the voice agent is talking to right now; tool calls are attributed to it.
current_role = "supervisor"

# Set by the server: called with each newly placed call so it can dial a real
# phone when that is switched on. Left as None, calls only ring the web page.
on_call_placed = None
ROLES = ("employee", "supervisor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    product_id INTEGER,
    reason TEXT NOT NULL,
    greeting TEXT NOT NULL,
    also TEXT,                       -- other products covered by the same call: ",a,b,"
    status TEXT NOT NULL,            -- ringing | answered | ended | declined | missed
    created_at REAL NOT NULL,
    answered_at REAL,
    ended_at REAL
);
-- Products the store has switched off: still counted, never acted on.
-- Kept by name and left alone by a demo reset, so a switch stays where it was put.
CREATE TABLE IF NOT EXISTS watch_off (
    product TEXT PRIMARY KEY
);
"""


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(calls)").fetchall()]
        if "also" not in columns:  # a database made before calls could cover several products
            conn.execute("ALTER TABLE calls ADD COLUMN also TEXT")


def reset() -> None:
    global current_role
    current_role = "supervisor"
    with _lock, _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS calls")
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- #
# Call queue
# --------------------------------------------------------------------------- #
def _call_view(row: Any) -> Dict[str, Any]:
    return {
        "call_id": row["id"],
        "role": row["role"],
        "product": row["product"],
        "also": [n for n in (row["also"] or "").split(",") if n],
        "reason": row["reason"],
        "greeting": row["greeting"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


_CALL_SELECT = (
    "SELECT c.*, p.name AS product FROM calls c LEFT JOIN products p ON p.id = c.product_id"
)


def place_call(role: str, product: Optional[str], reason: str, greeting: str,
               also: Optional[List[str]] = None) -> Dict[str, Any]:
    """Queue an outgoing call. The matching phone starts ringing on its next poll.
    `also` names other products the same call covers."""
    if role not in ROLES:
        raise ShopError("BAD_ROLE", f"Unknown role '{role}'.")
    with _lock, _connect() as conn:
        product_id = find_product(conn, product)["id"] if product else None
        cur = conn.execute(
            "INSERT INTO calls (role, product_id, reason, greeting, also, status, created_at)"
            " VALUES (?,?,?,?,?, 'ringing', ?)",
            (role, product_id, reason, greeting, "," + ",".join(also) + "," if also else None, time.time()),
        )
        call_id = cur.lastrowid
    log_event("call", f"Calling the {role}",
              {"call_id": call_id, "product": product, "also": also or [], "reason": reason},
              agent="dispatcher", to=VOICE_AGENT[role])
    call = {"call_id": call_id, "role": role, "product": product, "also": also or [], "reason": reason}
    if on_call_placed is not None:
        on_call_placed({**call, "greeting": greeting})
    return call


def _expire_stale(conn: Any) -> None:
    conn.execute(
        "UPDATE calls SET status='missed', ended_at=? WHERE status='ringing' AND created_at < ?",
        (time.time(), time.time() - RING_TIMEOUT_SECONDS),
    )


def pending_call(role: str) -> Optional[Dict[str, Any]]:
    """The call currently ringing for a role, if any."""
    with _lock, _connect() as conn:
        _expire_stale(conn)
        row = conn.execute(
            _CALL_SELECT + " WHERE c.role=? AND c.status='ringing' ORDER BY c.id LIMIT 1", (role,)
        ).fetchone()
    return _call_view(row) if row else None


def get_call(call_id: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(_CALL_SELECT + " WHERE c.id=?", (call_id,)).fetchone()
    return _call_view(row) if row else None


def set_call_status(call_id: int, status: str) -> Optional[Dict[str, Any]]:
    stamp = "answered_at" if status == "answered" else "ended_at"
    with _lock, _connect() as conn:
        conn.execute(
            f"UPDATE calls SET status=?, {stamp}=? WHERE id=?", (status, time.time(), call_id)
        )
    call = get_call(call_id)
    if call:
        titles = {
            "answered": f"The {call['role']} answered",
            "ended": f"Call with the {call['role']} ended",
            "declined": f"The {call['role']} declined the call",
        }
        if status == "answered":
            global current_role
            current_role = call["role"]
        log_event("call", titles.get(status, f"Call {status}"), {"call_id": call_id},
                  agent=call["role"], to=VOICE_AGENT[call["role"]])
        if status == "ended":
            _log_call_summary(call_id, call["role"])
    return call


def _log_call_summary(call_id: int, role: str) -> None:
    """When a call ends, put on record what it achieved and what happens next.
    Built from what was actually done during the call, not from what was said."""
    with _connect() as conn:
        row = conn.execute("SELECT answered_at FROM calls WHERE id=?", (call_id,)).fetchone()
        since = row["answered_at"] if row and row["answered_at"] else None
        if since is None:
            return  # never answered: nothing was done
        orders = conn.execute(
            "SELECT o.id, o.quantity, o.total, o.eta_at, p.name AS product, s.name AS supplier"
            " FROM orders o JOIN products p ON p.id=o.product_id JOIN suppliers s ON s.id=o.supplier_id"
            " WHERE o.status IN ('placed','delivered') AND o.placed_at >= ?", (since,),
        ).fetchall()
        refills = conn.execute(
            "SELECT p.name AS product FROM refill_tasks t JOIN products p ON p.id=t.product_id"
            " WHERE t.status='done' AND t.done_at >= ?", (since,),
        ).fetchall()
        callbacks = conn.execute(
            "SELECT due_at FROM callbacks WHERE status='pending' AND created_at >= ?", (since,),
        ).fetchall()
    done: List[str] = []
    for o in orders:
        hours = max(0, round((o["eta_at"] - time.time()) / 3600))
        done.append(
            f"order #{o['id']} placed, {o['quantity']} {o['product']} from {o['supplier']}"
            f" for {o['total']:.0f} rupees, arriving in about {hours} hours."
            " The camera will confirm when it is on the shelf"
        )
    for r in refills:
        done.append(f"{r['product']} shelf refilled")
    for c in callbacks:
        minutes = max(1, round((c["due_at"] - time.time()) / 60))
        done.append(f"will call back in {minutes} minute{'s' if minutes != 1 else ''}")
    summary = "; ".join(done) if done else "no action taken"
    log_event("call", f"Summary: {summary}", {"call_id": call_id, "actions": len(done)},
              agent=VOICE_AGENT[role], to=role)


def fire_due_callbacks() -> List[Dict[str, Any]]:
    """Ring back anyone whose promised callback has fallen due. Called on a
    timer by the server. A callback waits if that person is already on a call.

    A callback to the employee about a product is a shelf check: if the shelf
    is above its threshold when it falls due, the job is done and nobody is
    rung. Only a shelf that is still low gets a second call."""
    placed: List[Dict[str, Any]] = []
    refilled: List[Any] = []
    with _lock, _connect() as conn:
        _expire_stale(conn)
        due = conn.execute(
            "SELECT c.*, p.name AS product, p.shelf_qty, p.shelf_threshold FROM callbacks c"
            " LEFT JOIN products p ON p.id=c.product_id"
            " WHERE c.status='pending' AND c.due_at <= ? ORDER BY c.due_at", (time.time(),),
        ).fetchall()
        ready = []
        for row in due:
            if row["role"] == "employee" and row["product"] and row["shelf_qty"] > row["shelf_threshold"]:
                conn.execute("UPDATE callbacks SET status='done' WHERE id=?", (row["id"],))
                conn.execute(
                    "UPDATE refill_tasks SET status='done', done_at=? WHERE status='open' AND (product_id=?"
                    " OR product_id IN (SELECT id FROM products WHERE shelf_qty > shelf_threshold))",
                    (time.time(), row["product_id"]),
                )
                refilled.append(row)
                continue
            busy = conn.execute(
                "SELECT 1 FROM calls WHERE role=? AND status IN ('ringing','answered')", (row["role"],)
            ).fetchone()
            if busy:
                continue
            conn.execute("UPDATE callbacks SET status='done' WHERE id=?", (row["id"],))
            ready.append(row)
    for row in refilled:
        log_event("decision",
                  f"Checked the {row['product']} shelf as promised: refilled, {row['shelf_qty']} on the shelf. No call needed.",
                  {"product": row["product"], "on_shelf": row["shelf_qty"]},
                  agent="dispatcher", to="shelf_watcher")
    for row in ready:
        role, product = row["role"], row["product"]
        still_low = role == "employee" and product
        log_event("decision",
                  f"Checked the {product} shelf as promised: still {row['shelf_qty']}. Ringing the employee again."
                  if still_low else f"Callback is due: ringing the {role} back",
                  {"product": product, "note": row["note"]},
                  agent="dispatcher", to=VOICE_AGENT[role])
        placed.append(place_call(
            role, product, f"callback: {row['note']}",
            prompts.callback_greeting(role, product, row["shelf_qty"], row["note"]),
        ))
    return placed


# --------------------------------------------------------------------------- #
# Per-product switch: is the agent allowed to act on this product?
# --------------------------------------------------------------------------- #
def _is_off(conn: Any, name: str) -> bool:
    return conn.execute("SELECT 1 FROM watch_off WHERE product=?", (name,)).fetchone() is not None


def set_enabled(product: str, enabled: bool) -> Dict[str, Any]:
    """Switch the agent on or off for one product. Off: the shelf is still
    counted, but a low count places no call and creates no refill task.
    Switching back on acts at once if the product is low right now."""
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        if enabled:
            conn.execute("DELETE FROM watch_off WHERE product=?", (prod["name"],))
        else:
            conn.execute("INSERT OR IGNORE INTO watch_off (product) VALUES (?)", (prod["name"],))
    log_event("system", f"Agent switched {'on' if enabled else 'off'} for {prod['name']}",
              {"product": prod["name"], "enabled": enabled})
    action = triage(prod["name"]) if enabled else {"action": "none"}
    return {"ok": True, "product": prod["name"], "enabled": enabled, **action}


def _has_live_call(conn: Any, prod: Any) -> bool:
    """Is a call ringing or in progress that is about this product, or covers it?"""
    _expire_stale(conn)
    return conn.execute(
        "SELECT 1 FROM calls WHERE status IN ('ringing','answered') AND (product_id=? OR also LIKE ?)",
        (prod["id"], f"%,{prod['name']},%"),
    ).fetchone() is not None


def _is_being_handled(conn: Any, prod: Any) -> bool:
    """Switched off, on a call, or already with a refill task or callback."""
    if _is_off(conn, prod["name"]) or _has_live_call(conn, prod):
        return True
    return conn.execute(
        "SELECT 1 FROM refill_tasks WHERE product_id=? AND status='open'"
        " UNION SELECT 1 FROM callbacks WHERE product_id=? AND status='pending'",
        (prod["id"], prod["id"]),
    ).fetchone() is not None


def _nearly_low_companions(conn: Any, primary: Any, needs_refill: bool) -> List[Any]:
    """Other products close enough to their low mark to deal with on the same
    call: ones the employee can refill from the back room when the call is a
    refill, ones with an empty back room and nothing on order when it is a reorder."""
    found = []
    rows = conn.execute("SELECT * FROM products WHERE id<>? ORDER BY id", (primary["id"],)).fetchall()
    for row in rows:
        if row["shelf_qty"] > near_low_mark(row) or _is_being_handled(conn, row):
            continue
        can_refill = min(refill_target(row) - row["shelf_qty"], row["backroom_qty"]) > 0
        if needs_refill and can_refill:
            found.append(row)
        elif not needs_refill and row["backroom_qty"] <= 0:
            on_order = conn.execute(
                "SELECT 1 FROM orders WHERE product_id=? AND status='placed'", (row["id"],)
            ).fetchone()
            if not on_order:
                found.append(row)
    return found


# --------------------------------------------------------------------------- #
# Refill tasks (employee side)
# --------------------------------------------------------------------------- #
def open_refill_tasks(product: Optional[str] = None) -> Dict[str, Any]:
    with _connect() as conn:
        sql = (
            "SELECT t.*, p.name AS product, p.unit, p.shelf_qty, p.backroom_qty FROM refill_tasks t"
            " JOIN products p ON p.id=t.product_id WHERE t.status='open'"
        )
        args: List[Any] = []
        if product:
            sql += " AND t.product_id=?"
            args.append(find_product(conn, product)["id"])
        rows = conn.execute(sql + " ORDER BY t.id", args).fetchall()
    tasks = [{
        "task_id": r["id"], "product": r["product"], "unit": r["unit"],
        "bring_quantity": r["quantity"], "on_shelf_now": r["shelf_qty"],
        "in_back_room": r["backroom_qty"],
    } for r in rows]
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


def complete_refill(product: str, quantity: Optional[int] = None) -> Dict[str, Any]:
    """The employee says the shelf is refilled: move stock from back room to shelf."""
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        task = conn.execute(
            "SELECT * FROM refill_tasks WHERE product_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (prod["id"],),
        ).fetchone()
        moved = quantity if quantity and quantity > 0 else (task["quantity"] if task else 0)
        if moved <= 0:
            raise ShopError("NO_REFILL_TASK", f"There is no open refill task for {prod['name']}.")
        moved = min(moved, prod["backroom_qty"], prod["shelf_capacity"] - prod["shelf_qty"])
        if moved <= 0:
            raise ShopError("NOTHING_TO_MOVE", "The back room is empty or the shelf is already full.")
        conn.execute(
            "UPDATE products SET shelf_qty=shelf_qty+?, backroom_qty=backroom_qty-? WHERE id=?",
            (moved, moved, prod["id"]),
        )
        if task:
            conn.execute(
                "UPDATE refill_tasks SET status='done', done_at=? WHERE id=?", (time.time(), task["id"])
            )
    return {
        "ok": True, "product": prod["name"], "moved_to_shelf": moved,
        "on_shelf_now": prod["shelf_qty"] + moved, "in_back_room_now": prod["backroom_qty"] - moved,
    }


def report_shortage(product: str, found_quantity: int = 0) -> Dict[str, Any]:
    """The employee could not find the stock the system expected. Correct the
    back-room count and, if nothing is left, escalate to the supervisor."""
    found = max(0, int(found_quantity or 0))
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        conn.execute("UPDATE products SET backroom_qty=? WHERE id=?", (found, prod["id"]))
        conn.execute(
            "UPDATE refill_tasks SET status='done', done_at=? WHERE product_id=? AND status='open'",
            (time.time(), prod["id"]),
        )
    log_event("stock", f"Back room count corrected for {prod['name']}",
              {"product": prod["name"], "expected": prod["backroom_qty"], "found": found},
              agent="floor_agent", to="dispatcher")
    if found > 0:
        return {"ok": True, "product": prod["name"], "in_back_room_now": found,
                "escalated_to_supervisor": False,
                "next_step": f"Ask the employee to bring the {found} that are there."}
    call = place_call(
        "supervisor", prod["name"],
        f"{prod['name']} is low on the shelf and the back room is empty",
        prompts.supervisor_alert_greeting(prod["name"], prod["shelf_qty"]),
    )
    return {"ok": True, "product": prod["name"], "in_back_room_now": 0,
            "escalated_to_supervisor": True, "supervisor_call_id": call["call_id"]}


# --------------------------------------------------------------------------- #
# Triage: a shelf count came in, decide what to do
# --------------------------------------------------------------------------- #
def update_shelf(product: str, quantity: int, source: str = "manual") -> Dict[str, Any]:
    """Record a new shelf count and act on it if the product is now low."""
    quantity = max(0, int(quantity))
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        changed = prod["shelf_qty"] != quantity
        conn.execute("UPDATE products SET shelf_qty=? WHERE id=?", (quantity, prod["id"]))
    if changed:
        log_event("shelf", f"Shelf count: {quantity} {prod['name']}",
                  {"product": prod["name"], "count": quantity, "was": prod["shelf_qty"], "source": source},
                  agent="shelf_watcher", to="dispatcher")
    if source == "camera" and quantity > prod["shelf_qty"]:
        _camera_saw_restock(prod, quantity)
    action = triage(prod["name"]) if quantity <= prod["shelf_threshold"] else {"action": "none"}
    return {"ok": True, "product": prod["name"], "on_shelf": quantity, **action}


def _camera_saw_restock(prod: Any, quantity: int) -> None:
    """The camera saw more of a product than before. If someone was asked to
    refill it, or an order was on its way, that is now confirmed by sight
    rather than by anyone's word."""
    if quantity <= prod["shelf_threshold"]:
        return
    added = quantity - prod["shelf_qty"]
    with _lock, _connect() as conn:
        task = conn.execute(
            "SELECT id FROM refill_tasks WHERE product_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (prod["id"],),
        ).fetchone()
        order = None
        if task:
            conn.execute(
                "UPDATE refill_tasks SET status='done', done_at=? WHERE id=?", (time.time(), task["id"])
            )
            conn.execute(
                "UPDATE products SET backroom_qty=MAX(0, backroom_qty-?) WHERE id=?", (added, prod["id"])
            )
        else:
            order = conn.execute(
                "SELECT id FROM orders WHERE product_id=? AND status='placed' ORDER BY id LIMIT 1",
                (prod["id"],),
            ).fetchone()
            if order:
                conn.execute("UPDATE orders SET status='delivered' WHERE id=?", (order["id"],))
    if task:
        log_event("decision", f"Camera confirms the {prod['name']} shelf was refilled",
                  {"product": prod["name"], "on_shelf": quantity, "added": added},
                  agent="shelf_watcher", to="dispatcher")
    elif order:
        log_event("decision", f"Camera confirms order #{order['id']} arrived: {prod['name']} is back on the shelf",
                  {"product": prod["name"], "on_shelf": quantity, "order_number": order["id"]},
                  agent="shelf_watcher", to="order_gate")


def adjust_shelf(product: str, delta: int, source: str = "manual") -> Dict[str, Any]:
    """Step a shelf count up or down from whatever it is now. Used by the
    dashboard buttons so quick repeated presses cannot overwrite each other."""
    with _connect() as conn:
        prod = find_product(conn, product)
    target = max(0, min(prod["shelf_capacity"], prod["shelf_qty"] + int(delta)))
    return update_shelf(prod["name"], target, source)


def triage(product: str) -> Dict[str, Any]:
    """Decide who to call about a low product. Does nothing if someone is
    already dealing with it."""
    with _lock, _connect() as conn:
        prod = find_product(conn, product)
        if prod["shelf_qty"] > prod["shelf_threshold"]:
            return {"action": "none", "why": "shelf is above its threshold"}
        if _is_off(conn, prod["name"]):
            return {"action": "none", "why": "the agent is switched off for this product"}
        if _has_live_call(conn, prod):
            return {"action": "none", "why": "a call about this product is already in progress"}
        open_task = conn.execute(
            "SELECT 1 FROM refill_tasks WHERE product_id=? AND status='open'", (prod["id"],)
        ).fetchone()
        on_order = conn.execute(
            "SELECT 1 FROM orders WHERE product_id=? AND status='placed'", (prod["id"],)
        ).fetchone()
        if open_task:
            return {"action": "none", "why": "the employee already has a refill task"}
        waiting = conn.execute(
            "SELECT 1 FROM callbacks WHERE product_id=? AND status='pending'", (prod["id"],)
        ).fetchone()
        if waiting:
            # They said "later". Do not ring again until the callback is due.
            return {"action": "none", "why": "a callback about this product is already scheduled"}
        # Refill to 80% of the shelf, not to the brim.
        refill_qty = min(refill_target(prod) - prod["shelf_qty"], prod["backroom_qty"])
        if refill_qty > 0:
            conn.execute(
                "INSERT INTO refill_tasks (product_id, quantity, status, created_at) VALUES (?,?,'open',?)",
                (prod["id"], refill_qty, time.time()),
            )
        # While someone is being rung anyway, add the products that are nearly low.
        companions = []
        if refill_qty > 0 or not on_order:
            for row in _nearly_low_companions(conn, prod, needs_refill=refill_qty > 0):
                bring = min(refill_target(row) - row["shelf_qty"], row["backroom_qty"])
                if refill_qty > 0:
                    conn.execute(
                        "INSERT INTO refill_tasks (product_id, quantity, status, created_at)"
                        " VALUES (?,?,'open',?)", (row["id"], bring, time.time()),
                    )
                companions.append({"product": row["name"], "unit": row["unit"],
                                   "on_shelf": row["shelf_qty"], "bring": max(0, bring)})
    also = [c["product"] for c in companions]
    name, unit = prod["name"], prod["unit"]
    log_event("decision", f"{name} is low: {prod['shelf_qty']} left on the shelf",
              {"product": name, "on_shelf": prod["shelf_qty"], "threshold": prod["shelf_threshold"]},
              agent="dispatcher", to="shop_tools")
    if refill_qty > 0:
        log_event("decision", f"Back room has {prod['backroom_qty']}. Sending the employee to refill.",
                  {"product": name, "in_back_room": prod["backroom_qty"], "bring": refill_qty},
                  agent="dispatcher", to="floor_agent")
        _log_companions(companions, "floor_agent")
        call = place_call(
            "employee", name, f"refill {refill_qty} {name} from the back room"
            + (f", and {', '.join(also)} while there" if also else ""),
            prompts.employee_alert_greeting(name, unit, prod["shelf_qty"], refill_qty, companions),
            also,
        )
        return {"action": "call_employee", "bring_quantity": refill_qty, "also_refill": companions, **call}
    if on_order:
        log_event("decision", f"Back room is empty but {name} is already on order. No call needed.",
                  {"product": name}, agent="dispatcher", to="order_gate")
        return {"action": "none", "why": "already on order"}
    log_event("decision", "Back room is empty. Calling the supervisor about a reorder.",
              {"product": name, "in_back_room": 0}, agent="dispatcher", to="procurement_agent")
    _log_companions(companions, "procurement_agent")
    call = place_call(
        "supervisor", name, f"{name} is low on the shelf and the back room is empty"
        + (f"; {', '.join(also)} nearly low too" if also else ""),
        prompts.supervisor_alert_greeting(name, prod["shelf_qty"], companions),
        also,
    )
    return {"action": "call_supervisor", "also_reorder": companions, **call}


def _log_companions(companions: List[Dict[str, Any]], to: str) -> None:
    if not companions:
        return
    listed = ", ".join(f"{c['product']} ({c['on_shelf']} left)" for c in companions)
    log_event("decision", f"Nearly low as well: {listed}. Adding to the same call.",
              {"products": [c["product"] for c in companions]}, agent="dispatcher", to=to)


def product_details(product: str) -> Dict[str, Any]:
    """Everything known about one product, for the dashboard's details view:
    stock, sales, the demand forecast, supplier quotes and what is in motion."""
    sales = shop_db.sales_rate(product)
    name = sales["product"]
    forecast = shop_db.demand_forecast(name)
    quotes = shop_db.supplier_quotes(name)
    orders = shop_db.order_status(name)["orders"]
    with _lock, _connect() as conn:
        prod = find_product(conn, name)
        task = conn.execute(
            "SELECT quantity FROM refill_tasks WHERE product_id=? AND status='open'", (prod["id"],)
        ).fetchone()
        callback = conn.execute(
            "SELECT role, due_at FROM callbacks WHERE product_id=? AND status='pending' ORDER BY due_at LIMIT 1",
            (prod["id"],),
        ).fetchone()
        enabled = not _is_off(conn, name)
    return {
        "product": name,
        "unit": prod["unit"],
        "enabled": enabled,
        "on_shelf": prod["shelf_qty"],
        "shelf_capacity": prod["shelf_capacity"],
        "low_threshold": prod["shelf_threshold"],
        "nearly_low_mark": near_low_mark(prod),
        "refill_up_to": refill_target(prod),
        "is_low": prod["shelf_qty"] <= prod["shelf_threshold"],
        "in_back_room": prod["backroom_qty"],
        "total_in_store": sales["total_in_store"],
        "pack_size": prod["pack_size"],
        "sold_per_day": sales["sold_per_day"],
        "hours_until_sold_out": sales["hours_until_sold_out"],
        "forecast": {k: forecast.get(k) for k in (
            "upcoming_occasion", "days_until_occasion", "expected_uplift_percent",
            "forecast_sold_per_day", "recommended_order_quantity", "normal_order_quantity", "basis",
        )},
        "budget_remaining": quotes["budget_remaining"],
        "quotes": quotes["quotes"],
        "cheapest_that_can_fill": quotes["cheapest_that_can_fill"],
        "fastest_that_can_fill": quotes["fastest_that_can_fill"],
        "orders": orders,
        "refill_task": {"bring_quantity": task["quantity"]} if task else None,
        "callback": {
            "who": callback["role"],
            "in_minutes": max(0, round((callback["due_at"] - time.time()) / 60)),
        } if callback else None,
    }


# --------------------------------------------------------------------------- #
# Dashboard snapshot
# --------------------------------------------------------------------------- #
def snapshot() -> Dict[str, Any]:
    """Everything the dashboard shows: shelves, budget, orders, calls, tasks."""
    data = shop_db.snapshot()
    with _lock, _connect() as conn:
        _expire_stale(conn)
        calls = conn.execute(_CALL_SELECT + " ORDER BY c.id DESC LIMIT 6").fetchall()
        proposals = conn.execute(
            "SELECT o.id, o.quantity, o.total, p.name AS product, s.name AS supplier FROM orders o"
            " JOIN products p ON p.id=o.product_id JOIN suppliers s ON s.id=o.supplier_id"
            " WHERE o.status='proposed' ORDER BY o.id DESC"
        ).fetchall()
        on_order = {
            r["name"] for r in conn.execute(
                "SELECT DISTINCT p.name FROM orders o JOIN products p ON p.id=o.product_id"
                " WHERE o.status='placed'"
            )
        }
        off = {r["product"] for r in conn.execute("SELECT product FROM watch_off")}
    tasks = open_refill_tasks()["tasks"]
    refilling = {t["product"] for t in tasks}
    for product in data["products"]:
        product["refill_pending"] = product["product"] in refilling
        product["on_order"] = product["product"] in on_order
        product["enabled"] = product["product"] not in off
    # Lets a dashboard notice that the demo was reset from somewhere else.
    data["last_event_id"] = shop_db.last_event_id()
    data["calls"] = [_call_view(c) for c in calls]
    data["refill_tasks"] = tasks
    data["proposals"] = [dict(p) for p in proposals]
    return data
