"""Per-user chat history, held in memory.

**History resets when the server restarts.** That is a deliberate tradeoff at
this stage, not an oversight: persisting chat turns means another table, a
retention decision, and a migration story, none of which the demo needs. It is
called out in Task 7's manual steps so a tester who reloads after a restart and
finds an empty thread knows it is by design.

What gets stored is only the **final** user message and the **final** reply per
turn -- never the intermediate function-call parts. Two reasons:

* Function calls carry ids and thought signatures that are valid only within
  the turn that produced them. Replaying stale ones into a later request is at
  best noise and at worst rejected by the API.
* A customer's history then contains no numeric engine ids and no raw tool
  output, so replaying it into the next turn's prompt cannot leak detail the
  role isn't meant to see.

Not thread-safe in any strong sense, and intentionally so. Under uvicorn's
default worker model the mutations here are individually atomic (dict
assignment, `list.append`, slice-assignment), so the worst concurrent outcome
is one turn's ordering being interleaved with another's -- not corruption. Note
the flip side: with more than one worker process each has its own copy, so a
user's history would appear to jump around. Single worker for now.
"""

# Cap per user, counted in *messages* (each turn adds two: the user's and the
# assistant's). 20 keeps roughly ten turns of context, which is enough for
# "and what about engine 11?" to resolve while keeping the prompt small -- and
# small matters on a free tier metered by tokens per minute.
MAX_HISTORY_MESSAGES = 20

# user_id -> [{"role": "user"|"assistant", "content": str}, ...], oldest first.
_history: dict[int, list[dict]] = {}


def get_history(user_id: int) -> list[dict]:
    """Return this user's recent messages, oldest first.

    Returns a **copy**, so a caller building a prompt out of it cannot mutate
    the stored history by appending the current turn to what it got back.
    Empty list for a user who hasn't spoken yet.
    """
    return list(_history.get(user_id, []))


def append_turn(user_id: int, user_message: str, reply: str) -> None:
    """Record one completed exchange, trimming to `MAX_HISTORY_MESSAGES`.

    Call this **after** the reply is produced, so a turn that failed partway
    doesn't leave a question in the history with no answer under it -- which
    would make the next turn's context read as if the assistant ignored them.
    """
    messages = _history.setdefault(user_id, [])
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": reply})
    # Trim from the front: the oldest messages are the ones worth losing.
    if len(messages) > MAX_HISTORY_MESSAGES:
        del messages[: len(messages) - MAX_HISTORY_MESSAGES]


def clear_history(user_id: int) -> None:
    """Drop everything stored for one user.

    Not required by the plan; here because a tester switching between demo
    accounts needs a way to start clean without restarting the server.
    """
    _history.pop(user_id, None)
