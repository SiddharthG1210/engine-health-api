"""The single entry point for executing a tool the model asked for.

`dispatch_tool_call` is the choke point between "the LLM said something" and
"the system did something". Everything the model produces is untrusted input
here: the tool name, the argument types, and above all the engine id. This
module's job is to turn that into either a real result or a safe refusal --
never into an exception that kills the chat request.

The safety model, in one line: **the model may *name* an engine; Python decides
whether it can be *reached*.** There is no path in which a rejected id is
quietly swapped for one the caller is allowed to see.

Three layers, in this order, and the order matters:

1. `allowed_engine_ids` -- the caller's roster, checked before the DB is
   touched, so "doesn't exist" and "isn't yours" are indistinguishable.
2. `ROLE_TOOL_MAP` -- redundant with `app.tools.access` never offering the
   tool, and kept precisely because it is redundant.
3. `assert_engine_access` -- the ownership check that holds even if a caller
   forgets layer 1 entirely.
"""

from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import assert_engine_access
from app.data_access.errors import EngineNotFoundError, NoSensorDataError
from app.data_access.fetchers import get_latest_cycle, get_rul_window
from app.db.models import Engine, User
from app.ml import inference
from app.roles import ROLE_TOOL_MAP
from app.tools.specs import (
    ANOMALY_SCORE_SPEC,
    DEGRADATION_STAGE_SPEC,
    PREDICT_RUL_SPEC,
)

# The one sentence a caller gets for *any* engine outside their roster --
# whether it belongs to another customer, is unassigned, or does not exist at
# all. Deliberately uninformative: three different sentences would be a working
# oracle for enumerating the fleet. Do not make it more helpful.
OUT_OF_SCOPE_MESSAGE = "That engine isn't on your account."
NO_ENGINE_MESSAGE = "No engine specified."

# SQLite stores integers as signed 64-bit; handing it anything wider raises
# OverflowError from the driver, which would escape this module's "errors are
# returned, not raised" contract and 500 the chat turn. Python ints are
# unbounded, so an id that large has to be refused before it reaches the DB.
_MAX_DB_INT = 2**63 - 1
_MIN_DB_INT = -(2**63)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool's model-facing declaration bound to the Python that runs it.

    Keeping the two together means a tool cannot be offered to a model without
    also being executable, and every handler has the same signature -- which is
    what lets the fetchers stay hardcoded per shape instead of parameterised.
    """

    name: str
    spec: dict
    # (db, engine_id) -> the JSON-safe payload that goes in the "result" key.
    handler: Callable[[Session, int], dict]


def _run_predict_rul(db: Session, engine_id: int) -> dict:
    """Fetch a 30-cycle window and predict remaining useful life."""
    window = get_rul_window(db, engine_id)
    # inference.predict_rul returns a bare float; wrapped in a dict so every
    # tool's "result" has the same object shape for the model to read.
    return {"predicted_rul": inference.predict_rul(window)}


def _run_anomaly_score(db: Session, engine_id: int) -> dict:
    """Fetch the latest single cycle and score it against healthy behaviour."""
    return inference.anomaly_score(get_latest_cycle(db, engine_id))


def _run_degradation_stage(db: Session, engine_id: int) -> dict:
    """Fetch a 30-cycle window and classify the wear stage.

    Note the returned dict contains a "label" key holding the *health stage*
    (Healthy / Warning / Critical). That is exactly why the engine's friendly
    name is returned as a top-level `engine_label` and never merged in here.
    """
    return inference.degradation_stage(get_rul_window(db, engine_id))


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "predict_rul": ToolDefinition(
        name="predict_rul", spec=PREDICT_RUL_SPEC, handler=_run_predict_rul
    ),
    "anomaly_score": ToolDefinition(
        name="anomaly_score", spec=ANOMALY_SCORE_SPEC, handler=_run_anomaly_score
    ),
    "degradation_stage": ToolDefinition(
        name="degradation_stage",
        spec=DEGRADATION_STAGE_SPEC,
        handler=_run_degradation_stage,
    ),
}


def _coerce_engine_id(raw: object) -> int | None:
    """Normalise whatever the model put in `engine_id` to an int, or None.

    JSON has no integer type and the SDK's own coercion has shifted between
    versions, so an id can arrive as 31, 31.0, or "31". All three mean the same
    engine and all three are accepted. Anything else -- 31.5, "the red one",
    None, a list -- is rejected rather than guessed at.

    `bool` is rejected **first and explicitly**. `isinstance(True, int)` is
    True in Python, so without that branch a model emitting `engine_id: true`
    would be silently answered about **engine 1**.

    Coercion happens before any comparison: a str id would never match a set of
    ints, so coercing late would turn the allowlist into a no-op.

    Values too wide for a 64-bit column (1e20, a 25-digit string) are rejected
    too -- see `_MAX_DB_INT`. They are not engine ids by any reading, and
    letting one reach `db.get` raises OverflowError from inside the driver.
    """
    value: int | None = None

    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        # NaN and the infinities all fail is_integer(), so they fall through.
        value = int(raw) if raw.is_integer() else None
    elif isinstance(raw, str):
        candidate = raw.strip()
        # isascii() guards isdigit(), which is True for characters such as the
        # superscript three and Arabic-Indic digits that int() then refuses or
        # reads as a different number than the model meant.
        if candidate.isascii() and candidate.isdigit():
            value = int(candidate)

    if value is None or not (_MIN_DB_INT <= value <= _MAX_DB_INT):
        return None
    return value


def _failure(tool_name: str, engine_id: int | None, message: str) -> dict:
    """Build the standard failure payload.

    `engine_label` is always None on failure -- filling it in would mean
    loading the engine, which is the DB round-trip the allowlist ordering
    exists to avoid (and a timing difference is an oracle too).
    """
    return {
        "tool": tool_name,
        "engine_id": engine_id,
        "engine_label": None,
        "ok": False,
        "error": message,
    }


def dispatch_tool_call(
    tool_name: str,
    args: dict,
    db: Session,
    user: User,
    *,
    allowed_engine_ids: frozenset[int] | None,
) -> dict:
    """Run one model-requested tool call and return a result dict.

    Args:
        tool_name: the function name the model emitted.
        args: the model's arguments; only `engine_id` is read.
        db: an open SQLAlchemy session.
        user: the authenticated caller.
        allowed_engine_ids: the set of engine ids this caller may reach.
            `None` means unrestricted, and is the **engineer path only**.

    `allowed_engine_ids` is keyword-only with **no default** on purpose: a
    safety parameter that defaults to "unrestricted" is a widening waiting to
    happen. Forcing every call site to spell it out turns a forgotten argument
    into an immediate TypeError instead of silent full access.

    Returns:
        On success, a dict with keys `tool`, `engine_id`, `engine_label`,
        `ok=True` and `result`. On failure, the same keys but `engine_label` is
        None, `ok` is False, and `error` replaces `result`.

    **Errors are returned, not raised.** These dicts get fed back to the model
    as function responses. A raised exception would 500 the whole chat turn; a
    returned error lets the model say "that engine isn't on your account" and
    carry on -- and stops one bad engine number in a six-call question from
    taking the five good answers down with it.

    Known, accepted residual: engine ids are the dataset's own numbering, so a
    customer who can see their own ids can infer the fleet is numbered roughly
    1-100. Closing that needs opaque per-customer handles; out of scope here,
    so don't describe this boundary as airtight.
    """
    # --- 1. Normalise the engine id before anything compares or queries it. ---
    engine_id = _coerce_engine_id(args.get("engine_id") if args else None)
    if engine_id is None:
        return _failure(tool_name, None, NO_ENGINE_MESSAGE)

    # --- 2. Allowlist: the customer safety boundary, checked before the DB. ---
    scope = allowed_engine_ids
    # Belt and braces: only an engineer is ever legitimately unrestricted, so a
    # caller that forgets to pass a scope for anyone else fails closed rather
    # than inheriting full access.
    if scope is None and user.role != "engineer":
        scope = frozenset()

    # `is not None`, written literally and never shortened to `if scope and ...`
    # -- frozenset() is falsy, so the short form would skip the gate entirely
    # for a customer who owns nothing. Likeliest bug in this file.
    if scope is not None and engine_id not in scope:
        return _failure(tool_name, engine_id, OUT_OF_SCOPE_MESSAGE)

    # --- 3. Role gate. Redundant with app.tools.access never offering this
    # tool to this role, and that redundancy is the point: a later edit to the
    # specs or the prompts then cannot widen access on its own. ---
    if tool_name not in ROLE_TOOL_MAP.get(user.role, []):
        return _failure(
            tool_name, engine_id, "That tool isn't available for your account."
        )

    definition = TOOL_REGISTRY.get(tool_name)
    if definition is None:
        # Only reachable if ROLE_TOOL_MAP names a tool the registry lacks.
        return _failure(tool_name, engine_id, "That tool isn't available.")

    # --- 4 and 5. Load the engine, confirm ownership, then fetch and infer. ---
    try:
        engine = db.get(Engine, engine_id)
        if engine is None:
            raise EngineNotFoundError(engine_id)
        # The layer that actually *guarantees* the ownership property. The
        # allowlist above only buys a uniform message and an earlier exit.
        assert_engine_access(user, engine)
        result = definition.handler(db, engine_id)
    except EngineNotFoundError:
        # Reachable only on the unrestricted (engineer) path -- a scoped caller
        # was already turned away above -- so naming the id is safe here.
        return _failure(tool_name, engine_id, f"Engine {engine_id} isn't in the system.")
    except NoSensorDataError:
        return _failure(
            tool_name, engine_id, f"No sensor data is recorded for engine {engine_id}."
        )
    except HTTPException:
        # The 403 from assert_engine_access, converted to the *same* sentence
        # the allowlist uses so the two layers look identical from outside.
        return _failure(tool_name, engine_id, OUT_OF_SCOPE_MESSAGE)

    # --- 6. Success. ---
    return {
        "tool": tool_name,
        "engine_id": engine_id,
        # Top-level and named `engine_label`, never `label`: degradation_stage
        # already returns a "label" key holding the health stage, so nesting
        # this or renaming it would overwrite the answer. Echoing the
        # authoritative name is also what makes a mis-resolved label visible --
        # the allowlist cannot stop the model picking the wrong engine among
        # ones the customer *does* own; naming it in the reply can.
        "engine_label": engine.label,
        "ok": True,
        "result": result,
    }
