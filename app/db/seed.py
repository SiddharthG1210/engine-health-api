"""Seed the demo database from the NASA C-MAPSS FD001 test set.

Run as: python -m app.db.seed

Reads `data/test_FD001.txt` (sensor history for every test engine) and
`data/RUL_FD001.txt` (true remaining RUL at the test cutoff, one integer per
line, line N = engine N). The true RUL is used only here, to pick a
Healthy/Warning/Critical demo spread -- it is never read by inference or
agent code (see `Engine.true_rul_at_cutoff` in models.py).

Five demo engines are picked and spread across four customer accounts: three
customers own one engine each, and `customer4@demo.local` owns *two* -- the
account that exercises engine disambiguation ("how's Engine B?") in chat.

Safe to re-run: if the database already has users, it prints a message and
exits without touching anything. Delete app.db to start over.
"""

import sys
from typing import Any, cast

import pandas as pd
from passlib.context import CryptContext
from sqlalchemy import inspect as sa_inspect

from app.config import DATA_DIR
from app.db.base import Base, SessionLocal, engine as db_engine
from app.db.models import Engine, EngineCycle, User

TEST_FILE = DATA_DIR / "test_FD001.txt"
RUL_FILE = DATA_DIR / "RUL_FD001.txt"

DEMO_PASSWORD = "demo1234"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

COLUMNS = (
    ["engine_id", "cycle", "setting_1", "setting_2", "setting_3"]
    + [f"sensor_{i}" for i in range(1, 22)]
)

# Customer-facing engine names, handed out in ascending engine-id order per
# customer. Only ever needs to be as long as one customer's engine count (2).
ENGINE_LABELS = ["Engine A", "Engine B", "Engine C", "Engine D"]


def stage_label(rul: int) -> str:
    """Map a true RUL in cycles to its health stage name.

    Thresholds are kept identical to `app.ml.inference.degradation_stage`
    (`> 100` Healthy, `> 30` Warning, else Critical) *including the strict
    comparison at 30*: this table's whole purpose is to predict what chat will
    say, so disagreeing at exactly RUL 30 would make it lie. Used only for the
    printed summary -- never fed back into any model.
    """
    if rul > 100:
        return "Healthy"
    if rul > 30:
        return "Warning"
    return "Critical"


def load_cycles(path) -> pd.DataFrame:
    """Parse the whitespace-separated, header-less C-MAPSS test file."""
    return pd.read_csv(path, sep=r"\s+", header=None, names=COLUMNS)


def load_true_rul(path) -> dict[int, int]:
    """Parse RUL_FD001.txt into `{engine_id: true_rul_at_cutoff}`."""
    with open(path) as f:
        values = [int(line.strip()) for line in f if line.strip()]
    # line N (1-indexed) is engine N's true RUL at the test cutoff
    return {engine_id: rul for engine_id, rul in enumerate(values, start=1)}


def pick_demo_engines(true_rul: dict[int, int]) -> dict[str, int]:
    """Pick 5 distinct demo engine ids dynamically from the parsed true RULs.

    Engine ids are never hardcoded -- the spread is derived from the data, so
    the demo still works if the dataset is swapped.

    - `critical` / `fleet_critical`: the two lowest RULs.
    - `healthy` / `fleet_healthy`: the two highest RULs.
    - `warning`: the middle of the 30-100 band, chosen from what is *left*
      after the four above are removed. Excluding them before choosing is what
      makes all five distinct structurally -- an earlier version only excluded
      on the fallback path, so an in-band pick could collide with the min or
      max and the second customer link silently overwrote the first.

    The `fleet_*` pair belongs to the multi-engine customer and comes from
    opposite extremes deliberately: if chat ever answers about the wrong one of
    the two, the difference must be impossible to miss.

    Raises `ValueError` if fewer than 5 engines were parsed.
    """
    if len(true_rul) < 5:
        raise ValueError(
            f"Need at least 5 engines to build the demo spread, got {len(true_rul)}."
        )

    # Sort ascending by RUL. `true_rul` is built in engine-id order and Python's
    # sort is stable, so ties break by engine_id -- don't "optimize" this away,
    # the demo's reproducibility across re-seeds depends on it.
    by_rul = sorted(true_rul.items(), key=lambda pair: pair[1])

    critical_id = by_rul[0][0]
    fleet_critical_id = by_rul[1][0]
    healthy_id = by_rul[-1][0]
    fleet_healthy_id = by_rul[-2][0]

    already_picked = {critical_id, fleet_critical_id, healthy_id, fleet_healthy_id}
    remaining = [pair for pair in by_rul if pair[0] not in already_picked]

    # `30 <` and not `30 <=`, to match stage_label / app.ml.inference: at exactly
    # RUL 30 the stage is Critical, so an inclusive bound here would pick a
    # "warning" demo engine that the summary table then prints as Critical.
    in_band = [pair for pair in remaining if 30 < pair[1] <= 100]
    if in_band:
        warning_id = in_band[len(in_band) // 2][0]
    else:
        # No *remaining* engine lands in-band -- fall back to whichever is
        # closest to the band's midpoint, so the spread degrades gracefully.
        warning_id = min(remaining, key=lambda pair: abs(pair[1] - 65))[0]

    picked = {
        "healthy": healthy_id,
        "warning": warning_id,
        "critical": critical_id,
        "fleet_healthy": fleet_healthy_id,
        "fleet_critical": fleet_critical_id,
    }
    assert len(set(picked.values())) == 5, f"demo engine ids collided: {picked}"
    return picked


def has_stale_schema() -> bool:
    """True if `app.db` predates the multi-engine schema (no `engines.label`).

    `Base.metadata.create_all` adds missing *tables* but never missing
    *columns*, and there is no Alembic in this project. Without this check an
    old database would take the ordinary "already seeded" path, exit 0, and
    only fail much later -- at chat time -- with `no such column: engines.label`.

    Uses SQLAlchemy's runtime schema inspector to read the *live* table
    definition rather than the ORM's idea of it.
    """
    columns = sa_inspect(db_engine).get_columns("engines")
    return not any(col["name"] == "label" for col in columns)


def main() -> None:
    if not TEST_FILE.exists() or not RUL_FILE.exists():
        print(
            f"Missing data files. Expected both:\n  {TEST_FILE}\n  {RUL_FILE}\n"
            "Download test_FD001.txt and RUL_FD001.txt from the NASA C-MAPSS "
            "dataset (see README) and place them in data/."
        )
        sys.exit(1)

    Base.metadata.create_all(bind=db_engine)

    # Must run *before* the "already seeded" check below: a pre-Amendment-A
    # database has users, so it would otherwise take that path and exit 0.
    if has_stale_schema():
        print(
            "app.db predates the multi-engine schema (no engines.label column) "
            "-- delete app.db and re-run this script."
        )
        sys.exit(1)

    db = SessionLocal()

    try:
        if db.query(User).first() is not None:
            print("Already seeded -- found existing users. Delete app.db to reset.")
            return

        print("Parsing data files...")
        cycles_df = load_cycles(TEST_FILE)
        true_rul = load_true_rul(RUL_FILE)

        try:
            demo = pick_demo_engines(true_rul)
        except ValueError as exc:
            print(exc)
            sys.exit(1)
        print(
            "Picked demo engines -> "
            + ", ".join(f"{name}: {eid}" for name, eid in demo.items())
        )

        print(f"Inserting {cycles_df['engine_id'].nunique()} engines / "
              f"{len(cycles_df)} sensor rows...")
        for engine_id in sorted(cycles_df["engine_id"].unique()):
            db.add(Engine(
                engine_id=int(engine_id),
                true_rul_at_cutoff=true_rul.get(int(engine_id)),
            ))
        db.flush()  # engines must exist before cycles reference them via FK

        # `cycles_df.to_dict(orient="records")` is typed by pandas-stubs as
        # `list[dict[Hashable, Any]]`, since a DataFrame's column labels are
        # *generally* allowed to be any hashable (int, tuple, ...), not just
        # str. `bulk_insert_mappings` wants `Iterable[Dict[str, Any]]`, and
        # because `Dict`'s key type is invariant, Pyright/Pylance flags the
        # call even though it's safe here: `cycles_df`'s columns always come
        # from the `COLUMNS` list above, which is all plain strings, so every
        # dict key really is a `str` at runtime. The cast just tells the type
        # checker what we already know -- it has no runtime effect.
        db.bulk_insert_mappings(
            EngineCycle,
            cast(list[dict[str, Any]], cycles_df.to_dict(orient="records")),
        )

        password_hash = pwd_context.hash(DEMO_PASSWORD)
        engineer = User(email="engineer@demo.local", password_hash=password_hash, role="engineer")
        db.add(engineer)

        # Fixed, deterministic credentials on purpose: there is no signup or
        # password reset anywhere in this project, so random passwords would
        # mean one lost terminal scrollback locks you out of your own demo.
        #
        # Every slot holds a *list* of engine ids, even the single-engine ones,
        # so single- and multi-engine customers run one code path.
        customer_slots: list[tuple[str, list[int]]] = [
            ("customer1@demo.local", [demo["healthy"]]),
            ("customer2@demo.local", [demo["warning"]]),
            ("customer3@demo.local", [demo["critical"]]),
            # The multi-engine account -- the only one that exercises
            # "which engine did you mean?" disambiguation.
            ("customer4@demo.local", [demo["fleet_critical"], demo["fleet_healthy"]]),
        ]

        # Rows for the printed summary table: (email, label, engine_id).
        assignments: list[tuple[str, str, int]] = []
        for email, engine_ids in customer_slots:
            user = User(email=email, password_hash=password_hash, role="customer")
            db.add(user)
            db.flush()  # need user.id before linking

            # Ascending id order is deliberate: it makes "Engine A" the lower
            # id, so the old "always use the lowest engine_id" behaviour would
            # resurface as *"how's Engine B?"* answered with Engine A's stage.
            for label, engine_id in zip(ENGINE_LABELS, sorted(engine_ids)):
                # db.get + attribute assignment, not query(...).update({...}):
                # a bulk update returns a row count that is easy to discard, so
                # a mismatched pair of data files would link nothing while still
                # printing a success table. A missing row fails loudly here.
                engine_row = db.get(Engine, engine_id)
                if engine_row is None:
                    raise RuntimeError(
                        f"Engine {engine_id} is in RUL_FD001.txt but has no rows in "
                        "test_FD001.txt -- the two data files do not match."
                    )
                engine_row.customer_id = user.id
                engine_row.label = label
                assignments.append((email, label, engine_id))

        db.commit()

        print("\nSeed complete. Demo accounts (all passwords: demo1234):\n")
        # `label` before `engine_id`: the label is the customer-facing key, the
        # id is only the debugging key.
        header = (
            f"{'email':<24}{'role':<12}{'label':<12}{'engine_id':<12}"
            f"{'true_rul':<10}{'stage'}"
        )
        print(header)
        print("-" * len(header))
        print(f"{'engineer@demo.local':<24}{'engineer':<12}{'--':<12}{'--':<12}{'--':<10}--")
        # One row per (customer, engine) pair, with the email repeated rather
        # than blanked on the multi-engine customer, so the table stays greppable.
        for email, label, engine_id in assignments:
            rul = true_rul[engine_id]
            print(
                f"{email:<24}{'customer':<12}{label:<12}{engine_id:<12}"
                f"{rul:<10}{stage_label(rul)}"
            )
        print(
            "\ncustomer4@demo.local is the multi-engine account (two engines).\n"
            "The stage column above comes from the TRUE RUL, while chat answers "
            "come from the PREDICTED RUL. They usually agree but are not\n"
            "guaranteed to -- check the debug route before calling a difference a bug."
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
