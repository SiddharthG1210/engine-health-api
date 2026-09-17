"""Fetch raw sensor history out of the DB and shape it for the ML models.

Division of labour: the agent decides *which* engine to ask about; these two
functions own *how* the data for that engine is fetched and shaped. Nothing
here is parameterised by window length or column selection -- both are fixed
properties of the trained models, so exposing them as arguments would only let
a caller build an input the models were never validated on.

Both functions return **raw, unscaled** values. Scaling happens exactly once,
inside `app.ml.inference`, using the `MinMaxScaler` that was fit at training
time. Scaling here as well would silently double-transform the input.
"""

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_access.errors import EngineNotFoundError, NoSensorDataError
from app.db.models import Engine, EngineCycle
from app.ml.feature_columns import FEATURE_COLS

# The LSTM was trained on fixed-length 30-cycle sequences (`sequence_length`
# in training.ipynb). Not configurable: the model's weights encode it.
SEQUENCE_LENGTH = 30


def _require_engine(db: Session, engine_id: int) -> Engine:
    """Load an engine row or raise `EngineNotFoundError`.

    `Session.get` checks the session's identity map first, so when the caller
    (e.g. `dispatch_tool_call`) has already loaded this engine in the same
    session, this costs no extra SQL round-trip.
    """
    engine = db.get(Engine, engine_id)
    if engine is None:
        raise EngineNotFoundError(engine_id)
    return engine


def _project(rows: list[EngineCycle]) -> np.ndarray:
    """Turn ORM rows into a float array of just the 15 model features.

    Column order comes from `FEATURE_COLS`, never from the ORM's own attribute
    order -- `EngineCycle` stores all 21 raw sensors, and the scaler was fit on
    a specific 15-column ordering. Reading by name keeps the two in step even
    if the model gains or loses columns.
    """
    return np.array(
        [[getattr(row, col) for col in FEATURE_COLS] for row in rows],
        dtype=np.float64,
    )


def get_rul_window(db: Session, engine_id: int) -> np.ndarray:
    """Return the engine's most recent 30 cycles as a raw (30, 15) array.

    Used by both `predict_rul` and `degradation_stage`, which share a model.

    Short histories are padded by **repeating the first cycle at the front**
    until there are 30 rows. This is not an arbitrary choice: it is exactly
    what `get_last_window` does in training.ipynb, and the reported RMSE of
    15.77 cycles was measured against inputs built that way. Zero-padding or
    edge-repeating the *last* row instead would change results silently, with
    no error to notice.

    Raises:
        EngineNotFoundError: no such engine.
        NoSensorDataError: the engine exists but has no recorded cycles.
    """
    _require_engine(db, engine_id)

    rows = (
        db.execute(
            select(EngineCycle)
            .where(EngineCycle.engine_id == engine_id)
            .order_by(EngineCycle.cycle.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NoSensorDataError(engine_id)

    window = _project(list(rows))

    if len(window) < SEQUENCE_LENGTH:
        # np.repeat on the 1-row slice window[0:1] keeps it 2-D, so vstack
        # lines the padding up above the real history rather than beside it.
        padding = np.repeat(window[0:1], SEQUENCE_LENGTH - len(window), axis=0)
        window = np.vstack([padding, window])

    window = window[-SEQUENCE_LENGTH:]

    # `app.ml.inference` does not check its input shape -- a mis-shaped array
    # reaches the LSTM and comes back as a plausible-looking wrong number. This
    # is the only code that builds those windows, so it is the place to assert
    # the contract. Unreachable unless FEATURE_COLS and the padding disagree.
    if window.shape != (SEQUENCE_LENGTH, len(FEATURE_COLS)):
        raise RuntimeError(
            f"Built a {window.shape} window for engine {engine_id}; "
            f"expected ({SEQUENCE_LENGTH}, {len(FEATURE_COLS)})"
        )

    return window


def get_latest_cycle(db: Session, engine_id: int) -> np.ndarray:
    """Return the engine's single most recent cycle as a raw (15,) array.

    Used by `anomaly_score`, which judges one reading at a time rather than a
    trend. "Most recent" is the highest `cycle` number, taken from the DB with
    an ORDER BY/LIMIT so no history is loaded just to discard it.

    Raises:
        EngineNotFoundError: no such engine.
        NoSensorDataError: the engine exists but has no recorded cycles.
    """
    _require_engine(db, engine_id)

    row = (
        db.execute(
            select(EngineCycle)
            .where(EngineCycle.engine_id == engine_id)
            .order_by(EngineCycle.cycle.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        raise NoSensorDataError(engine_id)

    # _project returns (1, 15) for a single row; the model wants a flat (15,).
    return _project([row])[0]
