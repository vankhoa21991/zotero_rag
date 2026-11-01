"""shelf CLI: serve, set-password, create-token, extract, import-zotero.

Contract: `main(argv)` is importable for tests; data dir comes from
SHELF_DATA_DIR (see config.load_settings). `create-token` prints the raw
token exactly once.
"""
import argparse
import getpass
import json
from datetime import datetime
from pathlib import Path

from shelf import auth, config, db


def _open():
    settings = config.load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    return settings, conn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="shelf", description="self-hosted library server")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the web server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8321)

    sub.add_parser("set-password", help="set the web login password")

    t = sub.add_parser("create-token", help="create an API token (printed once)")
    t.add_argument("name")
    t.add_argument("--scope", choices=["read", "write"], default="write")

    e = sub.add_parser("extract", help="extract text from pending attachments")
    e.add_argument("--limit", type=int, default=None,
                   help="process at most N attachments (for testing)")

    zi = sub.add_parser("import-zotero",
                        help="import a Zotero library into a running shelf server")
    zi.add_argument("--zotero-dir", default=str(Path.home() / "Zotero"),
                    help="directory containing zotero.sqlite and storage/ (default: ~/Zotero)")
    zi.add_argument("--server", required=True,
                    help="shelf server base URL, e.g. http://127.0.0.1:8321")
    zi.add_argument("--token", default=None, help="write token (prompted if omitted)")
    zi.add_argument("--dry-run", action="store_true", help="read and count, send nothing")

    args = p.parse_args(argv)

    if args.cmd == "import-zotero":
        from shelf import importer
        from shelf.zotero_reader import ZoteroReader

        zdir = Path(args.zotero_dir).expanduser()
        db_path = zdir / "zotero.sqlite"
        if not db_path.exists():
            print(f"error: {db_path} not found")
            return 1
        token = args.token or getpass.getpass("shelf write token: ")
        client = importer.ApiClient(args.server, token)
        try:
            with ZoteroReader(db_path, zdir / "storage") as reader:
                rep = importer.import_zotero(
                    reader,
                    client,
                    dry_run=args.dry_run,
                    progress=lambda stage, done, total: print(
                        f"\r{stage}: {done}/{total}", end="", flush=True
                    ),
                )
        finally:
            client.close()
        if not args.dry_run:
            print()
        text = rep.render()
        print(text)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        txt_path = Path(f"zotero-import-report-{ts}.txt")
        json_path = Path(f"zotero-import-report-{ts}.json")
        txt_path.write_text(text + "\n", encoding="utf-8")
        json_path.write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
        print(f"report written to {txt_path} and {json_path}")
        return 1 if rep.failures else 0

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("shelf.app:create_app", factory=True, host=args.host, port=args.port)
        return 0

    settings, conn = _open()
    try:
        if args.cmd == "set-password":
            pw = getpass.getpass("New password: ")
            if not pw:
                print("refusing to set an empty password")
                return 1
            auth.set_password(conn, pw)
            conn.commit()
            print("password set")
        elif args.cmd == "create-token":
            try:
                raw = auth.create_token(conn, args.name, args.scope)
            except ValueError as e:
                print(f"error: {e}")
                return 1
            conn.commit()
            print(raw)
        elif args.cmd == "extract":
            from shelf import extract

            counts = extract.run_pending(conn, settings.files_dir, limit=args.limit)
            conn.commit()
            print(
                f"done={counts['done']} empty={counts['empty']} failed={counts['failed']}"
            )
    finally:
        conn.close()
    return 0
