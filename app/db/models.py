"""ORM models: users, engines, and their raw sensor cycle history.

`Engine.true_rul_at_cutoff` and `EngineCycle` store ground truth / raw
telemetry for reference, seeding, and verification only -- inference code
(`app.ml`, `app.data_access`) must never read `true_rul_at_cutoff`, and must
project `EngineCycle` rows onto `app.ml.feature_columns.FEATURE_COLS` rather
than assuming column order.
"""

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Plain string, not a DB enum -- adding a role (e.g. "technician") later
    # is then just a change to app.roles.ROLE_TOOL_MAP, no migration needed.
    role: Mapped[str] = mapped_column(String, nullable=False)
    # Stored as UTC but *without* a tzinfo label, deliberately.
    #
    # SQLite has no real date type -- it keeps datetimes as text, and a plain
    # DateTime column has nowhere to put a timezone. Writing a labelled
    # ("aware") value here would therefore still read back unlabelled
    # ("naive"), and `datetime.now(timezone.utc) - user.created_at` would raise
    # TypeError, since Python refuses to subtract a naive datetime from an
    # aware one rather than guess how far apart they are.
    #
    # Stripping the label on the way in makes both ends naive, so that
    # subtraction works. The value is still UTC -- anything comparing against
    # it must be UTC too (`datetime.now(timezone.utc).replace(tzinfo=None)`),
    # never a bare `datetime.now()`, which is local time.
    #
    # DateTime(timezone=True) is NOT the fix: SQLAlchemy's SQLite dialect drops
    # the offset regardless. It would only start holding on Postgres.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class Engine(Base):
    __tablename__ = "engines"

    # The test set's own engine numbering -- used as-is, not a surrogate key.
    engine_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # Customer-facing name ("Engine A"), scoped per customer -- NOT globally
    # unique, and NULL for unassigned engines (engineers use the numeric id).
    # Seed-written only: this string is interpolated verbatim into the customer
    # system prompt, so a user-editable label would be a prompt-injection channel.
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    # Reference/verification only -- never fed to inference or agent code.
    true_rul_at_cutoff: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EngineCycle(Base):
    __tablename__ = "engine_cycles"

    engine_id: Mapped[int] = mapped_column(
        ForeignKey("engines.engine_id"), primary_key=True
    )
    cycle: Mapped[int] = mapped_column(Integer, primary_key=True)

    setting_1: Mapped[float] = mapped_column(Float)
    setting_2: Mapped[float] = mapped_column(Float)
    setting_3: Mapped[float] = mapped_column(Float)

    # All 21 raw sensors are stored, not just the 15 in FEATURE_COLS --
    # storage is a separate concern from feature selection.
    sensor_1: Mapped[float] = mapped_column(Float)
    sensor_2: Mapped[float] = mapped_column(Float)
    sensor_3: Mapped[float] = mapped_column(Float)
    sensor_4: Mapped[float] = mapped_column(Float)
    sensor_5: Mapped[float] = mapped_column(Float)
    sensor_6: Mapped[float] = mapped_column(Float)
    sensor_7: Mapped[float] = mapped_column(Float)
    sensor_8: Mapped[float] = mapped_column(Float)
    sensor_9: Mapped[float] = mapped_column(Float)
    sensor_10: Mapped[float] = mapped_column(Float)
    sensor_11: Mapped[float] = mapped_column(Float)
    sensor_12: Mapped[float] = mapped_column(Float)
    sensor_13: Mapped[float] = mapped_column(Float)
    sensor_14: Mapped[float] = mapped_column(Float)
    sensor_15: Mapped[float] = mapped_column(Float)
    sensor_16: Mapped[float] = mapped_column(Float)
    sensor_17: Mapped[float] = mapped_column(Float)
    sensor_18: Mapped[float] = mapped_column(Float)
    sensor_19: Mapped[float] = mapped_column(Float)
    sensor_20: Mapped[float] = mapped_column(Float)
    sensor_21: Mapped[float] = mapped_column(Float)
