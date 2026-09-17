"""The two chat endpoints -- one per role, never one shared endpoint.

`POST /api/chat/engineer` and `POST /api/chat/customer` do the same four things
(load history, run the turn, record the turn, return the reply) but they are
separate routes with separate role gates and **separate response shapes**, and
that separation is the point:

* The engineer response carries `tool_calls` -- the raw dispatch results, with
  numeric engine ids and model output in them. The customer response carries
  `reply` and nothing else.
* Because they are two functions, there is no `if role == "customer"` branch
  that could ever be got wrong. A customer reaching the engineer route is
  stopped by `require_role` before any code runs; there is no code path in
  which the engineer payload is built for a customer at all.

The agent modules mirror that split (`run_engineer_turn` returns
`{"reply", "tool_calls"}`, `run_customer_turn` returns `{"reply"}`), so the two
halves of the contract disagree loudly rather than silently if anyone tries to
merge them later.

**History is only recorded on success.** If a turn raises, nothing is appended:
a stored question with no answer under it would make the *next* turn's prompt
read as though the assistant ignored the user, and the model tends to apologise
for the earlier "silence" instead of answering.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.agents import session_store
from app.agents.gemini_client import AssistantBusyError
from app.agents.intake import run_customer_turn
from app.agents.orchestrator import run_engineer_turn
from app.auth.dependencies import require_role
from app.db.base import get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Built once at import rather than per route. `require_role` is a factory, so
# calling it inline in two decorators would build two equivalent dependencies
# and FastAPI would treat them as distinct entries in the dependency cache.
require_engineer = require_role("engineer")
require_customer = require_role("customer")

# A ceiling on one message, in characters. Not a security control -- it is
# quota protection. The free tier is metered per minute on tokens as well as
# requests, and one turn costs at least two model calls, so a pasted logfile
# would burn the budget for every other user of the demo. ~2000 characters is
# far more than any question this app can usefully answer.
MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    """The request body for both chat routes: `{"message": "..."}`."""

    message: str = Field(..., max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Reject an empty or whitespace-only message with a 422.

        Caught here rather than in the agent layer because a blank message
        would still cost two model calls to be told there was no question --
        real requests against a per-minute quota, spent on nothing.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


@router.post("/engineer")
def chat_engineer(
    body: ChatRequest,
    user: User = Depends(require_engineer),
    db: Session = Depends(get_db),
) -> dict:
    """Run one engineer chat turn.

    Returns:
        `{"reply": str, "tool_calls": [...]}`. Each entry in `tool_calls` is a
        dispatch result: `tool`, `engine_id`, `engine_label`, `ok`, and then
        either `result` (on success) or `error` (on refusal). The list is
        returned even when empty -- a clarifying question legitimately runs no
        tools, and Task 7's "what I checked" panel needs to distinguish "ran
        nothing" from "the field is missing".

    Deliberately **no `response_model`**: FastAPI would filter each tool-call
    dict down to the declared fields, and these dicts vary by outcome
    (`result` xor `error`) and by tool (`predicted_rul` vs the anomaly
    payload). Declaring a model here would silently drop the error strings that
    make a refused engine visible in the UI.
    """
    history = session_store.get_history(user.id)

    try:
        result = run_engineer_turn(
            user=user, db=db, history=history, message=body.message
        )
    except AssistantBusyError as exc:
        # Gemini was rate-limited or down through every retry. This is an
        # expected condition on the free tier, not a server fault, so it comes
        # back as a normal 200 chat reply -- `str(exc)` is already a
        # user-safe sentence ("The assistant is busy..."). Returning a 500
        # here would show the user an error page for something a retry in a
        # few seconds fixes, and would waste the polite wording the retry
        # logic exists to produce.
        #
        # Known residual: `tool_calls` is empty even when the failure came
        # from the *communicator* call, i.e. after every tool already ran
        # successfully -- those results are lost, because the exception is
        # raised out of `run_engineer_turn` and carries nothing with it.
        # The user sees "busy, try again" with an empty "what I checked"
        # panel, which is honest about the turn producing no answer but
        # under-reports the work done. Fixing it properly means having the
        # agent layer attach its partial results to the exception, which is
        # `app/agents/`'s call to make, not this route's.
        logger.warning("Engineer turn for user %s hit a busy assistant", user.id)
        return {"reply": str(exc), "tool_calls": []}

    session_store.append_turn(user.id, body.message, result["reply"])
    return result


@router.post("/customer")
def chat_customer(
    body: ChatRequest,
    user: User = Depends(require_customer),
    db: Session = Depends(get_db),
) -> dict:
    """Run one customer chat turn.

    Returns:
        `{"reply": str}` -- the plain-language answer and nothing else. No tool
        detail, no numeric engine ids, no internal state. The engines a
        customer may reach are resolved server-side from the database inside
        `run_customer_turn`; nothing about scope is read from this request.
    """
    history = session_store.get_history(user.id)

    try:
        result = run_customer_turn(
            user=user, db=db, history=history, message=body.message
        )
    except AssistantBusyError as exc:
        logger.warning("Customer turn for user %s hit a busy assistant", user.id)
        return {"reply": str(exc)}

    session_store.append_turn(user.id, body.message, result["reply"])
    return result
