"""Resolve a role to the tool declarations that role is allowed to be offered.

This is the **only** place a role becomes a list of tool schemas. The agent
pipeline calls it *before* building a model request, so a customer's request
body simply does not contain the `predict_rul` / `anomaly_score` declarations
at all -- rather than containing them alongside an instruction not to use them.
Gating at the tool-list level instead of the prompt level is the difference
between a rule the model could be talked out of and a capability it never had.

`app.tools.registry.dispatch_tool_call` re-checks the same map at execution
time. That is intentional duplication: this function decides what is *offered*,
that one decides what may *run*, and neither trusts the other to have happened.
"""

from app.roles import ROLE_TOOL_MAP
from app.tools.registry import TOOL_REGISTRY


def get_tools_for_role(role: str) -> list[dict]:
    """Return the function declarations to offer a caller with this role.

    Args:
        role: a key of `ROLE_TOOL_MAP` (validated at login by
            `app.auth.dependencies.get_current_user`, which 401s on anything
            unrecognised, so an unknown role should not reach here).

    Returns:
        The tool specs, in `ROLE_TOOL_MAP` order. Empty for a role with no
        tools -- "technician" today. Callers must treat these dicts as
        read-only; they are the registry's own module-level spec objects, not
        copies, so mutating one would change the tool for every later request.

    Raises:
        KeyError: the role is not in `ROLE_TOOL_MAP`. Deliberately not
            softened to an empty list: an unrecognised role reaching this point
            is a bug upstream, and returning "no tools" would hide it behind a
            chat assistant that merely seems unhelpful.
    """
    return [TOOL_REGISTRY[name].spec for name in ROLE_TOOL_MAP[role]]
