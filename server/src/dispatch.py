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
from shop_db import ShopError, _connect, _lock, find_product, log_event

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
    status TEXT NOT NULL,            -- ringing | answered | ended | declined | missed
    created_at REAL NOT NULL,
    answered_at REAL,
    ended_at REAL
);
"""


def init() -> None:
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


def reset() -> None:
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
        "reason": row["reason"],
        "greeting": row["greeting"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


_CALL_SELECT = (
    "SELECT c.*, p.name AS product FROM calls c LEFT JOIN products p ON p.id = c.product_id"
)


def place_call(role: str, product: Optional[str], reason: str, greeting: str) -> Dict[str, Any]:
    """Queue an outgoing call. The matching phone starts ringing on its next poll."""
    if role not in ROLES:
        raise ShopError("BAD_ROLE", f"Unknown role '{role}'.")
    with _lock, _connect() as conn:
        product_id = find_product(conn, product)["id"] if product else None
        cur = conn.execute(
            "INSERT INTO calls (role, product_id, reason, greeting, status, created_at)"
            " VALUES (?,?,?,?, 'ringing', ?)",
            (role, product_id, reason, greeting, time.time()),
        )
        call_id = cur.lastrowid
    log_event("call", f"Calling the {role}", {"call_id": call_id, "product": product, "reason": reason},
              agent="dispatcher", to=VOICE_AGENT[role])
    call = {"call_id": call_id, "role": role, "product": product, "reason": reason}
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
    return call


def _has_live_call(conn: Any, product_id: int) -> bool:
    _expire_stale(conn)
    return conn.execute(
        "SELECT 1 FROM calls WHERE product_id=? AND status IN ('ringing','answered')", (product_id,)
    ).fetchone() is not None


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
        if _has_live_call(conn, prod["id"]):
            return {"action": "none", "why": "a call about this product is already in progress"}
        open_task = conn.execute(
            "SELECT 1 FROM refill_tasks WHERE product_id=? AND status='open'", (prod["id"],)
        ).fetchone()
        on_order = conn.execute(
            "SELECT 1 FROM orders WHERE product_id=? AND status='placed'", (prod["id"],)
        ).fetchone()
        if open_task:
            return {"action": "none", "why": "the employee already has a refill task"}
        refill_qty = min(prod["shelf_capacity"] - prod["shelf_qty"], prod["backroom_qty"])
        if refill_qty > 0:
            conn.execute(
                "INSERT INTO refill_tasks (product_id, quantity, status, created_at) VALUES (?,?,'open',?)",
                (prod["id"], refill_qty, time.time()),
            )
    name, unit = prod["name"], prod["unit"]
    log_event("decision", f"{name} is low: {prod['shelf_qty']} left on the shelf",
              {"product": name, "on_shelf": prod["shelf_qty"], "threshold": prod["shelf_threshold"]},
              agent="dispatcher", to="shop_tools")
    if refill_qty > 0:
        log_event("decision", f"Back room has {prod['backroom_qty']}. Sending the employee to refill.",
                  {"product": name, "in_back_room": prod["backroom_qty"], "bring": refill_qty},
                  agent="dispatcher", to="floor_agent")
        call = place_call(
            "employee", name, f"refill {refill_qty} {name} from the back room",
            prompts.employee_alert_greeting(name, unit, prod["shelf_qty"], refill_qty),
        )
        return {"action": "call_employee", "bring_quantity": refill_qty, **call}
    if on_order:
        log_event("decision", f"Back room is empty but {name} is already on order. No call needed.",
                  {"product": name}, agent="dispatcher", to="order_gate")
        return {"action": "none", "why": "already on order"}
    log_event("decision", "Back room is empty. Calling the supervisor about a reorder.",
              {"product": name, "in_back_room": 0}, agent="dispatcher", to="procurement_agent")
    call = place_call(
        "supervisor", name, f"{name} is low on the shelf and the back room is empty",
        prompts.supervisor_alert_greeting(name, prod["shelf_qty"]),
    )
    return {"action": "call_supervisor", **call}


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
    tasks = open_refill_tasks()["tasks"]
    refilling = {t["product"] for t in tasks}
    for product in data["products"]:
        product["refill_pending"] = product["product"] in refilling
        product["on_order"] = product["product"] in on_order
    data["calls"] = [_call_view(c) for c in calls]
    data["refill_tasks"] = tasks
    data["proposals"] = [dict(p) for p in proposals]
    return data
