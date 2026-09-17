# Build log

Terse, per-task notes for whoever builds the next task. Check here first for
what you need; fall back to `plan.md` (your own task section, then others') or
the codebase itself only if something you need isn't recorded here. Append your
own entry when your task is done — a few lines, not prose.

---

## Task 0 — Scaffolding, config, dependencies — DONE

- Created `app/` package (`db/ auth/ ml/ data_access/ tools/ agents/ api/ static/`, all with `__init__.py`), plus `data/` and `ml_artifacts/` at root.
- `app/roles.py`: `ROLE_TOOL_MAP = {"engineer":[...3 tools...], "customer":["degradation_stage"], "technician":[]}`, `VALID_ROLES = set(ROLE_TOOL_MAP)`. Zero-dependency module — import this, not something re-exporting it.
- `app/config.py`: env-backed settings (`GEMINI_API_KEY`, `JWT_SECRET_KEY`, `GEMINI_MODEL_ROUTING`/`GEMINI_MODEL_COMMUNICATOR` default `"gemini-2.5-flash"`, `JWT_ALGORITHM="HS256"`, `JWT_EXPIRE_MINUTES` default 60, `DATABASE_URL` default `sqlite:///<repo>/app.db`, `ML_ARTIFACTS_DIR`, `DATA_DIR`, `BASE_DIR`).
- **Deviation from plan.md, intentional — do not revert**: missing `GEMINI_API_KEY`/`JWT_SECRET_KEY` is NOT raised at import. Call `config.validate_runtime_config()` instead — Task 6's `app/main.py` must call it at startup. Import-time raising would break Task 1/2's offline scripts, which import `config` but need neither key.
- `requirements.txt` rewritten in full (grouped); added `pandas` (was missing), `sqlalchemy`, `python-jose[cryptography]`, `passlib[bcrypt]`, `python-multipart`, `google-genai`, `python-dotenv`.
- `.gitignore`: added `*.db` and `data/`.
- Verified: `app.roles` imports clean; `app.config` imports clean with no `.env` present; `validate_runtime_config()` correctly raises naming both missing vars.

**User still needs to do**: create `.env` (keys can be blank for now), `pip install -r requirements.txt`.
Above user manual task is completed.

**For Task 1 and Task 2** (both unblocked, independent of each other): import `app.config` for `ML_ARTIFACTS_DIR`/`DATA_DIR`/`DATABASE_URL` — don't hardcode paths.

---

## Task 1 — ML serving core — DONE

- Moved the model code out of root `main.py` into `app/ml/`:
  - `feature_columns.py` — the 15 sensor names, same order as before.
  - `architectures.py` — `EngineRUL` and `EngineAutoencoder`, unchanged.
  - `inference.py` — loads scaler + both models + threshold once at import (from `ML_ARTIFACTS_DIR`), exposes `predict_rul(raw_window)`, `anomaly_score(raw_cycle)`, `degradation_stage(raw_window)`. All take plain numpy arrays, raw/unscaled — no FastAPI, DB, or auth in this file.
- Fixed the load-order smell called out in `CLAUDE.md`: artifacts now load at the top of the file, before the functions that use them (old `main.py` loaded `scaler.pkl` on its last line).
- Logic is otherwise a straight move, not a rewrite — same scaling, same thresholds (RUL > 100 Healthy, 30–100 Warning, < 30 Critical), same rounding.
- Verified myself (temporarily copied the 4 root artifact files into `ml_artifacts/`, ran all three functions on zero-arrays, deleted the copies again — root files are untouched, `ml_artifacts/` is empty, ready for your move). All three ran and returned sane values/shapes, no errors.
- Noticed in passing, not fixed (not in scope): `scaler.pkl` throws a sklearn `InconsistentVersionWarning` (pickled with 1.6.1, environment has 1.7.2). Harmless for now, flagging in case it becomes real later.

**User's manual steps are done**: artifacts moved into `ml_artifacts/`, sanity check re-run against the real files — `predict_rul(np.zeros((30,15)))` returned `93.68`, no crash.

**For Task 2**: independent of this task, nothing needed from here.
**For Task 4**: import `FEATURE_COLS` from `app.ml.feature_columns` and the three functions from `app.ml.inference` — signatures match plan.md exactly (`predict_rul(raw_window) -> float`, `anomaly_score(raw_cycle) -> dict`, `degradation_stage(raw_window) -> dict`).

---

## Task 2 — Database and demo seeding — DONE (superseded in part by Amendment A, at the bottom of this file)

> **Read this first if you're implementing Task 2a.** Everything below describes what was
> actually built and is still an accurate picture of the code and DB *as they exist today*.
> But the 3-engine / 3-customer setup it describes is exactly what Task 2a replaces. Where
> this entry and Amendment A disagree, **Amendment A wins.**

- `app/db/base.py`: `engine`, `SessionLocal`, `Base`, `get_db()` — reads `DATABASE_URL` from config, SQLite-safe threading.
- `app/db/models.py`: `User` (email/password_hash/role, role is plain string), `Engine` (`engine_id` PK = the dataset's own numbering, `customer_id` nullable FK, `true_rul_at_cutoff` — **reference only, never read by inference/agent code**), `EngineCycle` (all 21 raw sensors stored, composite PK `engine_id`+`cycle`).
- `app/db/seed.py`, run as `python -m app.db.seed`: parses `data/test_FD001.txt` + `data/RUL_FD001.txt`, picks 3 demo engines dynamically (highest RUL, lowest RUL, one in the 30–100 band — with a closest-to-65 fallback if nothing lands in-band), creates `engineer@demo.local` + 3 `customerN@demo.local` accounts (all password `demo1234`), links each customer to their picked engine, prints a summary table. Idempotent: exits early with a clear message if users already exist — delete `app.db` to reset. Creates tables itself (`Base.metadata.create_all`) since `app/main.py` doesn't exist yet.
- **Fixed a real bug, not just this task's code**: `passlib[bcrypt]` (as pinned in Task 0's requirements.txt) is broken against `bcrypt>=4.1` — passlib 1.7.4 is unmaintained and its bcrypt backend detection throws. Added `bcrypt<4.1` to `requirements.txt` under the Auth section. **This affects Task 3 too** — its `hash_password`/`verify_password` will hit the same crash if the environment has a newer bcrypt installed; no code change needed there, just make sure `bcrypt<4.1` is actually installed (`pip install -r requirements.txt` picks it up automatically now).
- Verified end-to-end against synthetic FD001-shaped data in a throwaway temp DB (not the real project files): demo-engine picking, user/engine linking, idempotent re-run, and password hash round-trip (`verify` true for correct password, false for wrong) all confirmed working.

**User's manual steps are done**: real `test_FD001.txt`/`RUL_FD001.txt` placed in `data/`, `python -m app.db.seed` run against them. Printed table confirmed 3 genuinely different stages — engine 25 Healthy (RUL 145), engine 97 Warning (RUL 82), engine 34 Critical (RUL 7). Picking logic needed no adjustment *for the 3-engine spread* — but Amendment A now requires **5** distinct engines and a 4th customer, so `pick_demo_engines` is being rewritten in Task 2a regardless. These three ids stay the same; two more get added. `.env` also has `JWT_SECRET_KEY` + `JWT_EXPIRE_MINUTES=60` set now (`GEMINI_API_KEY` still blank, not needed until Task 5).

**For Task 3**: `User` fields are `id`, `email`, `password_hash`, `role`; `Engine.customer_id` is the FK to key off of; `get_db()` lives in `app/db/base.py`. Password hashes were created with `passlib.context.CryptContext(schemes=["bcrypt"])` — use the same in `security.py` so verification matches.

---

## Task 3 — Auth and authorization — DONE

- `app/auth/security.py`: password hashing (`hash_password`/`verify_password`, same `bcrypt` scheme as the seed script) and JWT helpers (`create_access_token(user_id, role)`, `decode_access_token(token)` — HS256, payload has `sub`/`role`/`exp`).
- `app/auth/dependencies.py`: `oauth2_scheme` (login URL `/api/auth/login`, for Task 6); `get_current_user` — decodes the token, loads the `User`, 401s on any problem including an unrecognized role; `require_role(*allowed)` — dependency factory, 403s if the user's role isn't in the list; `assert_engine_access(user, engine)` — engineers see everything, customers only their own engine, everyone else denied by default (fails closed, so a future role needs an explicit branch to see anything).
- No deviations from plan.md.
- Verified against the real seeded DB (not just synthetic data): logged in both `engineer@demo.local` and `customer1@demo.local` password hashes, round-tripped a JWT, confirmed engineer access to any engine, customer access to their own engine, and a 403 when a customer's user object is checked against another customer's engine.

**User's manual steps**: JWT secret was already generated and in `.env` from Task 2's setup — nothing new needed here. Confirm the hash/verify sanity check yourself if you want (see plan.md Task 3, manual step 2) — I already ran the equivalent against live data above.

**For Task 4**: import `assert_engine_access` from `app.auth.dependencies` when building `dispatch_tool_call`. **For Task 6**: import `get_current_user`, `require_role`, `create_access_token`, `verify_password` for the auth routes — `require_role("engineer")` / `require_role("customer")` is the pattern for gating chat routes.

**Code review run twice on Tasks 0-3** (second pass at higher effort). Two bugs found in this task's code, both fixed and re-verified:
  - `get_current_user` crashed with a raw 500 instead of a clean 401 when a token's `sub` claim wasn't a valid integer.
  - The 401 was a single module-level `HTTPException` object reused on every raise. Confirmed by experiment that this leaves `__traceback__` pointing at the last request's frame, keeping that request's bearer token and DB session reachable in memory (and two threads raising it at once would stomp on each other). Now built fresh per raise by `_credentials_error()` — **don't "optimize" it back into a shared constant.**
  - Re-verified after both fixes: valid login works; garbage / bad-`sub` / missing-`sub` / unknown-user tokens all 401; cross-customer engine access and `require_role` both 403.

**Findings in other tasks' files — NOT fixed, out of Task 3's scope.** Whoever owns these should pick them up:
  - `app/db/seed.py`: (a) two demo customers can be assigned the *same* engine when few engines fall in the 30-100 RUL band — the second link silently overwrites the first, leaving one customer owning nothing and 403ing on every request (reviewer reproduced with a 3-engine set; the real 100-engine FD001 data doesn't trigger it); (b) the printed "Warning" label uses `rul >= 30` while `app/ml/inference.py` uses `rul > 30`, so they disagree at exactly RUL 30; (c) the customer→engine `.update()` return value is discarded, so a mismatched data-file pair would link nothing while still printing a success table.
  - `app/config.py`: `JWT_EXPIRE_MINUTES=` left blank in `.env` (the same convention `.env` already uses for `GEMINI_API_KEY`) makes `int("")` raise at import, bypassing the documented default of 60.
  - `app/db/models.py`: `created_at` writes an aware datetime into a naive `DateTime` column and reads back naive, so subtracting it from `datetime.now(timezone.utc)` raises `TypeError`.
  - `requirements.txt`: `scikit-learn` is unpinned while `scaler.pkl` is a pickled `MinMaxScaler` — the version skew Task 1 flagged is now wider (pickled 1.6.1, installed 1.9.0). Worth pinning before it silently changes scaling.
  - **Root `main.py` is already broken** — the Task 1 artifact move means `import main` now raises `FileNotFoundError: 'rul_model.pth'`, and `README.md` still tells you to run `uvicorn main:app --reload`. plan.md Task 6 step 4-5 already schedules deleting it and fixing the README; just don't be surprised by it before then.

---

## Amendment A — multi-engine customers — PLANNED HERE, code landed in Task 2a (below)

**What changed and why.** The plan said a customer owning several engines would get their **lowest-numbered** engine, and that whatever engine the model named would be **discarded**. So a customer owning engines 31 and 39 asking "how's engine 39?" would have been answered about **engine 31** — silently wrong. Replaced with real disambiguation: name any engine you own and get that one; name one you don't and you're told so, with **no substitution**.

**Caught it in time.** That logic existed only in plan.md prose — `app/agents/`, `app/tools/`, `app/data_access/`, `app/api/` are still empty. So Tasks 4-7 get built right the first time; nothing has to be unwound.

**This session edited `plan.md` and `log.md` only.** The code work is now **Task 2a** (see plan.md) — `Engine.label`, the seed rewrite, and a re-seed. **Task 2a must land before Task 4.**

**The design, in one line**: the model may *name* an engine; Python decides whether it can be *reached*. `dispatch_tool_call`'s `forced_engine_id` becomes `allowed_engine_ids: frozenset[int] | None` (keyword-only, no default). Three things in there are load-bearing and easy to get wrong — plan.md spells all three out:
  1. The allowlist check runs **before** the Engine is loaded. After it, "doesn't exist" and "not yours" give different error strings, which is a working oracle for enumerating the fleet.
  2. `if allowed_engine_ids is not None`, written literally — `frozenset()` is falsy, so the short version silently lets a customer who owns nothing through the gate.
  3. The engine's friendly name is top-level `engine_label`, never `"label"` — `degradation_stage()` already returns a `"label"` key holding the health stage, and it would be overwritten.

**Seeding changes** (Task 2a): 5 distinct demo engines instead of 3, and a new `customer4@demo.local` owning **two** of them. Against the real data that's engine 31 (RUL 8, Critical) as "Engine A" and engine 39 (RUL 142, Healthy) as "Engine B" — deliberately opposite extremes, and `Engine A` is deliberately the lower id, so the old lowest-id bug would answer "how's Engine B?" with *Critical*. Impossible to miss.

**Three previously-logged seed defects get fixed as a side effect** of that rewrite, not as separate work: the duplicate-engine pick, `stage_label`'s `>= 30` vs `> 30`, and the discarded `.update()` row count.

**`app.db` must be deleted and re-seeded** — there's no Alembic, and `create_all` adds missing tables but never missing *columns*, so an old `app.db` would fail later with `no such column: engines.label`. Task 2a adds a guard that detects this and exits rather than pretending it seeded.

**Tasks 2 and 3 stay `[x]`** even though their specs changed — a `[x]` means the code was written and verified, and unchecking would invite a rebuild of working code. Task 2a is the delta. Task 3's only change is a docstring word ("the engine" → "any engine"); `assert_engine_access` was already generic and needs **no behavior change**.

---

## Task 2a — Amendment A catch-up (`Engine.label` + re-seed) — DONE

- `app/db/models.py`: added `label` to `Engine` (nullable string, sits between `customer_id` and `true_rul_at_cutoff`). Seed is the only writer — it goes straight into the customer system prompt in Task 5, so keep it that way.
- `app/db/seed.py` now picks **5** engines instead of 3: two lowest RUL, two highest, one mid-band. The mid-band pick is made from what's *left* after the other four are removed, so all five are distinct by construction — that's the old duplicate-engine bug gone, not patched.
- Customer slots are now `list[tuple[str, list[int]]]` — single- and multi-engine accounts run one loop. `customer4@demo.local` owns two engines. Labels are handed out per customer in ascending engine-id order (`Engine A`, `Engine B`).
- Linking switched to `db.get` + attribute assignment; if an engine row is missing it raises now instead of quietly linking nothing.
- `stage_label` threshold fixed to `> 30`, matching `app/ml/inference.py` exactly.
- New `has_stale_schema()` runs **before** the "already seeded" check: an old DB with no `engines.label` prints a message and exits 1.
- Summary table gained a `label` column and prints one row per (customer, engine), plus a note about true-vs-predicted RUL.
- `app/auth/dependencies.py`: docstring wording only ("the engine" → "any engine"). No behavior change, as planned.

**All manual steps are done — nothing left for you here.**
- Ran the seed against the **old** `app.db` first: printed the stale-schema message, exit code 1. Guard confirmed working.
- Deleted `app.db`, re-seeded. Result matches what plan.md predicted exactly: 34 (RUL 7, Critical), 31 (8, Critical), 97 (82, Warning), 39 (142, Healthy), 25 (145, Healthy). customer1–3 own one engine each across the three stages; customer4 has `Engine A` = 31 (Critical) and `Engine B` = 39 (Healthy).
- Also checked: re-running the seed says "already seeded" and changes nothing; the DB rows carry the right customer + label and no unassigned engine picked up a label; `assert_engine_access` lets customer4 into both 31 and 39 and 403s on engine 25.
- Checked the **predicted** stages too, not just the true RUL — engine 31 → 6.73 Critical, engine 39 → 123.88 Healthy. So customer4's two engines differ in the answer chat will actually give, which is the whole point of that account.
- Edge-tested `pick_demo_engines` on synthetic data: still 5 distinct ids when nothing lands in the 30–100 band, and a clear error under 5 engines.

**For Task 4**: `Engine.label` exists and is populated. User ids are engineer=1, customer1–4 = 2–5. **customer4 (id 5) owns engines 31 and 39** — that's the account to test `allowed_engine_ids` with. Asking it about engine 25 must come back "That engine isn't on your account.", never engine 31's data.

**Code review run on Task 2a.** One in-scope finding, fixed: `pick_demo_engines`'s band filter used `30 <= rul` while `stage_label`/inference use `rul > 30`, so an engine at exactly RUL 30 could be picked as the "warning" demo and then printed as **Critical** — the same boundary bug one function up. Now `30 < rul <= 100`. Real data is unaffected (engine 97, RUL 82, still the pick — **no re-seed needed**). Note the residual, which is not a bug: if no remaining engine is genuinely in the Warning band, the closest-to-65 fallback still picks something that prints Critical. There's no Warning engine to find in that case; the fallback is doing what it says.

Other review findings are all **outside Task 2a** and left alone — `User.created_at` naive/aware mismatch, `app/config.py`'s blank-env-var handling (`JWT_EXPIRE_MINUTES=` → `int("")` at import), unpinned `scikit-learn` vs the 1.6.1-pickled scaler, and README still saying `uvicorn main:app --reload`. First three were already logged under Task 3; the README one is Task 6's step 4-5. One new: `app/ml/inference.py` never enforces the documented `(30, 15)` window shape — it happily returned a number for 12- and 3-row windows. **That's Task 4's to handle** in `get_rul_window`, which is the code that builds those windows.

---

## Task 4 — Data fetchers, tool registry, role gate — DONE

- `app/data_access/errors.py`: `EngineNotFoundError`, `NoSensorDataError` (plus a `DataAccessError` base so a caller can catch both at once). Plain exceptions, not HTTP ones — the caller decides how to surface them.
- `app/data_access/fetchers.py`: `get_rul_window(db, engine_id)` → raw (30, 15); `get_latest_cycle(db, engine_id)` → raw (15,). Columns are read **by name** from `FEATURE_COLS`, never by ORM order. Short histories are padded by repeating cycle 1 at the front, exactly like `get_last_window` in the notebook. Neither scales anything — that still happens only in `app/ml/inference.py`.
- Picked up the shape gap the last review left for this task: `get_rul_window` now raises if the window it built isn't (30, 15). It's the only code that builds those windows, so it's the place to check.
- `app/tools/specs.py`: the three tool descriptions the router model reads. Each takes only `engine_id` — the model never sees or invents sensor data. Plain dicts, so nothing outside `app/agents/` has to import the Gemini SDK; confirmed google-genai 2.19.0 accepts them as-is.
- `app/tools/registry.py`: `TOOL_REGISTRY` (spec + handler per tool) and `dispatch_tool_call(...)`. Order inside it is deliberate: coerce the engine id → allowlist → role gate → load engine + ownership check → fetch + predict. Every failure comes back as `{"ok": False, "error": ...}` instead of raising, so one bad engine number in a multi-engine question doesn't kill the good answers.
- `app/tools/access.py`: `get_tools_for_role(role)`. The only place a role becomes a tool list. Unknown role raises rather than quietly returning no tools.
- Both easy-to-get-wrong bits from Amendment A are in and tested: the allowlist is written `is not None` (empty set denies), and the engine's friendly name is top-level `engine_label`, never `label`.

**All manual steps are done — nothing left for you here.** Ran plan.md's 10-case checklist against the real seeded DB, all pass, plus a few extras. Highlights: engine 31 → Critical and 39 → Healthy for customer4, so its two engines really do give different answers; asking for someone else's engine and asking for engine 99999 return the *same* sentence, compared with `==` not by eye; the empty allowlist denies; `engine_id: true` is refused instead of being read as engine 1. Also proved the ownership check still denies on its own when the allowlist is wrong, and that a 4-cycle engine gets padded correctly (checked in a throwaway in-memory DB — the real `app.db` was only read, never written).

**Code review run at high effort.** One real bug in this task's code, fixed and re-verified: a huge `engine_id` (`1e20`, a 25-digit string) became a Python int too wide for SQLite, and the driver's `OverflowError` escaped the "errors are returned, not raised" rule and would have 500'd the chat turn. Now refused during coercion. Everything else the review flagged is outside Task 4 and already on the list — README still pointing at the deleted `main.py` (Task 6's job), `User.created_at` naive/aware, unpinned `scikit-learn`, and a blank `JWT_EXPIRE_MINUTES=` in `.env` crashing at import.

**For Task 5**: call `get_tools_for_role(user.role)` *before* building the request — a customer's request body then never contains the other two declarations. Then `dispatch_tool_call(tool_name, args, db, user, allowed_engine_ids=...)` — keyword-only with **no default**, so forgetting it is a `TypeError`, not silent full access. Pass `None` only for engineers; for a customer pass `frozenset` of their owned ids (and note that passing `None` for a customer now fails closed anyway). Results always carry `engine_id` + `engine_label`, and the communicator prompt should make the model name the engine it's reporting on — that's what makes a mis-resolved label visible. Treat the specs from `get_tools_for_role` as read-only; they're the registry's own dicts, not copies.

---

## Task 5 — Gemini agent pipeline — DONE

Seven new files in `app/agents/`. A chat turn now works like this: a **routing**
model picks which tools to run, Python runs every one of them, then a
**communicator** model writes the reply. Two model calls per turn, minimum.
- `gemini_client.py` — the only place that talks to Gemini. Retries on rate
  limits, then gives up cleanly with "the assistant is busy, try again".
- `prompts.py` — the four system prompts (routing + reply-writing, one pair per
  role).
- `tool_loop.py` — runs **every** tool call the model asks for, not just the
  first. Caps at 10 calls / 5 rounds per message.
- `orchestrator.py` (engineers) and `intake.py` (customers) — same loop, but
  customers are locked to the engines they own.
- `communicator.py` — writes the final reply. Gets **no tools**, so it can only
  describe what already happened.
- `session_store.py` — chat history in memory, 20 messages per user. **Wipes on
  server restart** — that's intended, not a bug.

**The model in plan.md is dead — this is the one thing to know.** `gemini-2.5-flash`
returns 404 for newer API keys ("no longer available to new users"). It still
shows up in the model list, so only a real call reveals it. Now using
**`gemini-3.5-flash-lite`** for both calls; defaults updated in `app/config.py`.
Full reasoning in **plan.md, Amendment B**.

**Rate limits — tested for real, and the published numbers are wrong.**
| model | actual free-tier limit | verdict |
|---|---|---|
| `gemini-3.5-flash-lite` | 15 per minute | what we use |
| `gemini-3.6-flash` | 5 per minute, **and a second cap at 20** | unusable here |
| `gemini-2.5-flash` | — | 404 |

Google's docs say 10/min for the Flash tier; the real 429 message says 5. Believe
the error, not the docs. `gemini-3.6-flash` looked like the better model but runs
dry after a handful of turns and stays dry — an early test run died on its first
question. **Practical ceiling: roughly 5-7 chat messages per minute.** Hitting
that shows "the assistant is busy" instead of an error page, which is correct
behaviour, not a fault.

**One real bug found and fixed.** Asked about an engine they don't own, a
customer was told their engines were "Engine A, Engine B, and **Engine C**" —
Engine C does not exist. Cause: the reply-writing step was told to list the
customer's engines but was never actually given the list, so it made one up. It
now receives the real list. Re-tested: "How is Engine C doing?" → *"That engine
isn't on your account. The engines on your account are Engine A and Engine B."*

**Verified against the real seeded database** (read-only; nothing written).
Engineers: single questions, three-tool reports, two engines at once, and a
clarifying question when no engine is named. Customers: single-engine
auto-filled, no jargon and no engine numbers ever shown. **customer4 (owns 31
"Engine A" Critical + 39 "Engine B" Healthy) is the Amendment A test and it
passes** — "How is Engine B?" reports Engine B's healthy verdict, never Engine
A's. Asking for someone else's engine is refused with no substitute offered.
Plus 19 offline tests of the call caps and history trimming, and a direct check
that the allowlist still refuses engines 25 / 99999 / 1 with identical wording.

Two things worth knowing, both fine but surprising:
- The routing model often refuses a foreign engine **itself**, so no tool call
  happens at all. The allowlist is still there underneath — tested separately.
- "Any update?" from a two-engine customer checks **both** engines rather than
  asking which. Not a guess, and both are theirs, so it was left alone.

**Pro models are not an option — don't re-check this.** `gemini-2.5-pro` 404s
like 2.5-flash; `gemini-pro-latest` and `gemini-3.1-pro-preview` both return
`limit: 0`, i.e. the free tier includes zero Pro requests. Not a quota you can
pace around — it needs billing enabled. If that ever happens, just set
`GEMINI_MODEL_COMMUNICATOR` in `.env` to a Pro model; routing stays on
flash-lite and no code changes.

**Nothing left for you to do.** Your Gemini key was already in `.env` and I
tested it directly — it works, and the limits above are that key's real numbers.

**For Task 6**: `run_engineer_turn(user, db, history, message)` → `{"reply",
"tool_calls"}`; `run_customer_turn(user, db, history, message)` → `{"reply"}`
only (deliberately different shapes, so a route can't leak engineer diagnostics
to a customer). History via `session_store.get_history(user.id)` /
`append_turn(user.id, message, reply)`. **Catch
`gemini_client.AssistantBusyError`** in the chat route and return `str(exc)` —
it's already a user-safe sentence. `config.validate_runtime_config()` still
needs calling at startup.

---

## Task 6 — API routes and app assembly — DONE

The app is now a real server you can log into and talk to. Four new files:

- `app/api/auth_routes.py` — log in (get a token), and "who am I" so the
  frontend knows whether to show the engineer or customer screen. **No signup
  route on purpose** — otherwise anyone could sign themselves up as an engineer.
- `app/api/chat_routes.py` — the two chat endpoints, one per role.
- `app/api/debug_routes.py` — engineer-only, gives the raw prediction with no AI
  in the way. This is how you check whether a chat answer is actually correct.
- `app/main.py` — starts everything up.

**Things worth knowing:**

- **The server refuses to start if the Gemini key or JWT secret is missing**, and
  tells you which one. Better than finding out mid-chat.
- **When Gemini is rate-limited, chat replies "The assistant is busy, try again
  in a few seconds" as a normal message** instead of showing an error page. That
  was a gap in the plan; it's handled now.
- **Login is form-encoded, not JSON.** Sending JSON gives a 422 error. This is
  the most likely thing to trip up Task 7.
- The old open endpoints are **not** coming back. Anyone could have read any
  engine through them, which would make all the access rules pointless.

**Both manual steps are done — nothing left for you here.**

- Engineer asked *"what is the RUL of engine 31 and engine 39?"* → **two**
  separate checks came back, one per engine, correct numbers for each. The
  multi-engine requirement works end to end.
- customer4 asked *"how are all my engines doing?"* → *"Engine A is in a state
  where we'd recommend arranging a service soon, while Engine B is running well
  and nothing needs attention right now."* Both engines, different verdicts, no
  engine numbers, no jargon.
- Also asked customer4 about engine 34 (someone else's) → it offered only their
  own two engines and gave **no data at all** about 34. No substitution.
- 45 other checks all pass: wrong password, expired/garbage tokens, a customer
  trying the engineer chat or the debug routes (403 every time), blank and
  oversized messages, and the real server booting under uvicorn.

**Deleted the old root `main.py`** (step 4) — nothing imported it and its model
files had already moved in Task 1, so it was dead code. **README rewritten**
(step 5) with the real setup order: install → data files → `.env` → seed →
`uvicorn app.main:app --reload`.

**Also refreshed `CLAUDE.md`**, which still described the deleted `main.py` and
told the next session to run it. All the still-true notes about keeping the ML
code in step with the notebook were kept as-is.

**Code review run at high effort.** One real bug fixed: logging in as
`Engineer@Demo.local` (any capital letter) was rejected as a wrong password.
Email is now matched case-insensitively.

Three findings left alone, all outside this task or accepted:
  - If Gemini runs out of quota **after** the tools already ran, those results
    are thrown away and the "what I checked" panel shows nothing. Not wrong,
    just under-reports. Fixing it means the agent code passing its partial
    results along — that's `app/agents/`'s call, not the route's.
  - Under heavy simultaneous use, slow chat turns could tie up the server's
    worker threads. Fine for a single-user demo; would matter with real traffic.
  - Chat history isn't locked, so two overlapping messages from the same user
    could interleave. Already noted in Task 5.

**For Task 7**: everything it needs is live and confirmed working. Start the
server with `uvicorn app.main:app --reload`. Right now `/` returns a small JSON
message saying the UI isn't built yet — dropping `index.html` into
`app/static/` replaces that automatically, no code change needed. Remember the
free tier is roughly **5-7 messages per minute**; clicking through the UI fast
will show "the assistant is busy", which is the limit working, not a bug.

---

## Task 7 — Frontend — DONE

Three files in `app/static/`: `index.html`, `app.js`, `style.css`. No
framework, no build step, and **no Python changed** — Task 6 had already wired
the server to serve them the moment they appeared.

One page holding a login screen and a chat screen. The token is kept in the
browser; on load the page asks the server "who am I?" and shows the engineer or
the customer screen based on the answer. A stale or expired token drops you
back to login by itself. Engineers get a collapsible **"What I checked"** box
under every reply, listing each tool that ran and which engine it ran on —
green when it answered, red when it refused.

**Login is form-encoded, not JSON.** Task 6's warning was right; JSON gives a
422. Confirmed both ways.

**Tested end to end against the running server, and all of it passes:**
- Engineer: one engine = 1 check, two engines = **2** checks, full report =
  **3** checks. Every number matches the debug routes exactly.
- customer1: *"Engine A is running well, nothing needs attention right now."*
  No numbers, no jargon, no engine number.
- customer4 (owns two): *"How's Engine B?"* answers about **Engine B**, not
  Engine A. The old lowest-id bug is still dead.
- Someone else's engine, or a made-up one: no data at all, ever.
- Customer poking the engineer-only routes: 403 every time.
- Restarting the server wipes the chat history, exactly as intended.

**Two things to know before testing this by hand:**

1. **Ask the ambiguous question first.** *"How's my engine?"* should reply
   "which one — Engine A or Engine B?", and it does — but only in a fresh
   conversation. If you've already asked about both engines, it answers from
   that earlier context and looks broken when it isn't. Restart the server
   between tries, or ask it first.
2. **Pace yourself.** Free Gemini is about 5–7 messages a minute. "The
   assistant is busy" is the limit working. The UI says so under the message
   box, and an empty "What I checked" panel after a busy reply is that same
   limit, not a broken panel.

**One deviation from the plan, and it is not a leak.** The plan wants the same
sentence word-for-word whether a customer asks about someone else's engine or a
made-up number. The *rule* is identical — engines 34, 99999, 25 and 1 all come
back with the exact same sentence and no data, checked directly. But the
sentence the customer finally reads is rewritten by the AI, and it phrases a
plausible number ("which engine did you mean?") differently from an outlandish
one ("that engine isn't on your account").

That is the AI guessing from how the number looks, not reading the database — a
customer's prompt only ever lists their own engines, so it has no way to know
what else exists. No engine data escapes either way. Strictly, though, it could
hint at roughly where the engine numbering stops. Fixing it means refusals
skipping the rewrite step, which is `app/agents/` work, not the frontend's.

**Left for you:** click through it in a browser — there's no browser on this
machine, so everything above went over the same HTTP calls the page makes,
not through the actual UI.
