"""Single source of truth for user roles and which tools each role may call.

This module deliberately imports nothing from the rest of the project.
`app.auth` needs it to validate the role on a JWT, and `app.tools` needs it to
resolve a role to a list of tool schemas -- if it lived in either package, the
two would have to import each other.

Adding a new role (e.g. "technician") is a one-line change here. Note that a
role appearing in this map only controls which tools it is *offered*; per-engine
ownership is enforced separately in `app.auth.dependencies.assert_engine_access`,
which denies any role it doesn't explicitly recognise.
"""

ROLE_TOOL_MAP: dict[str, list[str]] = {
    "engineer": ["predict_rul", "anomaly_score", "degradation_stage"],
    "customer": ["degradation_stage"],
    "technician": [],  # reserved -- scoped later, currently grants nothing
}

VALID_ROLES = set(ROLE_TOOL_MAP)
