"""FastAPI auth dependencies: current-user resolution, role gating, engine ownership.

`get_current_user` fails closed on anything wrong with the token *or* the role
it carries -- a corrupted/unrecognized role in the DB must 401, not silently
grant a valid-but-powerless session. `assert_engine_access` fails closed too:
every role not explicitly recognized here is denied, so scoping a future role
(e.g. "technician") means adding a branch, not accidentally inheriting access.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db.base import get_db
from app.db.models import Engine, User
from app.roles import VALID_ROLES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def _credentials_error() -> HTTPException:
    """Build a fresh 401. Deliberately not a module-level singleton: raising the
    same exception object every time leaves `__traceback__` pointing at the last
    request's frame, keeping that request's bearer token and DB session
    reachable, and two threads raising it at once would stomp on each other.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise _credentials_error()

    user_id = payload.get("sub")
    if user_id is None:
        raise _credentials_error()

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise _credentials_error()

    user = db.get(User, user_id)
    if user is None:
        raise _credentials_error()

    if user.role not in VALID_ROLES:
        raise _credentials_error()

    return user


def require_role(*allowed: str):
    """Dependency factory: 403s unless the current user's role is in `allowed`."""

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this resource",
            )
        return user

    return _check


def assert_engine_access(user: User, engine: Engine) -> None:
    """Raise 403 unless `user` is allowed to see `engine`'s data.

    Engineers see any engine. Customers see only any engine assigned to them.
    Every other role -- including future ones -- is denied by default.
    """
    if user.role == "engineer":
        return
    if user.role == "customer" and engine.customer_id == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this engine",
    )
