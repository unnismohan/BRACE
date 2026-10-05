"""BRACE authentication — extracted application responsibility."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from db import database, get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _make_token(username: str, system_role: str) -> str:
    import runtime as mod_runtime

    exp = datetime.now(timezone.utc) + timedelta(minutes=mod_runtime.JWT_EXPIRE_MIN)
    with database() as conn:
        row = conn.execute(
            "SELECT session_version FROM users WHERE username=?", (username,)
        ).fetchone()
    return jwt.encode(
        {
            "sub": username,
            "role": system_role,
            "sv": row["session_version"],
            "exp": exp,
        },
        mod_runtime.JWT_SECRET,
        algorithm=mod_runtime.JWT_ALGORITHM,
    )


def _authenticate(token: str, allow_password_change: bool = False) -> dict:
    import runtime as mod_runtime

    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(
            token, mod_runtime.JWT_SECRET, algorithms=[mod_runtime.JWT_ALGORITHM]
        )
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    with database() as conn:
        row = conn.execute(
            "SELECT username, system_role, session_version, must_change_password FROM users WHERE username=?",
            (payload.get("sub"),),
        ).fetchone()
    if not row or payload.get("sv") != row["session_version"]:
        raise HTTPException(401, "Session revoked. Sign in again.")
    if row["must_change_password"] and (not allow_password_change):
        raise HTTPException(403, "Password change required")
    return {"username": row["username"], "role": row["system_role"]}


def _current_user(request: Request, token: str = Depends(oauth2_scheme)) -> dict:
    return _authenticate(
        token, request.url.path in {"/api/auth/change-password", "/api/auth/logout"}
    )


def _require_sys_admin(user=Depends(_current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "System admin required")
    return user


def _get_project_role(
    project_id: int, username: str, user_system_role: str
) -> Optional[str]:
    """Return effective project role. Sys admins have project_admin everywhere."""
    if user_system_role == "admin":
        return "project_admin"
    conn = get_db()
    row = conn.execute(
        "SELECT pm.project_role FROM project_members pm\n           JOIN users u ON pm.user_id = u.id\n           WHERE pm.project_id=? AND u.username=?",
        (project_id, username),
    ).fetchone()
    conn.close()
    return row["project_role"] if row else None


def _require_project_role(*roles: str):
    """Dependency factory — requires one of the given project roles."""

    def dep(project_id: int, user=Depends(_current_user)):
        role = _get_project_role(project_id, user["username"], user["role"])
        if role not in roles:
            raise HTTPException(403, f"Requires project role: {', '.join(roles)}")
        return {**user, "project_role": role}

    return dep


_proj_viewer = _require_project_role("viewer", "tester", "project_admin")
_proj_tester = _require_project_role("tester", "project_admin")
_proj_admin = _require_project_role("project_admin")
ROLE_CAPS = {
    "viewer": {"view"},
    "tester": {"view", "run", "edit"},
    "project_admin": {"view", "run", "edit", "manage"},
}


def _require_cap(project_id: int, user: dict, cap: str) -> str:
    """Authorise `user` for `cap` on `project_id`, or raise 403.

    Used by resource-scoped endpoints. Every one of these previously carried its
    own inline role tuple, and they had already drifted apart — deleting a suite
    demanded project_admin in one place and accepted tester in another.
    """
    role = _get_project_role(project_id, user["username"], user["role"])
    if cap not in ROLE_CAPS.get(role or "", ()):
        raise HTTPException(
            403,
            f"Requires '{cap}' permission on this project (your role: {role or 'none'})",
        )
    return role


def _owned_row(conn, sql: str, ident, user: dict, cap: str, missing: str):
    """Fetch a resource row, authorise the caller against its project, or raise.

    Closes the connection on both failure paths — the callers open it before
    they can know which project the resource belongs to, and an early `raise`
    without this leaks the handle.
    """
    row = conn.execute(sql, (ident,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, missing)
    try:
        _require_cap(row["project_id"], user, cap)
    except HTTPException:
        conn.close()
        raise
    return row


def _run_or_404(conn, run_id: str, user):
    tr = conn.execute(
        "\n        SELECT tr.*, tg.name AS group_name\n        FROM test_runs tr LEFT JOIN test_groups tg ON tr.group_id = tg.id\n        WHERE tr.run_id=?\n    ",
        (run_id,),
    ).fetchone()
    if not tr:
        conn.close()
        raise HTTPException(404, "Run not found")
    if not _get_project_role(tr["project_id"], user["username"], user["role"]):
        conn.close()
        raise HTTPException(403)
    return tr


def _require_proj_admin(project_id: int, user: dict) -> None:
    _require_cap(project_id, user, "manage")
