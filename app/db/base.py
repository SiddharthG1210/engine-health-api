"""SQLAlchemy engine/session setup, driven by `app.config.DATABASE_URL`."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# `check_same_thread=False` is needed for SQLite specifically -- FastAPI can
# hand a request to a different thread than the one that opened the session.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yield a session, always close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
