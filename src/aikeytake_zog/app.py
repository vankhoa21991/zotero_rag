"""App factory for aikeytake_zog.

Contract: `create_app(settings)` returns a configured FastAPI app; importable
as `aikeytake_zog.app:create_app` for `uvicorn --factory`. The data dir and schema are
created if missing, and the session secret is generated on first run.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aikeytake_zog import api, auth, config, db, web
from aikeytake_zog.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = config.load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    auth.session_secret(conn)  # generate once on first run
    conn.commit()
    conn.close()

    app = FastAPI(title="aikeytake-zog", docs_url="/api/docs")
    app.state.settings = settings
    app.state.throttle = auth.LoginThrottle()
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(moz|chrome)-extension://.+",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(api.build_router(), prefix="/api")
    api.snapshot_routes(app)
    web.web_routes(app)
    return app
