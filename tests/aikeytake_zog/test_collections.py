"""Tests for aikeytake_zog.collections: tree, moves, membership, delete."""
import pytest

from aikeytake_zog import changelog, collections, items


def _tree(conn):
    parent = collections.create(conn, "Parent", actor="user")
    child = collections.create(conn, "Child", parent_id=parent, actor="user")
    grandchild = collections.create(conn, "Grandchild", parent_id=child, actor="user")
    conn.commit()
    return parent, child, grandchild


def test_create_and_tree_shape(conn):
    parent, child, grandchild = _tree(conn)
    roots = collections.tree(conn)
    assert len(roots) == 1 and roots[0]["id"] == parent
    assert [c["id"] for c in roots[0]["children"]] == [child]
    assert [c["id"] for c in roots[0]["children"][0]["children"]] == [grandchild]


def test_move_collection_into_own_descendant_raises(conn):
    parent, child, grandchild = _tree(conn)
    with pytest.raises(ValueError):
        collections.move(conn, parent, grandchild, actor="user")
    with pytest.raises(ValueError):
        collections.move(conn, parent, parent, actor="user")
    conn.commit()
    row = conn.execute("SELECT parent_id FROM collections WHERE id=?", (parent,)).fetchone()
    assert row["parent_id"] is None


def test_move_collection_and_revert(conn):
    parent, child, _ = _tree(conn)
    other = collections.create(conn, "Other", actor="user")
    conn.commit()
    bid = collections.move(conn, child, other, actor="user")
    conn.commit()
    assert (
        conn.execute("SELECT parent_id FROM collections WHERE id=?", (child,)).fetchone()["parent_id"]
        == other
    )
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert (
        conn.execute("SELECT parent_id FROM collections WHERE id=?", (child,)).fetchone()["parent_id"]
        == parent
    )


def test_delete_collection_reparents_children_keeps_items(conn):
    parent, child, _ = _tree(conn)
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    collections.add_items(conn, parent, [iid], actor="user")
    conn.commit()
    collections.delete(conn, parent, actor="user")
    conn.commit()
    assert (
        conn.execute("SELECT parent_id FROM collections WHERE id=?", (child,)).fetchone()["parent_id"]
        is None
    )
    assert [r["id"] for r in items.list_items(conn)] == [iid]


def test_create_collection_is_undoable(conn):
    cid = collections.create(conn, "Undoable", actor="user")
    conn.commit()
    bid = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()["id"]
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert (
        conn.execute("SELECT COUNT(*) c FROM collections WHERE id=?", (cid,)).fetchone()["c"] == 0
    )


def test_delete_collection_revert_restores_id_children_and_items(conn):
    parent, child, grandchild = _tree(conn)
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    collections.add_items(conn, child, [iid], actor="user")
    conn.commit()
    bid = collections.delete(conn, parent, actor="user")
    conn.commit()

    # after delete: children reparented, item untouched
    assert (
        conn.execute("SELECT parent_id FROM collections WHERE id=?", (child,)).fetchone()["parent_id"]
        is None
    )
    assert [r["id"] for r in items.list_items(conn)] == [iid]

    changelog.revert_batch(conn, bid)
    conn.commit()
    # original id restored; children back under it; item still in the child collection
    row = conn.execute("SELECT parent_id FROM collections WHERE id=?", (parent,)).fetchone()
    assert row is not None and row["parent_id"] is None
    assert conn.execute("SELECT parent_id FROM collections WHERE id=?", (child,)).fetchone()["parent_id"] == parent
    assert [r["id"] for r in items.list_items(conn, collection_id=child)] == [iid]


def test_add_remove_items_and_counts(conn):
    a = collections.create(conn, "A", actor="user")
    i1 = items.create_item(conn, {"title": "1"}, actor="user")
    i2 = items.create_item(conn, {"title": "2"}, actor="user")
    conn.commit()
    collections.add_items(conn, a, [i1, i2], actor="user")
    # duplicate add is a no-op
    collections.add_items(conn, a, [i1], actor="user")
    conn.commit()
    roots = collections.tree(conn)
    assert roots[0]["count"] == 2
    collections.remove_items(conn, a, [i2], actor="user")
    conn.commit()
    assert [r["id"] for r in items.list_items(conn, collection_id=a)] == [i1]


def test_move_items_between_collections_and_revert(conn):
    a = collections.create(conn, "A", actor="user")
    b = collections.create(conn, "B", actor="user")
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    collections.add_items(conn, a, [iid], actor="user")
    conn.commit()
    bid = collections.move_items(conn, [iid], a, b, actor="user")
    assert isinstance(bid, int) and bid > 0
    conn.commit()
    assert items.list_items(conn, collection_id=a) == []
    assert [r["id"] for r in items.list_items(conn, collection_id=b)] == [iid]
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert [r["id"] for r in items.list_items(conn, collection_id=a)] == [iid]
    assert items.list_items(conn, collection_id=b) == []
