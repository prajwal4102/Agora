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


def test_each_agent_can_only_run_the_tools_for_its_job(shop):
    import dispatch
    import mcp_server

    every_tool = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert mcp_server.TOOL_SCOPE["employee"] | mcp_server.TOOL_SCOPE["supervisor"] == every_tool

    dispatch.current_role = "employee"
    refused = json.loads(mcp_server.propose_order(product="cola can", supplier="Sri Lakshmi Traders", quantity=72))
    assert refused["ok"] is False and refused["error"] == "OUT_OF_SCOPE"
    assert json.loads(mcp_server.get_supplier_quotes(product="cola can"))["error"] == "OUT_OF_SCOPE"
    assert shop.order_status()["count"] == 0  # nothing was priced or placed
    assert json.loads(mcp_server.get_refill_tasks())["ok"] is True

    dispatch.current_role = "supervisor"
    assert json.loads(mcp_server.propose_order(product="cola can", supplier="Sri Lakshmi Traders", quantity=72))["ok"] is True
    assert json.loads(mcp_server.complete_refill_task(product="water bottle"))["error"] == "OUT_OF_SCOPE"


def test_hindi_filler_phrases_fit_agoras_limit():
    """Agora refuses to start the call when a non-Latin filler phrase is over 20 characters."""
    import prompts

    for phrase in prompts.fillers_for("employee"):
        assert len(phrase) <= 20, phrase


def test_forecast_orders_more_before_an_occasion(shop):
    import seasons
    try:
        seasons.set_simulated("none")
        plain = shop.demand_forecast("chocolate bar")
        assert plain["upcoming_occasion"] is None
        assert plain["recommended_order_quantity"] == plain["normal_order_quantity"] == 120

        seasons.set_simulated("Valentine's Day")
        lifted = shop.demand_forecast("chocolate bar")
        assert lifted["upcoming_occasion"] == "Valentine's Day"
        assert lifted["expected_uplift_percent"] == 50
        assert lifted["recommended_order_quantity"] == 168  # 54 a day for 3 days, in packs of 24
        assert lifted["normal_order_quantity"] == 120
        # The quotes are priced for the forecast quantity, so the totals match.
        quotes = shop.supplier_quotes("chocolate bar")
        assert quotes["quantity"] == 168 and quotes["normal_order_quantity"] == 120
        # An occasion that does not move this product leaves it alone.
        assert shop.demand_forecast("cola can")["upcoming_occasion"] is None
        assert shop.demand_forecast("pen")["recommended_order_quantity"] == 90
    finally:
        seasons.set_simulated(None)


def test_gate_needs_a_clear_spoken_yes(shop):
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 120)
    for words in ("no, not now", "wait, let me think", "hmm the delivery time", "yes but wait", "नहीं"):
        with pytest.raises(shop.ShopError) as err:
            shop.confirm_order(proposal["proposal_id"], words)
        assert err.value.code == "APPROVAL_NOT_CLEAR", words
    assert shop.order_status()["count"] == 0
    for words in ("Yeah. Please confirm.", "ok go ahead", "हाँ, कर दो"):
        assert shop._approval_problem(words) is None, words


def test_gate_refuses_to_confirm_before_the_proposal_can_be_heard(shop, monkeypatch):
    monkeypatch.setattr(shop, "MIN_READOUT_SECONDS", 4.0)
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 120)
    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(proposal["proposal_id"], "yes")
    assert err.value.code == "NOT_READ_OUT"
    with shop._connect() as conn:
        conn.execute("UPDATE orders SET created_at=created_at-10 WHERE id=?", (proposal["proposal_id"],))
    assert shop.confirm_order(proposal["proposal_id"], "yes")["status"] == "placed"


def test_gate_refuses_a_second_order_and_a_changed_price(shop):
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 48)
    with shop._connect() as conn:
        conn.execute("UPDATE supplier_prices SET unit_price=unit_price+2 WHERE product_id=?"
                     " AND supplier_id=(SELECT supplier_id FROM orders WHERE id=?)",
                     (conn.execute("SELECT product_id FROM orders WHERE id=?",
                                   (proposal["proposal_id"],)).fetchone()[0], proposal["proposal_id"]))
    with pytest.raises(shop.ShopError) as err:
        shop.confirm_order(proposal["proposal_id"], "yes")
    assert err.value.code == "PRICE_CHANGED"

    fresh = shop.propose_order("cola can", "Sri Lakshmi Traders", 48)
    shop.confirm_order(fresh["proposal_id"], "yes, place it")
    with pytest.raises(shop.ShopError) as err:
        shop.propose_order("cola can", "QuickDrop Supply", 24)
    assert err.value.code == "ALREADY_ON_ORDER"
