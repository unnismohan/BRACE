"""BRACE routes.results — extracted application responsibility."""

import asyncio
from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from jose import JWTError, jwt
from db import get_db
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    token: str = "",
    user=Depends(mod_authentication.oauth2_scheme),
):
    """Server-sent events for one run: status, per-case verdicts, completion.

    EventSource cannot set an Authorization header, so the token also comes in
    as a query parameter — the same accommodation the results route already
    makes for <img> and <iframe>.

    Replaces polling while a run is live. The 3s poll re-queried the whole run
    for every watcher; this pushes ~100 bytes per case as it finishes.
    """
    import authentication as mod_authentication
    import runtime as mod_runtime

    raw = token or user
    if not raw:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(
            raw, mod_runtime.JWT_SECRET, algorithms=[mod_runtime.JWT_ALGORITHM]
        )
        who = await asyncio.to_thread(mod_authentication._authenticate, raw)
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    conn = get_db()
    tr = conn.execute(
        "SELECT project_id, status, passed, failed, total FROM test_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not tr:
        conn.close()
        raise HTTPException(404, "Run not found")
    if not mod_authentication._get_project_role(
        tr["project_id"], who["username"], who["role"]
    ):
        conn.close()
        raise HTTPException(403)
    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM test_run_items WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
    }
    conn.close()
    subs = mod_runtime._run_subs.setdefault(run_id, set())
    if len(subs) >= mod_runtime.SSE_MAX_SUBS:
        raise HTTPException(503, "Too many live watchers for this run")
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subs.add(queue)

    def _sse(event: str, data: dict) -> str:
        import json as _json

        return f"event: {event}\ndata: {_json.dumps(data, default=str)}\n\n"

    async def generate():
        import runtime as mod_runtime

        try:
            live = mod_runtime._active_runs.get(run_id)
            yield _sse(
                "summary",
                {
                    "status": (live or tr)["status"],
                    "passed": (live or tr)["passed"],
                    "failed": (live or tr)["failed"],
                    "total": tr["total"],
                    "status_counts": counts,
                },
            )
            if tr["status"] not in ("running", "queued"):
                yield _sse("done", {"status": tr["status"]})
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event, data = await asyncio.wait_for(
                        queue.get(), timeout=mod_runtime.SSE_HEARTBEAT_SEC
                    )
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event, data)
                if event == "done":
                    return
        finally:
            subs.discard(queue)
            if not subs:
                mod_runtime._run_subs.pop(run_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/runs/{run_id}/log")
async def stream_run_log(run_id: str, user=Depends(mod_authentication._current_user)):
    """Stream console logs from all items in a run."""
    import authentication as mod_authentication
    import runtime as mod_runtime

    conn = get_db()
    tr = conn.execute(
        "SELECT project_id FROM test_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if not tr:
        conn.close()
        raise HTTPException(404)
    role = mod_authentication._get_project_role(
        tr["project_id"], user["username"], user["role"]
    )
    if not role:
        conn.close()
        raise HTTPException(403)
    project_id = tr["project_id"]
    conn.close()
    run_dir = mod_runtime._project_results(project_id) / run_id

    async def generate():
        import runtime as mod_runtime

        pos = 0
        elapsed = 0
        while elapsed < mod_runtime.SSE_TIMEOUT:
            log_files = sorted(run_dir.rglob("console.log")) if run_dir.exists() else []
            full_text = ""
            for lf in log_files:
                try:
                    full_text += f"\n--- {lf.parent.name} ---\n"
                    full_text += lf.read_text(errors="replace")
                except Exception:
                    pass
            if len(full_text) > pos:
                chunk = full_text[pos:]
                pos = len(full_text)
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
            live = mod_runtime._active_runs.get(run_id)
            if live and live.get("status") in ("passed", "failed", "cancelled"):
                yield "data: __DONE__\n\n"
                break
            if not live:
                conn2 = get_db()
                row = conn2.execute(
                    "SELECT status FROM test_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                conn2.close()
                if row and row["status"] in ("passed", "failed", "cancelled"):
                    yield "data: __DONE__\n\n"
                    break
            await asyncio.sleep(2)
            elapsed += 2
        else:
            yield "data: __TIMEOUT__\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/results/{project_id}/{run_id}/{subpath:path}")
async def serve_result(
    project_id: int,
    run_id: str,
    subpath: str,
    request: Request,
    token: Optional[str] = None,
):
    import authentication as mod_authentication
    import runtime as mod_runtime

    raw = (
        token
        or (
            request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            or None
        )
        or request.cookies.get(mod_runtime.RESULTS_COOKIE)
    )
    if not raw:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(
            raw, mod_runtime.JWT_SECRET, algorithms=[mod_runtime.JWT_ALGORITHM]
        )
        user = await asyncio.to_thread(mod_authentication._authenticate, raw)
    except JWTError:
        raise HTTPException(401, "Invalid token")
    role = mod_authentication._get_project_role(
        project_id, user["username"], user["role"]
    )
    if not role:
        raise HTTPException(403)
    base = mod_runtime._project_results(project_id).resolve()
    path = (base / run_id / subpath).resolve()
    if not mod_runtime._contained(base, path):
        raise HTTPException(400, "Path traversal denied")
    if not path.is_file():
        raise HTTPException(404, "File not found")
    resp = FileResponse(str(path))
    if token and payload.get("exp"):
        max_age = int(payload["exp"] - datetime.now().timestamp())
        if max_age > 0:
            resp.set_cookie(
                mod_runtime.RESULTS_COOKIE,
                raw,
                max_age=max_age,
                path="/results",
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
            )
    return resp
