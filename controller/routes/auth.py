"""BRACE routes.auth — extracted application responsibility."""

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel
from db import database, get_db, pwd_context
from security import login_limiter, validate_password
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePwRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    import authentication as mod_authentication

    login_limiter.check(request.client.host if request.client else "unknown")
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username=?", (req.username,)
    ).fetchone()
    conn.close()
    if not row or not pwd_context.verify(req.password, row["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = mod_authentication._make_token(row["username"], row["system_role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": row["username"],
        "system_role": row["system_role"],
        "must_change_password": bool(row["must_change_password"]),
    }


@router.put("/api/auth/change-password")
def change_password(
    req: ChangePwRequest, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication

    validate_password(req.new_password)
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username=?", (user["username"],)
    ).fetchone()
    if not row or not pwd_context.verify(req.old_password, row["password_hash"]):
        conn.close()
        raise HTTPException(400, "Current password incorrect")
    conn.execute(
        "UPDATE users SET password_hash=?, must_change_password=0, session_version=session_version+1 WHERE username=?",
        (pwd_context.hash(req.new_password), user["username"]),
    )
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "access_token": mod_authentication._make_token(user["username"], user["role"]),
    }


@router.post("/api/auth/logout")
def logout(user=Depends(mod_authentication._current_user)):
    """Revoke account sessions and drop the results cookie.

    Without this the cookie outlives the session, and on a shared machine the
    next person could still open the previous user's run reports by URL.
    """
    import runtime as mod_runtime

    with database() as conn:
        conn.execute(
            "UPDATE users SET session_version=session_version+1 WHERE username=?",
            (user["username"],),
        )
    resp = Response(status_code=204)
    resp.delete_cookie(mod_runtime.RESULTS_COOKIE, path="/results")
    return resp
