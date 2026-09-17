"""The customer chat turn: scoped to the engines they own, in plain language.

This is the security-relevant half of the pipeline. Three properties hold here,
and each is enforced by Python rather than by asking the model nicely:

1. **The roster comes only from the database.** `Engine.customer_id == user.id`
   and nothing else -- not the request body, not the chat history, not anything
   the model produced on a previous turn. That single rule is what makes the
   allowlist trustworthy; every other guarantee here is downstream of it.
2. **The model may name an engine; Python decides whether it can be reached.**
   The roster in the system prompt lets the model resolve "Engine B" to an id,
   which is safe precisely because `dispatch_tool_call` then checks that id
   against the same set the roster was built from. A wrong resolution produces
   a refusal, never another customer's data.
3. **A refused engine is never swapped for one they do own.** No "did you
   mean", no falling back to the lowest id. That helpful-looking retry is
   exactly the bug Amendment A exists to kill: a customer owning engines 31 and
   39 asking about 39 must never be answered about 31.

The reply always goes through the communicator -- results, refusals and small
talk alike -- so the plain-language filter can't be skipped by a turn that
happened to take an unusual route.
"""

import logging

from sqlalchemy.orm import Session

from app import config
from app.agents import communicator, gemini_client, prompts, tool_loop
from app.db.models import Engine, User
from app.roles import ROLE_TOOL_MAP
from app.tools.access import get_tools_for_role
from app.tools.registry import NO_ENGINE_MESSAGE

logger = logging.getLogger(__name__)

# This module hard-codes the assumption that a customer is offered exactly the
# one health tool: `sole_engine_id` filling and the single-tool wording in
# INTAKE_SYSTEM both depend on it. If ROLE_TOOL_MAP ever grants customers a
# second tool, both need another look -- so fail loudly at import rather than
# discovering it in something a customer reads. A raise, not an `assert`, so
# `python -O` cannot strip it.
if ROLE_TOOL_MAP["customer"] != ["degradation_stage"]:
    raise RuntimeError(
        "app.agents.intake assumes customers get exactly ['degradation_stage']; "
        f"ROLE_TOOL_MAP says {ROLE_TOOL_MAP['customer']}. Revisit this module."
    )

# Returned without any model call when the account has no engines linked.
NO_ENGINES_REPLY = (
    "There are no engines linked to your account yet. "
    "Once one is added, you'll be able to check its health here."
)


def _load_roster(db: Session, user: User) -> list[Engine]:
    """Load the engines this customer owns, in a stable order.

    `.all()`, not `.one_or_none()` -- owning several engines is now the normal
    case (see `customer4` in the demo seed), and `one_or_none` *raises* on
    multiple rows.

    `order_by(engine_id)` so the roster reads identically every turn. An
    unordered query is free to return rows in a different order between calls,
    which would let "Engine A" mean different things across two turns of the
    same conversation.
    """
    return (
        db.query(Engine)
        .filter(Engine.customer_id == user.id)
        .order_by(Engine.engine_id)
        .all()
    )


def _roster_lines(engines: list[Engine]) -> list[str]:
    """Render the roster block for the system prompt: `- Engine A (id 31)`.

    Both parts are needed: the tool takes an id, the customer speaks in labels.

    Falls back to `Engine {id}` when `label` is NULL so a mis-seeded row puts
    something sensible in the prompt rather than the literal string "None",
    which the model would happily read back out to the customer.
    """
    return [
        f"- {engine.label or f'Engine {engine.engine_id}'} (id {engine.engine_id})"
        for engine in engines
    ]


def run_customer_turn(
    user: User,
    db: Session,
    history: list[dict],
    message: str,
) -> dict:
    """Run one customer chat turn.

    Args:
        user: the authenticated customer.
        db: an open SQLAlchemy session.
        history: prior messages from `app.agents.session_store.get_history`.
        message: the new user message.

    Returns:
        `{"reply": str}` and nothing else. No tool detail, no engine ids, no
        internal state reaches a customer -- deliberately a different shape
        from `run_engineer_turn`'s, so a route handler cannot accidentally
        render the engineer's diagnostics panel for a customer.

    Raises:
        AssistantBusyError: Gemini stayed unavailable through every retry.
    """
    engines = _load_roster(db, user)

    # Zero engines: answer from Python. No model call, no dispatch, no roster
    # sent to a model that would then have to be trusted not to invent one.
    # (Belt and braces: if dispatch were somehow reached, `frozenset()` denies
    # everything -- see the `is not None` rule in app.tools.registry.)
    if not engines:
        logger.info("Customer %s has no engines linked", user.id)
        return {"reply": NO_ENGINES_REPLY}

    allowed_engine_ids = frozenset(engine.engine_id for engine in engines)
    system_prompt = prompts.build_intake_system(_roster_lines(engines))

    # Exactly one engine owned -- remember its id so a call that omits
    # `engine_id` can be completed from the roster rather than guessed at.
    sole_engine_id = engines[0].engine_id if len(engines) == 1 else None
    engine_labels = [engine.label or f"Engine {engine.engine_id}" for engine in engines]

    def pre_dispatch(tool_name: str, args: dict) -> dict | None:
        """Apply the customer-specific argument rules before a call dispatches.

        Returning None lets the call go through to `dispatch_tool_call`;
        returning a dict short-circuits it with that dict as the result.

        This lives here rather than inside `dispatch_tool_call` on purpose:
        dispatch stays dumb and role-agnostic, and every rule that depends on
        *who is asking* sits in that role's own module where it can be read in
        one place.
        """
        has_engine_id = args.get("engine_id") is not None

        if not has_engine_id and sole_engine_id is not None:
            # One engine on the account, so "how's my engine?" is unambiguous.
            # Safe to fill in because the value comes from the roster -- the
            # model contributed nothing to it.
            args["engine_id"] = sole_engine_id
            return None

        if not has_engine_id:
            # Several engines and no target named. Do NOT guess, and do NOT
            # default to the lowest id. Skip the call and record a synthetic
            # refusal, which the communicator turns into "which one did you
            # mean -- Engine A or Engine B?". Handling it here instead of
            # hoping the model asks costs nothing and is deterministic.
            #
            # It rides the normal tool_results list, so communicator.summarize
            # needs no special case and its signature is unchanged.
            logger.info(
                "Customer %s called %s with no engine_id and owns %d engines",
                user.id,
                tool_name,
                len(engines),
            )
            return {
                "tool": tool_name,
                "engine_id": None,
                "engine_label": None,
                "ok": False,
                "error": NO_ENGINE_MESSAGE,
                # The communicator needs the options to offer; labels only,
                # never ids, since this string reaches a customer.
                "available_engines": engine_labels,
            }

        # An engine was named. Let dispatch decide -- if it is one of theirs
        # the allowlist passes and the result carries `engine_label`; if it is
        # not, dispatch returns "That engine isn't on your account." and we do
        # nothing to soften or retry that.
        return None

    contents = gemini_client.build_contents(history, message)
    tools = gemini_client.build_tools(get_tools_for_role("customer"))

    final_text, tool_results = tool_loop.run_tool_loop(
        model=config.GEMINI_MODEL_ROUTING,
        system_prompt=system_prompt,
        contents=contents,
        tools=tools,
        db=db,
        user=user,
        # Always a real set for a customer, never None. Empty is impossible
        # here -- the no-engines case returned above -- but an empty set would
        # deny everything anyway, which is the correct failure direction.
        allowed_engine_ids=allowed_engine_ids,
        pre_dispatch=pre_dispatch,
    )

    # Everything goes through the communicator, including plain text with no
    # tool results. That keeps the tone consistent across small talk, results
    # and refusals -- and stops the refusal wording drifting into something
    # more informative than intended, which is the thing the identical
    # "isn't on your account" sentence exists to prevent.
    reply = communicator.summarize(
        role="customer",
        original_message=message,
        tool_results=tool_results,
        # The roster reaches the communicator too, not just the routing model.
        # Without it, a turn the router refused on its own arrives with empty
        # tool_results and the communicator is asked to name engines it has
        # never been told about -- which is how it once produced an "Engine C"
        # for a customer owning exactly A and B. Labels only; ids never reach
        # customer-facing text.
        available_engines=engine_labels,
    )
    return {"reply": reply}
