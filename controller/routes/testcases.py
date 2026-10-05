"""BRACE routes.testcases — extracted application responsibility."""

import io
from datetime import datetime
from typing import Optional
from fastapi import Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from db import database, get_db, next_tc_code, row_to_dict, rows_to_list
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class QuarantineInput(BaseModel):
    quarantined: bool
    reason: str = ""


@router.put("/api/test-cases/{tc_id}/quarantine")
def quarantine_case(
    tc_id: int, req: QuarantineInput, user=Depends(mod_authentication._current_user)
):
    from runtime import audit

    with database() as conn:
        tc = conn.execute(
            "SELECT project_id,tc_code FROM test_cases WHERE id=?", (tc_id,)
        ).fetchone()
        if not tc:
            raise HTTPException(404, "Test case not found")
        mod_authentication._require_cap(tc["project_id"], user, "edit")
        if req.quarantined and not req.reason.strip():
            raise HTTPException(400, "Give a reason for quarantine")
        conn.execute(
            "UPDATE test_cases SET quarantined=?,quarantine_reason=? WHERE id=?",
            (
                int(req.quarantined),
                req.reason[:2000] if req.quarantined else None,
                tc_id,
            ),
        )
    audit(
        user,
        "tc.quarantine",
        project_id=tc["project_id"],
        target=tc["tc_code"],
        quarantined=req.quarantined,
    )
    return {"ok": True}


class TCCreate(BaseModel):
    name: str
    description: Optional[str] = None
    suite_path: Optional[str] = None
    extra_args: Optional[str] = None
    tags: Optional[str] = None


class TCUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    suite_path: Optional[str] = None
    extra_args: Optional[str] = None
    tags: Optional[str] = None


@router.get("/api/projects/{project_id}/test-cases")
def list_test_cases(
    project_id: int,
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(mod_authentication._proj_viewer),
):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM test_cases WHERE project_id=? ORDER BY tc_code LIMIT ? OFFSET ?",
        (project_id, limit if limit is not None else -1, offset),
    ).fetchall()
    conn.close()
    return rows_to_list(rows)


@router.get("/api/projects/{project_id}/tags")
def list_tags(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    """Every tag in use in this project, with how many cases carry it.

    Drives the filter dropdown and the "run everything tagged X" selector, so
    testers pick from what exists instead of guessing at spelling.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT tags FROM test_cases WHERE project_id=? AND COALESCE(tags,'') != ''",
        (project_id,),
    ).fetchall()
    conn.close()
    counts: dict = {}
    for r in rows:
        for t in (r["tags"] or "").strip(",").split(","):
            if t:
                counts[t] = counts.get(t, 0) + 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.items())]


@router.post("/api/projects/{project_id}/test-cases")
def create_test_case(
    project_id: int, req: TCCreate, user=Depends(mod_authentication._proj_tester)
):
    import runtime as mod_runtime

    conn = get_db()
    tc_code = next_tc_code(conn)
    conn.execute(
        "INSERT INTO test_cases (tc_code, project_id, name, description, suite_path, extra_args, tags) VALUES (?,?,?,?,?,?,?)",
        (
            tc_code,
            project_id,
            req.name,
            req.description,
            req.suite_path,
            req.extra_args,
            mod_runtime._norm_tags(req.tags or ""),
        ),
    )
    conn.commit()
    uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM test_cases WHERE id=?", (uid,)).fetchone()
    conn.close()
    mod_runtime.audit(
        user, "tc.create", project_id=project_id, target=tc_code, name=req.name
    )
    return row_to_dict(row)


@router.post("/api/projects/{project_id}/test-cases/bulk-csv")
async def bulk_create_test_cases(
    project_id: int,
    file: UploadFile = File(...),
    user=Depends(mod_authentication._proj_tester),
):
    """CSV: name, description (opt), suite_path (opt), extra_args (opt),
    suite (opt, pipe-separated), tags (opt, comma/space separated)"""
    import runtime as mod_runtime
    import csv

    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames:
        reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    created = []
    conn = get_db()
    suite_cache: dict = {}
    suites_created: list = []

    def _resolve_suite(suite_name: str) -> Optional[int]:
        key = suite_name.casefold()
        if key in suite_cache:
            return suite_cache[key]
        row = conn.execute(
            "SELECT id FROM test_groups WHERE project_id=? AND name=? COLLATE NOCASE",
            (project_id, suite_name),
        ).fetchone()
        if row:
            gid = row["id"]
        else:
            conn.execute(
                "INSERT INTO test_groups (project_id, name, description) VALUES (?,?,?)",
                (project_id, suite_name, "Created by bulk CSV upload"),
            )
            conn.commit()
            gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            suites_created.append(suite_name)
        suite_cache[key] = gid
        return gid

    for raw_row in reader:
        row = {
            (k or "").strip().lower(): v.strip() if isinstance(v, str) else v
            for k, v in raw_row.items()
        }
        name = (row.get("name") or "").strip()
        if not name:
            continue
        tc_code = next_tc_code(conn)
        conn.execute(
            "INSERT INTO test_cases (tc_code, project_id, name, description, suite_path, extra_args, tags) VALUES (?,?,?,?,?,?,?)",
            (
                tc_code,
                project_id,
                name,
                (row.get("description") or "").strip() or None,
                (row.get("suite_path") or "").strip() or None,
                (row.get("extra_args") or "").strip() or None,
                mod_runtime._norm_tags(row.get("tags") or ""),
            ),
        )
        conn.commit()
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        assigned = []
        raw_suites = (row.get("suite") or row.get("suites") or "").strip()
        for suite_name in [s.strip() for s in raw_suites.split("|") if s.strip()]:
            gid = _resolve_suite(suite_name)
            order = conn.execute(
                "SELECT COALESCE(MAX(order_idx), -1) + 1 AS n FROM group_test_cases WHERE group_id=?",
                (gid,),
            ).fetchone()["n"]
            conn.execute(
                "INSERT OR REPLACE INTO group_test_cases (group_id, test_case_id, order_idx) VALUES (?,?,?)",
                (gid, uid, order),
            )
            conn.commit()
            assigned.append(suite_name)
        created.append(
            {"id": uid, "tc_code": tc_code, "name": name, "suites": assigned}
        )
    conn.close()
    mod_runtime.audit(
        user,
        "tc.bulk_create",
        project_id=project_id,
        created=len(created),
        suites_created=suites_created,
        file=file.filename,
    )
    return {
        "created": len(created),
        "suites_created": suites_created,
        "suites_used": len(suite_cache),
        "test_cases": created,
    }


@router.get("/api/test-cases/{tc_id}/history")
def test_case_history(
    tc_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(mod_authentication._current_user),
):
    """Execution timeline for one test case, newest first, plus summary stats.

    Answers the question the aggregate flakiness list cannot: has this been
    failing forever, or did it start failing on a particular date?
    """
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    tc = conn.execute(
        "SELECT id, tc_code, name, project_id FROM test_cases WHERE id=?", (tc_id,)
    ).fetchone()
    if not tc:
        conn.close()
        raise HTTPException(404, "Test case not found")
    if not mod_authentication._get_project_role(
        tc["project_id"], user["username"], user["role"]
    ):
        conn.close()
        raise HTTPException(403)
    rows = rows_to_list(
        conn.execute(
            "\n        SELECT tri.status, tri.started_at, tri.finished_at, tri.rf_run_id,\n               tr.run_id, tr.run_name, tr.triggered_by, tr.started_at AS run_started\n        FROM test_run_items tri\n        JOIN test_runs tr ON tr.run_id = tri.run_id\n        WHERE tri.test_case_id=? AND tr.project_id=?\n        ORDER BY tr.started_at DESC, tr.id DESC, tri.id DESC LIMIT ? OFFSET ?\n    ",
            (tc_id, tc["project_id"], limit, offset),
        ).fetchall()
    )
    total = conn.execute(
        "SELECT COUNT(*) FROM test_run_items i JOIN test_runs r ON r.run_id=i.run_id WHERE i.test_case_id=? AND r.project_id=?",
        (tc_id, tc["project_id"]),
    ).fetchone()[0]
    conn.close()
    results_dir = mod_runtime._project_results(tc["project_id"])
    for r in rows:
        d = None
        if r["started_at"] and r["finished_at"]:
            try:
                d = (
                    datetime.fromisoformat(r["finished_at"])
                    - datetime.fromisoformat(r["started_at"])
                ).total_seconds()
            except ValueError:
                d = None
        r["duration_sec"] = round(d, 1) if d is not None else None
        r["has_log"] = (
            bool(r["rf_run_id"])
            and (results_dir / r["run_id"] / r["rf_run_id"] / "log.html").exists()
        )
    done = [r for r in rows if r["status"] in ("passed", "failed")]
    passed = sum((1 for r in done if r["status"] == "passed"))
    durs = [r["duration_sec"] for r in done if r["duration_sec"] is not None]
    streak, streak_status = (0, None)
    for r in done:
        if streak_status is None:
            streak_status, streak = (r["status"], 1)
        elif r["status"] == streak_status:
            streak += 1
        else:
            break
    first_failure = None
    if streak_status == "failed":
        for r in reversed(done[:streak]):
            first_failure = r["run_started"]
            break
    return {
        "test_case": {"id": tc["id"], "tc_code": tc["tc_code"], "name": tc["name"]},
        "history": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "stats_scope": "page",
        "stats": {
            "executions": len(done),
            "passed": passed,
            "failed": len(done) - passed,
            "pass_rate": round(passed / len(done) * 100, 1) if done else None,
            "streak": streak,
            "streak_status": streak_status,
            "failing_since": first_failure,
            "avg_duration": round(sum(durs) / len(durs), 1) if durs else None,
            "last_status": done[0]["status"] if done else None,
        },
    }


@router.put("/api/test-cases/{tc_id}")
def update_test_case(
    tc_id: int, req: TCUpdate, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    tc = conn.execute(
        "SELECT project_id, tc_code, source_path FROM test_cases WHERE id=?", (tc_id,)
    ).fetchone()
    if not tc:
        conn.close()
        raise HTTPException(404)
    try:
        mod_authentication._require_cap(tc["project_id"], user, "edit")
    except HTTPException:
        conn.close()
        raise
    for field, val in [
        ("name", req.name),
        ("description", req.description),
        ("suite_path", req.suite_path),
        ("extra_args", req.extra_args),
        ("tags", None if req.tags is None else mod_runtime._norm_tags(req.tags)),
    ]:
        if val is not None:
            conn.execute(f"UPDATE test_cases SET {field}=? WHERE id=?", (val, tc_id))
    conn.commit()
    row = conn.execute("SELECT * FROM test_cases WHERE id=?", (tc_id,)).fetchone()
    conn.close()
    mod_runtime.audit(
        user,
        "tc.update",
        project_id=tc["project_id"],
        target=tc["tc_code"],
        fields=[
            f
            for f in ("name", "description", "suite_path", "extra_args", "tags")
            if getattr(req, f) is not None
        ],
        git_synced=bool(tc["source_path"]),
    )
    return row_to_dict(row)


@router.delete("/api/test-cases/{tc_id}")
def delete_test_case(tc_id: int, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    tc = conn.execute(
        "SELECT project_id, tc_code FROM test_cases WHERE id=?", (tc_id,)
    ).fetchone()
    if not tc:
        conn.close()
        raise HTTPException(404)
    try:
        mod_authentication._require_cap(tc["project_id"], user, "manage")
    except HTTPException:
        conn.close()
        raise
    conn.execute(
        "UPDATE test_run_items SET test_case_id=NULL WHERE test_case_id=?", (tc_id,)
    )
    conn.execute("DELETE FROM test_cases WHERE id=?", (tc_id,))
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user, "tc.delete", project_id=tc["project_id"], target=tc["tc_code"]
    )
    return {"deleted": tc_id}
