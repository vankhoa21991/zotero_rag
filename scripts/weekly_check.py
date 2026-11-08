#!/usr/bin/env python3
"""End-to-end weekly-flow check (plan R-D step 1).

Usage:  uv run --extra server --extra pdf --with numpy python scripts/weekly_check.py <out_dir>

Spins up a throwaway aikeytake-zog server (temp data dir, random port) with a
password and write/read tokens created through the server CLI, seeds ~60 items
in 3 topics filed into 3 leaves under a parent group plus a `New` collection
with 6 new items (2 per topic), then runs the real weekly zrag sequence in
order:

    zrag describe --collection New   (summaries written back as notes)
    zrag index && zrag embed         (fake deterministic embeddings, see below)
    zrag place --collection New --json
    zrag file ... --from New --to <leaf>   (per topic)

and asserts: each new item suggested its topic's leaf; `New` is empty; the 6
items sit only in the approved leaves; the parent group holds no items; every
move is recorded in the change log inside an applied batch. Exits non-zero on
any failed check and writes <out_dir>/report.json.

Fakes (deterministic, no model files):
- LLM: an in-process OpenAI-compatible HTTP server; `describe` really talks to
  it over HTTP. The reply echoes the document text, so summaries stay
  in-topic and the flow is reproducible.
- Embeddings: `zotero_rag.embed.Embedder` is monkeypatched with a bag-of-words
  embedder over the topic vocabulary (15 dims). `zrag index`/`embed` run the
  real command code paths; only the model is fake. Same-topic items score
  ~1.0 cosine, cross-topic ~0.
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

# ---------------- seed data: 3 topics with disjoint vocabularies -------------

TOPICS = {
    "Quantum": ["quantum", "qubit", "entanglement", "superposition", "circuit"],
    "Bread": ["sourdough", "fermentation", "crust", "flour", "dough"],
    "Baroque": ["baroque", "organ", "fugue", "counterpoint", "harpsichord"],
}
VOCAB = [w for ws in TOPICS.values() for w in ws]
N_FILED = 20  # per topic


def filed_text(topic: str, i: int) -> tuple[str, str]:
    w = TOPICS[topic]
    title = f"{topic} study {i}: {w[0]} and {w[1]} in {w[2]} systems"
    abstract = (f"This is the {i}th {topic.lower()} study. It examines {w[0]}, {w[1]} "
                f"and {w[2]}, reporting measurements of {w[3]} across many {w[4]} trials.")
    return title, abstract


def new_text(topic: str, i: int) -> tuple[str, str]:
    w = TOPICS[topic]
    title = f"New {topic} paper {i}: {w[0]} meets {w[2]}"
    abstract = (f"A fresh {topic.lower()} article about {w[0]}, {w[1]} and {w[3]}, "
                f"with experiments on {w[4]}.")
    return title, abstract


# ---------------- fakes -------------------------------------------------------

class FakeLLMHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible /chat/completions that echoes the document text."""

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        # keep the topic words: echo the document portion of the prompt
        doc = user.split("Text:\n", 1)[1][:600] if "Text:\n" in user else user[:600]
        content = f"Deterministic summary. The document reads: {doc}"
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # quiet
        pass


class FakeEmbedder:
    """Bag-of-words over the topic vocabulary; deterministic, no model file."""

    model_name = "fake-embed"
    dim = len(VOCAB)

    def __init__(self, model_path=None, device="cpu"):
        pass

    def embed(self, texts):
        import numpy as np

        out = []
        for t in texts:
            words = (t or "").lower().split()
            v = np.array([words.count(w) for w in VOCAB], dtype="float32")
            n = np.linalg.norm(v)
            out.append((v / n).tolist() if n else [0.0] * self.dim)
        return out


def start_fake_llm() -> tuple[str, ThreadingHTTPServer]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLMHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1", srv


# ---------------- server + API helpers ----------------------------------------

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def api(url: str, token: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


class Check:
    """Collects pass/fail lines; the run fails if any check failed."""

    def __init__(self):
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, cond: bool, detail: str = ""):
        self.results.append((name, bool(cond), detail))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
        return cond


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"          # server data dir (fresh)
    zrag_dir = out_dir / "zrag"          # index db, summaries, description log
    zrag_dir.mkdir(exist_ok=True)
    checks = Check()
    batch_ids: list[int] = []
    total = 0
    srv: subprocess.Popen | None = None
    llm_srv: ThreadingHTTPServer | None = None
    embed_mod = None
    old_env: dict = {}
    old_embedder = None

    bin_aikeytake = Path(sys.executable).parent / "aikeytake-zog"
    if not bin_aikeytake.exists():
        bin_aikeytake = shutil.which("aikeytake-zog") or sys.exit(
            f"error: aikeytake-zog console script not found next to {sys.executable}")

    def server_cli(*args, inp: str | None = None) -> str:
        r = subprocess.run([str(bin_aikeytake), *args], input=inp, capture_output=True,
                           text=True, start_new_session=True,
                           env={**os.environ, "AIKEYTAKE_ZOG_DATA_DIR": str(data_dir)})
        if r.returncode != 0:
            raise RuntimeError(f"server CLI {args} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    # --- 1. server setup through the CLI: password + write token + read token
    print("== server setup (CLI) ==")
    server_cli("set-password", inp="weekly-check-pass\n")
    write_tok = server_cli("create-token", "weekly-write", "--scope", "write")
    read_tok = server_cli("create-token", "weekly-read", "--scope", "read")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    logf = open(out_dir / "server.log", "wb")
    srv = subprocess.Popen([str(bin_aikeytake), "serve", "--port", str(port)],
                           stdout=logf, stderr=subprocess.STDOUT,
                           env={**os.environ, "AIKEYTAKE_ZOG_DATA_DIR": str(data_dir)})
    try:
        for _ in range(100):
            if srv.poll() is not None:
                raise RuntimeError("server exited during startup; see server.log")
            try:
                api(base + "/api/health", read_tok)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not become healthy; see server.log")
        print(f"   server up at {base} (data dir {data_dir})")

        # --- 2. seed: parent group + 3 leaves + New; 60 filed items, 6 in New
        print("== seeding ==")
        topics_id = api(base + "/api/collections", write_tok, "POST", {"name": "Topics"})["id"]
        new_id = api(base + "/api/collections", write_tok, "POST", {"name": "New"})["id"]
        leaf_ids = {}
        for t in TOPICS:
            leaf_ids[t] = api(base + "/api/collections", write_tok, "POST",
                              {"name": t, "parent_id": topics_id})["id"]

        def add_item(title: str, abstract: str) -> int:
            return api(base + "/api/items", write_tok, "POST",
                       {"type": "webpage", "title": title, "abstract": abstract,
                        "url": f"https://example.org/{title[:20].replace(' ', '-')}"}).get("id")

        filed: dict[str, list[int]] = {}
        for t in TOPICS:
            ids = [add_item(*filed_text(t, i)) for i in range(N_FILED)]
            api(base + "/api/collections/%d/items" % leaf_ids[t], write_tok, "POST",
                {"item_ids": ids})
            filed[t] = ids
        new_items: dict[str, list[int]] = {}
        for t in TOPICS:
            ids = [add_item(*new_text(t, i)) for i in range(2)]
            api(base + "/api/collections/%d/items" % new_id, write_tok, "POST",
                {"item_ids": ids})
            new_items[t] = ids
        total = sum(len(v) for v in filed.values()) + sum(len(v) for v in new_items.values())
        print(f"   {total} items: {N_FILED}/topic filed into 3 leaves, "
              f"{sum(len(v) for v in new_items.values())} in New")

        # --- 3. fake LLM over HTTP + fake embedder class
        llm_base, llm_srv = start_fake_llm()
        import zotero_rag.embed as embed_mod
        real_embedder = embed_mod.Embedder
        embed_mod.Embedder = FakeEmbedder  # make_embedder() imports it lazily
        old_embedder = real_embedder

        env = {**os.environ,
               "ZOG_URL": base, "ZOG_TOKEN": write_tok,
               "LLM_BASE": llm_base, "LLM_KEY": "fake",
               "EMBED_MODEL": "fake-embed"}  # value irrelevant: class is patched
        old_env = {k: os.environ.get(k) for k in env}
        os.environ.update(env)  # in-process commands read ZOG_URL/LLM_BASE from os.environ

        from zotero_rag import cli as zcli

        # All zrag commands run in-process (same code paths as the CLI; the real
        # server is reached over HTTP). In-process is required for embed so the
        # FakeEmbedder monkeypatch applies.
        def zrag_inproc(*argv):
            import io, contextlib
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = zcli.main(list(argv))
            return rc, out.getvalue(), err.getvalue()

        db = str(zrag_dir / "index.db")

        # --- 4. the weekly sequence
        print("== zrag describe --collection New (write-back) ==")
        rc, out, err = zrag_inproc("describe", "--collection", "New",
                                   "--md-dir", str(zrag_dir / "summaries"),
                                   "--out", str(zrag_dir / "descriptions.jsonl"))
        checks.ok("describe exits 0", rc == 0, err[-400:])
        import ast
        counts = ast.literal_eval(err.split("done: ", 1)[1]) if "done: " in err else {}
        checks.ok("describe summarized all 6 new items", counts.get("ok") == 6, str(counts))
        checks.ok("all 6 summaries written back as notes", counts.get("written_back") == 6, str(counts))

        print("== zrag index && zrag embed ==")
        rc, out, err = zrag_inproc("index", "--db", db)
        checks.ok("index exits 0", rc == 0, err[-400:])
        rc, out, err = zrag_inproc("embed", "--db", db)
        checks.ok("embed exits 0", rc == 0, err[-400:])
        checks.ok("all items embedded", "embedded %d docs" % total in err, err[-200:])

        print("== zrag place --collection New --json ==")
        rc, out, err = zrag_inproc("place", "--collection", "New", "--json", "--db", db)
        checks.ok("place exits 0", rc == 0, err[-400:])
        rows = {r["id"]: r for r in json.loads(out)} if rc == 0 else {}
        for t, ids in new_items.items():
            want = f"Topics/{t}"
            for iid in ids:
                r = rows.get(iid) or {}
                checks.ok(f"new item {iid} ({t}) suggests {want}",
                          r.get("leaf_path") == want,
                          f"suggested {r.get('leaf_path')!r} level={r.get('level')}")

        print("== zrag file ... --from New --to <leaf> ==")
        batch_ids = []
        for t, ids in new_items.items():
            rc, out, err = zrag_inproc("file", *map(str, ids), "--from", "New",
                                       "--to", f"Topics/{t}")
            checks.ok(f"file {t} exits 0", rc == 0, err[-300:])
            line = out.strip()
            ok = line.startswith("batch ") and line.split()[1].isdigit()
            checks.ok(f"file {t} prints a batch id", ok, repr(line))
            if ok:
                batch_ids.append(int(line.split()[1]))

        # --- 5. postconditions
        print("== postconditions ==")
        rows_new = api(base + f"/api/items?collection={new_id}&limit=200", read_tok)
        checks.ok("New is empty after filing", len(rows_new) == 0, f"{len(rows_new)} left")

        all_moved = [i for ids in new_items.values() for i in ids]
        stray = []
        for iid in all_moved:
            full = api(base + f"/api/items/{iid}", read_tok)
            have = {c["id"] for c in full.get("collections", [])}
            want = {leaf_ids[t] for t, ids in new_items.items() if iid in ids}
            if have != want:
                stray.append((iid, sorted(have), sorted(want)))
        checks.ok("each moved item sits only in its approved leaf", not stray, str(stray[:3]))

        parent_rows = api(base + f"/api/items?collection={topics_id}&limit=200", read_tok)
        checks.ok("parent group 'Topics' holds no items", len(parent_rows) == 0,
                  f"{len(parent_rows)} direct members")

        conn = sqlite3.connect(data_dir / "aikeytake-zog.db")
        bad = []
        for iid in all_moved:
            n = conn.execute(
                "SELECT COUNT(*) FROM changes c JOIN batches b ON b.id=c.batch_id "
                "WHERE c.entity='collection_item' AND c.entity_id=? AND b.state='applied'",
                (str(iid),)).fetchone()[0]
            if n < 2:  # leave New + join leaf, both inside applied batches
                bad.append((iid, n))
        conn.close()
        checks.ok("every move is recorded in an applied batch", not bad, str(bad))

    except Exception as e:
        checks.ok(f"run completed without error", False, repr(e))
    finally:
        if embed_mod is not None:
            embed_mod.Embedder = old_embedder
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if srv is not None:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()
            logf.close()
        if llm_srv is not None:
            llm_srv.shutdown()

    # --- report
    failed = [r for r in checks.results if not r[1]]
    report = {
        "server": base, "port": port, "items_seeded": total,
        "batches": batch_ids,
        "checks": [{"name": n, "pass": p, "detail": d} for n, p, d in checks.results],
        "passed": len(checks.results) - len(failed),
        "failed": len(failed),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\n{report['passed']} passed, {report['failed']} failed — report: {out_dir / 'report.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[0])
        sys.exit("usage: weekly_check.py <out_dir>")
    sys.exit(main(Path(sys.argv[1])))
