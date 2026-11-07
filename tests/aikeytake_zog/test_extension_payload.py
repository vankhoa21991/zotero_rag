"""The extension's item payload must be accepted by the server (POST /api/items)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aikeytake_zog import auth, db as zog_db
from aikeytake_zog.app import create_app
from aikeytake_zog.config import Settings

EXT = Path(__file__).resolve().parents[2] / "extension"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

FACTS = {
    "url": "https://journal.example/articles/42",
    "title": "Tab title",
    "lang": "en",
    "metas": [
        {"name": "citation_title", "content": "Deep <i>RAG</i>\n systems"},
        {"name": "citation_author", "content": "Doe, Jane"},
        {"name": "citation_author", "content": "Plain Name"},
        {"name": "citation_publication_date", "content": "2023/05/04"},
        {"name": "citation_doi", "content": "https://doi.org/10.1000/xyz"},
        {"name": "citation_abstract", "content": "An abstract."},
        {"name": "og:site_name", "content": "J Example"},
    ],
}


def _payload(tmp_path, facts):
    url = (EXT / "src/metadata.js").as_uri()
    script = (
        f"import {{buildItemPayload, parseTags}} from {json.dumps(url)};"
        f"const f = {json.dumps(facts)};"
        "console.log(JSON.stringify({payload: buildItemPayload(f), tags: parseTags('ml, ML ,, rag')}));"
    )
    r = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(r.stdout)


def test_payload_is_accepted_by_server(tmp_path):
    out = _payload(tmp_path, FACTS)
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    conn = zog_db.connect(settings.db_path)
    auth.set_password(conn, "pw")
    tok = auth.create_token(conn, "ext", "write")
    conn.commit()
    conn.close()
    c = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    r = c.post("/api/items", json=out["payload"], headers=h)
    assert r.status_code in (200, 201), r.text
    iid = r.json()["id"]
    assert c.post(f"/api/items/{iid}/tags", json={"names": out["tags"]}, headers=h).status_code == 200

    item = c.get(f"/api/items/{iid}", headers=h).json()
    assert item["type"] == "journalArticle"
    assert item["title"] == "Deep RAG systems"
    assert item["date"] == "2023-05-04"
    assert item["doi"] == "10.1000/xyz"
    assert item["abstract"] == "An abstract."
    assert item["url"] == FACTS["url"]
    assert item["creators"][0]["last"] == "Doe"
    assert item["creators"][1]["name"] == "Plain Name"
    assert item["fields"] == {"website": "J Example", "language": "en"}
    assert sorted(item["tags"]) == ["ml", "rag"]
