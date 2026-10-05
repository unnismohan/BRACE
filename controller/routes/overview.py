"""Project health, actionable failures, schedules, and queue counts."""

from fastapi import APIRouter, Depends
import authentication as auth
import runtime as rt
from db import database
from scheduler import next_run_times, SCHEDULER_TZ

router = APIRouter()


@router.get("/api/projects/{project_id}/overview")
def overview(project_id: int, user=Depends(auth._proj_viewer)):
    with database() as conn:
        stats = dict(
            conn.execute(
                "SELECT COUNT(*) total,SUM(last_run_at IS NOT NULL) executed,SUM(quarantined) quarantined FROM test_cases WHERE project_id=?",
                (project_id,),
            ).fetchone()
        )
        failures = [
            dict(row)
            for row in conn.execute(
                "SELECT tr.run_id,tr.run_name,tr.started_at,tri.id AS item_id,tri.tc_name,tri.fail_summary FROM test_runs tr JOIN test_run_items tri ON tri.run_id=tr.run_id WHERE tr.project_id=? AND tri.status='failed' ORDER BY tr.started_at DESC,tr.id DESC,tri.id DESC LIMIT 8",
                (project_id,),
            )
        ]
        schedules = []
        for row in conn.execute(
            "SELECT s.*,g.name FROM schedules s JOIN test_groups g ON g.id=s.group_id WHERE s.project_id=? AND s.enabled=1",
            (project_id,),
        ):
            times = next_run_times(row["cron_expr"], 1)
            if times:
                schedules.append(
                    {
                        "name": row["name"],
                        "next_run": times[0],
                        "overlap_policy": row["overlap_policy"],
                    }
                )
        retries = conn.execute(
            "SELECT COUNT(*) FROM test_run_items i JOIN test_runs r ON r.run_id=i.run_id WHERE r.project_id=? AND i.passed_after_retry=1",
            (project_id,),
        ).fetchone()[0]
    live = [
        state
        for state in rt._active_runs.values()
        if state.get("project_id") == project_id
    ]
    return {
        "cases": stats,
        "failures": failures,
        "schedules": sorted(schedules, key=lambda value: value["next_run"])[:5],
        "timezone": SCHEDULER_TZ,
        "queued": sum(state["status"] == "queued" for state in live),
        "running": sum(state["status"] == "running" for state in live),
        "passed_after_retry": retries,
    }
