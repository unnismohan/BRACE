"""BRACE routes.projects — extracted application responsibility."""

import asyncio
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from db import database, decrypt_token, encrypt_token, get_db, next_tc_code
from scheduler import build_trigger
import git_sync
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    git_url: Optional[str] = None
    git_branch: str = "main"
    git_username: Optional[str] = None
    git_token: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    dom_capture_enabled: Optional[bool] = None


@router.get("/api/projects")
def list_projects(user=Depends(mod_authentication._current_user)):
    conn = get_db()
    if user["role"] == "admin":
        rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
    else:
        rows = conn.execute(
            "\n            SELECT p.* FROM projects p\n            JOIN project_members pm ON p.id = pm.project_id\n            JOIN users u ON pm.user_id = u.id\n            WHERE u.username=? ORDER BY p.name\n        ",
            (user["username"],),
        ).fetchall()
    stats = {
        r["project_id"]: dict(r)
        for r in conn.execute(
            "\n        SELECT project_id, COUNT(*) total,\n               SUM(last_run_status='passed') passed, SUM(last_run_status='failed') failed\n        FROM test_cases GROUP BY project_id\n    "
        ).fetchall()
    }
    last_runs = {
        r["project_id"]: dict(r)
        for r in conn.execute(
            "\n        SELECT project_id, status, started_at FROM (\n            SELECT project_id, status, started_at,\n                   ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY started_at DESC, id DESC) rank\n            FROM test_runs\n        ) WHERE rank=1\n    "
        ).fetchall()
    }
    roles = {
        r["project_id"]: r["project_role"]
        for r in conn.execute(
            "\n        SELECT pm.project_id, pm.project_role FROM project_members pm\n        JOIN users u ON pm.user_id=u.id WHERE u.username=?\n    ",
            (user["username"],),
        ).fetchall()
    }
    result = []
    for p in rows:
        d = dict(p)
        d.pop("git_token", None)
        stat = stats.get(d["id"], {})
        last_run = last_runs.get(d["id"], {})
        d.update(
            tc_count=stat.get("total", 0),
            tc_passed=stat.get("passed") or 0,
            tc_failed=stat.get("failed") or 0,
            last_run_status=last_run.get("status"),
            last_run_at=last_run.get("started_at"),
            has_git=bool(d.get("git_url")),
            my_role="project_admin" if user["role"] == "admin" else roles.get(d["id"]),
        )
        result.append(d)
    conn.close()
    return result


@router.post(
    "/api/projects", dependencies=[Depends(mod_authentication._require_sys_admin)]
)
def create_project(req: ProjectCreate, user=Depends(mod_authentication._current_user)):
    import runtime as mod_runtime

    conn = get_db()
    conn.execute(
        "INSERT INTO projects (name, description, git_url, git_branch, git_username, git_token) VALUES (?,?,?,?,?,?)",
        (
            req.name,
            req.description,
            req.git_url,
            req.git_branch,
            req.git_username,
            encrypt_token(req.git_token or ""),
        ),
    )
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, project_role) VALUES (?,?,?)",
        (
            pid,
            conn.execute(
                "SELECT id FROM users WHERE username=?", (user["username"],)
            ).fetchone()[0],
            "project_admin",
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    d = dict(row)
    d.pop("git_token", None)
    mod_runtime.audit(
        user,
        "project.create",
        project_id=pid,
        target=req.name,
        git_url=req.git_url or None,
    )
    return d


@router.get("/api/projects/{project_id}")
def get_project(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Project not found")
    d = dict(row)
    d.pop("git_token", None)
    d["has_git"] = bool(d.get("git_url"))
    return d


@router.put("/api/projects/{project_id}")
def update_project(
    project_id: int, req: ProjectUpdate, user=Depends(mod_authentication._proj_admin)
):
    import runtime as mod_runtime

    conn = get_db()
    if req.name:
        conn.execute(
            "UPDATE projects SET name=?        WHERE id=?", (req.name, project_id)
        )
    if req.description is not None:
        conn.execute(
            "UPDATE projects SET description=? WHERE id=?",
            (req.description, project_id),
        )
    if req.status:
        conn.execute(
            "UPDATE projects SET status=?      WHERE id=?", (req.status, project_id)
        )
    if req.dom_capture_enabled is not None:
        conn.execute(
            "UPDATE projects SET dom_capture_enabled=? WHERE id=?",
            (1 if req.dom_capture_enabled else 0, project_id),
        )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "project.update",
        project_id=project_id,
        name=req.name,
        status=req.status,
        dom_capture_enabled=req.dom_capture_enabled,
    )
    return {"ok": True}


@router.delete("/api/projects/{project_id}")
def delete_project(
    project_id: int, user=Depends(mod_authentication._require_sys_admin)
):
    import runtime as mod_runtime

    conn = get_db()
    row = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "project.delete",
        project_id=project_id,
        target=row["name"] if row else str(project_id),
    )
    return {"deleted": project_id}


class GitConfigUpdate(BaseModel):
    git_url: Optional[str] = None
    git_branch: str = "main"
    git_username: Optional[str] = None
    git_token: Optional[str] = None


@router.get("/api/projects/{project_id}/git-config")
def get_git_config(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    row = conn.execute(
        "SELECT git_url, git_branch, git_username, git_token FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    return {
        "git_url": row["git_url"],
        "git_branch": row["git_branch"],
        "git_username": row["git_username"],
        "has_token": bool(row["git_token"]),
    }


@router.put("/api/projects/{project_id}/git-config")
def update_git_config(
    project_id: int, req: GitConfigUpdate, user=Depends(mod_authentication._proj_admin)
):
    import runtime as mod_runtime

    conn = get_db()
    if req.git_url is not None:
        conn.execute(
            "UPDATE projects SET git_url=? WHERE id=?", (req.git_url, project_id)
        )
    conn.execute(
        "UPDATE projects SET git_branch=? WHERE id=?", (req.git_branch, project_id)
    )
    if req.git_username is not None:
        conn.execute(
            "UPDATE projects SET git_username=? WHERE id=?",
            (req.git_username, project_id),
        )
    if req.git_token is not None:
        conn.execute(
            "UPDATE projects SET git_token=? WHERE id=?",
            (encrypt_token(req.git_token), project_id),
        )
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user,
        "git.config_update",
        project_id=project_id,
        git_url=req.git_url,
        git_branch=req.git_branch,
        git_token=req.git_token is not None,
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/git-sync")
async def git_pull(project_id: int, user=Depends(mod_authentication._proj_tester)):
    import runtime as mod_runtime

    conn = get_db()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    git_url = row["git_url"] or ""
    branch = row["git_branch"] or "main"
    username = row["git_username"] or ""
    token = decrypt_token(row["git_token"] or "")
    if not git_url:
        raise HTTPException(400, "No git URL configured for this project")
    suites_dir = mod_runtime._project_suites(project_id)
    clone_dir = Path("/tmp") / f"brace-git-{project_id}"
    if username or token:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(git_url)
        netloc = (
            f"{username}:{token}@{p.hostname}" if username else f"{token}@{p.hostname}"
        )
        if p.port:
            netloc += f":{p.port}"
        git_url = urlunparse(p._replace(netloc=netloc))

    def _redact(text: str) -> str:
        """git echoes the remote URL on failure — with the embedded PAT in it.
        Strip any credentials before this reaches the browser or the logs."""
        if not text:
            return text
        out = re.sub("(https?://)[^/\\s@]+@", "\\1***@", text)
        for secret in (token, username):
            if secret and len(secret) > 3:
                out = out.replace(secret, "***")
        return out

    async def _run(cmd, cwd=None):
        return await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, cwd=cwd
        )

    async def stream():
        import runtime as mod_runtime

        yield f"[BRACE] Git sync — project {project_id}, branch: {branch}\n"
        if clone_dir.exists():
            yield "[BRACE] Existing clone — fetching latest…\n"
            for lock in clone_dir.rglob("*.lock"):
                try:
                    lock.unlink()
                    yield f"[BRACE] Removed stale lock: {lock.name}\n"
                except OSError:
                    pass
            r = await _run(
                ["git", "fetch", "--depth=1", "origin", branch], cwd=clone_dir
            )
            yield _redact(r.stdout + r.stderr)
            if r.returncode == 0:
                r2 = await _run(
                    ["git", "reset", "--hard", f"origin/{branch}"], cwd=clone_dir
                )
                yield _redact(r2.stdout + r2.stderr)
                if r2.returncode != 0:
                    yield "[BRACE] Reset failed — will re-clone…\n"
                    shutil.rmtree(clone_dir, ignore_errors=True)
            else:
                yield "[BRACE] Fetch failed — will re-clone…\n"
                shutil.rmtree(clone_dir, ignore_errors=True)
        if not clone_dir.exists():
            yield "[BRACE] Cloning repository…\n"
            r = await _run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--branch",
                    branch,
                    git_url,
                    str(clone_dir),
                ]
            )
            yield _redact(r.stdout + r.stderr)
            if r.returncode != 0:
                yield f"\n[BRACE ERROR] Clone failed (exit {r.returncode})\n"
                return
        yield "\n[BRACE] Copying scripts to project suites dir…\n"

        def _copy_all() -> int:
            """Thousands of file copies — run off the event loop."""
            suites_dir.mkdir(parents=True, exist_ok=True)
            n = 0
            for ext in (".robot", ".resource", ".py", ".yaml", ".yml", ".csv", ".xlsx"):
                for s in sorted(clone_dir.rglob(f"*{ext}")):
                    if ".git" in s.parts:
                        continue
                    d = suites_dir / s.relative_to(clone_dir)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(s, d)
                    if ext == ".robot":
                        n += 1
            return n

        copied = await asyncio.to_thread(_copy_all)
        revision = await _run(["git", "rev-parse", "HEAD"], cwd=clone_dir)
        if revision.returncode == 0:
            with database() as conn:
                conn.execute(
                    "UPDATE projects SET last_git_commit=? WHERE id=?",
                    (revision.stdout.strip(), project_id),
                )
        yield f"[BRACE] Done — {copied} robot file(s) synced\n"
        mod_runtime.audit(
            user, "git.pull", project_id=project_id, branch=branch, files=copied
        )
        if (row["sync_mode"] or "manual") == "git":
            yield "\n[BRACE] Reconciling test cases from the repository…\n"
            try:
                res = await asyncio.to_thread(
                    _run_tc_sync, project_id, user["username"]
                )
                s = git_sync.summary(res)
                yield f"[BRACE] Test cases — added {s['added']}, updated {s['updated']}, unchanged {s['unchanged']}, missing {s['missing']}\n"
                for e in res.get("errors", [])[:10]:
                    yield f"[BRACE WARN] {e}\n"
            except Exception as exc:
                yield f"[BRACE ERROR] Test case sync failed: {exc}\n"

    return StreamingResponse(stream(), media_type="text/plain")


class SyncConfigReq(BaseModel):
    sync_mode: str
    sync_cron: Optional[str] = None


def _run_tc_sync(project_id: int, username: str, dry_run: bool = False) -> dict:
    """Blocking — parses every .robot file. Always call via to_thread."""
    import runtime as mod_runtime

    conn = get_db()
    try:
        res = git_sync.sync_project(
            conn,
            project_id,
            mod_runtime._project_suites(project_id),
            next_tc_code,
            mod_runtime._norm_tags,
            dry_run=dry_run,
        )
        if not dry_run:
            import json as _json

            conn.execute(
                "UPDATE projects SET last_sync_at=?, last_sync_result=? WHERE id=?",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    _json.dumps(git_sync.summary(res)),
                    project_id,
                ),
            )
            conn.commit()
    finally:
        conn.close()
    if not dry_run:
        s = git_sync.summary(res)
        mod_runtime.audit(username, "project.sync", project_id=project_id, **s)
    return res


@router.get("/api/projects/{project_id}/sync-config")
def get_sync_config(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    conn = get_db()
    row = conn.execute(
        "SELECT sync_mode, sync_cron, last_sync_at, last_sync_result, git_url FROM projects WHERE id=?",
        (project_id,),
    ).fetchone()
    counts = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN source_path IS NOT NULL THEN 1 ELSE 0 END) AS synced, SUM(CASE WHEN sync_status='missing' THEN 1 ELSE 0 END)   AS missing FROM test_cases WHERE project_id=?",
        (project_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    import json as _json

    try:
        last = _json.loads(row["last_sync_result"]) if row["last_sync_result"] else None
    except ValueError:
        last = None
    return {
        "sync_mode": row["sync_mode"] or "manual",
        "sync_cron": row["sync_cron"],
        "last_sync_at": row["last_sync_at"],
        "last_sync": last,
        "has_git": bool(row["git_url"]),
        "counts": {
            "total": counts["total"] or 0,
            "synced": counts["synced"] or 0,
            "missing": counts["missing"] or 0,
        },
    }


@router.put("/api/projects/{project_id}/sync-config")
def put_sync_config(
    project_id: int, req: SyncConfigReq, user=Depends(mod_authentication._proj_admin)
):
    import jobs as mod_jobs
    import runtime as mod_runtime

    if req.sync_mode not in ("manual", "git"):
        raise HTTPException(400, "sync_mode must be 'manual' or 'git'")
    if req.sync_cron:
        try:
            build_trigger(req.sync_cron)
        except Exception as exc:
            raise HTTPException(400, f"Invalid sync schedule: {exc}")
    conn = get_db()
    conn.execute(
        "UPDATE projects SET sync_mode=?, sync_cron=? WHERE id=?",
        (req.sync_mode, req.sync_cron or None, project_id),
    )
    conn.commit()
    conn.close()
    mod_jobs._reload_sync_jobs()
    mod_runtime.audit(
        user,
        "project.sync_config",
        project_id=project_id,
        sync_mode=req.sync_mode,
        sync_cron=req.sync_cron,
    )
    return get_sync_config(project_id, user)


@router.post("/api/projects/{project_id}/sync")
async def sync_test_cases(
    project_id: int,
    dry_run: bool = False,
    user=Depends(mod_authentication._proj_tester),
):
    """Reconcile the test case list against the .robot files on disk.

    dry_run reports exactly what would change without writing anything — worth
    running first on a project with existing hand-created cases.
    """
    res = await asyncio.to_thread(_run_tc_sync, project_id, user["username"], dry_run)
    res["summary"] = git_sync.summary(res)
    return res
