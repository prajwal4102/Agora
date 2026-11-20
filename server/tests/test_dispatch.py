"""Triage: who gets called when a shelf runs low, and the employee side."""
import pytest


@pytest.fixture
def dispatch(shop):
    import dispatch as module

    return module


def test_low_shelf_with_back_room_stock_calls_employee(shop, dispatch):
    result = dispatch.update_shelf("water bottle", 3)
    assert result["action"] == "call_employee"
    assert result["bring_quantity"] == 9  # shelf holds 12, 3 left
    call = dispatch.pending_call("employee")
    assert call["product"] == "water bottle"
    assert "9 bottle" in call["greeting"]
    assert dispatch.pending_call("supervisor") is None


def test_low_shelf_with_empty_back_room_calls_supervisor(shop, dispatch):
    result = dispatch.update_shelf("cola can", 2)
    assert result["action"] == "call_supervisor"
    assert "back room is empty" in dispatch.pending_call("supervisor")["greeting"]


def test_healthy_shelf_and_repeat_counts_do_not_call(shop, dispatch):
    assert dispatch.update_shelf("water bottle", 8)["action"] == "none"
    dispatch.update_shelf("cola can", 2)
    assert dispatch.update_shelf("cola can", 1)["action"] == "none"  # already ringing


def test_refill_moves_stock_and_closes_task(shop, dispatch):
    dispatch.update_shelf("water bottle", 3)
    done = dispatch.complete_refill("water bottle")
    assert done["on_shelf_now"] == 12 and done["in_back_room_now"] == 15
    assert dispatch.open_refill_tasks()["count"] == 0


def test_missing_back_room_stock_escalates_to_supervisor(shop, dispatch):
    dispatch.update_shelf("water bottle", 3)
    result = dispatch.report_shortage("water bottle", 0)
    assert result["escalated_to_supervisor"] is True
    assert dispatch.pending_call("supervisor")["product"] == "water bottle"
    assert shop.shelf_status("water bottle")["in_back_room"] == 0


def test_no_second_call_once_the_product_is_on_order(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2)
    dispatch.set_call_status(call["call_id"], "ended")
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 72)
    shop.confirm_order(proposal["proposal_id"], "yes")
    assert dispatch.update_shelf("cola can", 1)["action"] == "none"


def test_stepping_the_shelf_applies_to_the_current_count(shop, dispatch):
    for _ in range(5):
        result = dispatch.adjust_shelf("cola can", -1)
    assert result["on_shelf"] == 4  # 9 on the shelf, five removed
    assert result["action"] == "call_supervisor"
    assert dispatch.adjust_shelf("cola can", -99)["on_shelf"] == 0


def test_events_say_which_part_of_the_system_acted(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2, source="camera")
    trail = [(e["detail"].get("agent"), e["detail"].get("to")) for e in shop.list_events()]
    assert trail == [
        ("shelf_watcher", "dispatcher"),        # the count came in
        ("dispatcher", "shop_tools"),           # it is low
        ("dispatcher", "procurement_agent"),    # back room empty: reorder
        ("dispatcher", "procurement_agent"),    # calling the supervisor
    ]
    dispatch.set_call_status(call["call_id"], "answered")
    assert shop.list_events()[-1]["detail"]["agent"] == "supervisor"
    assert dispatch.current_role == "supervisor"

    import json

    import mcp_server

    json.loads(mcp_server.propose_order(product="cola can", supplier="Sri Lakshmi Traders", quantity=72))
    last = shop.list_events()[-1]["detail"]
    assert (last["agent"], last["to"]) == ("procurement_agent", "order_gate")


def test_a_promised_callback_really_rings(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2)
    dispatch.set_call_status(call["call_id"], "answered")
    shop.schedule_callback("cola can", 1, "decide on the cola order", role="supervisor")
    dispatch.set_call_status(call["call_id"], "ended")
    assert "will call back in 1 minute" in shop.list_events()[-1]["title"]

    # Not due yet, and the low shelf does not make it ring early either.
    assert dispatch.fire_due_callbacks() == []
    assert dispatch.update_shelf("cola can", 1)["action"] == "none"

    with shop._connect() as conn:
        conn.execute("UPDATE callbacks SET due_at = due_at - 120")
    placed = dispatch.fire_due_callbacks()
    assert len(placed) == 1 and placed[0]["role"] == "supervisor"
    ringing = dispatch.pending_call("supervisor")
    assert "calling back as you asked about cola can" in ringing["greeting"]
    assert dispatch.fire_due_callbacks() == []  # rings once, not on every check


def test_a_callback_waits_while_that_person_is_on_a_call(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2)
    dispatch.set_call_status(call["call_id"], "answered")
    shop.schedule_callback("cola can", 1, "later", role="supervisor")
    with shop._connect() as conn:
        conn.execute("UPDATE callbacks SET due_at = due_at - 120")
    assert dispatch.fire_due_callbacks() == []          # still talking
    dispatch.set_call_status(call["call_id"], "ended")
    assert len(dispatch.fire_due_callbacks()) == 1


def test_a_call_ends_with_a_summary_of_what_was_done(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2)
    dispatch.set_call_status(call["call_id"], "answered")
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 72)
    order = shop.confirm_order(proposal["proposal_id"], "yes")
    dispatch.set_call_status(call["call_id"], "ended")
    summary = shop.list_events()[-1]
    assert summary["title"].startswith(f"Summary: order #{order['order_number']} placed, 72 cola can")
    assert "camera will confirm" in summary["title"]
    assert (summary["detail"]["agent"], summary["detail"]["to"]) == ("procurement_agent", "supervisor")


def test_a_call_where_nothing_was_done_says_so(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2)
    dispatch.set_call_status(call["call_id"], "answered")
    dispatch.set_call_status(call["call_id"], "ended")
    assert shop.list_events()[-1]["title"] == "Summary: no action taken"
