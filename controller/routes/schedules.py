"""BRACE routes.schedules — extracted application responsibility."""

from typing import Literal, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from db import get_db, rows_to_list
from scheduler import SCHEDULER_TZ, build_trigger, next_run_times, scheduled_job_ids
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class ScheduleCreate(BaseModel):
    group_id: int
    cron_expr: str
    overlap_policy: Literal["queue", "skip"] = "queue"


class ScheduleUpdate(BaseModel):
    cron_expr: Optional[str] = None
    enabled: Optional[bool] = None
    overlap_policy: Optional[Literal["queue", "skip"]] = None


@router.get("/api/projects/{project_id}/schedules")
def list_schedules(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    rows = rows_to_list(
        conn.execute(
            "\n        SELECT s.*, tg.name AS group_name\n        FROM schedules s JOIN test_groups tg ON s.group_id = tg.id\n        WHERE s.project_id=? ORDER BY s.created_at DESC\n    ",
            (project_id,),
        ).fetchall()
    )
    for s in rows:
        s["enabled"] = bool(s.get("enabled"))
        s["next_runs"] = next_run_times(s["cron_expr"], 3) if s["enabled"] else []
        last = conn.execute(
            "\n            SELECT run_id, status, started_at, passed, failed FROM test_runs\n            WHERE project_id=? AND group_id=? AND triggered_by='scheduler'\n            ORDER BY started_at DESC, id DESC LIMIT 1\n        ",
            (project_id, s["group_id"]),
        ).fetchone()
        s["last_run"] = dict(last) if last else None
    conn.close()
    return {"schedules": rows, "timezone": SCHEDULER_TZ}


@router.get("/api/admin/scheduler-jobs")
def scheduler_jobs(user=Depends(mod_authentication._require_sys_admin)):
    """What APScheduler actually holds right now — for diagnosing 'my schedule
    didn't fire' without reading pod logs."""
    return {"timezone": SCHEDULER_TZ, "jobs": scheduled_job_ids()}


@router.post("/api/projects/{project_id}/schedules/preview")
def preview_cron(
    project_id: int, req: dict, user=Depends(mod_authentication._proj_viewer)
):
    """Validate a cron expression and show when it would next fire."""
    expr = (req.get("cron_expr") or "").strip()
    try:
        build_trigger(expr)
    except Exception as exc:
        raise HTTPException(400, f"Invalid cron expression: {exc}")
    return {
        "valid": True,
        "timezone": SCHEDULER_TZ,
        "next_runs": next_run_times(expr, 5),
    }


@router.post("/api/projects/{project_id}/schedules")
def create_schedule(
    project_id: int, req: ScheduleCreate, user=Depends(mod_authentication._proj_tester)
):
    import jobs as mod_jobs
    import runtime as mod_runtime

    try:
        build_trigger(req.cron_expr)
    except Exception as exc:
        raise HTTPException(400, f"Invalid cron expression: {exc}")
    conn = get_db()
    if not conn.execute(
        "SELECT 1 FROM test_groups WHERE id=? AND project_id=?",
        (req.group_id, project_id),
    ).fetchone():
        conn.close()
        raise HTTPException(404, "Suite not found in this project")
    conn.execute(
        "INSERT INTO schedules (project_id, group_id, cron_expr, overlap_policy) VALUES (?,?,?,?)",
        (project_id, req.group_id, req.cron_expr, req.overlap_policy),
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    mod_jobs._reload_all_jobs()
    mod_runtime.audit(
        user,
        "schedule.create",
        project_id=project_id,
        target=str(sid),
        cron=req.cron_expr,
        group_id=req.group_id,
    )
    return {"id": sid, "cron_expr": req.cron_expr}


@router.put("/api/schedules/{sid}")
def update_schedule(
    sid: int, req: ScheduleUpdate, user=Depends(mod_authentication._current_user)
):
    import authentication as mod_authentication
    import jobs as mod_jobs
    import runtime as mod_runtime

    conn = get_db()
    s = mod_authentication._owned_row(
        conn,
        "SELECT project_id FROM schedules WHERE id=?",
        sid,
        user,
        "edit",
        "Schedule not found",
    )
    if req.cron_expr:
        try:
            build_trigger(req.cron_expr)
        except Exception as exc:
            conn.close()
            raise HTTPException(400, f"Invalid cron expression: {exc}")
        conn.execute(
            "UPDATE schedules SET cron_expr=? WHERE id=?", (req.cron_expr, sid)
        )
    if req.enabled is not None:
        conn.execute(
            "UPDATE schedules SET enabled=? WHERE id=?", (1 if req.enabled else 0, sid)
        )
    if req.overlap_policy is not None:
        conn.execute(
            "UPDATE schedules SET overlap_policy=? WHERE id=?",
            (req.overlap_policy, sid),
        )
    conn.commit()
    conn.close()
    mod_jobs._reload_all_jobs()
    mod_runtime.audit(
        user,
        "schedule.update",
        project_id=s["project_id"],
        target=str(sid),
        cron=req.cron_expr,
        enabled=req.enabled,
    )
    return {"ok": True}


@router.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import jobs as mod_jobs
    import runtime as mod_runtime

    conn = get_db()
    s = mod_authentication._owned_row(
        conn,
        "SELECT project_id, cron_expr FROM schedules WHERE id=?",
        sid,
        user,
        "manage",
        "Schedule not found",
    )
    conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    mod_jobs._reload_all_jobs()
    mod_runtime.audit(
        user,
        "schedule.delete",
        project_id=s["project_id"],
        target=str(sid),
        cron=s["cron_expr"],
    )
    return {"ok": True}
