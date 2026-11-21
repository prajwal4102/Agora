"""Triage: who gets called when a shelf runs low, and the employee side."""
import pytest


@pytest.fixture
def dispatch(shop):
    import dispatch as module

    return module


def test_low_shelf_with_back_room_stock_calls_employee(shop, dispatch):
    result = dispatch.update_shelf("water bottle", 3)
    assert result["action"] == "call_employee"
    assert result["bring_quantity"] == 6  # refill to 80% of 12 is 9, and 3 are left
    call = dispatch.pending_call("employee")
    assert call["product"] == "water bottle"
    assert "6 bottle" in call["greeting"]
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
    assert done["on_shelf_now"] == 9 and done["in_back_room_now"] == 18
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


def test_a_promise_is_told_apart_from_an_answer():
    from call_watch import looks_like_unkept_promise

    # What the agent really said on a call before going silent for 78 seconds.
    assert looks_like_unkept_promise("Checking now.Sure, I'll check the current stock status again. One moment. ")
    assert looks_like_unkept_promise("Let me verify that for you.")
    assert looks_like_unkept_promise("एक second, मैं check कर रही हूँ।")
    # Answers and questions hand the turn back properly.
    assert not looks_like_unkept_promise("We have 2 chocolate bars on the shelf and none in the back room.")
    assert not looks_like_unkept_promise("Shall I check suppliers for a reorder?")
    assert not looks_like_unkept_promise("Order number 3 is placed and arrives in twelve hours.")


def test_a_stalled_agent_is_pushed_once_and_only_when_it_did_nothing(shop):
    import time

    from call_watch import CallWatcher

    class Line:
        def __init__(self, role, content, start, end):
            self.role, self.content, self.speech_start_ms, self.speech_end_ms = role, content, start, end

    now = time.time()
    ms = lambda seconds_ago: int((now - seconds_ago) * 1000)  # noqa: E731
    call = [
        Line("user", "Wait, check once again, it might be there.", ms(12), ms(10)),
        Line("assistant", "Sure, I'll check the current stock status again. One moment.", ms(9), ms(6)),
    ]
    watcher = CallWatcher("supervisor", "procurement_agent")
    assert watcher.stalled(call, now) is True       # promised, six seconds of nothing
    assert watcher.stalled(call, now) is False      # pushed once, not again

    # Same promise, but a tool call followed: the answer is coming, leave it alone.
    shop.log_event("tool", "Checked shelf and back-room stock", {"tool": "get_shelf_status"})
    assert CallWatcher("supervisor", "procurement_agent").stalled(call, now) is False

    # The person spoke last: the agent is about to reply.
    waiting = call + [Line("user", "Hello?", ms(2), ms(1))]
    assert CallWatcher("supervisor", "procurement_agent").stalled(waiting, now) is False


def test_the_call_is_captioned_line_by_line(shop):
    from call_watch import NUDGE, CallWatcher

    class Line:
        def __init__(self, role, content):
            self.role, self.content = role, content

    watcher = CallWatcher("supervisor", "procurement_agent")
    first = watcher.captions([Line("assistant", "Hello, this is Asha."), Line("user", "Check the suppliers.")])
    assert [(c["agent"], c["to"]) for c in first] == [("procurement_agent", "supervisor"), ("supervisor", "procurement_agent")]
    later = watcher.captions([Line("assistant", "Hello, this is Asha."), Line("user", "Check the suppliers."),
                              Line("user", NUDGE), Line("assistant", "Sri Lakshmi is cheapest.")])
    assert [c["text"] for c in later] == ["Sri Lakshmi is cheapest."]   # no repeats, and the push is not shown


def test_a_switched_off_product_is_counted_but_never_acted_on(shop, dispatch):
    dispatch.set_enabled("water bottle", False)
    result = dispatch.update_shelf("water bottle", 1)
    assert result["action"] == "none"
    assert result["on_shelf"] == 1  # still counted
    assert dispatch.pending_call("employee") is None
    assert dispatch.open_refill_tasks()["count"] == 0
    row = next(p for p in dispatch.snapshot()["products"] if p["product"] == "water bottle")
    assert row["enabled"] is False
    # Other products are unaffected.
    assert dispatch.update_shelf("cola can", 2)["action"] == "call_supervisor"


def test_switching_a_low_product_back_on_acts_at_once(shop, dispatch):
    dispatch.set_enabled("water bottle", False)
    dispatch.update_shelf("water bottle", 1)
    result = dispatch.set_enabled("water bottle", True)
    assert result["action"] == "call_employee"
    assert dispatch.pending_call("employee")["product"] == "water bottle"


def _make_callbacks_due(shop):
    with shop._connect() as conn:
        conn.execute("UPDATE callbacks SET due_at=0")


def test_a_promise_to_refill_is_checked_and_stays_quiet_if_done(shop, dispatch):
    call = dispatch.update_shelf("water bottle", 3)
    dispatch.set_call_status(call["call_id"], "answered")
    shop.schedule_callback("water bottle", 10, "check the shelf", role="employee")
    dispatch.set_call_status(call["call_id"], "ended")
    # Nothing moved yet: the promise did not refill the shelf.
    assert shop.shelf_status("water bottle")["on_shelf"] == 3
    assert dispatch.update_shelf("water bottle", 3)["action"] == "none"  # no second call meanwhile

    dispatch.update_shelf("water bottle", 12)  # the employee refills it
    _make_callbacks_due(shop)
    assert dispatch.fire_due_callbacks() == []
    assert dispatch.pending_call("employee") is None
    assert "No call needed" in shop.list_events()[-1]["title"]
    assert dispatch.open_refill_tasks()["count"] == 0


def test_a_promise_to_refill_that_is_not_kept_rings_again(shop, dispatch):
    call = dispatch.update_shelf("water bottle", 3)
    dispatch.set_call_status(call["call_id"], "answered")
    shop.schedule_callback("water bottle", 10, "check the shelf", role="employee")
    dispatch.set_call_status(call["call_id"], "ended")
    _make_callbacks_due(shop)
    placed = dispatch.fire_due_callbacks()
    assert len(placed) == 1 and placed[0]["role"] == "employee"
    assert "refill नहीं हुआ" in dispatch.pending_call("employee")["greeting"]


def test_refill_leaves_room_on_the_shelf(shop, dispatch):
    result = dispatch.update_shelf("pen", 1)
    assert result["bring_quantity"] == 3  # shelf holds 5, refill stops at 4
    assert shop.shelf_status("pen")["refill_up_to"] == 4


def test_nearly_low_products_join_the_same_refill_call(shop, dispatch):
    dispatch.update_shelf("biscuit packet", 6)  # low mark is 4: nearly low, no call by itself
    assert dispatch.pending_call("employee") is None
    result = dispatch.update_shelf("water bottle", 3)
    assert result["action"] == "call_employee"
    assert [c["product"] for c in result["also_refill"]] == ["biscuit packet"]
    call = dispatch.pending_call("employee")
    assert call["also"] == ["biscuit packet"] and "biscuit packet" in call["greeting"]
    assert dispatch.open_refill_tasks()["count"] == 2
    # the nearly-low product is already covered, so falling further rings nobody
    assert dispatch.update_shelf("biscuit packet", 3)["action"] == "none"


def test_nearly_low_products_join_the_same_reorder_call(shop, dispatch):
    dispatch.update_shelf("cola can", 6)  # nearly low, back room empty
    assert dispatch.pending_call("supervisor") is None
    result = dispatch.update_shelf("chocolate bar", 2)
    assert result["action"] == "call_supervisor"
    assert [c["product"] for c in result["also_reorder"]] == ["cola can"]
    assert "cola can at 6" in dispatch.pending_call("supervisor")["greeting"]
    assert dispatch.update_shelf("cola can", 3)["action"] == "none"  # covered by the live call
    # a healthy shelf, or one that needs the other kind of call, is left out
    assert "water bottle" not in dispatch.pending_call("supervisor")["greeting"]
