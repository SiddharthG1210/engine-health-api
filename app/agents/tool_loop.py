"""The shared "execute every call the model asked for" loop.

Both roles run this. Engineer and customer differ only in three arguments --
system prompt, tool list, and `allowed_engine_ids` -- so there is one copy of
the loop rather than two near-identical ones in `orchestrator.py` and
`intake.py`. That is a deliberate call: the engine allowlist threads through
here, so one copy means one place to audit and one place where "every call
really did execute" is verified. Two copies drift, and the half that drifts is
the security-relevant half.

What the loop does each round:

1. Ask the model, with tools attached.
2. If it returned no function calls, stop -- its text is the answer.
3. Otherwise run **every** call it emitted (not just the first), through
   `dispatch_tool_call`, which returns refusals rather than raising.
4. Append the model's own turn plus one function response per call, and loop.

Two things about step 3 are easy to get subtly wrong and are guarded here:

* **Every** call runs. `gemini-3.6-flash` was observed emitting six parallel
  calls for "full status report on engines 10 and 11"; handling only the first
  would answer one sixth of the question and look like the model's fault.
* Each function response echoes the originating call's `id`. Parallel calls to
  the *same* tool for *different* engines are distinguishable only by that id,
  so dropping it is exactly how two engines' results blur into one -- the bug
  Amendment A exists to prevent.
"""

import logging

from google.genai import types
from sqlalchemy.orm import Session

from app.agents import gemini_client
from app.db.models import User
from app.tools.registry import dispatch_tool_call

logger = logging.getLogger(__name__)

# Budget for one chat turn. Both caps are needed, and they cap different
# things: "full status report on engines 10 and 11" is legitimately six calls.
# If the model emits all six at once that is ONE round; if it works through
# them one at a time it is SIX rounds. A round cap alone would silently
# truncate a valid question in the second case, and a call cap alone would let
# a confused model ping-pong forever in the first.
MAX_TOOL_CALLS = 10
MAX_ROUNDS = 5

# Returned to the model when the budget runs out mid-turn, so it writes an
# honest partial answer instead of pretending the remaining engines came back.
BUDGET_EXHAUSTED_MESSAGE = (
    "Call limit reached for this message; this check was not run."
)


def _function_response_part(
    call: types.FunctionCall, payload: dict
) -> types.Part:
    """Package one dispatch result as the function response the model reads.

    `id` is echoed from the call it answers. With parallel calls to the same
    tool for different engines, the name alone is ambiguous -- verified against
    the live API: three `degradation_stage` calls came back correctly attributed
    per engine only with the ids matched up.

    The whole result dict is handed over, including `engine_id` and
    `engine_label`. That redundancy is intentional: the identity of the engine
    travels *inside* the payload as well as in the id, so an attribution mistake
    would have to happen twice to be invisible.
    """
    return types.Part(
        function_response=types.FunctionResponse(
            name=call.name,
            id=call.id,
            response=payload,
        )
    )


def run_tool_loop(
    model: str,
    system_prompt: str,
    contents: list[types.Content],
    tools: list[types.Tool] | None,
    db: Session,
    user: User,
    allowed_engine_ids: frozenset[int] | None,
    pre_dispatch=None,
) -> tuple[str | None, list[dict]]:
    """Drive the model until it stops asking for tools, running every call.

    Args:
        model: the Gemini model id for the routing call.
        system_prompt: `ORCHESTRATOR_SYSTEM`, or the rendered customer intake
            prompt from `prompts.build_intake_system`.
        contents: the conversation so far. **Mutated in place** as rounds are
            appended -- callers pass a list they own, not shared state.
        tools: from `gemini_client.build_tools`, or None for a role with none.
        db: an open SQLAlchemy session.
        user: the authenticated caller.
        allowed_engine_ids: the engine ids this caller may reach. `None` means
            unrestricted and is the **engineer path only**; a customer always
            passes their owned set, even when it is empty. Passed straight
            through to `dispatch_tool_call`, which is where it is enforced.
        pre_dispatch: optional `(tool_name, args) -> dict | None` hook, applied
            to each call's arguments *before* dispatch. Returning a dict short-
            circuits that call with that dict as its result; returning None
            lets it proceed. This is how `intake.py` fills in the engine id for
            a single-engine customer, and how it refuses to guess for a
            multi-engine one -- role-specific policy stays in the role's own
            module, and `dispatch_tool_call` stays dumb and role-agnostic.

    Returns:
        `(final_text, tool_results)`. `final_text` is the model's text when it
        finished without asking for more tools, else None. `tool_results` is
        every dispatch result from the turn, in the order they ran.

    Note there is **no dedupe** of repeated `(tool, engine_id)` pairs. It was
    considered and left out: the caps already bound the budget, and a dedupe
    that ever collapsed two calls differing only by engine would resurrect the
    exact bug Amendment A exists to kill. The cheap protection is not worth
    owning that risk.
    """
    tool_results: list[dict] = []
    calls_made = 0

    for round_number in range(1, MAX_ROUNDS + 1):
        response = gemini_client.generate(
            system_prompt=system_prompt,
            contents=contents,
            tools=tools,
            model=model,
        )

        calls = response.function_calls or []
        if not calls:
            # The model answered in text: a clarifying question, small talk, or
            # its wrap-up after the last round of results. Either way, done.
            return response.text, tool_results

        # The model's own turn must be appended verbatim, straight off the
        # response. Gemini 3 embeds thought signatures in these parts and the
        # API rejects the follow-up call if they are missing, so do not
        # reconstruct this Content by hand.
        contents.append(response.candidates[0].content)

        response_parts: list[types.Part] = []
        for call in calls:
            args = dict(call.args or {})

            if calls_made >= MAX_TOOL_CALLS:
                # Over budget. Still answer the call -- with a refusal, so the
                # model knows this engine went unchecked rather than assuming
                # silence means healthy.
                logger.warning(
                    "Tool-call budget (%d) exhausted for user %s; refusing %s",
                    MAX_TOOL_CALLS,
                    user.id,
                    call.name,
                )
                result = {
                    "tool": call.name,
                    "engine_id": args.get("engine_id"),
                    "engine_label": None,
                    "ok": False,
                    "error": BUDGET_EXHAUSTED_MESSAGE,
                }
            else:
                # Refusals count against the budget too. A model that keeps
                # retrying a rejected engine id has to terminate somewhere, and
                # this counter is the thing that makes it terminate.
                calls_made += 1
                result = None
                if pre_dispatch is not None:
                    result = pre_dispatch(call.name, args)
                if result is None:
                    result = dispatch_tool_call(
                        call.name,
                        args,
                        db,
                        user,
                        # Keyword-only with no default upstream, on purpose:
                        # forgetting it is a TypeError, not silent full access.
                        allowed_engine_ids=allowed_engine_ids,
                    )

            tool_results.append(result)
            response_parts.append(_function_response_part(call, result))

        # All responses for a round go back in one user-role Content, which is
        # what the API expects for parallel calls.
        contents.append(types.Content(role="user", parts=response_parts))

        if calls_made >= MAX_TOOL_CALLS:
            # Budget spent. Another round could only produce more calls, and
            # every one of them would be refused -- so it would cost a model
            # call (5-15 per minute on the free tier) to learn nothing. Stop
            # here and let the communicator write the honest partial answer
            # from the results already collected.
            logger.warning(
                "Stopping tool loop for user %s: %d calls used in %d round(s)",
                user.id,
                calls_made,
                round_number,
            )
            return None, tool_results

    logger.warning(
        "Tool loop hit MAX_ROUNDS (%d) for user %s with %d calls made",
        MAX_ROUNDS,
        user.id,
        calls_made,
    )
    # Out of rounds with the model still asking for tools. Return None for the
    # text -- the communicator writes the reply from whatever results we have,
    # which is the honest partial answer.
    return None, tool_results
