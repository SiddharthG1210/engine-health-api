"""Engineer-only ground-truth routes: the three ML tools, with no LLM involved.

These exist to answer "is the chat reply actually right?". A chat answer passes
through a routing model and a communicator model before a human reads it, so
when a reply looks wrong there is no way to tell whether the *model* misread
something or the *prediction* itself is off. These routes call the same
`dispatch_tool_call` the chat pipeline calls, with the same fetchers and the
same weights, and skip both model calls -- so the number they return is the
number the chat turn was working from.

They also **replace** the original unauthenticated `/predict-rul`,
`/anomaly-score` and `/degradation-stage` endpoints from the old root
`main.py`. Those are not restored anywhere: re-exposing raw fleet-wide
inference without auth would make every access control in this project
decorative, since anyone could read any engine by number.

**The one dangerous line in this file is `allowed_engine_ids=None`.** That is
the unrestricted setting, and it is the only place in the codebase outside
`orchestrator.py` that uses it. It is safe here for exactly one reason: every
route below is gated by `require_role("engineer")`, and engineers are entitled
to the whole fleet. If these routes are ever opened to another role, that
`None` becomes full-fleet access for that role, and the only thing left
standing between it and another customer's data is `assert_engine_access`
inside dispatch. Change the gate and you must change the scope in the same
commit.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.db.base import get_db
from app.db.models import User
from app.tools.registry import dispatch_tool_call

router = APIRouter(prefix="/api/debug", tags=["debug"])

# One factory call shared by all three routes -- see the note in chat_routes.
require_engineer = require_role("engineer")


class EngineRequest(BaseModel):
    """`{"engine_id": 34}` -- the same single argument the model-facing tool
    specs take, so these routes exercise the identical code path."""

    engine_id: int


def _dispatch(tool_name: str, engine_id: int, db: Session, user: User) -> dict:
    """Call one tool directly and hand back its dict, verbatim.

    A failed call (unknown engine, no sensor data) comes back as HTTP **200**
    with `ok: false` and an `error` string, not as a 4xx. That is intentional:
    these routes mirror the tool contract rather than REST semantics, so what
    a tester reads here is byte-for-byte what the model was handed for the same
    question. Translating it into an HTTP error would mean comparing two
    different shapes.
    """
    return dispatch_tool_call(
        tool_name,
        {"engine_id": engine_id},
        db,
        user,
        # Unrestricted -- see the module docstring. Safe only because of the
        # require_engineer gate on every route in this router.
        allowed_engine_ids=None,
    )


@router.post("/predict-rul")
def debug_predict_rul(
    body: EngineRequest,
    user: User = Depends(require_engineer),
    db: Session = Depends(get_db),
) -> dict:
    """Remaining useful life, in cycles, from the engine's last 30 cycles."""
    return _dispatch("predict_rul", body.engine_id, db, user)


@router.post("/anomaly-score")
def debug_anomaly_score(
    body: EngineRequest,
    user: User = Depends(require_engineer),
    db: Session = Depends(get_db),
) -> dict:
    """Autoencoder reconstruction error on the engine's latest single cycle."""
    return _dispatch("anomaly_score", body.engine_id, db, user)


@router.post("/degradation-stage")
def debug_degradation_stage(
    body: EngineRequest,
    user: User = Depends(require_engineer),
    db: Session = Depends(get_db),
) -> dict:
    """Healthy / Warning / Critical for the engine, plus the RUL behind it.

    This is the route to cross-check a customer chat reply against: the
    customer path is limited to this same tool, so the stage here is the stage
    their answer was written from.
    """
    return _dispatch("degradation_stage", body.engine_id, db, user)
