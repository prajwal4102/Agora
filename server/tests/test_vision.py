"""Camera counts: steadiness before a count is believed, and what a restock
seen by the camera confirms. The detector itself is not loaded here."""
import pytest


@pytest.fixture
def dispatch(shop):
    import dispatch as module

    return module


def test_count_is_believed_only_after_holding_steady():
    from vision import StableCounts

    counts = StableCounts(frames=3)
    assert counts.update({"water bottle": 5}) == {}
    assert counts.update({"water bottle": 5}) == {}
    assert counts.update({"water bottle": 5}) == {"water bottle": 5}
    # A hand passes in front for one frame: nothing changes.
    assert counts.update({"water bottle": 3}) == {}
    assert counts.update({"water bottle": 5}) == {}
    assert counts.stable == {"water bottle": 5}
    # Two bottles really are taken away.
    for _ in range(2):
        assert counts.update({"water bottle": 3}) == {}
    assert counts.update({"water bottle": 3}) == {"water bottle": 3}


def test_products_never_seen_are_not_reported_empty():
    from vision import StableCounts

    counts = StableCounts(frames=2)
    for _ in range(4):
        changed = counts.update({"water bottle": 4, "cola can": 0})
    assert "cola can" not in counts.stable and "cola can" not in changed
    # Once seen, running out does count.
    counts.update({"cola can": 2})
    counts.update({"cola can": 2})
    counts.update({"cola can": 0})
    assert counts.update({"cola can": 0}) == {"cola can": 0}


def test_camera_confirms_a_refill(shop, dispatch):
    dispatch.update_shelf("water bottle", 3, source="camera")
    assert dispatch.open_refill_tasks()["count"] == 1
    dispatch.update_shelf("water bottle", 11, source="camera")
    assert dispatch.open_refill_tasks()["count"] == 0
    assert shop.shelf_status("water bottle")["in_back_room"] == 16  # 24 minus the 8 added
    assert "refilled" in shop.list_events()[-1]["title"]


def test_camera_confirms_a_delivery(shop, dispatch):
    call = dispatch.update_shelf("cola can", 2, source="camera")
    dispatch.set_call_status(call["call_id"], "ended")
    proposal = shop.propose_order("cola can", "Sri Lakshmi Traders", 72)
    order = shop.confirm_order(proposal["proposal_id"], "yes")
    dispatch.update_shelf("cola can", 10, source="camera")
    assert shop.order_status("cola can")["orders"][0]["status"] == "delivered"
    assert f"order #{order['order_number']} arrived" in shop.list_events()[-1]["title"]


def test_a_manual_count_change_confirms_nothing(shop, dispatch):
    dispatch.update_shelf("water bottle", 3)
    dispatch.update_shelf("water bottle", 11)
    assert dispatch.open_refill_tasks()["count"] == 1


def test_camera_descriptions_can_set_their_own_confidence_floor():
    from vision import CONFIDENCE, parse_detect_label

    assert parse_detect_label("bottle|water bottle") == [("bottle", CONFIDENCE), ("water bottle", CONFIDENCE)]
    assert parse_detect_label("pen@0.1|ballpoint pen@0.1") == [("pen", 0.1), ("ballpoint pen", 0.1)]
    assert parse_detect_label(" | ") == []
    assert parse_detect_label("cup@oops") == [("cup", CONFIDENCE)]


def test_new_seed_products_reach_an_existing_database(shop):
    import sqlite3

    with sqlite3.connect(shop.DB_PATH) as conn:
        conn.execute("DELETE FROM supplier_prices WHERE product_id=(SELECT id FROM products WHERE name='pen')")
        conn.execute("DELETE FROM products WHERE name='pen'")
        conn.execute("UPDATE products SET shelf_qty=1 WHERE name='water bottle'")
    shop.init_db()
    assert shop.shelf_status("pens")["product"] == "pen"
    assert shop.supplier_quotes("pen", 50)["cheapest_that_can_fill"] == "Bharat Wholesale"
    assert shop.shelf_status("water bottle")["on_shelf"] == 1  # existing stock untouched


def test_wrapped_products_are_described_for_the_reader_not_the_detector():
    from vision import parse_detect_label, wrapped_description

    label = "~a chocolate bar in its wrapper"
    assert parse_detect_label(label) == []
    assert wrapped_description(label) == "a chocolate bar in its wrapper"
    assert wrapped_description("bottle|water bottle") is None


def test_a_box_around_two_neighbours_is_not_a_third_item():
    from vision import _without_repeats

    left = ("chocolate bar", 0.99, (170, 80, 280, 300))
    right = ("chocolate bar", 0.99, (246, 80, 360, 300))
    both = ("chocolate bar", 0.99, (200, 80, 360, 300))  # swallows most of each
    apart = ("chocolate bar", 0.99, (450, 80, 520, 300))
    kept = _without_repeats([both, left, right, apart])
    assert both not in kept and len(kept) == 3


def test_overlap_between_different_products_is_kept():
    from vision import _without_repeats

    bottle = ("water bottle", 0.9, (100, 0, 200, 300))
    bar_in_front = ("chocolate bar", 0.99, (110, 50, 190, 290))
    assert len(_without_repeats([bottle, bar_in_front])) == 2
