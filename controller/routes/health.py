"""BRACE routes.health — extracted application responsibility."""

from datetime import datetime
from fastapi import Depends, Response
from fastapi.responses import PlainTextResponse
from db import DB_PATH, get_db
import maintenance
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus text exposition. Hand-rolled to avoid a client dependency.

    Unauthenticated so an in-cluster scraper can reach it; it exposes only
    aggregate counters, never test content, project names or user identities.
    """
    import runtime as mod_runtime

    m, out = (mod_runtime._metrics, [])

    def emit(name, mtype, help_text, value, labels=""):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {mtype}")
        out.append(f"{name}{labels} {value}")

    active = sum(
        (1 for r in mod_runtime._active_runs.values() if r["status"] == "running")
    )
    queued = sum(
        (1 for r in mod_runtime._active_runs.values() if r["status"] == "queued")
    )
    emit("brace_up", "gauge", "1 when the controller is serving.", 1)
    emit(
        "brace_uptime_seconds",
        "gauge",
        "Seconds since process start.",
        round((datetime.now() - m["started_at"]).total_seconds(), 1),
    )
    emit("brace_runs_active", "gauge", "Runs currently executing.", active)
    emit("brace_runs_queued", "gauge", "Runs waiting for an execution slot.", queued)
    emit(
        "brace_run_slots_total",
        "gauge",
        "Configured concurrent run limit.",
        mod_runtime.MAX_CONCURRENT_RUNS,
    )
    emit(
        "brace_run_slots_available",
        "gauge",
        "Free execution slots.",
        max(0, mod_runtime.MAX_CONCURRENT_RUNS - active),
    )
    emit(
        "brace_tests_running",
        "gauge",
        "Robot processes (browsers) executing now.",
        len(mod_runtime._active_procs),
    )
    emit(
        "brace_test_slots_total",
        "gauge",
        "Configured concurrent robot-process limit.",
        mod_runtime.MAX_CONCURRENT_TESTS,
    )
    emit(
        "brace_test_slots_available",
        "gauge",
        "Free robot-process slots.",
        max(0, mod_runtime.MAX_CONCURRENT_TESTS - len(mod_runtime._active_procs)),
    )
    out.append("# HELP brace_runs_total Runs by final state since start.")
    out.append("# TYPE brace_runs_total counter")
    for state, key in (
        ("started", "runs_started"),
        ("passed", "runs_passed"),
        ("failed", "runs_failed"),
        ("cancelled", "runs_cancelled"),
    ):
        out.append(f'brace_runs_total{{state="{state}"}} {m[key]}')
    out.append("# HELP brace_tests_total Test cases executed by outcome since start.")
    out.append("# TYPE brace_tests_total counter")
    for state, key in (
        ("passed", "tests_passed"),
        ("failed", "tests_failed"),
        ("timeout", "tests_timeout"),
    ):
        out.append(f'brace_tests_total{{outcome="{state}"}} {m[key]}')
    emit(
        "brace_dom_captures_total",
        "counter",
        "Pages captured at locator failure since start.",
        m["dom_captures"],
    )
    emit(
        "brace_locator_proposals_total",
        "counter",
        "Locator replacements proposed deterministically since start.",
        m["dom_proposals"],
    )
    emit(
        "brace_test_duration_seconds_sum",
        "counter",
        "Total test-case execution seconds.",
        round(m["test_seconds"], 1),
    )
    emit(
        "brace_test_duration_seconds_count",
        "counter",
        "Test cases contributing to the duration sum.",
        m["test_count"],
    )
    _disk = maintenance.results_bytes()
    emit(
        "brace_results_disk_bytes",
        "gauge",
        "Bytes consumed by run results on disk. Sampled, not live — see brace_results_disk_age_seconds.",
        _disk["bytes"],
    )
    emit(
        "brace_results_disk_age_seconds",
        "gauge",
        "Age of the results-disk sample. Compare with BRACE_DISK_CACHE_TTL.",
        _disk["age_sec"],
    )
    try:
        db_bytes = DB_PATH.stat().st_size
    except OSError:
        db_bytes = -1
    emit("brace_database_bytes", "gauge", "Size of the SQLite database file.", db_bytes)
    emit(
        "brace_retention_days",
        "gauge",
        "Configured run retention in days; 0 means keep forever.",
        maintenance.RETENTION_DAYS,
    )
    _last = maintenance.last_result()
    if _last:
        emit(
            "brace_maintenance_last_runs_purged",
            "gauge",
            "Runs deleted by the most recent housekeeping job.",
            _last.get("runs", {}).get("runs", 0),
        )
        emit(
            "brace_maintenance_last_freed_bytes",
            "gauge",
            "Bytes reclaimed by the most recent housekeeping job.",
            _last.get("runs", {}).get("freed_bytes", 0)
            + _last.get("orphans", {}).get("freed_bytes", 0),
        )
    try:
        conn = get_db()
        for name, sql in (
            ("projects", "SELECT COUNT(*) FROM projects"),
            ("test_cases", "SELECT COUNT(*) FROM test_cases"),
            ("runs", "SELECT COUNT(*) FROM test_runs"),
            ("run_items", "SELECT COUNT(*) FROM test_run_items"),
        ):
            emit(
                f"brace_{name}_count",
                "gauge",
                f"Rows in {name}.",
                conn.execute(sql).fetchone()[0],
            )
        conn.close()
    except Exception:
        pass
    return "\n".join(out) + "\n"


@router.get("/health")
def health():
    """Liveness: is this process still serving? Nothing else.

    This deliberately does NOT touch the database. It used to, and under load
    that was actively harmful: with several tests executing, SQLite gets
    contended, get_db() can raise 'database is locked', /health returns 500,
    and the kubelet kills the pod — destroying every run in flight over a
    condition that would have cleared itself in a second.

    A liveness probe should only answer "is this wedged beyond recovery?".
    Anything that can fail transiently belongs in readiness, below.

    Public (probes hit it unauthenticated), so it must not expose tenant data.
    """
    import runtime as mod_runtime

    return {
        "status": "ok",
        "version": mod_runtime.APP_VERSION,
        "bss_env": mod_runtime.BSS_ENV,
        "pod_name": mod_runtime.POD_NAME,
        "image_tag": mod_runtime.IMAGE_TAG,
    }


@router.get("/health/ready")
def health_ready(response: Response):
    """Readiness: can this pod actually serve requests right now?

    Checks the database, but reports 503 rather than raising — readiness only
    removes the pod from the Service, it never restarts it. With replicas: 1
    that briefly returns 503 to the route, which is the correct, recoverable
    behaviour when the DB is momentarily busy.
    """
    import runtime as mod_runtime

    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
    except Exception as exc:
        mod_runtime.log.warning("Readiness check failed: %s", exc)
        response.status_code = 503
        return {"status": "degraded", "detail": str(exc)[:200]}
    return {"status": "ok", "tests_running": len(mod_runtime._active_procs)}


@router.get("/api/run-config")
def run_config(user=Depends(mod_authentication._current_user)):
    """Execution limits the run dialog needs to offer a sensible parallel box."""
    import runtime as mod_runtime

    return {
        "max_parallel": mod_runtime.MAX_CONCURRENT_TESTS,
        "default_parallel": mod_runtime.RUN_PARALLEL_DEFAULT,
    }


@router.get("/api/health-detail")
def health_detail(user=Depends(mod_authentication._require_sys_admin)):
    import runtime as mod_runtime

    conn = get_db()
    projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "version": mod_runtime.APP_VERSION,
        "bss_env": mod_runtime.BSS_ENV,
        "pod_name": mod_runtime.POD_NAME,
        "image_tag": mod_runtime.IMAGE_TAG,
        "projects": projects,
        "users": users,
        "max_concurrent_runs": mod_runtime.MAX_CONCURRENT_RUNS,
        "max_concurrent_tests": mod_runtime.MAX_CONCURRENT_TESTS,
        "run_parallel_default": mod_runtime.RUN_PARALLEL_DEFAULT,
        "tests_running": len(mod_runtime._active_procs),
        "test_timeout_sec": mod_runtime.TEST_TIMEOUT_SEC,
    }
