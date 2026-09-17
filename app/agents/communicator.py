"""The final, user-facing writing step -- one model call, no tools.

Every reply a user reads is written here, by a call that has **no tools
attached at all**. That is the whole point of splitting this out from routing:
this call cannot fetch anything, cannot reach an engine, and cannot widen what
the turn looked at. It can only phrase what already happened.

It also means the customer tone rules live in exactly one place. Customer
replies -- results, refusals, and small talk alike -- all go through
`COMMUNICATOR_CUSTOMER_SYSTEM`, so the plain-language filter can't be bypassed
by a turn that happened to take an unusual path.
"""

import json
import logging

from google.genai import types

from app import config
from app.agents import gemini_client
from app.agents.prompts import (
    COMMUNICATOR_CUSTOMER_SYSTEM,
    COMMUNICATOR_ENGINEER_SYSTEM,
)

logger = logging.getLogger(__name__)

_SYSTEM_BY_ROLE = {
    "engineer": COMMUNICATOR_ENGINEER_SYSTEM,
    "customer": COMMUNICATOR_CUSTOMER_SYSTEM,
}

# Shown if the model returns an empty body. Rare, but an empty chat bubble
# reads as a crash, and a customer must never be left thinking their engine
# came back clean when nothing was actually said.
_EMPTY_REPLY_FALLBACK = (
    "Sorry, I couldn't put together a reply for that. Please try asking again."
)


def summarize(
    role: str,
    original_message: str,
    tool_results: list[dict],
    available_engines: list[str] | None = None,
) -> str:
    """Turn raw tool results into the reply the user actually reads.

    Args:
        role: `"engineer"` or `"customer"`; selects the system prompt and so
            the entire register of the reply.
        original_message: what the user asked, so the reply answers *that*
            question rather than just narrating the JSON.
        tool_results: every dispatch result from the turn -- successes and
            refusals together, in the order they ran. May be empty.
        available_engines: the caller's engine **labels**, passed on the
            customer path. Not optional in spirit: the customer prompt is told
            to name the engines that *are* on the account whenever it turns one
            down, and without this it has no way to know them.

            Found by testing rather than by reading: asked about an engine they
            did not own, the routing model correctly refused without calling a
            tool, so `tool_results` was empty -- and the communicator, told to
            list their engines but given nothing to list, **invented "Engine
            C"** for a customer who owns only A and B. An instruction the model
            cannot satisfy truthfully gets satisfied untruthfully. Labels only,
            never ids: this list is rendered into text a customer reads.

    Returns:
        The reply text. Never empty; falls back to a fixed sentence.

    Raises:
        AssistantBusyError: propagated from `gemini_client.generate` when
            Gemini stays unavailable. The caller decides what to show.
        KeyError: unknown role. Not softened to a default -- picking the
            engineer prompt for an unrecognised role would leak cycle counts
            and reconstruction errors at whoever that role turns out to be.

    The results are passed as JSON in the user turn rather than pre-formatted
    prose because the shape carries meaning the model needs: `ok` false versus
    true, and `engine_label` sitting next to each result so multi-engine
    answers can be named correctly. Note `engine_id` is present in that JSON on
    the customer path too -- the prompt, not the payload, is what keeps numeric
    ids out of customer-facing text. Stripping them here would also strip the
    engineer path's ability to name engines, and the label is what a customer
    sees regardless.
    """
    system_prompt = _SYSTEM_BY_ROLE[role]

    payload: dict = {
        "user_message": original_message,
        "tool_results": tool_results,
    }
    if available_engines is not None:
        # Named so the prompt can point at it as the ONLY permitted source of
        # engine names. Included even when it is the empty list -- "you have
        # none" is information, and omitting the key would put the model back
        # in the position of guessing.
        payload["engines_on_this_account"] = available_engines
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=json.dumps(payload, indent=2, default=str))],
        )
    ]

    response = gemini_client.generate(
        system_prompt=system_prompt,
        contents=contents,
        # No tools. Not an oversight -- see the module docstring.
        tools=None,
        model=config.GEMINI_MODEL_COMMUNICATOR,
    )

    text = (response.text or "").strip()
    if not text:
        logger.warning("Communicator returned empty text for role=%s", role)
        return _EMPTY_REPLY_FALLBACK
    return text
