"""Function declarations for the three ML tools, in `google-genai`'s shape.

These are plain dicts rather than `google.genai.types.FunctionDeclaration`
objects so that nothing outside `app.agents` has to import the SDK; the SDK
accepts this dict form directly (`types.Tool(function_declarations=[spec])`)
and coerces the lowercase JSON-schema type names itself.

Two conventions worth keeping:

* **Every tool takes only `engine_id`.** The model never sees, constructs, or
  passes sensor arrays -- it picks a target, and Python owns the fetch, the
  shaping, and the scaling. That keeps a hallucinated number from ever becoming
  model input, and keeps the function-call payloads small.
* **The description does the routing.** There is no separate classifier
  deciding which tool answers a question; the routing model reads these
  strings. So they are written behaviourally ("use for ... questions") rather
  than as restatements of the function name.
"""

# Reused by all three specs: the single required argument, described in terms
# of what the model is choosing rather than what the DB stores.
_ENGINE_ID_PARAM = {
    "type": "object",
    "properties": {
        "engine_id": {
            "type": "integer",
            "description": "The numeric id of the engine to analyse.",
        }
    },
    "required": ["engine_id"],
}

PREDICT_RUL_SPEC: dict = {
    "name": "predict_rul",
    "description": (
        "Predicts remaining useful life (RUL) in operating cycles for one "
        "engine, from its recent sensor history. Use for 'how long does it "
        "have left' and maintenance-scheduling questions."
    ),
    "parameters": _ENGINE_ID_PARAM,
}

ANOMALY_SCORE_SPEC: dict = {
    "name": "anomaly_score",
    "description": (
        "Checks whether one engine's most recent sensor reading looks abnormal "
        "versus healthy engines. Use for 'is anything wrong right now' "
        "questions."
    ),
    "parameters": _ENGINE_ID_PARAM,
}

DEGRADATION_STAGE_SPEC: dict = {
    "name": "degradation_stage",
    "description": (
        "Classifies one engine's current wear stage as Healthy, Warning, or "
        "Critical. Use for general health or status questions."
    ),
    "parameters": _ENGINE_ID_PARAM,
}
