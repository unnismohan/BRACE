"""BRACE routes.members — extracted application responsibility."""

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from db import get_db, rows_to_list
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class MemberAdd(BaseModel):
    username: str
    project_role: str = "viewer"


@router.get("/api/projects/{project_id}/members")
def list_members(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    rows = conn.execute(
        "\n        SELECT u.id, u.username, u.full_name, u.email, pm.project_role\n        FROM project_members pm JOIN users u ON pm.user_id = u.id\n        WHERE pm.project_id=? ORDER BY u.username\n    ",
        (project_id,),
    ).fetchall()
    conn.close()
    return rows_to_list(rows)


class MemberBulkAdd(BaseModel):
    usernames: list[str]
    project_role: str = "viewer"


@router.post("/api/projects/{project_id}/members/bulk")
def add_members_bulk(
    project_id: int, req: MemberBulkAdd, user=Depends(mod_authentication._proj_admin)
):
    """Add several users to a project at one role, in a single transaction."""
    import runtime as mod_runtime

    if req.project_role not in ("viewer", "tester", "project_admin"):
        raise HTTPException(400, "project_role must be viewer | tester | project_admin")
    names = [n.strip() for n in dict.fromkeys(req.usernames) if n.strip()]
    if not names:
        raise HTTPException(400, "No usernames supplied")
    conn = get_db()
    marks = ",".join("?" * len(names))
    found = {
        r["username"]: r["id"]
        for r in conn.execute(
            f"SELECT id, username FROM users WHERE username IN ({marks})", names
        ).fetchall()
    }
    missing = [n for n in names if n not in found]
    if missing:
        conn.close()
        raise HTTPException(404, f"Unknown user(s): {', '.join(missing[:10])}")
    for uid in found.values():
        conn.execute(
            "INSERT OR REPLACE INTO project_members (project_id, user_id, project_role) VALUES (?,?,?)",
            (project_id, uid, req.project_role),
        )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "member.add_bulk",
        project_id=project_id,
        usernames=list(found),
        role=req.project_role,
    )
    return {"added": len(found), "role": req.project_role}


@router.post("/api/projects/{project_id}/members")
def add_member(
    project_id: int, req: MemberAdd, user=Depends(mod_authentication._proj_admin)
):
    import runtime as mod_runtime

    if req.project_role not in ("viewer", "tester", "project_admin"):
        raise HTTPException(400, "project_role must be viewer | tester | project_admin")
    conn = get_db()
    u = conn.execute(
        "SELECT id FROM users WHERE username=?", (req.username,)
    ).fetchone()
    if not u:
        conn.close()
        raise HTTPException(404, f"User '{req.username}' not found")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_members (project_id, user_id, project_role) VALUES (?,?,?)",
            (project_id, u["id"], req.project_role),
        )
        conn.commit()
    finally:
        conn.close()
    mod_runtime.audit(
        user,
        "member.add",
        project_id=project_id,
        target=req.username,
        role=req.project_role,
    )
    return {"ok": True}


@router.put("/api/projects/{project_id}/members/{uid}")
def update_member(
    project_id: int,
    uid: int,
    req: MemberAdd,
    user=Depends(mod_authentication._proj_admin),
):
    import runtime as mod_runtime

    if req.project_role not in ("viewer", "tester", "project_admin"):
        raise HTTPException(400, "Invalid project_role")
    conn = get_db()
    prev = conn.execute(
        "SELECT project_role FROM project_members WHERE project_id=? AND user_id=?",
        (project_id, uid),
    ).fetchone()
    who = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    conn.execute(
        "UPDATE project_members SET project_role=? WHERE project_id=? AND user_id=?",
        (req.project_role, project_id, uid),
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "member.role_change",
        project_id=project_id,
        target=who["username"] if who else str(uid),
        **{"from": prev["project_role"] if prev else None, "to": req.project_role},
    )
    return {"ok": True}


@router.delete("/api/projects/{project_id}/members/{uid}")
def remove_member(
    project_id: int, uid: int, user=Depends(mod_authentication._proj_admin)
):
    import runtime as mod_runtime

    conn = get_db()
    who = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    conn.execute(
        "DELETE FROM project_members WHERE project_id=? AND user_id=?",
        (project_id, uid),
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "member.remove",
        project_id=project_id,
        target=who["username"] if who else str(uid),
    )
    return {"ok": True}
