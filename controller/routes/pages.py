"""Bounded list APIs with server-side filtering and counts."""

from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
import authentication as auth
import runtime as rt
from db import database

router = APIRouter()


def like(value):
    return (
        "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


@router.get("/api/projects/{project_id}/test-cases/page")
def cases_page(
    project_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str = "",
    status: str = "",
    tag: str = "",
    exclude_group: int | None = None,
    user=Depends(auth._proj_viewer),
):
    conditions, params = ["tc.project_id=?"], [project_id]
    if q:
        conditions.append(
            "(tc.name LIKE ? ESCAPE '\\' OR tc.tc_code LIKE ? ESCAPE '\\' OR tc.description LIKE ? ESCAPE '\\' OR tc.suite_path LIKE ? ESCAPE '\\' OR tc.tags LIKE ? ESCAPE '\\')"
        )
        params += [like(q)] * 5
    if status == "never":
        conditions.append("tc.last_run_status IS NULL")
    elif status:
        conditions.append("tc.last_run_status=?")
        params.append(status)
    if tag:
        conditions.append("tc.tags LIKE ? ESCAPE '\\'")
        params.append(like("," + tag + ","))
    if exclude_group is not None:
        with database() as conn:
            if not conn.execute(
                "SELECT 1 FROM test_groups WHERE id=? AND project_id=?",
                (exclude_group, project_id),
            ).fetchone():
                raise HTTPException(404, "Suite not found in this project")
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM group_test_cases g WHERE g.group_id=? AND g.test_case_id=tc.id)"
        )
        params.append(exclude_group)
    clause = " AND ".join(conditions)
    with database() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM test_cases tc WHERE {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT tc.* FROM test_cases tc WHERE {clause} ORDER BY tc.tc_code,tc.id LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }


@router.get("/api/projects/{project_id}/runs/page")
def runs_page(
    project_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str = "",
    status: str = "",
    who: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    user=Depends(auth._proj_viewer),
):
    conditions, params = ["tr.project_id=?"], [project_id]
    if q:
        conditions.append(
            "(tr.run_name LIKE ? ESCAPE '\\' OR tr.run_id LIKE ? ESCAPE '\\')"
        )
        params += [like(q)] * 2
    if status:
        conditions.append("tr.status=?")
        params.append(status)
    if who:
        conditions.append("tr.triggered_by=?")
        params.append(who)
    for field, comparison, value in [
        ("date_from", ">=", date_from),
        ("date_to", "<=", date_to),
    ]:
        if value:
            conditions.append(f"substr(tr.started_at,1,10){comparison}?")
            params.append(value.isoformat())
    clause = " AND ".join(conditions)
    with database() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM test_runs tr WHERE {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT tr.*,tg.name AS group_name FROM test_runs tr LEFT JOIN test_groups tg ON tr.group_id=tg.id WHERE {clause} ORDER BY tr.started_at DESC,tr.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        result = []
        for row in rows:
            item = dict(row)
            item.pop("profile_secrets", None)
            if live := rt._active_runs.get(item["run_id"]):
                item.update(
                    status=live["status"], passed=live["passed"], failed=live["failed"]
                )
            result.append(item)
        users = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT triggered_by FROM test_runs WHERE project_id=? ORDER BY triggered_by",
                (project_id,),
            )
            if row[0]
        ]
        return {
            "items": result,
            "total": total,
            "offset": offset,
            "limit": limit,
            "users": users,
        }


@router.get("/api/users/page")
def users_page(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str = "",
    user=Depends(auth._require_sys_admin),
):
    clause = "username LIKE ? ESCAPE '\\' OR full_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR system_role LIKE ? ESCAPE '\\'"
    params = [like(q)] * 4
    with database() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM users WHERE {clause}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT id,username,full_name,email,system_role FROM users WHERE {clause} ORDER BY username,id LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }


@router.get("/api/projects/{project_id}/groups/page")
def groups_page(
    project_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str = "",
    user=Depends(auth._proj_viewer),
):
    with database() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM test_groups WHERE project_id=? AND name LIKE ? ESCAPE '\\'",
            (project_id, like(q)),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT g.*,COUNT(tc.id) tc_count,COALESCE(SUM(tc.last_run_status='passed'),0) passed_count,COALESCE(SUM(tc.last_run_status='failed'),0) failed_count FROM test_groups g LEFT JOIN group_test_cases m ON m.group_id=g.id LEFT JOIN test_cases tc ON tc.id=m.test_case_id AND tc.project_id=g.project_id WHERE g.project_id=? AND g.name LIKE ? ESCAPE '\\' GROUP BY g.id ORDER BY g.name,g.id LIMIT ? OFFSET ?",
            (project_id, like(q), limit, offset),
        )
        return {
            "items": [{**dict(row), "test_cases": []} for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }


@router.get("/api/projects/{project_id}/groups/{group_id}/cases/page")
def group_members_page(
    project_id: int,
    group_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(auth._proj_viewer),
):
    with database() as conn:
        if not conn.execute(
            "SELECT 1 FROM test_groups WHERE id=? AND project_id=?",
            (group_id, project_id),
        ).fetchone():
            raise HTTPException(404, "Suite not found in this project")
        total = conn.execute(
            "SELECT COUNT(*) FROM group_test_cases m JOIN test_cases tc ON tc.id=m.test_case_id WHERE m.group_id=? AND tc.project_id=?",
            (group_id, project_id),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT tc.*,m.order_idx FROM group_test_cases m JOIN test_cases tc ON tc.id=m.test_case_id WHERE m.group_id=? AND tc.project_id=? ORDER BY m.order_idx,tc.id LIMIT ? OFFSET ?",
            (group_id, project_id, limit, offset),
        )
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }
