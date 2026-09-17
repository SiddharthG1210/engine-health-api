"""Configuration, read from a .env file at the repo root (gitignored).

Everything here has a sensible default except GEMINI_API_KEY and JWT_SECRET_KEY,
which have no safe default and must be supplied.

Those two are checked by `validate_runtime_config()` rather than at import time,
and that distinction matters: the seed script (`python -m app.db.seed`) and the
ML sanity checks both import this module but need neither key. Failing at import
would make it impossible to set up the database before obtaining a Gemini key.
`app.main` calls the validator at startup instead, so the *server* still refuses
to boot misconfigured rather than failing as a confusing 500 mid-chat.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

# --- Required at runtime (see validate_runtime_config) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

# --- LLM ---
# plan.md locked in "gemini-2.5-flash", but that model now returns 404 for keys
# created after its cutover ("no longer available to new users"), so the
# defaults below were re-chosen against what this project's key can actually
# reach. Measured on the free tier in Aug 2026 -- and measured, not read off
# the docs, because the docs were wrong about it (see plan.md, Amendment B):
#
#   gemini-3.5-flash-lite   15 req/min sustained    ~0.8s   no thinking tokens
#   gemini-3.6-flash         5 req/min, AND a second
#                            tighter bucket at 20    ~2-4s   ~250 thought tok.
#   gemini-2.5-flash         404 for new keys
#
# One chat turn costs at least two calls (routing + communicator) and a
# multi-engine question costs more, so `gemini-3.6-flash` runs dry after a
# handful of turns and stays dry -- it is not viable for either job here
# despite being the stronger model. **flash-lite is the only free-tier model
# that sustains this app**, and it was verified to handle both jobs: it routes
# as accurately as 3.6-flash (6 parallel calls for "full report on engines 10
# and 11", and a clarifying question rather than a guess when no engine is
# named), and it holds the customer plain-language rules.
#
# The two settings stay separate even though they currently name the same
# model: they are the seam for splitting the load across two quota buckets if a
# second usable model appears, or for putting the communicator on a stronger
# paid model later without touching the routing path.
GEMINI_MODEL_ROUTING = os.getenv("GEMINI_MODEL_ROUTING", "gemini-3.5-flash-lite")
GEMINI_MODEL_COMMUNICATOR = os.getenv(
    "GEMINI_MODEL_COMMUNICATOR", "gemini-3.5-flash-lite"
)

# --- Auth ---
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# --- Storage ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'app.db'}")
ML_ARTIFACTS_DIR = BASE_DIR / os.getenv("ML_ARTIFACTS_DIR", "ml_artifacts")
DATA_DIR = BASE_DIR / os.getenv("DATA_DIR", "data")


def validate_runtime_config() -> None:
    """Raise if anything the running server needs is missing.

    Called from `app.main` at startup, not at import time -- see module docstring.
    """
    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", GEMINI_API_KEY),
            ("JWT_SECRET_KEY", JWT_SECRET_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Add them to {ENV_PATH} (see plan.md, Task 0)."
        )
