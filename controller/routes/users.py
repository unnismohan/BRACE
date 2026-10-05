"""BRACE routes.users — extracted application responsibility."""

import io
from typing import Optional
from fastapi import Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from db import get_db, pwd_context, rows_to_list
from security import validate_password
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class UserCreate(BaseModel):
    username: str
    password: str
    system_role: str = "user"
    full_name: Optional[str] = None
    email: Optional[str] = None


class UserUpdate(BaseModel):
    system_role: Optional[str] = None
    password: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None


@router.get("/api/users", dependencies=[Depends(mod_authentication._require_sys_admin)])
def list_users():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, system_role, full_name, email, must_change_password, created_at FROM users ORDER BY username"
    ).fetchall()
    conn.close()
    return rows_to_list(rows)


@router.post("/api/users")
def create_user(req: UserCreate, user=Depends(mod_authentication._require_sys_admin)):
    import runtime as mod_runtime

    validate_password(req.password)
    if req.system_role not in ("user", "admin"):
        raise HTTPException(400, "system_role must be user or admin")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, system_role, full_name, email, must_change_password) VALUES (?,?,?,?,?,1)",
            (
                req.username,
                pwd_context.hash(req.password),
                req.system_role,
                req.full_name,
                req.email,
            ),
        )
        conn.commit()
    except Exception:
        conn.close()
        raise HTTPException(409, "Username already exists")
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    mod_runtime.audit(
        user, "user.create", target=req.username, system_role=req.system_role
    )
    return {"id": uid, "username": req.username, "system_role": req.system_role}


@router.post("/api/users/bulk-csv")
async def bulk_create_users(
    file: UploadFile = File(...), user=Depends(mod_authentication._require_sys_admin)
):
    """CSV: username, password, system_role (opt), full_name (opt), email (opt)"""
    import runtime as mod_runtime
    import csv

    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    created, skipped = ([], [])
    conn = get_db()
    for row in reader:
        username = (row.get("username") or "").strip()
        password = (row.get("password") or "").strip()
        role = (row.get("system_role") or row.get("role") or "user").strip().lower()
        if not username or not password:
            skipped.append(username or "(empty)")
            continue
        if role not in ("user", "admin"):
            role = "user"
        try:
            validate_password(password)
            conn.execute(
                "INSERT INTO users (username, password_hash, system_role, full_name, email, must_change_password) VALUES (?,?,?,?,?,1)",
                (
                    username,
                    pwd_context.hash(password),
                    role,
                    (row.get("full_name") or "").strip() or None,
                    (row.get("email") or "").strip() or None,
                ),
            )
            conn.commit()
            created.append({"username": username, "system_role": role})
        except Exception:
            skipped.append(username)
    conn.close()
    mod_runtime.audit(
        user, "user.bulk_create", created=len(created), skipped=len(skipped)
    )
    return {"created": len(created), "skipped": skipped, "users": created}


@router.put("/api/users/{uid}")
def update_user(
    uid: int, req: UserUpdate, user=Depends(mod_authentication._require_sys_admin)
):
    import runtime as mod_runtime

    if req.password:
        validate_password(req.password)
    conn = get_db()
    before = conn.execute(
        "SELECT username, system_role FROM users WHERE id=?", (uid,)
    ).fetchone()
    if req.system_role:
        if req.system_role not in ("user", "admin"):
            conn.close()
            raise HTTPException(400, "Invalid system_role")
        conn.execute(
            "UPDATE users SET system_role=?, session_version=session_version+1 WHERE id=?",
            (req.system_role, uid),
        )
    if req.password:
        conn.execute(
            "UPDATE users SET password_hash=?, must_change_password=1, session_version=session_version+1 WHERE id=?",
            (pwd_context.hash(req.password), uid),
        )
    if req.full_name is not None:
        conn.execute("UPDATE users SET full_name=? WHERE id=?", (req.full_name, uid))
    if req.email is not None:
        conn.execute("UPDATE users SET email=? WHERE id=?", (req.email, uid))
    conn.commit()
    conn.close()
    target = before["username"] if before else str(uid)
    if req.system_role and before and (req.system_role != before["system_role"]):
        mod_runtime.audit(
            user,
            "user.role_change",
            target=target,
            **{"from": before["system_role"], "to": req.system_role}
        )
    mod_runtime.audit(
        user,
        "user.update",
        target=target,
        password_changed=bool(req.password),
        fields=[f for f in ("full_name", "email") if getattr(req, f) is not None],
    )
    return {"ok": True}


@router.delete("/api/users/{uid}")
def delete_user(uid: int, user=Depends(mod_authentication._require_sys_admin)):
    import runtime as mod_runtime

    conn = get_db()
    row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    mod_runtime.audit(user, "user.delete", target=row["username"] if row else str(uid))
    return {"deleted": uid}
