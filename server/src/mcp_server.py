"""MCP server (streamable-HTTP) exposing the shop tools. Agora cloud calls this
when the LLM emits a tool call.

This module is mounted in-process by server.py at /mcp — it is not run
standalone. Each @mcp.tool() function is a thin wrapper over shop_db: it runs
the business rule, logs an event for the dashboard, and returns compact JSON.
Business-rule failures come back as {"ok": false, "error": ...} rather than
exceptions, so the model can explain the problem and try another route."""
import functools
import inspect
import json
import os
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import dispatch
import shop_db

_endpoint = urlparse(os.getenv("MCP_ENDPOINT", ""))
_public_host = _endpoint.netloc
_public_origin = f"{_endpoint.scheme}://{_public_host}" if _endpoint.scheme and _public_host else ""

mcp = FastMCP(
    "shelf-agent-shop",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
            *([_public_host] if _public_host else []),
        ],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            *([_public_origin] if _public_origin else []),
        ],
    ),
)

shop_db.init_db()
dispatch.init()


def shop_tool(title: str) -> Callable:
    """Register a tool that logs its call and result and never raises a
    business error at the model."""

    def decorator(fn: Callable[..., Dict[str, Any]]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                result = fn(*args, **kwargs)
            except shop_db.ShopError as exc:
                result = exc.to_dict()
            except Exception as exc:  # keep the call alive; the model apologises
                result = {"ok": False, "error": "TOOL_FAILED", "message": str(exc)}
            # The voice agent on the line made this call. Purchases go through
            # the order gate; everything else is a lookup in the shop tools.
            gated = fn.__name__ in ("propose_order", "confirm_order")
            shop_db.log_event(
                "tool", title, {"tool": fn.__name__, "args": kwargs, "result": result},
                agent=dispatch.VOICE_AGENT.get(dispatch.current_role, "procurement_agent"),
                to="order_gate" if gated else "shop_tools",
            )
            print(f"[MCP TOOL] {fn.__name__} {kwargs} -> ok={result.get('ok')}", flush=True)
            return json.dumps(result, ensure_ascii=False)

        # The wrapper returns a JSON string, not the dict the wrapped function
        # is annotated with; say so, or FastMCP rejects the result.
        wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=str)
        wrapper.__annotations__ = {**fn.__annotations__, "return": str}
        return mcp.tool()(wrapper)

    return decorator


@shop_tool("Checked shelf and back-room stock")
def get_shelf_status(product: Optional[str] = None) -> Dict[str, Any]:
    """Current stock on the shelf and in the back room. Pass a product name for
    one product, or leave it empty for every product."""
    return shop_db.shelf_status(product)


@shop_tool("Checked how fast it sells")
def get_sales_rate(product: str) -> Dict[str, Any]:
    """How many units of a product sell per day, how long current stock will
    last, and a suggested order quantity. Call this before recommending an
    order quantity."""
    return shop_db.sales_rate(product)


@shop_tool("Compared supplier quotes")
def get_supplier_quotes(product: str, quantity: int) -> Dict[str, Any]:
    """Price, delivery time and availability from every supplier for a given
    quantity of a product. Call again whenever the quantity changes."""
    return shop_db.supplier_quotes(product, quantity)


@shop_tool("Checked purchase budget")
def get_budget() -> Dict[str, Any]:
    """How much of this month's purchase budget is left, in rupees."""
    return shop_db.budget_status()


@shop_tool("Prepared an order for approval")
def propose_order(product: str, supplier: str, quantity: int) -> Dict[str, Any]:
    """Price an order and hold it for the supervisor's approval. This does NOT
    place the order. It fails if the supplier is out of stock or the total is
    over budget, and tells you what would work instead. Call it again whenever
    the supervisor changes the quantity or supplier."""
    return shop_db.propose_order(product, supplier, quantity)


@shop_tool("Placed the approved order")
def confirm_order(proposal_id: int, approval_phrase: str) -> Dict[str, Any]:
    """Place a proposed order. Only call this after you have read the
    proposal's quantity, supplier and total aloud and the supervisor clearly
    said yes. approval_phrase is the supervisor's own words of approval."""
    return shop_db.confirm_order(proposal_id, approval_phrase)


@shop_tool("Looked up orders")
def get_order_status(product: Optional[str] = None) -> Dict[str, Any]:
    """Placed orders and when they will arrive. Optionally filter by product."""
    return shop_db.order_status(product)


@shop_tool("Scheduled a callback")
def schedule_callback(in_hours: float, note: str, product: Optional[str] = None) -> Dict[str, Any]:
    """Schedule a reminder call to the supervisor, for when they want to decide
    later. in_hours is how many hours from now."""
    return shop_db.schedule_callback(product, in_hours, note)


# --- Employee side: refilling the shelf from the back room ---
@shop_tool("Looked up refill tasks")
def get_refill_tasks(product: Optional[str] = None) -> Dict[str, Any]:
    """Open shelf-refill tasks for the floor employee: which product to bring
    from the back room and how many."""
    return dispatch.open_refill_tasks(product)


@shop_tool("Recorded the shelf refill")
def complete_refill_task(product: str, quantity: Optional[int] = None) -> Dict[str, Any]:
    """Record that the employee refilled the shelf. quantity is how many they
    brought; leave it empty if they brought what was asked."""
    return dispatch.complete_refill(product, quantity)


@shop_tool("Back room stock was missing")
def report_backroom_shortage(product: str, found_quantity: int = 0) -> Dict[str, Any]:
    """The employee could not find the expected stock in the back room.
    found_quantity is how many they actually found (0 if none). If none are
    left this alerts the supervisor about a reorder."""
    return dispatch.report_shortage(product, found_quantity)
