"""The engineer chat turn: route to tools, run them, then summarise.

Engineers get the full tool set and the whole fleet, so this module is the
simple half of the pipeline -- there is no roster to build and no allowlist to
compute. `allowed_engine_ids=None` (unrestricted) is correct here and **only**
here; the customer path in `app.agents.intake` always passes a real set.

Shape of a turn:

    history + message -> routing model (with tools)
                      -> every emitted call executed
                      -> communicator (no tools) -> reply

with one shortcut: if the routing model answers in text without calling
anything -- a clarifying question, small talk -- that text is returned as-is.
Sending it through the communicator would cost a second model call to reword a
sentence that is already the answer, and on a free tier metered per minute that
call is not free.
"""

import logging

from sqlalchemy.orm import Session

from app import config
from app.agents import communicator, gemini_client, tool_loop
from app.agents.prompts import ORCHESTRATOR_SYSTEM
from app.db.models import User
from app.tools.access import get_tools_for_role

logger = logging.getLogger(__name__)

# Shown when the routing model runs out of rounds without producing text and
# without producing a single result to summarise. Distinct from the busy
# message: nothing was rate-limited, the model just never converged.
_NO_ANSWER_FALLBACK = (
    "I wasn't able to work out what to check for that. "
    "Could you rephrase it, naming the engine number?"
)


def run_engineer_turn(
    user: User,
    db: Session,
    history: list[dict],
    message: str,
) -> dict:
    """Run one engineer chat turn.

    Args:
        user: the authenticated engineer.
        db: an open SQLAlchemy session.
        history: prior messages from `app.agents.session_store.get_history`.
        message: the new user message.

    Returns:
        `{"reply": str, "tool_calls": list[dict]}`. `tool_calls` is every
        dispatch result from the turn, which Task 7's UI renders as the
        "what was actually checked" panel -- so it is returned even when it is
        empty, rather than omitted.

    Raises:
        AssistantBusyError: Gemini stayed unavailable through every retry. Left
            for the route layer to convert into a user-visible message, since
            only it knows whether this is an HTTP response or something else.
    """
    contents = gemini_client.build_contents(history, message)
    tools = gemini_client.build_tools(get_tools_for_role("engineer"))

    final_text, tool_results = tool_loop.run_tool_loop(
        model=config.GEMINI_MODEL_ROUTING,
        system_prompt=ORCHESTRATOR_SYSTEM,
        contents=contents,
        tools=tools,
        db=db,
        user=user,
        # Unrestricted -- engineers see the whole fleet. This is the only call
        # site in the codebase allowed to pass None.
        allowed_engine_ids=None,
    )

    if not tool_results:
        # No tool ran, so there is nothing to summarise. Return the model's own
        # text and skip the second call entirely.
        reply = (final_text or "").strip() or _NO_ANSWER_FALLBACK
        return {"reply": reply, "tool_calls": []}

    reply = communicator.summarize(
        role="engineer",
        original_message=message,
        tool_results=tool_results,
    )
    return {"reply": reply, "tool_calls": tool_results}
