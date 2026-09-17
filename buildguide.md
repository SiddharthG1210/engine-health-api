# How This App Was Built

A walkthrough of the whole technical side of the project, in the order it was
built, explaining **why each piece exists** rather than restating what the code
says.

**How to read this.** Keep the source open beside it. This file rarely pastes
code — the files themselves are already heavily commented, and repeating them
here would just mean two copies to keep in sync. Instead, every section points
at the file and explains the thing a comment can't: what problem that file
solves, what would break without it, and what the *next* piece is going to need
from it.

**Who it's for.** Someone who knows basic Python — variables, functions,
classes, dictionaries — but hasn't necessarily used FastAPI, SQLAlchemy, JWTs,
or LLM tool-calling. Every one of those is explained where it first shows up,
and explained as *"here's what it's doing in this app"* rather than as a
textbook definition.

**What's not in here.** How the two neural networks were designed or trained.
Those live in `training.ipynb` and produce four files in `ml_artifacts/`. This
guide treats them as given — a box you feed numbers into and get numbers out
of — but it *does* cover in full how they're loaded, fed, and wired into the
rest of the app, because that plumbing is where the app's own bugs would live.

---

## Table of contents

- [Part 0 — The idea, and why the code is shaped this way](#part-0--the-idea-and-why-the-code-is-shaped-this-way)
- [Part 1 — The foundation: settings and roles](#part-1--the-foundation-settings-and-roles)
- [Part 2 — The database](#part-2--the-database)
- [Part 3 — Who are you, and what may you touch](#part-3--who-are-you-and-what-may-you-touch)
- [Part 4 — From database rows to model input](#part-4--from-database-rows-to-model-input)
- [Part 5 — The three tools, and the gate in front of them](#part-5--the-three-tools-and-the-gate-in-front-of-them)
- [Part 6 — The AI layer](#part-6--the-ai-layer)
- [Part 7 — The HTTP surface](#part-7--the-http-surface)
- [Part 8 — The browser page](#part-8--the-browser-page)
- [Part 9 — Full trace: one question, start to finish](#part-9--full-trace-one-question-start-to-finish)
- [Part 10 — The five ideas that explain everything else](#part-10--the-five-ideas-that-explain-everything-else)

---

# Part 0 — The idea, and why the code is shaped this way

## What the app does

There are three trained predictions available about a turbofan engine:

- how many operating cycles it has left (**RUL** — remaining useful life),
- whether its latest sensor reading looks abnormal (**anomaly score**),
- which wear stage it's in — Healthy, Warning, or Critical (**degradation
  stage**).

The old version of this project exposed those as three raw HTTP endpoints. You
posted a JSON array of sensor numbers, you got a JSON number back. That works,
but it means the caller has to know the sensor format, and it means anyone who
can reach the URL can read any engine.

The new version replaces that with **chat**. You type an ordinary sentence, an
LLM decides which of the three predictions to run and on which engine, Python
runs them, and a second LLM call writes the answer back in prose. Two kinds of
account get very different versions of this:

| | Engineers | Customers |
|---|---|---|
| Tools available | all three | degradation stage only |
| Engines reachable | the whole fleet | only the ones assigned to them |
| Answer style | technical, exact numbers | plain language, no numbers, no engine ids |
| Sees which tools ran | yes, in a panel | no |

## The one sentence the whole design hangs on

> **The model may *name* an engine. Python decides whether it can be *reached*.**

Keep that in your head for the rest of this document. It is the answer to
almost every "why is it written like that?" question you'll have.

The LLM is treated as a component that is *useful* but not *trusted*. It's good
at reading "how's Engine B doing?" and working out that means "run
degradation_stage on engine 39." That's a genuinely hard language problem and
the model solves it well. But the model is also perfectly capable of asking for
engine 34, which belongs to somebody else — because it misread, because it
hallucinated, or because the user talked it into it. So every single thing the
model asks for passes through one Python function that checks it against the
database before anything happens.

A useful way to think about it: the model writes a *request slip*. Python is
the clerk at the counter who decides whether to fill it.

## Why the build order is what it is

The app was built in eight passes, and this document follows the same order.
That's not just historical — it's **dependency order**. Each layer only uses
things built before it:

```
    settings & roles          ← nothing depends on anything
          ↓
       database               ← needs settings (where's the DB file?)
          ↓
      auth / login            ← needs the database (who are the users?)
          ↓
   fetch + shape data         ← needs the database (where's the sensor data?)
          ↓
    tools + the gate          ← needs auth (who's asking?) + fetchers
          ↓
      the AI layer            ← needs the tools (what can it call?)
          ↓
      HTTP routes             ← needs the AI layer + auth
          ↓
      browser page            ← needs the routes
```

Arrows only point downward. Nothing in `app/db/` knows that FastAPI exists;
nothing in `app/ml/` knows what a user is. That's what lets you read this
document straight through without ever needing to skip ahead.

Where a layer is shaped a particular way *because* of something further down,
this guide flags it as a **→ Forward reference** at the end of the section.
Those are the parts that look arbitrary until you've read the rest.

---

# Part 1 — The foundation: settings and roles

**Files:** [app/roles.py](app/roles.py), [app/config.py](app/config.py)

Two tiny files, built first, that everything else imports.

## `roles.py` — the smallest file in the project, and one of the most important

It holds one dictionary: [app/roles.py:14-18](app/roles.py#L14-L18)

```python
ROLE_TOOL_MAP = {
    "engineer":   ["predict_rul", "anomaly_score", "degradation_stage"],
    "customer":   ["degradation_stage"],
    "technician": [],
}
```

That's it. Role name → the tools that role is allowed to use.

**Why is this its own file, instead of living in `app/tools/`?** Because of a
problem called a **circular import**, which is worth understanding since it
shapes several decisions in this project.

Python runs a file top to bottom the first time something imports it. If file A
starts by importing file B, and file B starts by importing file A, Python gets
stuck: it can't finish A until B is done, and it can't finish B until A is
done. It either crashes or leaves you with a half-built module.

Here's how that would have happened:

- The auth code needs to check "is the role on this login token a real role?" —
  so it needs `ROLE_TOOL_MAP`.
- The tools code needs to answer "which tools does this role get?" — so it
  needs `ROLE_TOOL_MAP` too.

If the dictionary lived in the tools package, auth would import tools. And
tools needs to check permissions, so tools would import auth. Circular.

Putting the dictionary in a **leaf module** — a file that imports nothing else
from the project — breaks the cycle cleanly. Both packages can import it, and
it depends on neither.

**Why `"technician": []` is there.** No technician account exists. It's a
placeholder that proves the design extends: adding a role later is one line
here, not a redesign. It also serves as a live test of the fail-closed
behaviour you'll see in Part 3 — a role that exists but grants nothing should
be harmless, and it is.

## `config.py` — settings from a `.env` file

[app/config.py](app/config.py) reads a `.env` file at the project root and turns
its contents into plain Python constants that the rest of the app imports.

A `.env` file is just lines of `NAME=value`. It's gitignored, so secrets stay
off GitHub. `python-dotenv`'s `load_dotenv()` at
[app/config.py:22](app/config.py#L22) reads that file into the process's
environment variables, then `os.getenv("NAME", "fallback")` pulls each one out
with a default.

> **Python note — module-level code runs once.** Everything in `config.py` that
> isn't inside a `def` runs the very first time any file does `import
> app.config`, and never again. Python caches imported modules. So
> `GEMINI_API_KEY` is read from disk once at startup, not on every request.
> This pattern shows up again in Part 4, where the ML models are loaded the same
> way — and there it saves a lot more than a file read.

### The one genuinely interesting decision in this file

Two settings have no safe default: `GEMINI_API_KEY` and `JWT_SECRET_KEY`. The
app cannot work without them. The obvious move is to raise an error at the top
of the file if they're missing.

That was tried, and it was wrong. Here's why:
[app/config.py:1-12](app/config.py#L1-L12) explains it, and the check lives in a
function instead: [app/config.py:68](app/config.py#L68).

The database seeding script (`python -m app.db.seed`, Part 2) imports `config`
to find out where to put the database file. It does not need a Gemini key —
there's no AI involved in loading a text file into SQLite. But if `config.py`
raised on import, you would need a Gemini API key **before you could create your
database**, which is backwards: you set up the database on day one and get the
API key later.

So the rule is:

- **Import time**: read everything, complain about nothing.
- **`validate_runtime_config()`**: called by the web server at startup, raises
  and *names the missing variable*.

The offline scripts keep working. The server still refuses to boot
misconfigured. You get a clear error naming the variable instead of a confusing
500 halfway through someone's first chat.

> **A gotcha that's still live.** `JWT_EXPIRE_MINUTES = int(os.getenv(...,
> "60"))` at [app/config.py:60](app/config.py#L60) uses the default only when
> the variable is *absent*. If `.env` contains `JWT_EXPIRE_MINUTES=` with
> nothing after it, `os.getenv` returns the empty string — which is present, so
> the default never applies — and `int("")` raises immediately at import. This
> is recorded as a known gap in [CLAUDE.md](CLAUDE.md).

**→ Forward reference:** `validate_runtime_config()` is deliberately *not*
called anywhere yet. Part 7's [app/main.py:52](app/main.py#L52) calls it as the
first statement of the app factory. It sits there rather than at the top of that
file so that a future reordering of imports can't silently skip it.

---

# Part 2 — The database

**Files:** [app/db/base.py](app/db/base.py), [app/db/models.py](app/db/models.py),
[app/db/seed.py](app/db/seed.py)

## What's stored, and why there's a database at all

The app needs three things it can look up:

1. **Users** — who can log in, what their password is, what role they have.
2. **Engines** — which engines exist, and which customer (if any) owns each.
3. **Engine cycles** — the actual sensor readings, one row per engine per
   operating cycle. This is the raw telemetry the ML models eat.

Point 3 is the one that matters for the security story. The sensor numbers live
**server-side, in the database**. The user never sends sensor data and the LLM
never sees it. All anyone can do is name an engine; Python looks the readings up
itself. That means a hallucinated number can never become model input — the
worst a bad engine id can do is get refused.

The database is SQLite: a single file, `app.db`, sitting in the project root. No
server to install.

## SQLAlchemy in three paragraphs

SQLAlchemy is an **ORM** — Object-Relational Mapper. The idea: you describe your
tables as Python classes, and then you work with Python objects instead of
writing SQL strings.

So instead of `SELECT * FROM engines WHERE engine_id = 31` and getting back a
tuple you index by position, you write `db.get(Engine, 31)` and get back an
`Engine` object with `.engine_id`, `.customer_id`, `.label` attributes. If you
change `engine.customer_id = 5` and then commit, SQLAlchemy writes the `UPDATE`
for you.

Three objects do the work, and they're all set up in
[app/db/base.py](app/db/base.py):

- **`engine`** ([base.py:12](app/db/base.py#L12)) — the connection to the
  database file. Confusingly named, since this project is *about* engines; this
  one is SQLAlchemy's database engine and has nothing to do with turbofans.
- **`SessionLocal`** ([base.py:13](app/db/base.py#L13)) — a factory that makes
  **sessions**. A session is one unit of work: a workspace where you load
  objects, change them, and then either commit or throw it all away.
- **`Base`** ([base.py:16](app/db/base.py#L16)) — the parent class every table
  class inherits from. Inheriting from it is what registers a class as a table.

The `check_same_thread=False` at [base.py:10](app/db/base.py#L10) is SQLite
being cautious: by default it refuses to be used from a thread other than the
one that opened it. FastAPI can hand a request to a different thread, so that
check has to be turned off. It's the standard setting for SQLite + FastAPI.

### `get_db()` — the pattern you'll see in every route

[app/db/base.py:20-26](app/db/base.py#L20-L26) is six lines that turn up
everywhere later:

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

> **Python note — `yield` makes this a generator.** A normal function runs to
> the end and returns. A function with `yield` in it *pauses* at the yield,
> hands the value out, and resumes when whoever's using it is finished. The
> `try/finally` means the `db.close()` runs no matter what — even if the code
> using the session raises an exception.

**Why this matters for FastAPI:** in Part 3 you'll see `db: Session =
Depends(get_db)` in function signatures. FastAPI runs the generator up to the
`yield` before your route body starts, hands you the session, and resumes it
after your route finishes. So every request gets a fresh session, and every
session is guaranteed to get closed — you never have to remember to close it,
and a crash mid-route can't leak a connection.

## `models.py` — the three tables

[app/db/models.py](app/db/models.py). The syntax is SQLAlchemy 2.0 style:
`name: Mapped[str] = mapped_column(String, nullable=False)`. The `Mapped[str]`
part is a **type hint** — it tells your editor and type-checker what Python type
comes back, purely for tooling. The `mapped_column(...)` part is the real
instruction that describes the database column.

### `User` — [models.py:18-29](app/db/models.py#L18-L29)

`id`, `email` (unique), `password_hash`, `role`, `created_at`.

Note the field is `password_hash`, **not `password`**. Part 3 covers why in
full, but the short version: the actual password is never stored anywhere.

**`role` is a plain `String`, not a database enum.** A database enum would
constrain the column to a fixed set of values at the schema level — which sounds
better until you want to add `technician`. Changing an enum means a schema
migration, and this project has no migration tool. As a plain string, adding a
role is a one-line change to `roles.py` and nothing else. The trade is that the
database will happily store `role = "banana"` — which is exactly why
[app/auth/dependencies.py:56](app/auth/dependencies.py#L56) re-checks the role
against `VALID_ROLES` on every single request.

> **A known bug, left in and documented.** `created_at` writes a
> *timezone-aware* datetime into a `DateTime` column, which SQLite stores as
> naive (no timezone). Reading it back gives you a naive datetime, and Python
> refuses to subtract a naive datetime from an aware one — so
> `datetime.now(timezone.utc) - user.created_at` raises `TypeError`. Nothing
> currently does that subtraction, so it's harmless today. It's in
> [CLAUDE.md](CLAUDE.md)'s known-gaps list.

### `Engine` — [models.py:32-46](app/db/models.py#L32-L46)

`engine_id` is the primary key, and it's **the dataset's own numbering**, not an
auto-incrementing id the app invents. Engine 31 in the app is engine 31 in
`test_FD001.txt`. That makes cross-checking against the raw data trivial.

`customer_id` is a **foreign key** to `users.id` — a column whose value must
match a row in another table, which is how the database expresses "this engine
belongs to that user." It's **nullable** (an engine may have no owner — the
engineer still sees it) and **deliberately not unique**, which is the important
part: a non-unique foreign key is how one customer owns several engines.

`label` — the customer-facing name, `"Engine A"`, `"Engine B"`. Three things
about it are deliberate:

- **It's per-customer, not global.** Every customer has an "Engine A". That's
  fine, because no customer ever sees another customer's roster.
- **It's NULL for unassigned engines.** Engineers refer to engines by number,
  so unowned engines don't need a friendly name.
- **Only the seed script ever writes it.** This is a security decision, and
  it's the one that's easiest to accidentally undo later. That string gets
  pasted **verbatim** into the system prompt sent to the LLM (Part 6). If a user
  could rename their own engine, they could rename it to a paragraph of
  instructions and inject text straight into the prompt the model is told to
  trust. As long as only the seed writes labels, that channel doesn't exist. The
  comment at [models.py:40-43](app/db/models.py#L40-L43) says so explicitly, so
  that anyone adding a rename feature has to read the warning first.

`true_rul_at_cutoff` — the real answer from the NASA dataset, i.e. how many
cycles the engine actually had left. It's stored for **reference only**. No
inference code and no agent code ever reads it — if it did, the app would be
grading its own homework. It's used in exactly one place: the seed script, to
pick a demo spread that covers all three health stages.

### `EngineCycle` — [models.py:49-83](app/db/models.py#L49-L83)

One row per engine per cycle. **Composite primary key** — `(engine_id, cycle)`
together identify a row, since neither is unique alone.

It stores **all 21 raw sensors**, even though the models only use 15. That's on
purpose: storage and feature selection are different concerns. The table is a
faithful record of raw telemetry; deciding which 15 columns feed the model is
Part 4's job. If the models were ever retrained on a different subset, no
database change would be needed.

## `seed.py` — filling the database

[app/db/seed.py](app/db/seed.py), run once with `python -m app.db.seed`. It
parses the two NASA text files, inserts every engine and every sensor row,
creates five demo accounts, and prints a summary table.

Most of it is straightforward parsing. Four decisions in it are worth reading
properly.

### 1. Demo engines are picked from the data, never hardcoded

[`pick_demo_engines`](app/db/seed.py#L74) sorts all 100 engines by their true
RUL and picks five:

- the two **lowest** RUL → both Critical
- the two **highest** RUL → both Healthy
- one from the **middle** of the 30–100 band → Warning

Hardcoding `[34, 31, 97, 39, 25]` would have been three lines shorter and would
break the moment anyone swapped datasets. Deriving them means the demo still
produces a sensible Healthy/Warning/Critical spread against different data.

Two subtleties in there that look like over-thinking and aren't:

**The middle pick happens *after* removing the other four**
([seed.py:109-110](app/db/seed.py#L109-L110)). An earlier version only excluded
them on the fallback path, so the "warning" pick could collide with one of the
extremes. Two customers then got assigned the *same* engine, the second write
silently overwrote the first, and one customer ended up owning nothing and
getting refused on every question. Excluding first makes all five distinct
*structurally* — not by luck, and not by a check afterwards. The
`assert len(set(picked.values())) == 5` at
[seed.py:130](app/db/seed.py#L130) is a belt-and-braces confirmation, not the
mechanism.

**The band test is `30 < rul <= 100`, not `30 <=`**
([seed.py:115](app/db/seed.py#L115)). The ML code classifies stages with
`rul > 30` — so an engine at exactly RUL 30 is Critical. An inclusive bound here
would have picked such an engine as the "Warning" demo and then printed it as
Critical in the summary table. That table's entire purpose is to predict what
chat will say, so a one-character disagreement makes it lie. There are **three**
places this threshold appears — the seed's band filter, the seed's
[`stage_label`](app/db/seed.py#L45), and
[app/ml/inference.py:79](app/ml/inference.py#L79) — and all three have to agree.

### 2. Fixed passwords, on purpose

Every demo account uses `demo1234`
([seed.py:32](app/db/seed.py#L32)). Random passwords would be more secure in the
abstract, but there is no signup route and no password reset anywhere in this
project — so one lost terminal scrollback would lock you out of your own demo
permanently. Deterministic wins here.

### 3. `customer4` owns two engines, from opposite extremes

[seed.py:213-220](app/db/seed.py#L213-L220). Customers 1–3 own one engine each,
covering Healthy / Warning / Critical. Customer 4 owns **two**: the
second-lowest RUL and the second-highest.

This account exists to catch one specific bug. An earlier design said a customer
owning several engines would just get their **lowest-numbered** one. So a
customer owning 31 and 39, asking *"how's Engine B?"*, would have been answered
about **engine 31** — silently, confidently wrong.

Because labels are handed out in **ascending engine-id order**
([seed.py:232](app/db/seed.py#L232)), "Engine A" is always the lower id. So if
that bug ever comes back, asking about Engine B returns Engine A's answer — and
since the two engines are at opposite health extremes, the reply flips from
*"running well"* to *"arrange a service soon"*. Impossible to miss.

That's the general shape of a good test fixture: not just *different*, but
different in a way that makes failure loud.

### 4. Two guards before it writes anything

[seed.py:162](app/db/seed.py#L162) — `has_stale_schema()` checks whether the
existing `app.db` has the `label` column, and exits with a clear message if not.

This exists because of how `Base.metadata.create_all` works: it creates missing
**tables**, but never missing **columns**. There's no migration tool here. So an
old database from before `label` was added would look perfectly fine at startup,
take the ordinary "already seeded" path, exit successfully — and then fail at
*chat time*, hours later, with `no such column: engines.label`, a long way from
its cause. The guard turns that into an immediate, readable instruction.

[seed.py:172](app/db/seed.py#L172) — the ordinary idempotency check. If any user
exists, print "already seeded" and change nothing. Re-running is safe; deleting
`app.db` is how you start over.

Note the order: **stale-schema check first**. A pre-`label` database *has* users,
so it would hit the "already seeded" path and exit 0 if the checks were the
other way round.

**→ Forward references from this part:**
- `Engine.customer_id` becomes the **allowlist** in Part 5 — the set of engine
  ids a customer is permitted to reach.
- `Engine.label` becomes the **roster** in Part 6, pasted into the customer's
  system prompt. That's why it's seed-only.
- `password_hash` is verified by Part 3's login route, using the same bcrypt
  settings the seed used here — the two have to match or nobody can log in.

---

# Part 3 — Who are you, and what may you touch

**Files:** [app/auth/security.py](app/auth/security.py),
[app/auth/dependencies.py](app/auth/dependencies.py)

Two concerns that sound the same and aren't:

- **Authentication** — *who are you?* Proving identity. Handled by passwords and
  tokens.
- **Authorization** — *what are you allowed to do?* Handled by roles and
  ownership checks.

`security.py` does the first. `dependencies.py` does both, and is where the
access model starts.

## Password hashing — [security.py:18-23](app/auth/security.py#L18-L23)

The database stores a **hash** of the password, never the password.

A hash is a one-way function. `"demo1234"` goes in, a fixed-length string of
gibberish comes out, and there is no way to run it backwards. To check a login,
you hash what the user typed and compare it to the stored hash. If someone
steals the database, they get hashes — not passwords.

This project uses **bcrypt**, via `passlib`. Two things make bcrypt right for
passwords specifically:

- **It's deliberately slow.** A general-purpose hash like SHA-256 is designed to
  be fast, which is a *disadvantage* here — fast means an attacker can try
  billions of guesses per second. bcrypt takes a few hundred milliseconds on
  purpose. Unnoticeable for one login, devastating for brute force.
- **It salts automatically.** A random value is mixed into every hash, so two
  users with the same password get different hashes, and a precomputed lookup
  table of common passwords is useless.

`verify_password` handles extracting the salt and re-hashing; you never touch it
directly.

> **The version trap.** `passlib` 1.7.4 is unmaintained and its bcrypt backend
> detection crashes against `bcrypt >= 4.1`. `requirements.txt` pins
> `bcrypt<4.1` for that reason. If logins start throwing a strange backend
> error, that pin is the first thing to check.

## JWTs — [security.py:26-35](app/auth/security.py#L26-L35)

After a successful login the server issues a **JWT** (JSON Web Token): a string
the browser stores and sends back with every later request, so you don't have to
log in on every click.

A JWT has three dot-separated parts: a header, a payload, and a signature. The
payload here is [security.py:29](app/auth/security.py#L29):

```python
{"sub": str(user_id), "role": role, "exp": expire}
```

`sub` is "subject" — who this token is about. `exp` is the expiry timestamp.

**The single most important thing to understand about JWTs: they are signed, not
encrypted.** The payload is only base64-encoded. Anyone holding the token can
read it — paste one into jwt.io and you'll see the user id and role in plain
text. What the signature guarantees is that it hasn't been *modified*: the
server signs it with `JWT_SECRET_KEY`, and any change to the payload makes the
signature stop matching.

So: never put a secret in a JWT payload. Do put identity in it, because tamper-
proof identity is exactly what it's for.

`decode_access_token` verifies the signature and the expiry, and raises
`JWTError` on any problem — expired, forged, or garbage.

> **Why `sub` is `str(user_id)` and not the int.** The JWT spec requires `sub`
> to be a string. Some libraries reject a numeric one. It gets converted back at
> [dependencies.py:48](app/auth/dependencies.py#L48) — inside a `try/except`,
> because that's now a string from an untrusted token and could be anything.

## FastAPI dependencies — the concept

This is the framework idea you need for the rest of the document.

In FastAPI, a route is a normal Python function with a decorator on it:

```python
@router.get("/me")
def read_me(user: User = Depends(get_current_user)) -> MeResponse:
```

> **Python note — decorators.** `@router.get("/me")` is a decorator. It's
> shorthand for "take the function defined below and pass it to
> `router.get("/me")`". The router keeps a note that this function handles GET
> requests to that path. The `@` line is doing registration, not calling
> anything at request time.

The interesting part is `user: User = Depends(get_current_user)`.

That default value isn't a value at all — it's an instruction. It tells FastAPI:
*before you run this function, run `get_current_user`, and pass its return value
in as `user`.* If `get_current_user` raises an HTTP error, your route body never
runs.

This is **dependency injection**, and the benefit here is specific: every
protected route in this app gets its authentication by *declaring* that it needs
a user. There's no `if not logged_in: return 401` line to forget at the top of a
route. A route that forgets the dependency has no `user` variable at all, so it
can't compile a reference to one — the mistake becomes a `NameError` during
development rather than an open endpoint in production.

Dependencies also nest. `get_current_user` itself declares
`db: Session = Depends(get_db)` — so FastAPI resolves `get_db` first, hands the
session to `get_current_user`, and hands the resulting user to your route.

## `get_current_user` — [dependencies.py:35-59](app/auth/dependencies.py#L35-L59)

The function that turns a token into a user. Five checks, and it **fails closed**
at every one — if anything is wrong, 401, never a partial success:

1. Decode the token → `JWTError` means invalid or expired.
2. Is there a `sub` claim at all?
3. Does `int(sub)` work? (A forged token could have `sub: "hello"`.)
4. Does a user with that id still exist? (The account could have been deleted
   since the token was issued.)
5. **Is `user.role` in `VALID_ROLES`?**

Check 5 is worth pausing on. The role comes from the *database*, freshly read,
not from the token. So it's already trustworthy in the tamper sense. This check
catches something else: a corrupted or hand-edited row with `role = "banana"`.
Without it, that user would authenticate fine and then flow into code that does
`ROLE_TOOL_MAP.get(role, [])` and quietly gets an empty list — a valid session
that silently does nothing, which is confusing to debug. With it, they're
rejected at the door.

> **Reading the role from the DB every request, not from the token.** The role
> *is* in the JWT payload — [auth_routes.py:105-107](app/api/auth_routes.py#L105-L107)
> explains it's there for debuggability. But no code trusts it. It's re-read
> from the database on every request, which means changing someone's role in the
> database takes effect on their next request, not when their token expires an
> hour later.

### The reused-exception bug

[dependencies.py:22-32](app/auth/dependencies.py#L22-L32) defines
`_credentials_error()`, a **function that builds a fresh 401 each time**. The
comment tells you not to "optimize" it into a module-level constant. Here's the
story, because it's a genuinely instructive bug.

The obvious version is:

```python
CREDENTIALS_ERROR = HTTPException(status_code=401, detail="...")   # DON'T
```

and then `raise CREDENTIALS_ERROR` in five places. One object, no repetition,
looks cleaner.

The problem: when Python raises an exception, it attaches a traceback to that
**object** as `__traceback__`. A traceback holds references to the stack frames
it passed through, and those frames hold every local variable in them — in this
case, the request's bearer token and its open database session.

With one shared exception object, raising it a second time replaces the first
traceback, but between requests that single module-level object is *still
holding* the previous request's frames alive. They can't be garbage collected.
And two threads raising it simultaneously would each overwrite the other's
traceback.

Building a fresh one per raise costs nothing measurable and makes the problem
not exist.

## `require_role` — [dependencies.py:62-73](app/auth/dependencies.py#L62-L73)

```python
def require_role(*allowed: str):
    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, ...)
        return user
    return _check
```

> **Python note — a function that returns a function.** `require_role` isn't a
> dependency itself; it *builds* one. Calling `require_role("engineer")` returns
> the inner `_check` function, and *that* is what you hand to `Depends`. The
> inner function remembers `allowed` from when it was created — that's a
> **closure**. It's how you get a dependency that takes configuration, since
> `Depends` doesn't let you pass arguments.

Usage in Part 7 reads as `Depends(require_engineer)` where
`require_engineer = require_role("engineer")`.

**Why 401 and 403 are different.** 401 Unauthorized means *"I don't know who you
are"* — bad token, log in again. 403 Forbidden means *"I know exactly who you
are, and no."* The frontend depends on the distinction: a 401 clears the stored
token and bounces to login, a 403 shows an error message and stays put. Getting
them backwards would log a customer out every time they poked an engineer-only
route, instead of just telling them no.

## `assert_engine_access` — [dependencies.py:76-89](app/auth/dependencies.py#L76-L89)

The ownership check, and the last line of defence in the access model:

```python
if user.role == "engineer":                                  return   # allowed
if user.role == "customer" and engine.customer_id == user.id: return  # allowed
raise HTTPException(403, ...)                                         # everyone else
```

Read the shape, not the conditions. It's an **allowlist**, not a blocklist. It
names the cases that are permitted and denies everything else by falling off the
end. A role nobody has thought of yet — `technician`, or anything added in five
years — is denied, because permitting it requires someone to write a branch for
it.

The opposite shape (`if role == "customer" and not owned: deny; return`) reads
almost identically and behaves completely differently: a new role would sail
through the `if` and land on the `return`, inheriting full access from nothing
more than not being mentioned.

**→ Forward reference:** this function is **layer 3** of a three-layer access
model. Part 5 builds layers 1 and 2 in front of it. It's important that this one
is last and independent: layers 1 and 2 buy a better error message and an earlier
exit, but *this* is the one that actually guarantees the property, and it holds
even if a future caller forgets the other two entirely.

---

# Part 4 — From database rows to model input

**Files:** [app/ml/feature_columns.py](app/ml/feature_columns.py),
[app/ml/inference.py](app/ml/inference.py),
[app/data_access/fetchers.py](app/data_access/fetchers.py),
[app/data_access/errors.py](app/data_access/errors.py)

This part is the seam between the database and the trained models. The models
themselves are out of scope here — what matters is that they expect input in an
extremely specific shape, and *nothing checks that for you*. Feed an LSTM a
wrongly-shaped array and it doesn't crash; it returns a plausible-looking wrong
number. So every rule in this part exists to make sure the shape is right by
construction.

## The four artifacts and how they're loaded

[app/ml/inference.py:20-30](app/ml/inference.py#L20-L30) loads four files from
`ml_artifacts/` at **module import** — not on each request:

| File | What the app does with it |
|---|---|
| `scaler.pkl` | Rescales raw sensor values into the range the models were trained on |
| `rul_model.pth` | Weights for the RUL network |
| `autoencoder_model.pth` | Weights for the anomaly network |
| `anomaly_threshold.pkl` | The single number above which reconstruction error counts as an anomaly |

`.pth` files hold only the **weights** (a `state_dict`), not the network
structure. That's why [app/ml/architectures.py](app/ml/architectures.py) has to
declare the classes: PyTorch builds an empty network from the class, then
`load_state_dict` pours the saved numbers into it. If the class doesn't match
what was saved, loading either errors or — worse — silently mismatches.

Three things about this loading are deliberate:

**Loaded once, at import.** Reading a `.pth` off disk and constructing the
network takes real time. Doing it per request would add that to every chat turn.
Module-level code runs once (see Part 1), so both models sit in memory ready to
go for the process's lifetime.

**Loaded at the top of the file, above the functions that use them.** The
original root `main.py` loaded the scaler on its *last line*, below the routes
that used it. That worked only because FastAPI doesn't execute route bodies at
import time — a coincidence, not a design. Here it's ordered properly.

**`.eval()` is called on both models** ([inference.py:24](app/ml/inference.py#L24),
[inference.py:28](app/ml/inference.py#L28)). PyTorch models have two modes.
Training mode leaves dropout active — layers that randomly zero out some values,
which helps training and would make predictions non-deterministic. Eval mode
switches that off. Forget it and the same engine gives slightly different answers
each time you ask.

## Scaling happens in exactly one place

The scaler is a `MinMaxScaler` that was **fit once during training** and is
reused as-is. It is never refit. Refitting it on live data would rescale
everything against a different range and quietly invalidate the model.

More importantly for this codebase: `scaler.transform(...)` appears **only**
inside `app/ml/inference.py` — three times, once per function. The fetchers in
the next section return **raw, unscaled** numbers, and their docstring
([fetchers.py:9-11](app/data_access/fetchers.py#L9-L11)) says so loudly. If a
fetcher also scaled, the input would be transformed twice, and there is no error
for that — just wrong answers.

> **Live warning worth knowing about.** `scikit-learn` is unpinned in
> `requirements.txt` while `scaler.pkl` was pickled with version 1.6.1. That
> already produces an `InconsistentVersionWarning` at startup. It's harmless
> today, but a pickle read by a different version than wrote it is exactly the
> kind of thing that changes behaviour silently. Noted in
> [CLAUDE.md](CLAUDE.md).

## `FEATURE_COLS` — 15 names whose order is load-bearing

[app/ml/feature_columns.py](app/ml/feature_columns.py) is a list of 15 sensor
names. The dataset has 21; six of them (`sensor_1, 5, 10, 16, 18, 19`) never
change value in FD001, so they carry no information and were dropped during
training.

**The order of this list matters more than anything else in this part.** The
scaler was fit on 15 columns in a specific order, and it applies a different
min/max to each position. Swap two entries and every value in those two columns
gets rescaled against the wrong range. No error. Just wrong numbers, forever.

Two files have to agree with this list and nothing enforces it: the training
notebook's column selection, and the loading code here. That's why the file
carries a "do not reorder or clean up" warning at
[feature_columns.py:3-5](app/ml/feature_columns.py#L3-L5) rather than just being
a bare list.

## The fetchers — [app/data_access/fetchers.py](app/data_access/fetchers.py)

Two functions, and a clear division of labour: **the agent decides *which*
engine; these decide *how* to fetch and shape it.**

### `_project` — reading columns by name

[fetchers.py:40-51](app/data_access/fetchers.py#L40-L51) turns ORM rows into a
NumPy array:

```python
np.array([[getattr(row, col) for col in FEATURE_COLS] for row in rows], ...)
```

> **Python note — `getattr`.** `getattr(row, "sensor_2")` is the same as
> `row.sensor_2`, except the attribute name is a *string* you can compute. That
> lets one line of code read 15 differently-named columns by looping over
> `FEATURE_COLS`.

This is the mechanism that makes the ordering rule safe. `EngineCycle` stores 21
sensors and the models want a specific 15 in a specific order. Reading **by
name** from `FEATURE_COLS` means the array is built in that list's order,
regardless of how the ORM class happens to be laid out. If someone reorders the
columns in `models.py`, nothing breaks. If someone relied on ORM attribute order
instead, it would.

### `get_rul_window` — [fetchers.py:54](app/data_access/fetchers.py#L54)

Returns the engine's last 30 cycles as a `(30, 15)` array. Used by both
`predict_rul` and `degradation_stage`, since they share a model.

**Why 30?** The LSTM was trained on fixed-length 30-cycle sequences. That number
is baked into the trained weights — it isn't a setting. Which is why
`SEQUENCE_LENGTH = 30` at [fetchers.py:24](app/data_access/fetchers.py#L24) is a
module constant and not a function parameter: exposing it as an argument would
just let a caller build an input the model was never validated on.

**The padding rule.** Some engines have fewer than 30 recorded cycles. Those get
padded by **repeating the first cycle at the front** until there are 30
([fetchers.py:86-90](app/data_access/fetchers.py#L86-L90)).

This is the single most arbitrary-looking line in the file and it's the least
arbitrary. It is exactly what the training notebook's `get_last_window` does. The
reported accuracy (RMSE 15.77 cycles) was measured against inputs built that way.
Zero-padding instead, or repeating the *last* row instead of the first, would
change results — with no error to notice, because a padded array is still a valid
array of the right shape. The comment at
[fetchers.py:59-64](app/data_access/fetchers.py#L59-L64) says all of this so it
can't get "tidied up".

**The shape assertion** at [fetchers.py:98-102](app/data_access/fetchers.py#L98-L102)
raises if the built window isn't exactly `(30, 15)`. Note where it lives: not in
`inference.py`, which doesn't check its input at all, but here — because *this is
the only code in the project that builds those windows*. Check the contract where
it's created, not where it's consumed. In practice it's unreachable unless
`FEATURE_COLS` and the padding logic disagree, which is precisely the bug worth
catching loudly.

### `get_latest_cycle` — [fetchers.py:107](app/data_access/fetchers.py#L107)

Returns one cycle as a `(15,)` array, for the anomaly check — which judges a
single reading rather than a trend.

Note it uses `ORDER BY cycle DESC LIMIT 1` rather than loading the history and
taking the last element. For an engine with 300 cycles that's 1 row off the disk
instead of 300.

## `errors.py` — plain exceptions, not HTTP ones

[app/data_access/errors.py](app/data_access/errors.py) defines
`EngineNotFoundError` and `NoSensorDataError`, with a shared `DataAccessError`
base so a caller can catch both in one clause.

They are deliberately **not** `HTTPException`. This layer knows it couldn't
produce data. It does *not* know who's asking. Part 5 catches these and turns
them into `{"ok": False, "error": "..."}` dictionaries to feed back to an LLM,
which wants a sentence it can read out — not a status code. If this layer picked
`404`, that caller would have to catch an HTTP object it never wanted and unwrap
it. Raise the specific thing; let the caller decide how to present it.

**→ Forward reference:** everything in this part is called from exactly one place
— `dispatch_tool_call` in Part 5. These functions take a database session and an
engine id and know nothing about users, roles, or permissions. All of that lives
one layer up.

---

# Part 5 — The three tools, and the gate in front of them

**Files:** [app/tools/specs.py](app/tools/specs.py),
[app/tools/registry.py](app/tools/registry.py),
[app/tools/access.py](app/tools/access.py)

This is the most security-relevant part of the project. Everything before it was
building capability; this is where capability meets permission.

## What "tool" means here

Modern LLM APIs support **function calling** (Gemini calls them "function
declarations"). The idea:

1. You send the model a question **plus a list of functions it may request**,
   described in JSON — name, what it does, what arguments it takes.
2. The model replies either with text, or with a structured *request* to call
   one of those functions with specific arguments.
3. **The model does not run anything.** It hands you a request. Your code
   decides whether and how to fulfil it, and sends the result back.
4. The model uses the result to write its answer.

Step 3 is the whole ballgame. The gap between "the model asked for
`degradation_stage(engine_id=34)`" and "engine 34's data was actually read" is
where this entire project's access control lives. That gap is `dispatch_tool_call`.

## `specs.py` — the descriptions the model reads

[app/tools/specs.py](app/tools/specs.py) holds three declarations. They're plain
Python dicts, not Gemini SDK objects — so nothing outside `app/agents/` has to
import the SDK, and the tools layer stays independent of which LLM provider is in
use.

Two conventions in this file carry real weight:

**Every tool takes only `engine_id`.** Nothing else. The model never sees sensor
values, never constructs an array, never passes data. It picks a *target*, and
Python owns the fetch, the shaping, and the scaling. That's what makes a
hallucinated sensor value structurally impossible — there's no parameter for one
to arrive in.

**The description does the routing.** There's no classifier deciding which of the
three tools answers a question. The model reads these strings and decides. So
they're written *behaviourally* —

> "Use for 'how long does it have left' and maintenance-scheduling questions."

— rather than as restatements of the function name. Compare with a description
like "Predicts RUL", which tells the model nothing it couldn't guess from the
name. If routing ever picks the wrong tool for a class of question, these strings
are the first place to fix it, and often the only place.

## `registry.py` — the choke point

[app/tools/registry.py](app/tools/registry.py). One function matters:
`dispatch_tool_call` at [registry.py:166](app/tools/registry.py#L166).

Everything the model produces is untrusted input here: the tool name, the
argument types, and above all the engine id.

### The signature is a safety feature

```python
def dispatch_tool_call(tool_name, args, db, user, *, allowed_engine_ids):
```

> **Python note — the bare `*`.** Everything after `*` in a signature is
> **keyword-only**: it *must* be passed by name, `allowed_engine_ids=...`, never
> positionally. And it has **no default value**, so it must be passed at all.

That's deliberate, and the reasoning generalises: *a safety parameter whose
default is "unrestricted" is a widening waiting to happen.* If it defaulted to
`None` (which here means unrestricted), then a future call site that forgot it
would get full fleet access, silently, and pass every test that didn't
specifically look for it.

With no default, forgetting it is a `TypeError` the first time that line runs.
The mistake becomes impossible to ship.

### The order of the checks is the design

[registry.py:205-257](app/tools/registry.py#L205-L257) — six steps, and the
order is load-bearing.

**1. Coerce the engine id** — [`_coerce_engine_id`](app/tools/registry.py#L109)

JSON has no integer type, and the SDK's own conversion has shifted between
versions, so an id can arrive as `31`, `31.0`, or `"31"`. All three mean the same
engine and all three are accepted. Anything else is rejected rather than guessed
at.

Three rejections in here are each a real bug that was found:

- **`bool` is rejected first and explicitly**
  ([registry.py:130](app/tools/registry.py#L130)). In Python, `bool` is a
  *subclass* of `int` — `isinstance(True, int)` is `True`, and `int(True)` is
  `1`. Without that first branch, a model emitting `engine_id: true` would be
  silently answered about **engine 1**.
- **Digit strings are checked with `isascii()` first**
  ([registry.py:142](app/tools/registry.py#L142)). `"³".isdigit()` is `True`
  (it's the superscript three), as are Arabic-Indic digits — and `int()` then
  either refuses them or reads a different number than intended.
- **Values too wide for a 64-bit column are rejected**
  ([registry.py:145](app/tools/registry.py#L145)). Python integers are unbounded;
  SQLite's are 64-bit. Handing `1e20` to `db.get` raises `OverflowError` from
  inside the driver — which would escape this module's "errors are returned, not
  raised" contract and crash the whole chat turn.

Coercion happens **first**, before any comparison, because a string `"31"` would
never match a set of integers. Coercing late would turn the allowlist into a
no-op that still looked correct.

**2. The allowlist** — [registry.py:210-222](app/tools/registry.py#L210-L222)

```python
if scope is not None and engine_id not in scope:
    return _failure(tool_name, engine_id, OUT_OF_SCOPE_MESSAGE)
```

`allowed_engine_ids` is the set of engine ids this caller may reach. `None` means
unrestricted, and that is the **engineer path only**.

Three details here, each of which has bitten someone:

- **`is not None`, written out literally.** The tempting short version,
  `if scope and engine_id not in scope`, is the same line with a hole in it:
  `frozenset()` is falsy in Python, so a customer who owns *nothing* would skip
  the gate entirely and fall through to the next check. The comment at
  [registry.py:218-220](app/tools/registry.py#L218-L220) calls this the likeliest
  bug in the file.
- **Belt and braces** at [registry.py:215](app/tools/registry.py#L215): if the
  caller is *not* an engineer and passed `None` anyway, it's converted to
  `frozenset()` — deny everything. A forgetful future caller fails closed rather
  than inheriting full access.
- **It runs before the database is touched.** This is the subtle one, explained
  below.

**Why checking before the DB matters.** Suppose the order were reversed: load the
engine first, then check ownership. Then:

- Asking about engine **99999** → "Engine 99999 isn't in the system."
- Asking about engine **34** (real, someone else's) → "That engine isn't on your
  account."

Two different sentences. Which means anyone can determine *which engine ids
exist* by asking about them one at a time and reading which refusal they get.
That's a working oracle for enumerating the fleet, built entirely out of error
messages.

Checking the allowlist first makes both return the **identical sentence** through
the **same code path**, with no database round-trip to time either. There's one
constant, [`OUT_OF_SCOPE_MESSAGE`](app/tools/registry.py#L45), and its comment
says: *do not make it more helpful.*

**And there is no substitution.** A customer naming an engine they don't own is
told so. They are never quietly given a different engine's data instead. The
"helpful" fallback is the bug, not the feature.

**3. The role gate** — [registry.py:227](app/tools/registry.py#L227)

`if tool_name not in ROLE_TOOL_MAP[user.role]: refuse`.

This is **redundant**. `access.py` (below) already ensures a customer's request
never contains the `predict_rul` declaration, so the model has no way to know it
exists, let alone call it.

The redundancy is the point. It's **defence in depth**: two independent
mechanisms enforcing the same rule, so that a future edit to the prompts or the
specs can't widen access on its own. Someone would have to break both.

**4–5. Load the engine, check ownership, fetch, predict** —
[registry.py:238-245](app/tools/registry.py#L238-L245)

`assert_engine_access` from Part 3 runs here. This is the layer that actually
*guarantees* the property; the allowlist above only bought a uniform message and
an earlier exit. Note that if it raises its 403, it's caught at
[registry.py:254](app/tools/registry.py#L254) and converted to the *same*
sentence the allowlist uses — so from the outside the two layers are
indistinguishable.

**6. Success** — [registry.py:260-272](app/tools/registry.py#L260-L272)

```python
{"tool": ..., "engine_id": ..., "engine_label": ..., "ok": True, "result": {...}}
```

**Why `engine_label` and not `label`.** `degradation_stage()` already returns a
`"label"` key — holding the health stage, `"Healthy"`/`"Warning"`/`"Critical"`.
Putting the engine's friendly name under `"label"` at either level would collide,
and merging would overwrite the answer with the engine's name.

**Why echo the label at all.** The allowlist stops a customer reaching an engine
they don't own. It cannot stop the model resolving "Engine B" to the wrong id
*among ones they do own* — both ids pass the allowlist. Nothing in the security
model catches that. So Python states which engine actually answered, and the
communicator prompt (Part 6) requires the reply to name it. That turns a residual
failure from silent into visible — you'd read "Engine A is running well" when you
asked about Engine B, and immediately know.

### Errors are returned, not raised

Every failure path in `dispatch_tool_call` **returns** `{"ok": False, "error":
"..."}`. Nothing propagates out.

Two reasons, both practical:

- These dicts get fed back to the model as function responses. A raised exception
  would 500 the entire chat request. A returned error lets the model say *"engine
  999 isn't in the system"* and carry on — which is the correct chat experience.
- It stops one bad engine number in a six-call question from taking the five good
  answers down with it. Ask "full report on engines 10 and 34" as a customer who
  owns 10 — you get the full report on 10, and a refusal for 34, in one reply.

### The documented residual

[registry.py:200-203](app/tools/registry.py#L200-L203) admits something openly:
engine ids are the dataset's own numbering, so a customer who can see their own
ids can infer the fleet is numbered roughly 1–100. Closing that would need opaque
per-customer handles, which is out of scope. It's written down rather than
glossed over, so nobody later describes this boundary as airtight when it isn't.

## `access.py` — role to tool list

[app/tools/access.py](app/tools/access.py) is one function,
`get_tools_for_role(role)`, and one idea in its docstring
([access.py:1-13](app/tools/access.py#L1-L13)) that's worth reading twice:

> Gating at the tool-list level instead of the prompt level is the difference
> between a rule the model could be talked out of and a capability it never had.

There are two ways to stop a customer using `predict_rul`:

1. Send the model all three tools and add "do not use predict_rul for customers"
   to the prompt.
2. **Never put `predict_rul` in the request at all.**

Option 1 is a *request*. Prompt instructions are text, and text can be argued
with, confused, or overridden by a sufficiently creative user message. Option 2
is a *fact*: the model cannot call a function it was never told about, no matter
what anyone types.

This app does option 2. The function raises `KeyError` on an unrecognised role
rather than returning an empty list — an unknown role reaching here is a bug
upstream, and returning "no tools" would hide it behind an assistant that merely
seems unhelpful.

**→ Forward reference:** Part 6 calls `get_tools_for_role(user.role)` *before*
building the model request, which is what makes the guarantee above hold. And it
passes `allowed_engine_ids=None` for engineers, `frozenset(their ids)` for
customers.

---

# Part 6 — The AI layer

**Files:** [app/agents/gemini_client.py](app/agents/gemini_client.py),
[app/agents/prompts.py](app/agents/prompts.py),
[app/agents/tool_loop.py](app/agents/tool_loop.py),
[app/agents/orchestrator.py](app/agents/orchestrator.py),
[app/agents/intake.py](app/agents/intake.py),
[app/agents/communicator.py](app/agents/communicator.py),
[app/agents/session_store.py](app/agents/session_store.py)

Seven files. The shape of a chat turn:

```
  your message + history
          ↓
  ROUTING call  (has tools)      "which tools, on which engines?"
          ↓
  every requested call runs through dispatch_tool_call  ← Part 5's gate
          ↓
  results fed back; routing model may ask for more (loop)
          ↓
  COMMUNICATOR call  (NO tools)  "write this up for a human"
          ↓
       the reply
```

**Two model calls per turn, minimum.** That's the single most important
structural fact in this part.

## Why two calls instead of one

The obvious design is one model call that picks tools *and* writes the answer.
It was split, for three reasons:

1. **The two jobs need different judgement.** Routing is judged on "did it pick
   the right tools for the right engines?" Writing is judged on tone and
   accuracy. One prompt trying to do both tends to narrate its tool use at the
   user — *"Let me check that for you... I'll run the degradation stage tool..."*
2. **Customer tone lives in exactly one place.** Every customer-facing sentence
   — results, refusals, and small talk alike — goes through the same
   plain-language prompt. There's no path where an unusual turn skips the filter.
3. **The writing call has no tools attached at all.** Not as a rule it's asked to
   follow, but structurally: the request contains no function declarations. So
   the step that produces the text a user reads *cannot* fetch anything, reach an
   engine, or widen what the turn looked at. It can only phrase what already
   happened.

Point 3 is the same trick as `access.py`. Make the thing impossible rather than
forbidden.

The cost is real — two calls per turn, and the free tier is metered per minute.
That's the direct cause of the ~5–7 messages/minute ceiling.

## `gemini_client.py` — the one door to the API

[app/agents/gemini_client.py](app/agents/gemini_client.py). Every call to Gemini
in the entire project goes through `generate()` here. Four things it owns:

**Lazy client construction** — [gemini_client.py:90](app/agents/gemini_client.py#L90).
The `genai.Client` is built on first use, never at import, guarded by
`if _client is None`. Same reasoning as Part 1's config decision: the offline
scripts import this chain and legitimately have no API key yet. A client built at
import would blow up `python -m app.db.seed`.

**Retry with backoff** — [gemini_client.py:228-263](app/agents/gemini_client.py#L228-L263).
Rate limits (429), overload (503), and transient faults (500) are retried. A 400
or 403 is **not** — that's a bug or a bad key, and retrying only delays the real
error.

The backoff has three separate ceilings, which is more thought than it looks:

- `MAX_BACKOFF_SECONDS = 20` — cap on any single sleep.
- `MAX_TOTAL_BACKOFF_SECONDS = 30` — cap on *all* sleeping across attempts. This
  is the app's answer to "quota exhausted": ride out a blip, but don't sit out a
  whole per-minute window. Holding an HTTP request open for 45 seconds is worse
  for the user than a quick honest "busy, try again."
- **Jitter** — [gemini_client.py:244](app/agents/gemini_client.py#L244) adds a
  random 0–0.5s. Without it, two chat turns that hit the same quota wall would
  sleep the same duration, wake in lockstep, and collide again.

[`_retry_delay`](app/agents/gemini_client.py#L160) takes **the longer** of the
app's own backoff and Google's suggested `retryDelay` from the error body. That
"longer" is a fix for observed behaviour: on a genuinely spent quota, Google
returned suggested delays of 1.3s and then 0.4s, both far too optimistic —
honouring them burned two of four attempts on responses that were still 429.
Google's number is worth respecting when it asks for *more* time, not for less.

**One clean sentence when it gives up** — `AssistantBusyError`, carrying
[`BUSY_MESSAGE`](app/agents/gemini_client.py#L67). Callers show `str(exc)`
directly; nobody ever sees an SDK exception or a status code.

**Automatic function calling is explicitly disabled** —
[gemini_client.py:221](app/agents/gemini_client.py#L221). The SDK has a mode
where it runs your tools for you. This app must never use it: the whole point is
that `dispatch_tool_call` sits between the model asking and Python answering.
Turning AFC off both documents that and silences a warning the SDK emits
otherwise.

> **The model choice, and why it's not the obvious one.** The plan said
> `gemini-2.5-flash`. That model now 404s for newer API keys — and it still
> appears in `models.list()`, so only an actual call reveals it. The stronger
> replacement, `gemini-3.6-flash`, turned out to have a free-tier limit of 5
> requests/minute *plus* a second tighter bucket at 20, which a two-calls-per-turn
> app empties in a handful of questions. So both settings point at
> `gemini-3.5-flash-lite` ([config.py:53-56](app/config.py#L53-L56)), the only
> free-tier model that sustains this workload. The two settings stay *separate*
> even though they name the same model, because quotas are counted per model —
> they're the seam for splitting load, or for putting just the communicator on a
> paid model later.

## `prompts.py` — four prompts, and what they are not

[app/agents/prompts.py](app/agents/prompts.py). Two model calls × two roles = four
prompts:

| | routing call | writing call |
|---|---|---|
| engineer | `ORCHESTRATOR_SYSTEM` | `COMMUNICATOR_ENGINEER_SYSTEM` |
| customer | `INTAKE_SYSTEM` (built per request) | `COMMUNICATOR_CUSTOMER_SYSTEM` |

The file's own docstring makes the crucial point
([prompts.py:15-20](app/agents/prompts.py#L15-L20)):

> **These prompts are not a security boundary.** ... Read them as UX, and never
> move an access rule out of Python and into this file.

The customer prompt *does* say "don't ask about engines that aren't yours." That
instruction saves a wasted API call when the model complies. It is not what stops
anything. What stops it is the allowlist in Part 5, which runs whether the model
complied or not.

### The routing prompts

`ORCHESTRATOR_SYSTEM` ([prompts.py:27](app/agents/prompts.py#L27)) spends most of
its length on one rule, stated three different ways:

> **One message often needs several calls.** Each call covers exactly ONE tool
> and ONE engine. ... "Full report on engines 10 and 11" is six calls, not one,
> and not two.

That's the multi-target requirement, and it's emphasised because the natural
failure is the model trying to be efficient — emitting one call and describing
several engines in the arguments, which produces one result presented as if it
covered everything.

`INTAKE_SYSTEM` is a **template** filled in per request by
[`build_intake_system`](app/agents/prompts.py#L98). The only thing that varies is
the roster block:

```
- Engine A (id 31)
- Engine B (id 39)
```

Both the label and the id, because the tool takes an id while the customer speaks
in labels. **This prompt is the only place label→id resolution happens** — and
letting the model do that resolution is safe *precisely because* Part 5's
allowlist independently bounds the result to ids the customer owns. A wrong
resolution produces a refusal, never someone else's data.

Two structural facts about the roster:

- **It's rebuilt from the database every single turn.** Never cached, never
  carried over from a previous turn.
- **It lives in the system instruction, never in `contents`.** Gemini takes a
  system prompt separately from the conversation. Nothing the customer types
  shares a channel with the roster, so nothing they type is positioned to edit,
  extend, or override it. That, plus labels being seed-only (Part 2), closes the
  prompt-injection path into the roster from both ends.

### The communicator prompts

`COMMUNICATOR_CUSTOMER_SYSTEM` ([prompts.py:148](app/agents/prompts.py#L148)) is
the strictest one. It bans a specific vocabulary — "RUL", "reconstruction error",
"threshold", raw cycle counts — and gives explicit translations:

> Warning → "it's still running fine, but it would be worth booking a check-up"

It also handles the mixed case: a turn can contain both successful checks and
refusals, and both must be reported. Dropping either would be a lie by omission.

## `tool_loop.py` — running every call the model asked for

[app/agents/tool_loop.py](app/agents/tool_loop.py). Both roles run this one loop.
They differ in exactly three arguments: system prompt, tool list,
`allowed_engine_ids`.

**Why one shared loop rather than two copies.** The allowlist threads through
here. One copy means one place to audit and one place where "every call really
did execute" is verified. Two copies drift — and the half that drifts is the
security-relevant half.

Each round: [tool_loop.py:128-207](app/agents/tool_loop.py#L128-L207)

1. Ask the model, with tools attached.
2. **No function calls?** Its text is the answer. Return.
3. Otherwise run **every** call it emitted, through `dispatch_tool_call`.
4. Append the model's turn plus one response per call, and loop.

Four details in there are easy to get subtly wrong:

**Every call, not just the first** — [tool_loop.py:149](app/agents/tool_loop.py#L149).
Gemini emits **parallel** function calls: one response can contain six requests
at once. Handling only `calls[0]` would answer a sixth of the question and look
like the model's fault.

**Each response echoes the call's `id`** —
[`_function_response_part`](app/agents/tool_loop.py#L57). Three parallel calls to
the *same* tool for *different* engines are distinguishable **only** by that id.
Drop it, and the model can't tell which result belongs to which engine — which is
exactly how two engines' results blur into one verdict. That's the failure the
whole multi-engine design exists to prevent, reappearing at a completely
different layer.

**The model's own turn is appended verbatim** —
[tool_loop.py:146](app/agents/tool_loop.py#L146):

```python
contents.append(response.candidates[0].content)
```

Not reconstructed by hand. Gemini 3 models embed **thought signatures** in
function-call parts — internal state the API validates on the follow-up call.
Rebuilding that turn manually drops them and the next request is rejected. Take
the object straight off the response.

**Two caps, capping different things** —
[tool_loop.py:47-48](app/agents/tool_loop.py#L47-L48):

```python
MAX_TOOL_CALLS = 10
MAX_ROUNDS = 5
```

"Full report on engines 10 and 11" is legitimately six calls. If the model emits
all six at once, that's **one round**. If it works through them one at a time,
that's **six rounds**. A round cap alone would silently truncate a valid question
in the second case; a call cap alone would let a confused model ping-pong forever
in the first. Both, and the feature works either way.

Refusals count against the budget too
([tool_loop.py:173](app/agents/tool_loop.py#L173)) — a model that keeps retrying
a rejected engine id has to terminate somewhere, and that counter is what makes
it.

And when the budget runs out mid-turn, the remaining calls are still *answered* —
with `BUDGET_EXHAUSTED_MESSAGE`. Silence would let the model assume the unchecked
engines were fine.

**The `pre_dispatch` hook** — [tool_loop.py:89](app/agents/tool_loop.py#L89) is
an optional callback applied to each call's arguments before dispatch. Return a
dict to short-circuit that call; return `None` to let it proceed. It exists so
that role-specific policy stays in the role's own module and `dispatch_tool_call`
stays dumb and role-agnostic. Only `intake.py` uses it, for the two rules below.

> **No dedupe, deliberately** — [tool_loop.py:119-123](app/agents/tool_loop.py#L119-L123).
> Skipping repeated `(tool, engine_id)` pairs was considered and rejected: the
> caps already bound the budget, and a dedupe that ever collapsed two calls
> differing only by engine would resurrect the exact multi-engine bug. Cheap
> protection isn't worth owning that risk.

## `orchestrator.py` — the engineer turn (the simple half)

[app/agents/orchestrator.py](app/agents/orchestrator.py), 93 lines. Engineers get
everything, so there's no roster to build and no allowlist to compute.

`allowed_engine_ids=None` at [orchestrator.py:79](app/agents/orchestrator.py#L79)
— unrestricted. The comment notes this is one of only two places in the codebase
allowed to pass `None` (the other is the debug routes in Part 7). Both are behind
`require_role("engineer")`.

One shortcut worth noting: if the routing model answers in **text with no tool
calls** — a clarifying question, small talk — that text is returned as-is
([orchestrator.py:82-86](app/agents/orchestrator.py#L82-L86)), skipping the
communicator entirely. There's nothing to summarise, and on a per-minute quota
that saved call is not free.

Returns `{"reply": str, "tool_calls": list}`.

## `intake.py` — the customer turn (the security-relevant half)

[app/agents/intake.py](app/agents/intake.py). Three properties hold here, each
enforced by Python rather than by asking the model nicely.

**1. The roster comes only from the database.**
[`_load_roster`](app/agents/intake.py#L57) queries
`Engine.customer_id == user.id`. Not from the request body, not from chat
history, not from anything the model produced on a previous turn. That single
rule is what makes the allowlist trustworthy — every other guarantee is
downstream of it.

Two small choices in that query matter:

- `.all()`, **not** `.one_or_none()`. Owning several engines is now the normal
  case, and `one_or_none` *raises* on multiple rows.
- `.order_by(Engine.engine_id)` — so the roster reads identically every turn. An
  unordered query may return rows in a different order between calls, which would
  let "Engine A" mean different things across two turns of one conversation.

**2. Zero engines is answered without any model call at all** —
[intake.py:121-123](app/agents/intake.py#L121-L123). A fixed sentence, no
dispatch, no roster sent to a model that would then have to be trusted not to
invent one.

**3. `pre_dispatch` handles the two argument cases** —
[intake.py:133-183](app/agents/intake.py#L133-L183):

- **Exactly one engine owned, model supplied no `engine_id`** → fill it in from
  the roster. Safe because the value comes from the database; the model
  contributed nothing to it. This is what makes *"how's my engine?"* work for a
  single-engine customer.
- **Several engines, no engine named** → do **not** guess, do **not** default to
  the lowest id. Skip the call and record a synthetic refusal, which the
  communicator turns into *"which one did you mean — Engine A or Engine B?"*.
  Handling it deterministically in Python costs nothing and beats hoping the
  model asks.
- **Model named an engine they own** → dispatch normally.
- **Model named an engine they don't own** → dispatch returns "That engine isn't
  on your account", and nothing here softens or retries it.

There's also an **import-time guard** at
[intake.py:44-48](app/agents/intake.py#L44-L48):

```python
if ROLE_TOOL_MAP["customer"] != ["degradation_stage"]:
    raise RuntimeError(...)
```

This module assumes customers get exactly one tool — both the `sole_engine_id`
filling and the single-tool wording in `INTAKE_SYSTEM` depend on it. If someone
grants customers a second tool, this fails loudly at startup rather than
producing something subtly wrong in a customer's reply. Note it's a `raise`, not
an `assert` — `python -O` strips asserts.

Returns `{"reply": str}` and **nothing else**. Deliberately a different shape from
the engineer's, so that a route handler cannot accidentally render engineer
diagnostics for a customer. The shapes disagree loudly rather than silently.

## `communicator.py` — and the "Engine C" bug

[app/agents/communicator.py](app/agents/communicator.py). One model call, no
tools, picks the prompt by role.

Results are passed as **JSON** rather than pre-formatted prose
([communicator.py:100-105](app/agents/communicator.py#L100-L105)), because the
shape carries meaning: `ok: false` versus `true`, and `engine_label` sitting next
to each result so multi-engine answers can be named correctly.

Now the bug, because it's the most instructive one in the project.

**Symptom.** A customer owning exactly two engines asked about an engine they
didn't own. The reply said their engines were *"Engine A, Engine B, and **Engine
C**."* Engine C does not exist.

**Cause.** The customer prompt says: *when you turn an engine down, list the ones
that are on the account.* Normally the labels are available inside `tool_results`.
But in this case the **routing model refused the engine on its own**, without
calling any tool — which is correct behaviour. So `tool_results` was empty, and
the communicator was instructed to list the customer's engines while having been
given nothing to list.

It had already said "Engine A" and "Engine B" from context. So it extended the
pattern.

**The general lesson**, written into the docstring at
[communicator.py:66-67](app/agents/communicator.py#L66-L67):

> An instruction the model cannot satisfy truthfully gets satisfied untruthfully.

**Fix.** The `available_engines` parameter
([communicator.py:45](app/agents/communicator.py#L45)) — the roster is passed to
the communicator too, not just the routing model. The prompt then points at it as
the *only* permitted source of engine names
([prompts.py:168-174](app/agents/prompts.py#L168-L174)). It's included even when
it's the empty list, because "you have none" is information, and omitting the key
would put the model back in the position of guessing.

Labels only, never ids — this list is rendered into text a customer reads.

Worth noting *how* this was found: not by reading the code, but by testing the
unusual path. The bug only appears when the router refuses on its own, which is
the one case where the happy path never runs.

## `session_store.py` — chat history, in memory

[app/agents/session_store.py](app/agents/session_store.py). A plain dictionary:
`user_id → list of messages`. Capped at 20 messages (about ten turns).

**It resets when the server restarts.** That's a deliberate trade-off at this
stage, not an oversight — persisting chat would mean another table, a retention
decision, and a migration story, none of which the demo needs. It's documented in
three places so a tester who reloads after a restart doesn't file it as a bug.

**Only the final user message and final reply are stored** — never the
intermediate function-call parts. Two reasons:

- Function calls carry ids and thought signatures valid only within the turn that
  produced them. Replaying stale ones is noise at best and rejected at worst.
- A customer's history then contains **no numeric engine ids and no raw tool
  output**, so replaying it into the next turn's prompt cannot leak detail their
  role isn't meant to see. The privacy property holds across turns, not just
  within one.

`get_history` returns a **copy**
([session_store.py:44](app/agents/session_store.py#L44)) so a caller building a
prompt can't accidentally mutate stored history by appending to what it got back.

**The single-worker requirement lives here.** This dict is per-process. Run two
uvicorn workers and each gets its own copy, so a user's conversation would appear
to jump between two different histories depending on which process caught the
request. That's why the README says single worker, and why
[app/main.py:7-10](app/main.py#L7-L10) repeats it.

---

# Part 7 — The HTTP surface

**Files:** [app/api/auth_routes.py](app/api/auth_routes.py),
[app/api/chat_routes.py](app/api/chat_routes.py),
[app/api/debug_routes.py](app/api/debug_routes.py),
[app/main.py](app/main.py)

Everything above is callable Python. This part makes it reachable over HTTP.

## Routers, and Pydantic

Each file defines an `APIRouter` with a `prefix`, e.g.
[auth_routes.py:32](app/api/auth_routes.py#L32). A router is a group of related
routes that gets attached to the app in one line later. It keeps `main.py` short
and keeps each area's routes in its own file.

**Pydantic** models (`class ChatRequest(BaseModel)`) describe request and response
shapes. FastAPI uses them to validate incoming JSON automatically — a request
missing `message`, or with `message` as a number, is rejected with a 422 before
your function runs. You never write validation code. The same models generate the
interactive docs at `/docs`.

## `auth_routes.py` — and the deliberate absence of signup

Two endpoints. **There is no signup route, and that's a design decision, not an
omission** ([auth_routes.py:8-12](app/api/auth_routes.py#L8-L12)).

A self-service registration endpoint would need some way to choose a role — and
any such path is a way for an anonymous caller to hand themselves `engineer`,
which is the entire authorisation model of this app, gone. Accounts exist only
via the seed script. New accounts are a seeding concern.

### `POST /api/auth/login` — [auth_routes.py:64](app/api/auth_routes.py#L64)

**Form-encoded, not JSON.** This is the thing most likely to trip you up.

`OAuth2PasswordRequestForm` parses `application/x-www-form-urlencoded`, and the
email goes in a field named **`username`** — OAuth2's password flow names it that
and renaming it would mean hand-rolling the form. Posting JSON here returns 422.

The upside of conforming: the Authorize button in FastAPI's `/docs` works against
this route with zero extra wiring.

**Case-insensitive email** — [auth_routes.py:91](app/api/auth_routes.py#L91).
SQLite's `=` is case-sensitive, and the seed writes lowercase addresses, so a
plain `==` turned `Engineer@Demo.local` into "incorrect email or password" for
someone typing their own address with a capital letter. Compared with
`func.lower()` on **both** sides rather than lowercasing only the input, so it
still works if a future seed writes a mixed-case address.

### The timing attack defence

[auth_routes.py:37-40](app/api/auth_routes.py#L37-L40) defines
`_TIMING_DUMMY_HASH` — a real bcrypt hash of a throwaway string that no account
uses and nothing authenticates against.

Its only job is to make every login take the same amount of time.

Recall from Part 3 that bcrypt is intentionally slow — a verify takes a few
hundred milliseconds. Now consider the naive login:

```python
user = find_user(email)
if user is None:
    raise error          # returns in microseconds
if not verify_password(...):
    raise error          # returns in ~300ms
```

Both raise the *same message*, which was the point. But they take **wildly
different times**. An attacker submitting a list of email addresses with garbage
passwords can read the response time and learn which addresses have accounts —
fast means no such user, slow means real user wrong password. The identical
wording is defeated by the clock.

The fix at [auth_routes.py:93-95](app/api/auth_routes.py#L93-L95): if no user was
found, verify the submitted password against the dummy hash anyway. Both branches
now pay for one bcrypt verify. Then check the outcome.

This is a good example of a whole *class* of bug — where information leaks
through something other than the response body. Response times, response sizes,
and which of two errors comes back are all channels.

### `GET /api/auth/me` — [auth_routes.py:112](app/api/auth_routes.py#L112)

Returns `{id, email, role}` so the frontend can pick a view.

**Why not just decode the JWT in the browser?** It would work — the payload is
readable (Part 3). Two reasons not to: it teaches the frontend to read a token it
isn't the audience for, and it would keep believing a **stale role** after one
changed in the database. Asking the server costs one request on page load and
doubles as a token-validity check.

Note what `MeResponse` does *not* contain
([auth_routes.py:51-57](app/api/auth_routes.py#L51-L57)): no password hash, and
no engine list. A customer's engines are resolved server-side per turn. If the
frontend held that list, it would become a thing a client could try to edit.

## `chat_routes.py` — two endpoints, never one

[app/api/chat_routes.py](app/api/chat_routes.py). Both routes do the same four
things: load history, run the turn, record the turn, return the reply. They could
obviously be one route with an `if`.

They're separate on purpose
([chat_routes.py:1-19](app/api/chat_routes.py#L1-L19)):

- Different **role gates** — `require_engineer` vs `require_customer`.
- Different **response shapes** — the engineer response carries `tool_calls`
  (numeric engine ids, raw model output); the customer response carries `reply`
  and nothing else.
- **Because they're two functions, there is no `if role == "customer"` branch
  that could ever be got wrong.** There is no code path in which the engineer
  payload is built for a customer at all. A customer reaching the engineer route
  is stopped by `require_role` before any code runs.

The agent modules mirror the split (`run_engineer_turn` returns
`{"reply", "tool_calls"}`, `run_customer_turn` returns `{"reply"}`), so the two
halves disagree loudly rather than silently if anyone tries to merge them later.

**`MAX_MESSAGE_CHARS = 2000`** ([chat_routes.py:56](app/api/chat_routes.py#L56))
— explicitly *not* a security control. It's quota protection. The free tier is
metered on tokens as well as requests, and one turn costs at least two calls, so
a pasted logfile would burn the budget for everyone else using the demo.

**Blank messages rejected at the Pydantic layer**
([chat_routes.py:64-76](app/api/chat_routes.py#L64-L76)) — a whitespace-only
message would otherwise cost two model calls to be told there was no question.

**`AssistantBusyError` becomes a normal 200 reply**
([chat_routes.py:107-126](app/api/chat_routes.py#L107-L126)). Rate limiting is an
expected condition on the free tier, not a server fault. Returning a 500 would
show an error page for something a retry in five seconds fixes, and would waste
the polite wording the retry logic exists to produce.

**History is recorded only on success** — `append_turn` is after the `try`. A
stored question with no answer under it would make the *next* turn's prompt read
as though the assistant ignored the user, and the model tends to apologise for
the earlier "silence" instead of answering.

**Deliberately no `response_model` on the engineer route**
([chat_routes.py:95-99](app/api/chat_routes.py#L95-L99)) — FastAPI would filter
each tool-call dict down to declared fields, and these dicts vary by outcome
(`result` xor `error`) and by tool. Declaring a model would silently drop the
error strings that make a refused engine visible in the UI.

> **A known limitation, documented in place.** If Gemini runs out of quota during
> the **communicator** call — after every tool already ran successfully — those
> results are lost, because the exception carries nothing with it. The user sees
> "busy, try again" with an empty "what I checked" panel. Honest about the turn
> producing no answer, but it under-reports the work done. Fixing it properly
> means the agent layer attaching partial results to the exception, which is
> `app/agents/`'s call to make, not the route's.

## `debug_routes.py` — checking the AI's homework

[app/api/debug_routes.py](app/api/debug_routes.py). Three engineer-only endpoints
that call the same tools with **no LLM involved**.

They exist to answer *"is the chat reply actually right?"* A chat answer passes
through two model calls before a human reads it, so when a reply looks wrong
there's no way to tell whether the model misread something or the prediction
itself is off. These routes call the same `dispatch_tool_call`, the same
fetchers, the same weights, and skip both model calls — so the number they return
is the number the chat turn was working from.

They also **replace** the original unauthenticated endpoints. Those aren't
restored anywhere: re-exposing raw fleet-wide inference without auth would make
every access control in this project decorative.

**The one dangerous line** is `allowed_engine_ids=None` at
[debug_routes.py:67](app/api/debug_routes.py#L67). The module docstring
([debug_routes.py:17-25](app/api/debug_routes.py#L17-L25)) is blunt about it: it's
safe for exactly one reason, which is that every route in this router is gated by
`require_role("engineer")`. Change the gate and you must change the scope in the
same commit.

A failed call returns HTTP **200** with `ok: false`, not a 4xx — so what a tester
reads here is byte-for-byte what the model was handed for the same question.
Translating it into an HTTP error would mean comparing two different shapes.

## `main.py` — assembly, in a specific order

[app/main.py](app/main.py). Four steps in `create_app()`, and the order is
deliberate:

**1. `config.validate_runtime_config()`** — [main.py:52](app/main.py#L52). The
first statement. A missing key is a refusal to boot naming the variable, rather
than a confusing 500 halfway through someone's first chat. The comment explains
it sits *inside* the factory rather than at module top-level, where it would read
as first but be one import-reorder away from silently doing nothing.

**2. `Base.metadata.create_all(bind=engine)`** — [main.py:70](app/main.py#L70).
Creates missing tables. A no-op against a seeded database, and **not** a
substitute for seeding: an empty schema has no users, so nobody can log in.

**3. Routers** — all three mount under `/api/*`.

**4. Static files, mounted at `/static` — never at `/`** —
[main.py:85](app/main.py#L85). A mount at the root path **shadows every route
registered after it**, so `/api/auth/login` would 404 while looking perfectly
correct in the source. `GET /` is an explicit route instead
([main.py:87](app/main.py#L87)) that serves `index.html`, falling back to a JSON
pointer at `/docs` if the file doesn't exist.

`STATIC_DIR.mkdir(exist_ok=True)` at [main.py:81](app/main.py#L81) is there
because `StaticFiles` raises at construction if the directory is missing, and git
doesn't track empty directories — so a fresh clone would fail to boot over a
folder.

---

# Part 8 — The browser page

**Files:** [app/static/index.html](app/static/index.html),
[app/static/app.js](app/static/app.js),
[app/static/style.css](app/static/style.css)

No framework, no build step, no dependencies. Three static files.

## One page, two views

[index.html](app/static/index.html) contains both a login view and a chat view,
and ships with **both hidden**. `app.js` reveals exactly one.

**Why one page rather than two HTML files** ([index.html:11-15](app/static/index.html#L11-L15)):
the token lives in `localStorage`, so "am I logged in?" is a question only
JavaScript can answer. With two pages, every load of the chat page would flash
unauthenticated markup before redirecting. Starting with both hidden means the
page never shows the wrong view while `/api/auth/me` is still in flight.

The login form is a real `<form>` rather than a button with a click handler, so
Enter in either field submits and the browser's own `required` validation runs
before any request is made.

## `app.js` — the three things that matter

[app/static/app.js](app/static/app.js), listed in its own header at
[app.js:10-25](app/static/app.js#L10-L25).

### 1. Login is form-encoded

[`requestToken`](app/static/app.js#L446) uses `URLSearchParams` as the body, which
makes the browser set `Content-Type: application/x-www-form-urlencoded` itself,
and puts the email in the field named `username`.

It deliberately does **not** go through the `apiFetch` helper — that one
JSON-encodes its body and attaches a bearer token, and neither is right here.

### 2. Any 401 clears the token and drops back to login

Tokens expire after 60 minutes, and the server may be re-seeded underneath a
logged-in tab. A stale token has to self-heal rather than leave the chat view
sitting there failing every send.

`UnauthorizedError` ([app.js:136](app/static/app.js#L136)) is a custom error class
so the response to a 401 is handled once rather than status-checked at every call
site.

One subtlety at [app.js:484-492](app/static/app.js#L484-L492): a 401 on `/me`
*immediately after* a successful login is treated as an ordinary sign-in failure
rather than routed through `handleUnauthorized` — which would just re-render the
form you're already looking at.

### 3. Model output is never inserted as HTML

Every render uses `textContent`. Nothing in the file assigns `innerHTML`.

This is the security rule of the frontend. The reply text comes from an LLM which
is in turn quoting user-supplied questions. If that were treated as markup, a
user could type a message containing a `<script>` tag, get the model to echo it,
and have it execute in the browser of anyone who saw that reply — **stored XSS**,
straight through the chat history.

`textContent` renders a `<script>` tag as the literal characters `<script>`. The
attack has nowhere to land.

## The "What I checked" panel

[`addToolPanel`](app/static/app.js#L395) renders a collapsible `<details>` element
under every engineer reply, one line per tool call, green for success and red for
refusal.

It's rendered for **every** engineer reply, including ones with an empty list,
and prints "No tools were run for this reply" in that case. Empty is legitimate
in two situations that look identical from outside — a clarifying question, or a
rate-limited turn — and saying so stops either being mistaken for a broken panel.

`describeToolCall` ([app.js:321](app/static/app.js#L321)) shows the server's
**exact** refusal sentence rather than a friendly rewrite. That's intentional: an
out-of-scope engine and a nonexistent one deliberately share one wording, and
seeing them differ in this panel would be a real finding worth noticing.

The panel is checked against `currentUser.role === "engineer"` rather than
against whether `data.tool_calls` exists
([app.js:543-549](app/static/app.js#L543-L549)) — so a future server change that
started sending the field to customers wouldn't quietly start showing them engine
numbers.

---

# Part 9 — Full trace: one question, start to finish

Now the parts connect. Two complete journeys.

## Trace A — engineer asks about two engines

> **"What is the RUL of engine 31 and engine 39?"**

**1. Browser** — [app.js:511](app/static/app.js#L511). Submit handler fires,
`preventDefault()` stops navigation, message added to the transcript, a
"Thinking…" placeholder appended, composer locked.

**2. HTTP** — `apiFetch("/api/chat/engineer", ...)` with
`Authorization: Bearer <jwt>`.

**3. FastAPI resolves dependencies before the route body runs:**
- `get_db` opens a session.
- `require_engineer` → `get_current_user` → decode the JWT, load user 1 from the
  database, confirm the role is valid, confirm it's `"engineer"`.
- Pydantic validates the body: non-empty, under 2000 characters.

**4. Route** — [chat_routes.py:79](app/api/chat_routes.py#L79). Loads history
(empty on a fresh conversation), calls `run_engineer_turn`.

**5. Orchestrator** — [orchestrator.py:67](app/agents/orchestrator.py#L67).
Builds `contents` from history + message. Calls
`get_tools_for_role("engineer")` → **all three specs**. Wraps them in a
`types.Tool`.

**6. Tool loop, round 1** — the routing model gets
`ORCHESTRATOR_SYSTEM` + the message + three tools. It returns **two parallel
function calls**:
```
predict_rul(engine_id=31)   id=call_a
predict_rul(engine_id=39)   id=call_b
```

**7. Dispatch, twice** — for each call, `dispatch_tool_call`:
- Coerce `31` → already an int, in 64-bit range. ✓
- Allowlist: `scope is None`, and the user *is* an engineer, so no restriction. ✓
- Role gate: `predict_rul` is in `ROLE_TOOL_MAP["engineer"]`. ✓
- Load engine 31; `assert_engine_access` returns immediately (engineer). ✓
- `get_rul_window(db, 31)` → 30 rows × 15 columns, raw, read by name.
- `inference.predict_rul(window)` → scale, `(1,30,15)` tensor, LSTM, round.
- Return `{tool, engine_id: 31, engine_label: "Engine A", ok: True, result: {predicted_rul: 6.73}}`

**8. Feed back** — both results appended as function responses, **each echoing
its own call id**, in one `Content`. The model's own turn was appended verbatim
just before.

**9. Round 2** — the model has both numbers and asks for nothing more. Returns
text → loop exits with two `tool_results`.

**10. Communicator** — [communicator.py:41](app/agents/communicator.py#L41). One
call, **no tools**, `COMMUNICATOR_ENGINEER_SYSTEM`, results as JSON. Writes a
reply naming each engine separately.

**11. Back up** — `{"reply": ..., "tool_calls": [two dicts]}` → route appends the
turn to history → JSON to the browser.

**12. Render** — placeholder text replaced via `textContent`, then
`addToolPanel` draws two green lines:
```
predict_rul · engine 31 (Engine A) — RUL 6.73 cycles
predict_rul · engine 39 (Engine B) — RUL 123.88 cycles
```
Two lines is the multi-engine requirement visibly working.

## Trace B — customer4 asks about an engine they don't own

> **"How is engine 34 doing?"** — asked by `customer4`, who owns 31 and 39.

Steps 1–3 are the same, except `require_customer` gates the route.

**4. Route** — `chat_customer` → `run_customer_turn`.

**5. Roster, from the database only** — [intake.py:115](app/agents/intake.py#L115):
```python
engines            = [Engine(31, "Engine A"), Engine(39, "Engine B")]
allowed_engine_ids = frozenset({31, 39})
roster_lines       = ["- Engine A (id 31)", "- Engine B (id 39)"]
```
Nothing here came from the request.

**6. Prompt built** — `build_intake_system(roster_lines)`. The roster goes in the
**system instruction**, not in `contents`. Tools =
`get_tools_for_role("customer")` → **only `degradation_stage`**. The other two
declarations are not in the request at all.

**7. Round 1** — two things can happen, and both end in the same place:

**(a) The routing model refuses on its own.** Its prompt lists only 31 and 39, so
it often replies in text without calling anything. `tool_results` is empty.

**(b) The model calls `degradation_stage(engine_id=34)` anyway.** Then:
- `pre_dispatch` sees an `engine_id` is present → returns `None`, proceed.
- `dispatch_tool_call`: coerce `34` ✓ → **allowlist: `34 not in {31, 39}`** →
  returns `{"ok": False, "error": "That engine isn't on your account."}`.

**The database was never touched.** No query ran for engine 34. It doesn't matter
whether engine 34 exists, is owned by someone else, or is fictional — the refusal
is identical and arrives at the identical speed. There's nothing to measure and
nothing to compare.

Nothing retries with 31 or 39.

**8. Communicator** — either way, `summarize(role="customer", ...)` runs with
`available_engines=["Engine A", "Engine B"]`. That parameter is why the reply
says "Engine A and Engine B" and not "Engine A, Engine B, and Engine C" —
path (a) is exactly the empty-`tool_results` case that produced that bug.

**9. Return `{"reply": ...}` — and nothing else.** No `tool_calls` field exists on
this path. The frontend checks `currentUser.role === "engineer"` before rendering
a panel, so nothing is drawn.

The customer reads something like:

> *That engine isn't on your account. The engines on your account are Engine A
> and Engine B.*

**Count the layers that had to fail for engine 34's data to escape:**

1. The tool list never offered anything but `degradation_stage`.
2. The allowlist rejected the id before any query ran.
3. The role gate would have rejected the tool.
4. `assert_engine_access` would have rejected the ownership.
5. `intake.py` never substitutes a different engine.
6. The customer response shape has no field to carry tool detail.
7. The frontend never renders a panel for a customer.

Any one of them alone is sufficient. That's the point.

---

# Part 10 — The five ideas that explain everything else

If you remember nothing else from this document:

### 1. The model may name a thing; Python decides whether it can be reached

Everything the LLM produces is untrusted input. There is exactly one function —
[`dispatch_tool_call`](app/tools/registry.py#L166) — where a model-requested
action becomes a real one, and it validates against the database, not against
what the model said. The gap between "the model asked" and "the system did" is
where the entire access model lives.

### 2. Make it impossible, not forbidden

Repeated at four different layers:

- A customer's request **does not contain** the other two tool declarations —
  rather than containing them with an instruction not to use them.
- The communicator call has **no tools attached** — so it cannot fetch anything,
  as a fact rather than a rule.
- `allowed_engine_ids` is **keyword-only with no default** — so forgetting it is
  a `TypeError`, not silent full access.
- `Engine.label` is **seed-written only** — so there's no prompt-injection
  channel to defend, because there's no way to write into it.

A rule can be argued with. A capability that was never granted cannot.

### 3. Fail closed, and fail identically

`assert_engine_access` names what's allowed and denies everything else, so a role
nobody has invented yet is denied by default. `get_current_user` 401s on anything
questionable. An empty allowlist denies rather than being read as falsy.

And when it refuses, **it refuses the same way every time**. "Doesn't exist" and
"isn't yours" are one sentence, reached by one code path, in the same amount of
time. Three helpful messages would be a working oracle for enumerating the fleet
— and the timing dummy hash in
[auth_routes.py](app/api/auth_routes.py#L37) is the same principle applied to the
clock instead of the wording.

### 4. Return errors where a raise would take good work down with it

`dispatch_tool_call` returns `{"ok": False, "error": ...}` instead of raising, so
one bad engine number in a six-call question doesn't kill the five good answers,
and the model can report the problem and carry on. The data-access layer raises
*plain* exceptions rather than HTTP ones, because it doesn't know whether its
caller wants a status code or a sentence.

Match the failure shape to what the caller can actually do with it.

### 5. Two things that must agree, and nothing enforcing it, is a comment's job

Several pairs in this project have to stay in lockstep with no mechanism to
enforce it:

- `FEATURE_COLS` ↔ the training notebook's column order
- `architectures.py` ↔ the notebook's model classes
- the padding rule ↔ `get_last_window` in the notebook
- the `> 30` threshold in three separate files

Every one of them carries a comment saying *why* it can't be tidied up. That's
not documentation for its own sake — it's the only guard rail those constraints
have.

---

## Where the rough edges are

Deliberately not smoothed over, and all recorded in
[CLAUDE.md](CLAUDE.md)'s known-gaps section:

| Gap | Why it's still there |
|---|---|
| Chat history in memory, single worker only | Persisting it needs a table, a retention policy, and a migration story |
| ~5–7 messages/minute ceiling | Free Gemini tier; two calls per turn is structural |
| `scikit-learn` unpinned vs a 1.6.1-pickled scaler | Already warns at startup; worth pinning before it changes scaling silently |
| `User.created_at` naive/aware mismatch | Nothing subtracts it yet, so it's latent |
| Blank `JWT_EXPIRE_MINUTES=` crashes at import | `os.getenv`'s default only applies when the variable is *absent* |
| No tests, no linter, no CI | The manual checklists in [plan.md](plan.md) are what stands in for them |

## Where to go next

- **[functionality.md](functionality.md)** — the same app from the user's side:
  every feature, in plain language, no code.
- **[log.md](log.md)** — what each build pass actually did, including the
  deviations from the plan and the bugs found along the way.
- **[plan.md](plan.md)** — the original design, plus the two amendments that
  changed it mid-build (multi-engine customers, and the model choice).
- **[CLAUDE.md](CLAUDE.md)** — the short orientation file, and the canonical
  known-gaps list.
