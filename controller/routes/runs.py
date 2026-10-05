import json

"""BRACE routes.runs — extracted application responsibility."""
import re
from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from db import database, get_db, rows_to_list
from execution import terminate_tree
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class RunRequest(BaseModel):
    tc_ids: Optional[list[int]] = None
    group_id: Optional[int] = None
    tag: Optional[str] = None
    run_name: Optional[str] = None
    extra_args: Optional[str] = None
    parallel: Optional[int] = None
    profile_id: Optional[int] = None
    retry_count: int = Field(0, ge=0, le=2)
    include_quarantined: bool = False


@router.post("/api/projects/{project_id}/runs")
async def trigger_run(
    project_id: int, req: RunRequest, user=Depends(mod_authentication._proj_tester)
):
    import execution_engine as mod_execution_engine
    import runtime as mod_runtime

    conn = get_db()
    if req.group_id:
        group_name = conn.execute(
            "SELECT name FROM test_groups WHERE id=? AND project_id=?",
            (req.group_id, project_id),
        ).fetchone()
        if not group_name:
            conn.close()
            raise HTTPException(404, "Suite not found in this project")
        members = conn.execute(
            "\n            SELECT tc.* FROM test_cases tc\n            JOIN group_test_cases gtc ON tc.id = gtc.test_case_id\n            WHERE gtc.group_id=? AND tc.project_id=? ORDER BY gtc.order_idx\n        ",
            (req.group_id, project_id),
        ).fetchall()
        tcs = rows_to_list(members)
        default_name = f"{group_name['name']} run"
    elif req.tc_ids:
        placeholders = ",".join("?" * len(req.tc_ids))
        members = conn.execute(
            f"SELECT * FROM test_cases WHERE id IN ({placeholders}) AND project_id=?",
            (*req.tc_ids, project_id),
        ).fetchall()
        tcs = rows_to_list(members)
        default_name = f"Ad-hoc run ({len(tcs)} TCs)"
    elif req.tag:
        tag = mod_runtime._norm_tag(req.tag)
        if not tag:
            conn.close()
            raise HTTPException(400, "Invalid tag")
        members = conn.execute(
            "SELECT * FROM test_cases WHERE project_id=? AND COALESCE(tags,'') LIKE ? ORDER BY tc_code",
            (project_id, f"%,{tag},%"),
        ).fetchall()
        tcs = rows_to_list(members)
        default_name = f"Tag run: {tag}"
    else:
        conn.close()
        raise HTTPException(400, "Provide tc_ids, group_id or tag")
    if not req.include_quarantined:
        tcs = [tc for tc in tcs if not tc.get("quarantined")]
    if not tcs:
        conn.close()
        raise HTTPException(400, "No test cases found")
    conn.close()
    out = mod_execution_engine._start_run(
        project_id,
        tcs,
        req.run_name or default_name,
        user["username"],
        req.extra_args,
        group_id=req.group_id,
        parallel=req.parallel,
        profile_id=req.profile_id,
        retry_count=req.retry_count,
    )
    mod_runtime.audit(
        user,
        "run.trigger",
        project_id=project_id,
        target=out["run_id"],
        total=out["total"],
        group_id=req.group_id,
        tag=req.tag,
        parallel=out["parallel"],
    )
    return out


class RerunReq(BaseModel):
    include_cancelled: bool = False


@router.post("/api/runs/{run_id}/rerun-failed")
async def rerun_failed(
    run_id: str, req: RerunReq, user=Depends(mod_authentication._current_user)
):
    """Re-run only the test cases that failed in a previous run.

    Each BRACE test case is its own robot invocation, so this is simply a new
    run over the failed subset — no --rerunfailed bookkeeping required.
    """
    import authentication as mod_authentication
    import execution_engine as mod_execution_engine
    import runtime as mod_runtime

    conn = get_db()
    tr = conn.execute(
        "SELECT project_id, run_name, profile_snapshot, retry_limit FROM test_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not tr:
        conn.close()
        raise HTTPException(404, "Run not found")
    project_id = tr["project_id"]
    try:
        mod_authentication._require_cap(project_id, user, "run")
    except HTTPException:
        conn.close()
        raise
    wanted = ["failed"] + (["cancelled"] if req.include_cancelled else [])
    marks = ",".join("?" * len(wanted))
    tcs = rows_to_list(
        conn.execute(
            f"SELECT DISTINCT tc.* FROM test_run_items tri\n            JOIN test_cases tc ON tc.id = tri.test_case_id\n            WHERE tri.run_id=? AND tri.status IN ({marks}) AND tc.project_id=?\n            ORDER BY tc.tc_code",
            (run_id, *wanted, project_id),
        ).fetchall()
    )
    conn.close()
    if not tcs:
        raise HTTPException(400, "Nothing to re-run — no failed test cases in that run")
    base = tr["run_name"] or run_id
    base = re.sub("\\s*\\(retry \\d+\\)$", "", base)
    out = mod_execution_engine._start_run(
        project_id,
        tcs,
        f"{base} (retry {len(tcs)})",
        user["username"],
        None,
        rerun_of=run_id,
        profile_id=json.loads(tr["profile_snapshot"] or "{}").get("id"),
        retry_count=tr["retry_limit"],
    )
    mod_runtime.audit(
        user,
        "run.rerun_failed",
        project_id=project_id,
        target=out["run_id"],
        rerun_of=run_id,
        total=out["total"],
    )
    return out


@router.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    tr = mod_authentication._owned_row(
        conn,
        "SELECT project_id FROM test_runs WHERE run_id=?",
        run_id,
        user,
        "run",
        "Run not found",
    )
    mod_runtime._cancelled_runs.add(run_id)
    for key in list(mod_runtime._active_procs.keys()):
        if key.startswith(run_id):
            proc = mod_runtime._active_procs.get(key)
            if proc:
                await terminate_tree(proc)
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE test_runs SET status='cancelled', finished_at=? WHERE run_id=?",
        (now, run_id),
    )
    conn.execute(
        "UPDATE test_run_items SET status='cancelled', finished_at=? WHERE run_id=? AND status IN ('pending','running')",
        (now, run_id),
    )
    conn.commit()
    conn.close()
    mod_runtime._active_runs.pop(run_id, None)
    mod_runtime._metrics["runs_cancelled"] += 1
    mod_runtime.log.info(
        "Run cancelled", extra={"run_id": run_id, "user": user["username"]}
    )
    mod_runtime.audit(user, "run.cancel", project_id=tr["project_id"], target=run_id)
    mod_runtime._publish(run_id, "done", {"status": "cancelled"})
    return {"cancelled": run_id}


@router.get("/api/projects/{project_id}/runs")
def list_runs(
    project_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(mod_authentication._proj_viewer),
):
    import runtime as mod_runtime

    conn = get_db()
    rows = conn.execute(
        "\n        SELECT tr.*, tg.name AS group_name\n        FROM test_runs tr\n        LEFT JOIN test_groups tg ON tr.group_id = tg.id\n        WHERE tr.project_id=? ORDER BY tr.started_at DESC, tr.id DESC LIMIT ? OFFSET ?\n    ",
        (project_id, limit, offset),
    ).fetchall()
    conn.close()
    result = rows_to_list(rows)
    for r in result:
        r.pop("profile_secrets", None)
        live = mod_runtime._active_runs.get(r["run_id"])
        if live:
            r["status"] = live["status"]
            r["passed"] = live["passed"]
            r["failed"] = live["failed"]
    return result


@router.get("/api/runs/{run_id}")
def get_run(
    run_id: str,
    include_items: bool = False,
    user=Depends(mod_authentication._current_user),
):
    """Run summary + per-status counts.

    Items are NOT included by default. A 1200-case run serialises to ~525 KB
    with them, and the detail view polls this every 3 s — that was 175 KB/s per
    viewer just to redraw a progress bar. The UI pages through
    /api/runs/{id}/items instead. include_items=1 restores the old shape for
    any external caller that still wants everything in one response.
    """
    import authentication as mod_authentication
    import reporting as mod_reporting
    import runtime as mod_runtime

    conn = get_db()
    tr = mod_authentication._run_or_404(conn, run_id, user)
    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM test_run_items WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
    }
    items = (
        conn.execute(
            "SELECT * FROM test_run_items WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        if include_items
        else []
    )
    conn.close()
    d = dict(tr)
    d.pop("profile_secrets", None)
    queued = [
        key
        for key, state in mod_runtime._active_runs.items()
        if state["status"] == "queued"
    ]
    d["queue_position"] = None
    d["queue_policy"] = "project_fair"
    d["queued_total"] = len(queued)
    d["slots_busy"] = sum(
        (state["status"] == "running" for state in mod_runtime._active_runs.values())
    )
    d["slots_total"] = mod_runtime.MAX_CONCURRENT_RUNS
    live = mod_runtime._active_runs.get(run_id)
    if live:
        d["status"] = live["status"]
        d["passed"] = live["passed"]
        d["failed"] = live["failed"]
    run_dir = mod_runtime._project_results(tr["project_id"]) / run_id
    d["passed_after_retry"] = conn_retry_count(run_id)
    d["status_counts"] = counts
    d["item_count"] = sum(counts.values())
    if include_items:
        item_list = []
        for it in items:
            i = dict(it)
            i["has_log"], i["has_report"] = mod_reporting._item_files(
                run_dir, it["rf_run_id"]
            )
            item_list.append(i)
        d["items"] = item_list
    d["has_combined_report"] = (run_dir / "combined" / "report.html").exists()
    d["has_combined_log"] = (run_dir / "combined" / "log.html").exists()
    d["has_source_manifest"] = (run_dir / "source-manifest.json").is_file()
    return d


_ITEM_LIST_COLS = "id, test_case_id, tc_code, tc_name, status, rf_run_id, started_at, finished_at, fail_summary, attempt_count, passed_after_retry"


@router.get("/api/runs/{run_id}/items")
def get_run_items(
    run_id: str,
    status: str = "",
    q: str = "",
    offset: int = 0,
    limit: int = 50,
    user=Depends(mod_authentication._current_user),
):
    """One page of a run's test cases, filtered by status and free text."""
    import authentication as mod_authentication
    import reporting as mod_reporting
    import runtime as mod_runtime

    limit = max(1, min(500, limit))
    offset = max(0, offset)
    conn = get_db()
    tr = mod_authentication._run_or_404(conn, run_id, user)
    where, params = (["run_id=?"], [run_id])
    if status:
        marks = ",".join(("?" for _ in status.split(",")))
        where.append(f"status IN ({marks})")
        params += [s.strip() for s in status.split(",")]
    if q.strip():
        where.append("(tc_code LIKE ? OR tc_name LIKE ? OR fail_summary LIKE ?)")
        params += [f"%{q.strip()}%"] * 3
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM test_run_items WHERE {clause}", params
    ).fetchone()["n"]
    rows = conn.execute(
        f"SELECT {_ITEM_LIST_COLS} FROM test_run_items WHERE {clause} ORDER BY id LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    run_dir = mod_runtime._project_results(tr["project_id"]) / run_id
    out = []
    for r in rows:
        i = dict(r)
        i["has_log"], i["has_report"] = mod_reporting._item_files(
            run_dir, r["rf_run_id"]
        )
        out.append(i)
    return {"total": total, "offset": offset, "limit": limit, "items": out}


@router.get("/api/runs/{run_id}/items/{item_id}")
def get_run_item(
    run_id: str, item_id: int, user=Depends(mod_authentication._current_user)
):
    """Full detail for one test case, including the failure text and screenshot."""
    import authentication as mod_authentication
    import diagnostics as mod_diagnostics
    import reporting as mod_reporting
    import runtime as mod_runtime

    conn = get_db()
    tr = mod_authentication._run_or_404(conn, run_id, user)
    row = conn.execute(
        "SELECT * FROM test_run_items WHERE run_id=? AND id=?", (run_id, item_id)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Test case not found in this run")
    i = dict(row)
    with database() as attempts_conn:
        i["attempts"] = [
            dict(attempt)
            for attempt in attempts_conn.execute(
                "SELECT attempt,status,duration_sec,artifact_dir FROM test_attempts WHERE item_id=? ORDER BY attempt",
                (item_id,),
            )
        ]
    i["has_log"], i["has_report"] = mod_reporting._item_files(
        mod_runtime._project_results(tr["project_id"]) / run_id, row["rf_run_id"]
    )
    if row["dom_capture"]:
        import json as _json

        item_dir = (
            mod_runtime._project_results(tr["project_id"]) / run_id / row["rf_run_id"]
        )
        i["captures"] = mod_diagnostics._read_captures(item_dir)
        prop = None
        if row["locator_sig"]:
            conn2 = get_db()
            prop = conn2.execute(
                "SELECT * FROM locator_proposals WHERE project_id=? AND signature=?",
                (tr["project_id"], row["locator_sig"]),
            ).fetchone()
            conn2.close()
        if prop:
            p = dict(prop)
            for k in ("candidates", "proposed"):
                try:
                    p[k] = _json.loads(p[k]) if p[k] else None
                except Exception:
                    p[k] = None
            i["locator_repair"] = p
    for attempt in i["attempts"]:
        directory = (
            mod_runtime._project_results(tr["project_id"])
            / run_id
            / row["rf_run_id"]
            / attempt["artifact_dir"]
            if row["rf_run_id"]
            else None
        )
        attempt["has_log"] = bool(directory and (directory / "log.html").is_file())
        attempt["has_console"] = bool(
            directory and (directory / "console.log").is_file()
        )
    return i


def conn_retry_count(run_id):
    with database() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM test_run_items WHERE run_id=? AND passed_after_retry=1",
            (run_id,),
        ).fetchone()[0]
