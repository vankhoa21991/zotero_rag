"""shelf CLI: serve, set-password, create-token, extract, import-zotero.

Contract: `main(argv)` is importable for tests; data dir comes from
SHELF_DATA_DIR (see config.load_settings). `create-token` prints the raw
token exactly once.
"""
import argparse
import getpass

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

    args = p.parse_args(argv)

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
