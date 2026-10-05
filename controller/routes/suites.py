"""BRACE routes.suites — extracted application responsibility."""

from typing import Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from db import get_db, row_to_dict, rows_to_list
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class GroupTCAdd(BaseModel):
    test_case_id: int
    order_idx: int = 0


@router.get("/api/projects/{project_id}/groups")
def list_groups(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    groups = conn.execute(
        "SELECT * FROM test_groups WHERE project_id=? ORDER BY name", (project_id,)
    ).fetchall()
    result = []
    for g in groups:
        members = conn.execute(
            "\n            SELECT tc.id, tc.tc_code, tc.name, tc.description, tc.suite_path,\n                   tc.last_run_status, gtc.order_idx\n            FROM test_cases tc\n            JOIN group_test_cases gtc ON tc.id = gtc.test_case_id\n            WHERE gtc.group_id=? ORDER BY gtc.order_idx\n        ",
            (g["id"],),
        ).fetchall()
        d = dict(g)
        d["test_cases"] = rows_to_list(members)
        d["tc_count"] = len(d["test_cases"])
        result.append(d)
    conn.close()
    return result


@router.post("/api/projects/{project_id}/groups")
def create_group(
    project_id: int, req: GroupCreate, user=Depends(mod_authentication._proj_tester)
):
    import runtime as mod_runtime

    conn = get_db()
    conn.execute(
        "INSERT INTO test_groups (project_id, name, description) VALUES (?,?,?)",
        (project_id, req.name, req.description),
    )
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM test_groups WHERE id=?", (uid,)).fetchone()
    conn.close()
    mod_runtime.audit(user, "suite.create", project_id=project_id, target=req.name)
    return row_to_dict(row)


@router.put("/api/groups/{gid}")
def update_group(
    gid: int, req: GroupCreate, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    g = mod_authentication._owned_row(
        conn,
        "SELECT project_id, name FROM test_groups WHERE id=?",
        gid,
        user,
        "edit",
        "Suite not found",
    )
    conn.execute(
        "UPDATE test_groups SET name=?, description=? WHERE id=?",
        (req.name, req.description, gid),
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "suite.update",
        project_id=g["project_id"],
        target=req.name,
        renamed_from=g["name"] if g["name"] != req.name else None,
    )
    return {"ok": True}


@router.delete("/api/groups/{gid}")
def delete_group(gid: int, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    g = mod_authentication._owned_row(
        conn,
        "SELECT project_id, name FROM test_groups WHERE id=?",
        gid,
        user,
        "edit",
        "Suite not found",
    )
    conn.execute("UPDATE test_runs SET group_id=NULL WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM schedules WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM test_groups WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user, "suite.delete", project_id=g["project_id"], target=g["name"]
    )
    return {"ok": True}


class GroupTCBulkAdd(BaseModel):
    test_case_ids: list[int]


@router.post("/api/groups/{gid}/test-cases/bulk")
def add_tcs_to_group(
    gid: int, req: GroupTCBulkAdd, user=Depends(mod_authentication._current_user)
):
    """Add many test cases to a suite in one transaction.

    The per-TC endpoint needed one HTTP round trip each, so adding a 75-case
    regression pack meant 75 requests and a partially-filled suite if one failed.
    """
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    g = mod_authentication._owned_row(
        conn,
        "SELECT project_id, name FROM test_groups WHERE id=?",
        gid,
        user,
        "edit",
        "Suite not found",
    )
    ids = list(dict.fromkeys(req.test_case_ids))
    if not ids:
        conn.close()
        raise HTTPException(400, "No test cases supplied")
    marks = ",".join("?" * len(ids))
    owned = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM test_cases WHERE id IN ({marks}) AND project_id=?",
            (*ids, g["project_id"]),
        ).fetchall()
    }
    rejected = [i for i in ids if i not in owned]
    if rejected:
        conn.close()
        raise HTTPException(
            404, f"{len(rejected)} test case(s) not found in this project"
        )
    start = conn.execute(
        "SELECT COALESCE(MAX(order_idx), -1) + 1 AS n FROM group_test_cases WHERE group_id=?",
        (gid,),
    ).fetchone()["n"]
    already = {
        r["test_case_id"]
        for r in conn.execute(
            "SELECT test_case_id FROM group_test_cases WHERE group_id=?", (gid,)
        ).fetchall()
    }
    added = 0
    for tc_id in ids:
        if tc_id in already:
            continue
        conn.execute(
            "INSERT INTO group_test_cases (group_id, test_case_id, order_idx) VALUES (?,?,?)",
            (gid, tc_id, start + added),
        )
        added += 1
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "suite.add_cases",
        project_id=g["project_id"],
        target=g["name"],
        added=added,
        requested=len(ids),
    )
    return {"added": added, "skipped_already_present": len(ids) - added}


@router.post("/api/groups/{gid}/test-cases")
def add_tc_to_group(
    gid: int, req: GroupTCAdd, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    g = mod_authentication._owned_row(
        conn,
        "SELECT project_id, name FROM test_groups WHERE id=?",
        gid,
        user,
        "edit",
        "Suite not found",
    )
    owned = conn.execute(
        "SELECT 1 FROM test_cases WHERE id=? AND project_id=?",
        (req.test_case_id, g["project_id"]),
    ).fetchone()
    if not owned:
        conn.close()
        raise HTTPException(404, "Test case not found in this project")
    conn.execute(
        "INSERT OR REPLACE INTO group_test_cases (group_id, test_case_id, order_idx) VALUES (?,?,?)",
        (gid, req.test_case_id, req.order_idx),
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user, "suite.add_cases", project_id=g["project_id"], target=g["name"], added=1
    )
    return {"ok": True}


@router.delete("/api/groups/{gid}/test-cases/{tc_id}")
def remove_tc_from_group(
    gid: int, tc_id: int, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    g = mod_authentication._owned_row(
        conn,
        "SELECT project_id, name FROM test_groups WHERE id=?",
        gid,
        user,
        "edit",
        "Suite not found",
    )
    conn.execute(
        "DELETE FROM group_test_cases WHERE group_id=? AND test_case_id=?", (gid, tc_id)
    )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "suite.remove_case",
        project_id=g["project_id"],
        target=g["name"],
        test_case_id=tc_id,
    )
    return {"ok": True}
