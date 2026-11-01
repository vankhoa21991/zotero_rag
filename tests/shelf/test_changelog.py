"""Tests for shelf.changelog: batches, records, revert/apply."""
import json

import pytest

from shelf import changelog, items


def test_begin_batch_and_list(conn):
    bid = changelog.begin_batch(conn, "user", label="tweak")
    conn.commit()
    assert isinstance(bid, int)
    rows = changelog.list_batches(conn)
    assert len(rows) == 1
    assert rows[0]["actor"] == "user"
    assert rows[0]["state"] == "applied"


def test_revert_batch_restores_old_values(conn):
    iid = items.create_item(conn, {"title": "First"}, actor="user")
    bid = items.update_item(conn, iid, {"title": "Second", "Reading status": "to read"}, actor="user")
    conn.commit()
    item = items.get_item(conn, iid)
    assert item["title"] == "Second"
    assert item["fields"] == {"Reading status": "to read"}

    changelog.revert_batch(conn, bid)
    conn.commit()
    item = items.get_item(conn, iid)
    assert item["title"] == "First"
    assert item["fields"] == {}  # added custom field removed on revert


def test_revert_of_batch_that_removed_field_restores_it(conn):
    iid = items.create_item(conn, {"title": "T", "Reading status": "done"}, actor="user")
    bid = items.update_item(conn, iid, {"Reading status": None}, actor="user")
    conn.commit()
    assert items.get_item(conn, iid)["fields"] == {}
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert items.get_item(conn, iid)["fields"] == {"Reading status": "done"}


def test_reverting_a_proposed_batch_marks_it_reverted(conn):
    bid = changelog.begin_batch(conn, "agent", state="proposed")
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert changelog.list_batches(conn)[0]["state"] == "reverted"


def test_apply_proposed_batch_changes_nothing_until_applied(conn):
    iid = items.create_item(conn, {"title": "Before"}, actor="user")
    bid = changelog.begin_batch(conn, "agent", state="proposed")
    items.update_item(conn, iid, {"title": "After"}, actor="agent", batch_id=bid)
    conn.commit()
    # Proposed: the new value is NOT applied yet.
    assert items.get_item(conn, iid)["title"] == "Before"
    changelog.apply_batch(conn, bid)
    conn.commit()
    assert items.get_item(conn, iid)["title"] == "After"
    assert changelog.list_batches(conn)[0]["state"] == "applied"


def test_revert_applied_batch_after_apply(conn):
    iid = items.create_item(conn, {"title": "A"}, actor="user")
    bid = items.update_item(conn, iid, {"title": "B"}, actor="user")
    conn.commit()
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert items.get_item(conn, iid)["title"] == "A"
    assert changelog.list_batches(conn)[0]["state"] == "reverted"


def test_revert_refuses_to_overwrite_later_batch_on_same_field(conn):
    iid = items.create_item(conn, {"title": "A"}, actor="user")
    b1 = items.update_item(conn, iid, {"title": "B"}, actor="user")
    items.update_item(conn, iid, {"date": "2024-01-01"}, actor="user")  # unrelated batch
    b3 = items.update_item(conn, iid, {"title": "C"}, actor="user")
    conn.commit()

    with pytest.raises(changelog.ConflictError):
        changelog.revert_batch(conn, b1)
    conn.rollback()

    # strict LIFO: revert the newest title change first, then the older one is allowed
    changelog.revert_batch(conn, b3)
    conn.commit()
    assert items.get_item(conn, iid)["title"] == "B"
    changelog.revert_batch(conn, b1)
    conn.commit()
    assert items.get_item(conn, iid)["title"] == "A"


def test_record_stores_json_encoded_values(conn):
    bid = changelog.begin_batch(conn, "user")
    changelog.record(conn, bid, "item", 1, "creators", [{"first": "A"}], [{"first": "B"}])
    conn.commit()
    row = conn.execute("SELECT old, new FROM changes WHERE batch_id=?", (bid,)).fetchone()
    assert json.loads(row["old"]) == [{"first": "A"}]
    assert json.loads(row["new"]) == [{"first": "B"}]
