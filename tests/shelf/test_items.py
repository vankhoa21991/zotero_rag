"""Tests for shelf.items: CRUD, custom fields, trash, bulk edits."""
from shelf import changelog, items


def test_update_core_and_custom_field_roundtrip(conn):
    iid = items.create_item(conn, {"title": "T", "Reading status": "to read"}, actor="user")
    conn.commit()
    item = items.get_item(conn, iid)
    assert item["title"] == "T"
    assert item["fields"] == {"Reading status": "to read"}

    bid = items.update_item(conn, iid, {"title": "T2", "Reading status": "read"}, actor="user")
    conn.commit()
    assert isinstance(bid, int) and bid > 0
    item = items.get_item(conn, iid)
    assert item["title"] == "T2"
    assert item["fields"] == {"Reading status": "read"}


def test_untitled_and_unicode_items(conn):
    a = items.create_item(conn, {"title": ""}, actor="user")
    b = items.create_item(conn, {"title": "📚 résumé", "type": "webpage"}, actor="user")
    conn.commit()
    titles = {r["title"] for r in items.list_items(conn)}
    assert "" in titles and "📚 résumé" in titles
    items.update_item(conn, b, {"title": "📚 résumé 2"}, actor="user")
    conn.commit()
    assert items.get_item(conn, b)["title"] == "📚 résumé 2"


def test_creators_stored_as_json_list(conn):
    iid = items.create_item(
        conn, {"title": "T", "creators": [{"first": "Ada", "last": "L"}]}, actor="user"
    )
    conn.commit()
    assert items.get_item(conn, iid)["creators"] == [{"first": "Ada", "last": "L"}]


def test_update_empty_changes_is_noop(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    before = len(changelog.list_batches(conn))
    assert items.update_item(conn, iid, {}, actor="user") == 0
    assert len(changelog.list_batches(conn)) == before


def test_bulk_update_is_one_batch(conn):
    a = items.create_item(conn, {"title": "A"}, actor="user")
    b = items.create_item(conn, {"title": "B"}, actor="user")
    conn.commit()
    before = len(changelog.list_batches(conn))
    bid = items.bulk_update(conn, [a, b], {"Reading status": "later"}, actor="user")
    conn.commit()
    batches = changelog.list_batches(conn)
    assert len(batches) == before + 1 and batches[0]["id"] == bid
    assert items.get_item(conn, a)["fields"]["Reading status"] == "later"
    assert items.get_item(conn, b)["fields"]["Reading status"] == "later"


def test_trash_hides_from_default_list_and_restore_returns(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    items.trash_item(conn, iid, actor="user")
    conn.commit()
    assert items.list_items(conn) == []
    assert [r["id"] for r in items.list_items(conn, trashed=True)] == [iid]
    items.restore_item(conn, iid, actor="user")
    conn.commit()
    assert [r["id"] for r in items.list_items(conn)] == [iid]


def test_list_items_filters_by_type_and_order(conn):
    a = items.create_item(conn, {"title": "A", "type": "paper"}, actor="user")
    b = items.create_item(conn, {"title": "B", "type": "webpage"}, actor="user")
    conn.commit()
    assert [r["id"] for r in items.list_items(conn, type="paper")] == [a]
    # modified desc: the newer item comes first
    assert [r["id"] for r in items.list_items(conn)][:1] == [b]


def test_update_missing_item_raises(conn):
    try:
        items.update_item(conn, 999, {"title": "x"}, actor="user")
        assert False, "expected ValueError"
    except ValueError:
        pass
