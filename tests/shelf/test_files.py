"""Tests for shelf.files: content-addressed file store."""
import hashlib
from pathlib import Path

import pytest

from shelf import files


def test_put_dedups_identical_content(tmp_path):
    data = b"hello world" * 100
    h1, s1 = files.put(tmp_path, data, "pdf")
    h2, s2 = files.put(tmp_path, data, "pdf")
    assert h1 == h2 == hashlib.sha256(data).hexdigest()
    assert s1 == s2 == len(data)
    on_disk = list((tmp_path / h1[:2]).glob(f"{h1}.*"))
    assert len(on_disk) == 1


def test_path_layout(tmp_path):
    data = b"abc"
    h, _ = files.put(tmp_path, data, "PDF")
    expected = tmp_path / h[:2] / f"{h}.pdf"
    assert files.path_for(tmp_path, h, "pdf") == expected
    assert expected.is_file()


def test_put_accepts_file_object(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"file object data")
    with open(src, "rb") as f:
        h, size = files.put(tmp_path, f, "bin")
    assert size == len(b"file object data")
    assert files.exists(tmp_path, h, "bin")


def test_zip_roundtrip_keeps_relative_assets(tmp_path):
    src = tmp_path / "snap"
    (src / "css").mkdir(parents=True)
    (src / "index.html").write_text("<html>hi</html>")
    (src / "css" / "a.css").write_text("body{}")
    data = files.zip_directory(src)
    h, _ = files.put(tmp_path, data, "zip")
    content, ctype = files.read_zip_member(tmp_path, h, "index.html")
    assert content == b"<html>hi</html>"
    assert ctype == "text/html"
    css, cctype = files.read_zip_member(tmp_path, h, "css/a.css")
    assert css == b"body{}"
    assert cctype == "text/css"


def test_read_zip_member_rejects_traversal(tmp_path):
    src = tmp_path / "snap"
    src.mkdir()
    (src / "index.html").write_text("x")
    h, _ = files.put(tmp_path, files.zip_directory(src), "zip")
    with pytest.raises(ValueError):
        files.read_zip_member(tmp_path, h, "../x")
    with pytest.raises(ValueError):
        files.read_zip_member(tmp_path, h, "/etc/passwd")


def test_read_zip_member_missing_raises(tmp_path):
    src = tmp_path / "snap"
    src.mkdir()
    (src / "index.html").write_text("x")
    h, _ = files.put(tmp_path, files.zip_directory(src), "zip")
    with pytest.raises(FileNotFoundError):
        files.read_zip_member(tmp_path, h, "nope.html")
