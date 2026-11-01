"""Tests for shelf.search: FTS5 full-text search."""
import pytest

from shelf import changelog, collections, items, notes, search, tags


def test_finds_by_title(conn):
    iid = items.create_item(conn, {"title": "Quantum entanglement basics"}, actor="user")
    other = items.create_item(conn, {"title": "Baking sourdough"}, actor="user")
    conn.commit()
    res = search.search(conn, "entanglement")
    assert [r["id"] for r in res] == [iid]
    assert res[0]["title"] == "Quantum entanglement basics"
    assert res[0]["snippet"]


def test_finds_word_only_in_attachment_text(conn):
    iid = items.create_item(conn, {"title": "A paper"}, actor="user")
    cur = conn.execute(
        "INSERT INTO attachments(item_id, kind, filename) VALUES(?, 'pdf', 'a.pdf')", (iid,)
    )
    aid = cur.lastrowid
    search.set_attachment_text(conn, aid, "The rare word zephyrquartz appears only here.")
    conn.commit()
    res = search.search(conn, "zephyrquartz")
    assert [r["id"] for r in res] == [iid]


def test_finds_in_notes_and_abstract(conn):
    iid = items.create_item(
        conn, {"title": "T", "abstract": "about frobnicators and widgets"}, actor="user"
    )
    notes.add(conn, iid, "<p>my note mentions gizmos</p>", actor="user")
    conn.commit()
    assert [r["id"] for r in search.search(conn, "frobnicators")] == [iid]
    assert [r["id"] for r in search.search(conn, "gizmos")] == [iid]


@pytest.mark.parametrize(
    "query", ['"', "(", "a AND", "-x", "foo:", "*", '"unclosed', "OR", "NEAR a b", "!!"]
)
def test_fts_syntax_chars_never_raise(conn, query):
    items.create_item(conn, {"title": "hello world"}, actor="user")
    conn.commit()
    res = search.search(conn, query)
    assert isinstance(res, list)


def test_empty_query_returns_empty_list(conn):
    items.create_item(conn, {"title": "hello"}, actor="user")
    conn.commit()
    assert search.search(conn, "") == []
    assert search.search(conn, "   ") == []


def test_filters_by_collection_and_tag(conn):
    a = collections.create(conn, "A", actor="user")
    b = collections.create(conn, "B", actor="user")
    i1 = items.create_item(conn, {"title": "alpha one"}, actor="user")
    i2 = items.create_item(conn, {"title": "alpha two"}, actor="user")
    collections.add_items(conn, a, [i1], actor="user")
    collections.add_items(conn, b, [i2], actor="user")
    tags.add(conn, [i2], ["special"], actor="user")
    conn.commit()
    assert [r["id"] for r in search.search(conn, "alpha", collection_id=a)] == [i1]
    assert [r["id"] for r in search.search(conn, "alpha", tag="special")] == [i2]


def test_trashed_excluded(conn):
    iid = items.create_item(conn, {"title": "doomed item"}, actor="user")
    conn.commit()
    items.trash_item(conn, iid, actor="user")
    conn.commit()
    assert search.search(conn, "doomed") == []
    items.restore_item(conn, iid, actor="user")
    conn.commit()
    assert [r["id"] for r in search.search(conn, "doomed")] == [iid]


def test_unicode_query(conn):
    iid = items.create_item(conn, {"title": "📚 résumé de la thèse"}, actor="user")
    conn.commit()
    assert [r["id"] for r in search.search(conn, "résumé")] == [iid]
    assert [r["id"] for r in search.search(conn, "thèse")] == [iid]


def test_edit_title_and_tag_then_revert_updates_index(conn):
    iid = items.create_item(conn, {"title": "Quantum entanglement basics"}, actor="user")
    conn.commit()
    assert [r["id"] for r in search.search(conn, "entanglement")] == [iid]

    bid = items.update_item(conn, iid, {"title": "Something else entirely"}, actor="user")
    tags.add(conn, [iid], ["quantum"], actor="user")
    conn.commit()
    assert search.search(conn, "entanglement") == []
    assert [r["id"] for r in search.search(conn, "quantum")] == [iid]

    changelog.revert_batch(conn, bid)
    conn.commit()
    assert [r["id"] for r in search.search(conn, "entanglement")] == [iid]


def test_scores_ordered_best_first(conn):
    # title match should rank above full-text-only match
    t = items.create_item(conn, {"title": "zephyrquartz study"}, actor="user")
    f = items.create_item(conn, {"title": "unrelated"}, actor="user")
    cur = conn.execute(
        "INSERT INTO attachments(item_id, kind, filename) VALUES(?, 'pdf', 'b.pdf')", (f,)
    )
    search.set_attachment_text(conn, cur.lastrowid, "zephyrquartz deep in the body text")
    conn.commit()
    res = search.search(conn, "zephyrquartz")
    assert [r["id"] for r in res] == [t, f]
    assert res[0]["score"] > res[1]["score"]
