# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A FastAPI app that serves predictive-maintenance answers for turbofan engines (NASA C-MAPSS dataset) **through chat**, not through raw JSON endpoints. Two PyTorch models trained in [training.ipynb](training.ipynb) sit behind three internal tools, and a two-step Gemini pipeline decides which tools to run for a given question.

The three ML capabilities:

- **`predict_rul`** — 30 cycles × 15 sensors → Remaining Useful Life, in cycles. Core prediction, via a 2-layer LSTM (`EngineRUL`).
- **`anomaly_score`** — 1 cycle × 15 sensors → reconstruction error + `is_anomaly` bool, via an autoencoder (`EngineAutoencoder`) trained only on healthy-engine data (high reconstruction error = doesn't look like healthy behavior).
- **`degradation_stage`** — same input/model as `predict_rul`, plus a rule-based mapping of RUL → stage: Healthy (RUL > 100), Warning (30–100), Critical (< 30).

Two roles reach them differently: **engineers** get all three tools across the whole fleet; **customers** get `degradation_stage` only, on the engines assigned to them, answered in plain language. Gating happens at the tool-list level — a customer's model call never receives the other two function declarations.

Reported metrics (from README, not re-verified in code): RMSE 15.77 cycles on the official NASA test set; 63.5% anomaly catch rate on critical engines.

**[plan.md](plan.md) is the build plan and [log.md](log.md) is the per-task changelog. Read `log.md` first** — it records what each task actually did, including deviations from the plan.

## Layout

```
app/
  main.py         app factory: config check -> create_all -> routers -> static
  config.py       env-backed settings; roles.py: ROLE_TOOL_MAP (the role gate)
  api/            auth_routes, chat_routes, debug_routes
  agents/         Gemini pipeline: routing model -> tool loop -> communicator
  tools/          registry.py's dispatch_tool_call -- the LLM/system choke point
  data_access/    fetchers that build model input from the DB
  ml/             architectures, feature columns, inference (loads artifacts)
  db/             models, session, seed
  static/         frontend (Task 7)
ml_artifacts/     rul_model.pth, autoencoder_model.pth, anomaly_threshold.pkl, scaler.pkl
data/             test_FD001.txt, RUL_FD001.txt (gitignored)
```

## Running it

```bash
pip install -r requirements.txt
python -m app.db.seed          # creates app.db + demo accounts; safe to re-run
uvicorn app.main:app --reload  # single worker only
```

Needs a root `.env` with `GEMINI_API_KEY` and `JWT_SECRET_KEY` — `app/main.py` calls `config.validate_runtime_config()` at startup and refuses to boot without them. Demo accounts are all password `demo1234`: `engineer@demo.local`, `customer1@demo.local`–`customer4@demo.local`.

The four model artifacts must exist in `ml_artifacts/` — they are git-tracked outputs of [training.ipynb](training.ipynb), not generated at startup. If retraining, run the whole notebook top to bottom (needs `train_FD001.txt`, `test_FD001.txt`, `RUL_FD001.txt` from the [NASA C-MAPSS dataset](https://www.kaggle.com/datasets/behrad3d/nasa-cmaps)) and replace all four.

There is no test suite, linter, or CI config in this repo.

## Architecture / data flow you need across files

**`app/ml/` and the training notebook must stay in lockstep — nothing enforces this automatically:**

- **`FEATURE_COLS`** in `app/ml/feature_columns.py` (15 named sensors) must match, in order, the `sensor_cols` the notebook arrives at after dropping constant-value sensors (`sensor_1, 5, 10, 16, 18, 19` are dropped as constant in the FD001 data). If the dataset or drop logic changes, `FEATURE_COLS` must be updated to match, or the scaler/model will silently receive misaligned columns. `app/data_access/fetchers.py` reads DB columns **by name** from this list, never by ORM order.
- **`scaler.pkl`** is a `MinMaxScaler` fit once on training data in the notebook and reused as-is at inference — never refit. Scaling happens in `app/ml/inference.py` and nowhere else; the fetchers return raw values.
- **Model class definitions are duplicated** between `app/ml/architectures.py` and `training.ipynb` (`EngineRUL`, `EngineAutoencoder`). They must be kept structurally identical, since `load_state_dict` will fail or silently mismatch if architecture drifts (input_size=15, hidden_size=64, num_layers=2, dropout=0.2 for the LSTM; 15→8→4→8→15 for the autoencoder).
- **Sequence shape convention**: RUL/degradation expect exactly 30 cycles (`sequence_length=30` in the notebook's `create_sequences`/`get_last_window`) × 15 sensors, unsqueezed to `(1, 30, 15)`. `get_rul_window` pads short engine histories by repeating cycle 1, matching the notebook, and raises if the window it built isn't `(30, 15)`.
- **Anomaly threshold** (`anomaly_threshold.pkl`) is `mean + 2*std` of reconstruction error on healthy-only rows (RUL == 125, i.e. the capped max), computed once in the notebook — not recalculated at request time.
- RUL labels in training are capped at 125 cycles (`df['RUL'].clip(upper=125)`), which is why "healthy" is operationally defined as RUL == 125 for the autoencoder's training set.

**The access model, which several files cooperate to enforce:**

- **The model may *name* an engine; Python decides whether it can be *reached*.** `dispatch_tool_call` in `app/tools/registry.py` is the only place a model-requested action becomes a real one. It takes a keyword-only `allowed_engine_ids` with **no default** — forgetting it is a `TypeError`, not silent full access.
- `allowed_engine_ids=None` means unrestricted and appears in exactly two places: the engineer orchestrator and `app/api/debug_routes.py`. Both are behind `require_role("engineer")`. Widening either gate without changing the scope is a fleet-wide leak.
- A refused engine is **never** substituted for one the caller does own, and "doesn't exist" and "isn't yours" return the identical sentence — three different messages would let someone enumerate the fleet.
- Errors from dispatch are **returned, not raised**, so one bad engine number in a six-call question doesn't take the five good answers down with it.

## Known gaps to be aware of when editing

- **Chat history is in memory** (`app/agents/session_store.py`) and resets on restart. That is deliberate at this stage — and it is why the server must run with a **single worker**.
- **Free-tier Gemini limits are real**: roughly 5–7 chat messages per minute (`gemini-3.5-flash-lite`, 15 requests/min, and one turn costs at least two calls). Hitting it shows "The assistant is busy" — that's the limit working, not a broken route. See log.md, Task 5, for measured numbers; the published docs were wrong.
- `scikit-learn` is unpinned in `requirements.txt` while `scaler.pkl` was pickled with 1.6.1 — this already emits an `InconsistentVersionWarning` at startup. Worth pinning before it silently changes scaling.
- `User.created_at` writes an aware datetime into a naive `DateTime` column, so subtracting it from `datetime.now(timezone.utc)` raises `TypeError`.
- A blank `JWT_EXPIRE_MINUTES=` in `.env` makes `int("")` raise at import, bypassing the documented default of 60.
