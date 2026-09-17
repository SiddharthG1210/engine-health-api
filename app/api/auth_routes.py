"""Login and identity routes.

Two endpoints, and deliberately no third:

* `POST /api/auth/login` -- exchange email + password for a JWT.
* `GET  /api/auth/me`    -- who the bearer of this token is.

**There is no signup route, and that is a design decision, not an omission.**
Accounts exist only via `python -m app.db.seed`. A self-service registration
endpoint would need a way to choose a role, and any such path is a way for an
anonymous caller to hand themselves `engineer` -- which is the entire
authorisation model of this app, gone. New accounts are a seeding concern.

`/api/auth/me` exists so the frontend can decide which view to render (engineer
chat vs customer chat) without decoding the JWT client-side. Client-side
decoding would work, but it teaches the frontend to read a token it is not the
audience for, and it would keep believing a stale role after one changed in the
database. Asking the server costs one request on page load.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, verify_password
from app.db.base import get_db
from app.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A real bcrypt hash of a throwaway string. **Not a credential**: no account
# uses it and nothing authenticates against it. It exists so that a login with
# an unknown email still pays the cost of one bcrypt verify, the same as a
# login with a known email and the wrong password. Without it, "no such user"
# returns in microseconds while "wrong password" takes ~300ms, and that gap is
# a usable oracle for enumerating which email addresses have accounts.
_TIMING_DUMMY_HASH = "$2b$12$2DAYhwLiHLYK0Cm4WijgYebPGTY0YUISmb/faqXZK.PGftk0CyUHy"


class TokenResponse(BaseModel):
    """The OAuth2 password-flow response shape, which /docs' Authorize button
    and Task 7's `app.js` both read."""

    access_token: str
    token_type: str


class MeResponse(BaseModel):
    """Just enough identity for the frontend to pick a view.

    Nothing sensitive: no password hash, no engine list. A customer's engines
    are resolved server-side per turn from `Engine.customer_id` -- if the
    frontend held that list it would become a thing a client could try to edit.
    """

    id: int
    email: str
    role: str


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Verify credentials and issue a JWT.

    The request is **form-encoded**, not JSON -- that is what
    `OAuth2PasswordRequestForm` parses, and it is what makes /docs' Authorize
    button work against this route without extra wiring. The email goes in the
    field named `username`; OAuth2 names it that way and renaming it would mean
    hand-rolling the form. Posting JSON here returns 422, which is the single
    most likely mistake when writing the frontend.

    The email is matched **case-insensitively**. SQLite's `=` is case-sensitive
    for non-ASCII-folded text and the seed writes lowercase addresses, so a
    plain `==` turns "Engineer@Demo.local" into "incorrect email or password"
    for someone typing their own address with a capital letter. Compared with
    `func.lower` on both sides rather than lowercasing only the input, so it
    still matches if a future seed ever writes a mixed-case address.

    Raises:
        HTTPException: 401 for an unknown email *or* a wrong password, with the
            same wording for both. Telling the two apart would confirm which
            addresses have accounts.
    """
    email = form_data.username.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()

    # Ordering matters: verify *something* in every branch. See _TIMING_DUMMY_HASH.
    password_hash = user.password_hash if user else _TIMING_DUMMY_HASH
    password_ok = verify_password(form_data.password, password_hash)

    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The role is baked into the token for debuggability, but no route trusts
    # it: `get_current_user` re-reads the role from the database on every
    # request, so a role changed in the DB takes effect immediately rather than
    # when the token expires.
    token = create_access_token(user_id=user.id, role=user.role)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/me", response_model=MeResponse)
def read_me(user: User = Depends(get_current_user)) -> MeResponse:
    """Return the authenticated user's id, email and role.

    Any problem with the token -- expired, forged, or pointing at a deleted
    user -- surfaces as a 401 from `get_current_user`, which is the signal
    Task 7's frontend uses to clear its stored token and drop back to login.
    """
    return MeResponse(id=user.id, email=user.email, role=user.role)
