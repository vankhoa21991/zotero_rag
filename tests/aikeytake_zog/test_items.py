"""Tests for aikeytake_zog.items: CRUD, custom fields, trash, bulk edits."""
from aikeytake_zog import changelog, items, search


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


def test_create_item_skips_null_custom_fields(conn):
    iid = items.create_item(
        conn, {"title": "T", "Status": None, "Venue": "ICML"}, actor="user"
    )
    conn.commit()
    assert items.get_item(conn, iid)["fields"] == {"Venue": "ICML"}


def test_create_item_is_recorded_and_undoable(conn):
    from aikeytake_zog import changelog

    iid = items.create_item(conn, {"title": "Undoable"}, actor="user")
    conn.commit()
    bid = conn.execute("SELECT id FROM batches ORDER BY id DESC LIMIT 1").fetchone()["id"]
    changelog.revert_batch(conn, bid)
    conn.commit()
    # reverting a creation trashes the item rather than hard-deleting it
    assert items.list_items(conn) == []
    assert [r["id"] for r in items.list_items(conn, trashed=True)] == [iid]


def test_update_rejects_reserved_field_names(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    for bad in ("id", "zotero_key", "created", "modified", "deleted_at"):
        try:
            items.update_item(conn, iid, {bad: "x"}, actor="user")
            assert False, f"expected ValueError for {bad}"
        except ValueError:
            pass


def test_update_rejects_non_scalar_custom_values(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    try:
        items.update_item(conn, iid, {"weird": {"nested": 1}}, actor="user")
        assert False, "expected ValueError for dict value"
    except ValueError:
        pass


def test_creators_must_be_list_of_dicts_or_strings(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    items.update_item(conn, iid, {"creators": [{"last": "LeCun"}]}, actor="user")
    conn.commit()
    try:
        items.update_item(conn, iid, {"creators": "Yann LeCun"}, actor="user")
        assert False, "expected ValueError for string creators"
    except ValueError:
        pass


def _att(conn, iid, kind, sha, missing=0, filename="f"):
    cur = conn.execute(
        "INSERT INTO attachments(item_id, kind, filename, sha256, ext, missing) VALUES(?,?,?,?,?,?)",
        (iid, kind, filename, sha, kind, missing),
    )
    return cur.lastrowid


def test_list_items_pdf_id_first_stored_pdf(conn):
    a = items.create_item(conn, {"title": "A"}, actor="user")
    b = items.create_item(conn, {"title": "B"}, actor="user")
    c = items.create_item(conn, {"title": "C"}, actor="user")
    _att(conn, a, "pdf", "aa" * 32, missing=1)
    good = _att(conn, a, "pdf", "bb" * 32)
    _att(conn, a, "pdf", "cc" * 32)
    _att(conn, b, "snapshot", "dd" * 32)
    _att(conn, c, "pdf", None)
    conn.commit()
    by_id = {r["id"]: r["pdf_id"] for r in items.list_items(conn)}
    assert by_id[a] == good
    assert by_id[b] is None
    assert by_id[c] is None


def _count_statements(conn, fn):
    stmts = []
    conn.set_trace_callback(stmts.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return len(stmts)


def test_list_items_row_summary(conn):
    iid = items.create_item(conn, {
        "title": "T", "date": "Published 2021-05-03", "url": "https://www.example.com/a",
        "creators": [{"first": "A", "last": "Chen"}, {"first": "B", "last": "Wang"}],
    }, actor="user")
    for n in "fedcbaz":
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (n,))
        conn.execute("INSERT INTO item_tags(item_id, tag_id) SELECT ?, id FROM tags WHERE name=?", (iid, n))
    conn.execute("INSERT INTO attachments(item_id, kind) VALUES(?, 'snapshot')", (iid,))
    conn.commit()
    row = items.list_items(conn)[0]
    assert row["creators_short"] == "Chen & Wang"
    assert row["year"] == "2021" and row["domain"] == "example.com"
    assert row["tags"] == ["a", "b", "c", "d", "e"]
    assert row["has_snapshot"] is True and row["pdf_id"] is None
    # search rows carry the same keys
    srow = search.search(conn, "T")[0]
    for k in ("creators_short", "year", "domain", "tags", "has_snapshot", "pdf_id"):
        assert srow[k] == row[k]

    def many(n):
        for i in range(n):
            items.create_item(conn, {"title": f"x{i}"}, actor="user")
        conn.commit()
    many(1)
    one = _count_statements(conn, lambda: items.list_items(conn, limit=2))
    many(20)
    twenty = _count_statements(conn, lambda: items.list_items(conn, limit=22))
    assert one == twenty


def test_row_summary_edge_cases(conn):
    def row(**kw):
        iid = items.create_item(conn, {"title": "E", **kw}, actor="user")
        conn.commit()
        return next(r for r in items.list_items(conn) if r["id"] == iid)
    assert row()["creators_short"] == "" and row()["year"] is None and row()["domain"] is None
    assert row(creators=[{"last": "Chen"}])["creators_short"] == "Chen"
    assert row(creators=[{"last": "C"}, {"last": "W"}, {"last": "X"}])["creators_short"] == "C et al."
    assert row(creators=[{"name": "WHO"}])["creators_short"] == "WHO"
    assert row(url="javascript:1")["domain"] is None
    assert row(date="12345")["year"] is None
    conn.execute("UPDATE items SET creators='{not json'")
    conn.commit()
    assert items.list_items(conn)[0]["creators_short"] == ""
