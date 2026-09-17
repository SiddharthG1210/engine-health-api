"""A thin, retrying wrapper around `google.genai.Client`.

Everything in `app.agents` reaches the Gemini API through `generate()` here, so
there is exactly one place that owns:

* **When the client is constructed.** Lazily, on first use -- never at import.
  `app.config.GEMINI_API_KEY` is legitimately absent while running the offline
  scripts (`python -m app.db.seed`, the ML sanity checks), and those import
  chains must not blow up just because a key hasn't been pasted in yet.
* **Retrying transient failures.** The free tier's per-minute limits are real
  and one chat turn costs at least two model calls (routing + communicator),
  more when the model works through engines a round at a time. Without a retry
  a burst of questions turns into raw 500s.
* **Converting a dead API into one clean, user-safe sentence.** Callers catch
  `AssistantBusyError` and show `BUSY_MESSAGE`; they never see an SDK exception.

Notes on the SDK, verified against **google-genai 2.19.0** with live calls
rather than from memory (the function-calling surface has moved between
versions, so re-check these if you bump the pin):

* Tools are declared as `types.Tool(function_declarations=[<plain dict>, ...])`.
  The SDK accepts the JSON-schema dicts in `app.tools.specs` as-is.
* Automatic function calling (AFC) is **explicitly disabled**. AFC only fires
  when you hand the SDK real Python callables, which we never do -- but leaving
  it on makes the SDK emit a "direct use of AFC is not recommended" warning on
  every single call. Disabling it also documents the intent: this codebase runs
  its own tool loop (`app.agents.tool_loop`) because it has to enforce the
  engine allowlist between the model asking and Python answering.
* The SDK already retries internally (3 attempts, 1s initial delay, doubling).
  The retry here sits *on top* of that and is deliberately slower -- an SDK
  retry handles a blip, this one handles a per-minute quota that needs several
  seconds to roll over.
"""

import logging
import random
import re
import time

from google import genai
from google.genai import errors, types

from app import config

logger = logging.getLogger(__name__)

# 429 = rate limited (free-tier RPM/TPM), 503 = model overloaded, 500 = a
# transient Google-side fault. All three are worth a second attempt; a 400 or
# 403 is our bug or a bad key and retrying only delays the real error.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})

MAX_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0
# Ceiling per sleep, so a 429 storm can't park an HTTP request for a minute.
MAX_BACKOFF_SECONDS = 20.0
# Ceiling on the *total* time spent sleeping across all attempts. This is what
# decides the app's answer to "quota exhausted": ride out a transient blip, but
# do not sit out a whole per-minute window. On the free tier a genuinely spent
# quota needs ~45s to roll over, and holding an HTTP request open that long is
# worse for the user than a quick, honest "busy, try again".
MAX_TOTAL_BACKOFF_SECONDS = 30.0

# The single sentence a user sees when Gemini stays unavailable. Kept here so
# the routing path, the customer path and the communicator all say the same
# thing, and so it never leaks a status code or a quota detail.
BUSY_MESSAGE = (
    "The assistant is busy at the moment. Please try that again in a few seconds."
)

# Google returns a suggested wait inside the 429 body as e.g. "retryDelay":
# "27s". Honouring it beats guessing, because per-minute quota windows are
# much longer than a doubling backoff would reach on its own.
_RETRY_DELAY_PATTERN = re.compile(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]")

_client: genai.Client | None = None


class AssistantBusyError(RuntimeError):
    """Gemini was unreachable or rate-limited after every retry.

    Carries `BUSY_MESSAGE` as its text so a caller can surface `str(exc)`
    directly without composing its own wording.
    """

    def __init__(self, message: str = BUSY_MESSAGE):
        super().__init__(message)


def get_client() -> genai.Client:
    """Return the process-wide `genai.Client`, building it on first use.

    Raises:
        RuntimeError: `GEMINI_API_KEY` is unset. Raised here rather than at
            import so the offline scripts keep working -- and the server-level
            check in `config.validate_runtime_config()` should have caught this
            at startup long before a request gets here.
    """
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set; add it to .env. "
                "(app.main calls config.validate_runtime_config() at startup "
                "so this should not be reachable from a running server.)"
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def build_tools(specs: list[dict]) -> list[types.Tool] | None:
    """Wrap function-declaration dicts into the SDK's `Tool` container.

    Args:
        specs: the read-only dicts from `app.tools.access.get_tools_for_role`.

    Returns:
        A one-element list holding every declaration, or **None** when `specs`
        is empty. None matters: passing `[types.Tool(function_declarations=[])]`
        for a role with no tools is a malformed request, whereas None is simply
        a normal text-only call.

    All declarations go in a single `Tool`, which is what the API expects for a
    set of ordinary functions; several `Tool` objects is the shape for mixing
    functions with built-ins like search, which this app doesn't do.
    """
    if not specs:
        return None
    return [types.Tool(function_declarations=specs)]


def build_contents(history: list[dict], message: str) -> list[types.Content]:
    """Turn stored history plus the new message into the SDK's `Content` list.

    Args:
        history: `app.agents.session_store` records, oldest first, each
            `{"role": "user"|"assistant", "content": str}`.
        message: what the user just sent, appended last.

    Returns:
        A fresh list, safe for `run_tool_loop` to append rounds onto -- the
        loop mutates what it is given, and the stored history must not grow
        function-call parts as a side effect.

    Gemini's role for the model's own turns is `"model"`, not `"assistant"`;
    the mapping is done here so `session_store` can stay in the more familiar
    chat vocabulary and know nothing about the SDK.
    """
    contents = [
        types.Content(
            role="model" if entry["role"] == "assistant" else "user",
            parts=[types.Part(text=entry["content"])],
        )
        for entry in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    return contents


def _retry_delay(exc: errors.APIError, backoff: float) -> float:
    """Decide how long to wait before the next attempt.

    Takes **the longer** of our exponential backoff and Google's own suggested
    `retryDelay`, then caps at `MAX_BACKOFF_SECONDS`.

    "The longer" rather than "Google's if present" is a fix for something
    observed live: on a spent quota Google returned `retryDelay` values of 1.3s
    and then 0.4s, both of which were optimistic -- retrying that soon simply
    burned two of the four attempts on responses that were still 429. Google's
    number is worth honouring when it asks for *more* time than we planned; it
    is not worth cutting the backoff short for.

    The delay is nested in the error payload's `details`, whose exact shape
    varies by error type, so a regex over the stringified details is
    deliberately more tolerant than walking a structure that may not be there.
    """
    suggested = 0.0
    match = _RETRY_DELAY_PATTERN.search(str(exc.details))
    if match:
        suggested = float(match.group(1))
    return min(max(backoff, suggested), MAX_BACKOFF_SECONDS)


def generate(
    system_prompt: str,
    contents: list[types.Content],
    tools: list[types.Tool] | None = None,
    model: str | None = None,
) -> types.GenerateContentResponse:
    """Make one Gemini call, retrying transient failures with backoff.

    Args:
        system_prompt: goes in `system_instruction`, never into `contents`.
            That separation is load-bearing for the customer path: the roster
            of engines a customer owns is rendered into the system prompt, and
            nothing the customer types shares a channel with it.
        contents: the conversation so far, as `types.Content` objects. For a
            tool follow-up this must include the model's **own** returned
            `candidates[0].content` object, unmodified -- Gemini 3 models embed
            thought signatures in those parts and rebuilding the turn by hand
            drops them, which the API rejects on the next call.
        tools: from `build_tools`, or None for a text-only call (the
            communicator always passes None -- it summarises, it never acts).
        model: defaults to `config.GEMINI_MODEL_ROUTING`.

    Returns:
        The raw SDK response. Callers read `.function_calls`, `.text`, and
        `.candidates[0].content` off it.

    Raises:
        AssistantBusyError: every attempt hit a retryable status.
        google.genai.errors.APIError: a non-retryable API error (bad key,
            malformed request). Deliberately **not** swallowed -- those are
            bugs to fix, not conditions to apologise for.
    """
    client = get_client()
    request_config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools,
        # See module docstring: we run our own loop, so AFC must stay off.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    backoff = INITIAL_BACKOFF_SECONDS
    total_slept = 0.0
    last_error: errors.APIError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return client.models.generate_content(
                model=model or config.GEMINI_MODEL_ROUTING,
                contents=contents,
                config=request_config,
            )
        except errors.APIError as exc:
            if exc.code not in RETRYABLE_STATUS_CODES:
                raise
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            delay = _retry_delay(exc, backoff)
            # Jitter so two chat turns that hit the same quota wall don't wake
            # up in lockstep and collide again on the retry.
            delay += random.uniform(0, 0.5)
            if total_slept + delay > MAX_TOTAL_BACKOFF_SECONDS:
                # Waiting longer would out-stay the budget. Give up now rather
                # than sleep a partial delay that is too short to help anyway.
                logger.warning(
                    "Gemini %s; total backoff budget (%.0fs) reached, giving up",
                    exc.code,
                    MAX_TOTAL_BACKOFF_SECONDS,
                )
                break
            logger.warning(
                "Gemini %s on attempt %d/%d; retrying in %.1fs",
                exc.code,
                attempt,
                MAX_ATTEMPTS,
                delay,
            )
            time.sleep(delay)
            total_slept += delay
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)

    logger.error(
        "Gemini unavailable after %d attempts (last status %s)",
        MAX_ATTEMPTS,
        last_error.code if last_error else "unknown",
    )
    raise AssistantBusyError()
