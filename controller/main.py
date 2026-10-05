"""BRACE main — extracted application responsibility."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from db import init_db
from scheduler import SCHEDULER_TZ, start_scheduler, stop_scheduler
import maintenance
import runtime as mod_runtime


def _audit_route_guards() -> None:
    """Warn at startup about any mutating API route with no authentication.

    Cheap insurance for the thing that goes wrong when a codebase grows a fourth
    role: one new @app.post added without a Depends, silently open to anyone.
    A test would catch it only if someone remembered to write one; this catches
    it every boot.
    """
    import runtime as mod_runtime

    guards = {"_current_user", "_require_sys_admin", "dep"}
    public = {"/api/auth/login"}
    unguarded = []
    stack_routes = list(app.routes)
    while stack_routes:
        route = stack_routes.pop()
        nested = getattr(route, "original_router", None)
        if nested is not None:
            stack_routes.extend(nested.routes)
            continue
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if (
            path in public
            or not path.startswith("/api/")
            or (not methods & {"POST", "PUT", "PATCH", "DELETE"})
        ):
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        found, stack = (False, list(dependant.dependencies))
        while stack and (not found):
            d = stack.pop()
            name = getattr(d.call, "__name__", "")
            if name in guards or "oauth2" in name.lower():
                found = True
            stack.extend(d.dependencies)
        if not found:
            unguarded.append(
                f"{'/'.join(sorted(methods & {'POST', 'PUT', 'PATCH', 'DELETE'}))} {path}"
            )
    if unguarded:
        mod_runtime.log.error(
            "SECURITY: %d mutating API route(s) have no auth dependency: %s",
            len(unguarded),
            ", ".join(sorted(unguarded)),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import jobs as mod_jobs
    import runtime as mod_runtime

    init_db()
    mod_runtime._preflight_security_check()
    _audit_route_guards()
    mod_runtime._reconcile_orphaned_runs()
    mod_runtime._slots()
    mod_runtime._main_loop = asyncio.get_running_loop()
    start_scheduler()
    mod_jobs._reload_all_jobs()
    _now = datetime.now()
    mod_runtime.log.info(
        "BRACE v2 started — env=%s tag=%s version=%s max_concurrent_runs=%d max_concurrent_tests=%d run_parallel=%d test_timeout=%ds scheduler_tz=%s container_tz=%s local_time=%s retention=%s",
        mod_runtime.BSS_ENV,
        mod_runtime.IMAGE_TAG,
        mod_runtime.APP_VERSION,
        mod_runtime.MAX_CONCURRENT_RUNS,
        mod_runtime.MAX_CONCURRENT_TESTS,
        mod_runtime.RUN_PARALLEL_DEFAULT,
        mod_runtime.TEST_TIMEOUT_SEC,
        SCHEDULER_TZ,
        os.getenv("TZ") or time.tzname[0],
        _now.strftime("%Y-%m-%d %H:%M:%S"),
        (
            f"{maintenance.RETENTION_DAYS}d (keep min {maintenance.RETENTION_KEEP_MIN}/project)"
            if maintenance.RETENTION_DAYS
            else "off"
        ),
    )
    yield
    stop_scheduler()


app = FastAPI(
    title="BRACE RF Controller", version=mod_runtime.APP_VERSION, lifespan=lifespan
)
from routes import admin as mod_routes_admin

app.include_router(mod_routes_admin.router)
from routes import ai as mod_routes_ai

app.include_router(mod_routes_ai.router)
from routes import auth as mod_routes_auth

app.include_router(mod_routes_auth.router)
from routes import diagnostics as mod_routes_diagnostics

app.include_router(mod_routes_diagnostics.router)
from routes import files as mod_routes_files

app.include_router(mod_routes_files.router)
from routes import health as mod_routes_health

app.include_router(mod_routes_health.router)
from routes import members as mod_routes_members

app.include_router(mod_routes_members.router)
from routes import projects as mod_routes_projects

app.include_router(mod_routes_projects.router)
from routes import reports as mod_routes_reports

app.include_router(mod_routes_reports.router)
from routes import results as mod_routes_results

app.include_router(mod_routes_results.router)
from routes import runs as mod_routes_runs

app.include_router(mod_routes_runs.router)
from routes import schedules as mod_routes_schedules

app.include_router(mod_routes_schedules.router)
from routes import suites as mod_routes_suites

app.include_router(mod_routes_suites.router)
from routes import testcases as mod_routes_testcases

app.include_router(mod_routes_testcases.router)
from routes import users as mod_routes_users

app.include_router(mod_routes_users.router)
from routes import profiles as profile_routes

app.include_router(profile_routes.router)
from routes import pages as page_routes, overview as overview_routes

app.include_router(page_routes.router)
app.include_router(overview_routes.router)
STATIC_DIR = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parent / "static")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
@app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def serve_spa(path: str = ""):
    html = STATIC_DIR / "index.html"
    if html.exists():
        return HTMLResponse(html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>BRACE — UI not found</h1>")
