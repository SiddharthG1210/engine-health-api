"""Plain exceptions raised by the data-access layer.

Deliberately *not* `fastapi.HTTPException`. This layer only knows that it
couldn't produce data; it does not know whether the caller is an HTTP route
(which wants a status code) or a tool dispatcher feeding results back to an LLM
(which wants a sentence the model can read out and carry on from). Choosing a
status code here would force the second caller to catch and unwrap an HTTP
object it never wanted -- so the decision is left to whoever catches these.

In practice `app.tools.registry.dispatch_tool_call` catches both and converts
them into `{"ok": False, "error": ...}` payloads.
"""


class DataAccessError(Exception):
    """Base class, so a caller can catch every fetcher failure in one clause."""


class EngineNotFoundError(DataAccessError):
    """No `engines` row exists for the requested engine id."""

    def __init__(self, engine_id: int):
        self.engine_id = engine_id
        super().__init__(f"Engine {engine_id} does not exist")


class NoSensorDataError(DataAccessError):
    """The engine exists but has no `engine_cycles` rows to build a window from.

    Distinct from `EngineNotFoundError` on purpose: this one means the demo
    seed is incomplete for that engine, not that the caller named a bad id.
    """

    def __init__(self, engine_id: int):
        self.engine_id = engine_id
        super().__init__(f"Engine {engine_id} has no sensor data")
