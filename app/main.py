"""The FastAPI application: config check, tables, routers, static frontend.

Run it with:

    uvicorn app.main:app --reload

**Single worker only.** `app.agents.session_store` keeps chat history in a
plain dict in process memory, so with two workers a user's requests would land
on whichever process the OS picked and their conversation would appear to jump
back and forth between two different histories.

This replaces the old root `main.py`, which loaded the models itself and served
three unauthenticated endpoints. That file is gone; its parts now live in
`app.ml` (the models), `app.tools` (running them), and `app.api.debug_routes`
(reaching them over HTTP, engineer-only).
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.api import auth_routes, chat_routes, debug_routes
from app.db import models  # noqa: F401 -- imported for the side effect below
from app.db.base import Base, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build the application.

    Ordering inside here is deliberate, and the first step is the important one.
    """
    # 1. Refuse to start misconfigured. `app.config` deliberately does NOT
    #    raise on a missing GEMINI_API_KEY / JWT_SECRET_KEY at import time,
    #    because the offline scripts (`python -m app.db.seed`, the ML sanity
    #    checks) import config and need neither key. The *server* does need
    #    both, so the check happens here -- as the first statement of the
    #    factory, so a missing key is a refusal to boot with a named variable,
    #    rather than a confusing 500 halfway through someone's first chat.
    #
    #    (It sits here rather than at module top-level between imports, where
    #    it would read as first but be one import-reorder away from silently
    #    doing nothing.)
    config.validate_runtime_config()

    app = FastAPI(
        title="Engine Health API",
        description=(
            "Chat front-end over the NASA C-MAPSS predictive-maintenance "
            "models. Engineers get the full tool set across the fleet; "
            "customers get plain-language health on the engines they own."
        ),
        version="1.0.0",
    )

    # 2. Create any missing tables. A no-op against a seeded database, and it
    #    is NOT a substitute for running the seed -- an empty schema has no
    #    users, so nobody can log in. Note it adds missing *tables* only, never
    #    missing *columns*: there is no Alembic here, so a schema change means
    #    deleting `app.db` and re-seeding (the seed script detects a stale
    #    schema and says so).
    Base.metadata.create_all(bind=engine)

    # 3. Routers. All three mount under /api/*, which is why the static files
    #    below must NOT be mounted at "/".
    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)
    app.include_router(debug_routes.router)

    # 4. The frontend. `StaticFiles` raises at construction if the directory is
    #    missing, and an empty directory is not something git tracks -- so
    #    create it rather than let a fresh clone fail to boot over a folder.
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    #    Mounted at "/static", never at "/": a mount at the root path shadows
    #    every route registered after it, so /api/auth/login would 404 while
    #    looking perfectly correct in the source.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        """Serve the single-page frontend.

        An explicit route rather than `StaticFiles(html=True)` mounted at root,
        for the shadowing reason above. Until Task 7 writes `index.html` this
        points at /docs instead of returning a 404, so the API is usable before
        the UI exists.
        """
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return JSONResponse(
                {
                    "message": "Engine Health API is running.",
                    "docs": "/docs",
                    "note": "The web UI has not been built yet.",
                }
            )
        return FileResponse(index_path)

    logger.info("Engine Health API ready")
    return app


app = create_app()
