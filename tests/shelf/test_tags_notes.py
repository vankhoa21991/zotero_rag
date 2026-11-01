"""Tests for shelf.tags and shelf.notes."""
import pytest

from shelf import changelog, items, notes, tags


def test_tag_add_remove_rename_revert(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    conn.commit()
    tags.add(conn, [iid], ["ml", "deep"], actor="user")
    conn.commit()
    assert {t["name"] for t in tags.all(conn)} == {"ml", "deep"}
    assert items.get_item(conn, iid)["tags"] == ["deep", "ml"]

    bid = tags.remove(conn, [iid], ["ml"], actor="user")
    conn.commit()
    assert items.get_item(conn, iid)["tags"] == ["deep"]
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert set(items.get_item(conn, iid)["tags"]) == {"ml", "deep"}

    bid2 = tags.rename(conn, "deep", "deeplearning", actor="user")
    conn.commit()
    assert {t["name"] for t in tags.all(conn)} == {"ml", "deeplearning"}
    changelog.revert_batch(conn, bid2)
    conn.commit()
    assert {t["name"] for t in tags.all(conn)} == {"ml", "deep"}


def test_tag_rename_to_existing_name_raises(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    tags.add(conn, [iid], ["a", "b"], actor="user")
    conn.commit()
    with pytest.raises(ValueError):
        tags.rename(conn, "a", "b", actor="user")


def test_tag_all_has_counts(conn):
    i1 = items.create_item(conn, {"title": "1"}, actor="user")
    i2 = items.create_item(conn, {"title": "2"}, actor="user")
    tags.add(conn, [i1, i2], ["shared"], actor="user")
    tags.add(conn, [i1], ["solo"], actor="user")
    conn.commit()
    by_name = {t["name"]: t["count"] for t in tags.all(conn)}
    assert by_name == {"shared": 2, "solo": 1}


def test_note_edit_and_revert(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    nid = notes.add(conn, iid, "<p>hello</p>", actor="user")
    conn.commit()
    bid = notes.update(conn, nid, "<p>changed</p>", actor="user")
    conn.commit()
    assert items.get_item(conn, iid)["notes"][0]["body"] == "<p>changed</p>"
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert items.get_item(conn, iid)["notes"][0]["body"] == "<p>hello</p>"


def test_note_delete_and_revert(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    nid = notes.add(conn, iid, "<p>bye</p>", actor="user")
    conn.commit()
    bid = notes.delete(conn, nid, actor="user")
    conn.commit()
    assert items.get_item(conn, iid)["notes"] == []
    changelog.revert_batch(conn, bid)
    conn.commit()
    assert len(items.get_item(conn, iid)["notes"]) == 1


def test_note_sanitize_strips_script_and_onclick_keeps_links():
    html = (
        '<p onclick="evil()">x</p><script>alert(1)</script>'
        '<a href="https://x.org" onclick="e()">link</a><img src="http://bad"><b>bold</b>'
    )
    out = notes.sanitize(html)
    assert "<script" not in out
    assert "onclick" not in out
    assert "<img" not in out
    assert '<a href="https://x.org"' in out and "link" in out
    assert "<b>bold</b>" in out


def test_note_add_sanitizes_body(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    nid = notes.add(conn, iid, "<script>alert(1)</script><p>ok</p>", actor="user")
    conn.commit()
    body = conn.execute("SELECT body FROM notes WHERE id=?", (nid,)).fetchone()["body"]
    assert "<script" not in body and "<p>ok</p>" in body


def test_note_add_to_missing_item_raises(conn):
    with pytest.raises(ValueError):
        notes.add(conn, 999, "hello", actor="user")


def test_annotation_note_update_and_delete_raise_read_only(conn):
    iid = items.create_item(conn, {"title": "T"}, actor="user")
    nid = notes.add(conn, iid, "<p>p.3: highlight</p>", actor="import", kind="annotation")
    conn.commit()
    with pytest.raises(notes.ReadOnlyNote):
        notes.update(conn, nid, "<p>x</p>", actor="user")
    with pytest.raises(notes.ReadOnlyNote):
        notes.delete(conn, nid, actor="user")
    conn.commit()
