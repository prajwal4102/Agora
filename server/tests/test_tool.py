"""Business rules behind the shop tools: matching, quoting, and the
propose-then-confirm gate on ordering."""
import asyncio
import json

import pytest


def test_spoken_product_names_match(shop):
    assert shop.shelf_status("water bottles")["product"] == "water bottle"
    assert shop.shelf_status("Coke")["product"] == "cola can"
    assert shop.shelf_status("chocolates")["product"] == "chocolate bar"
    with pytest.raises(shop.ShopError) as err:
        shop.shelf_status("washing machine")
    assert err.value.code == "UNKNOWN_PRODUCT"


def test_cheapest_supplier_is_out_of_cola(shop):
    quotes = shop.supplier_quotes("cola can", 120)
    by_name = {q["supplier"]: q for q in quotes["quotes"]}
    assert by_name["Bharat Wholesale"]["can_fill_order"] is False
    assert quotes["cheapest_that_can_fill"] == "Sri Lakshmi Traders"
    assert quotes["fastest_that_can_fill"] == "QuickDrop Supply"


def test_out_of_stock_supplier_cannot_be_proposed(shop):
    with pytest.raises(shop.ShopError) as err:
        shop.propose_order("cola can", "Bharat Wholesale", 120)
    assert err.value.code == "SUPPLIER_OUT_OF_STOCK"


def test_over_budget_reports_what_fits(shop):
    with pytest.raises(shop.ShopError) as err:
        shop.propose_order("cola can", "Sri Lakshmi Traders", 240)
    assert err.value.code == "OVER_BUDGET"
    assert err.value.details["max_affordable_quantity"] == 206


def test_order_needs_proposal_and_approval(shop):
    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(999, "yes go ahead")
    assert err.value.code == "NO_SUCH_PROPOSAL"

    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 120)
    assert proposal["total"] == 3480
    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(proposal["proposal_id"], "")
    assert err.value.code == "APPROVAL_REQUIRED"
    assert shop.order_status()["count"] == 0

    placed = shop.confirm_order(proposal["proposal_id"], "yes, place it")
    assert placed["status"] == "placed"
    assert placed["budget_remaining"] == 2520
    assert shop.order_status("cola")["orders"][0]["quantity"] == 120

    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(proposal["proposal_id"], "yes")
    assert err.value.code == "ALREADY_PLACED"


def test_changed_quantity_replaces_the_old_proposal(shop):
    first = shop.propose_order("cola can", "Sri Lakshmi Traders", 120)
    second = shop.propose_order("cola can", "Sri Lakshmi Traders", 72)
    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(first["proposal_id"], "yes")
    assert err.value.code == "PROPOSAL_REPLACED"
    assert shop.confirm_order(second["proposal_id"], "ok half is fine")["total"] == 2088


def test_tools_return_errors_as_data_and_log_events(shop):
    import mcp_server

    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"get_shelf_status", "get_supplier_quotes", "propose_order", "confirm_order"} <= names

    result = json.loads(mcp_server.propose_order(product="cola can", supplier="Bharat Wholesale", quantity=120))
    assert result == {
        "ok": False,
        "error": "SUPPLIER_OUT_OF_STOCK",
        "message": "Bharat Wholesale is out of cola can. Check the other suppliers.",
        "supplier": "Bharat Wholesale",
    }
    assert shop.list_events()[-1]["detail"]["tool"] == "propose_order"


def test_tool_call_through_mcp_returns_json(shop):
    import mcp_server

    content, _ = asyncio.run(mcp_server.mcp.call_tool("get_sales_rate", {"product": "coke"}))
    result = json.loads(content[0].text)
    assert result["product"] == "cola can"
    assert result["suggested_order_quantity"] == 120


def test_one_call_gives_everything_for_a_recommendation(shop):
    brief = shop.supplier_quotes("chocolate bar")
    assert brief["quantity"] == brief["suggested_order_quantity"] == 120
    assert (brief["on_shelf"], brief["in_back_room"], brief["sold_per_day"]) == (5, 0, 36)
    assert brief["cheapest_that_can_fill"] == "Sri Lakshmi Traders"   # the cheapest only has 48
    assert brief["budget_remaining"] == 6000
    # A different amount can still be quoted when the supervisor asks.
    assert shop.supplier_quotes("chocolate bar", 48)["cheapest_that_can_fill"] == "Bharat Wholesale"


def test_agents_are_told_to_act_not_announce():
    import prompts

    for role in ("supervisor", "employee"):
        assert "Never end your turn on a promise" in prompts.prompt_for(role)
        assert len(prompts.fillers_for(role)) >= 3
