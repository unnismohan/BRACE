"""BRACE runtime — extracted application responsibility."""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from db import database, encryption_available, get_db
from scheduler import SCHEDULER_TZ

log = logging.getLogger(__name__)


class _JsonLogFormatter(logging.Formatter):
    """One JSON object per line, so a log stack can index the fields.

    Anything passed via `extra=` (run_id, project_id, user…) is merged in, which
    is what makes a run traceable across the many log lines it produces.
    """

    _BUILTIN = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "asctime",
        "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        import json as _json

        out = {
            "ts": datetime.fromtimestamp(record.created).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in self._BUILTIN and (not k.startswith("_")):
                out[k] = v
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return _json.dumps(out, default=str)


def _configure_logging() -> None:
    fmt = os.getenv("BRACE_LOG_FORMAT", "text").strip().lower()
    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(_JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.getenv("BRACE_LOG_LEVEL", "INFO").strip().upper() or "INFO")


_configure_logging()
SUITES_DIR = Path(os.getenv("SUITES_DIR", "/opt/rf/suites"))
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/opt/rf/results"))
APP_VERSION = os.getenv("BRACE_VERSION", "").strip() or "2.2.1"
BSS_ENV = os.getenv("BSS_ENV", "staging")
IMAGE_TAG = os.getenv("IMAGE_TAG", "unknown")
POD_NAME = os.getenv("HOSTNAME", "unknown")
JWT_SECRET = os.getenv("JWT_SECRET", "brace-default-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MIN = 480
SSE_TIMEOUT = 1800
ALLOWED_EXTS = {".robot", ".yaml", ".yml", ".txt", ".py", ".resource", ".csv", ".xlsx"}
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("BRACE_MAX_CONCURRENT_RUNS", "3")))
MAX_CONCURRENT_TESTS = max(
    1, int(os.getenv("BRACE_MAX_CONCURRENT_TESTS", str(MAX_CONCURRENT_RUNS)))
)
RUN_PARALLEL_DEFAULT = max(
    1,
    min(
        MAX_CONCURRENT_TESTS,
        int(os.getenv("BRACE_RUN_PARALLEL", str(MAX_CONCURRENT_TESTS))),
    ),
)
TEST_TIMEOUT_SEC = max(60, int(os.getenv("BRACE_TEST_TIMEOUT_SEC", "1800")))
_active_runs: dict = {}
_active_procs: dict = {}
_cancelled_runs: set = set()
_run_slots: Optional[asyncio.Semaphore] = None
_test_slots: Optional[asyncio.Semaphore] = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_fair_gate = None
_fair_loop = None


def fair_gate():
    from execution import ProjectFairGate

    global _fair_gate, _fair_loop
    loop = asyncio.get_running_loop()
    if _fair_gate is None or _fair_loop is not loop:
        _fair_gate = ProjectFairGate(MAX_CONCURRENT_RUNS)
        _fair_loop = loop
    return _fair_gate


_run_subs: dict = {}
SSE_MAX_SUBS = max(1, int(os.getenv("BRACE_SSE_MAX_SUBS", "20")))
SSE_HEARTBEAT_SEC = 15


def _publish(run_id: str, event: str, data: dict) -> None:
    """Fan one event out to everyone watching this run. Never raises, never blocks.

    Called from the executor's hot path, so a slow or dead subscriber must not
    be able to stall a test finishing: queues are unbounded-put via put_nowait
    and a full one simply drops the event — the client's next reconnect (or the
    fallback poll) resynchronises from the database, which is the source of
    truth either way.
    """
    subs = _run_subs.get(run_id)
    if not subs:
        return
    for q in list(subs):
        try:
            q.put_nowait((event, data))
        except Exception:
            pass


_metrics = {
    "runs_started": 0,
    "runs_passed": 0,
    "runs_failed": 0,
    "runs_cancelled": 0,
    "tests_passed": 0,
    "tests_failed": 0,
    "tests_timeout": 0,
    "test_seconds": 0.0,
    "test_count": 0,
    "dom_captures": 0,
    "dom_proposals": 0,
    "started_at": datetime.now(),
}


def _slots() -> asyncio.Semaphore:
    """Lazily create the semaphore — it must bind to the running event loop."""
    global _run_slots
    if _run_slots is None:
        _run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
    return _run_slots


def _tslots() -> asyncio.Semaphore:
    """Global cap on concurrent robot processes (i.e. concurrent browsers)."""
    global _test_slots
    if _test_slots is None:
        _test_slots = asyncio.Semaphore(MAX_CONCURRENT_TESTS)
    return _test_slots


def _preflight_security_check() -> None:
    """Refuse to start a non-local deployment with insecure defaults."""
    problems, warnings = ([], [])
    if (
        os.getenv("BRACE_REQUIRE_ISOLATION", "").lower() in {"1", "true", "yes"}
        and os.getenv("BRACE_RUNNER_MODE", "local") != "remote"
    ):
        problems.append(
            "Isolated execution is required: configure BRACE_RUNNER_MODE=remote and project runner endpoints"
        )
    if JWT_SECRET == "brace-default-secret-change-in-prod":
        problems.append(
            "JWT_SECRET is the built-in default — anyone can forge a login token. Set it to a long random value."
        )
    elif len(JWT_SECRET) < 32:
        warnings.append(f"JWT_SECRET is only {len(JWT_SECRET)} chars; use 32+.")
    if not encryption_available():
        problems.append(
            "Credential encryption is unavailable. Set a valid BRACE_ENCRYPT_KEY."
        )
    tz_env = (os.getenv("TZ") or "").strip()
    if not tz_env:
        warnings.append(
            f"TZ is not set, so the container clock is {time.tzname[0]}. Timestamps are stored and displayed in that zone — set TZ (usually to the same value as BRACE_SCHEDULER_TZ={SCHEDULER_TZ}) or times in the UI will not match anyone's watch."
        )
    elif tz_env != SCHEDULER_TZ:
        warnings.append(
            f"TZ ({tz_env}) and BRACE_SCHEDULER_TZ ({SCHEDULER_TZ}) differ. Cron expressions will fire on one clock while timestamps are recorded on another — set both the same unless you specifically want this."
        )
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT must_change_password FROM users WHERE username='admin'"
        ).fetchone()
        conn.close()
        if row and row["must_change_password"]:
            warnings.append(
                "The bootstrap 'admin' account still has its initial password. Change it immediately after first login."
            )
    except Exception:
        pass
    for w in warnings:
        log.warning("SECURITY: %s", w)
    if problems:
        for p in problems:
            log.error("SECURITY: %s", p)
        if BSS_ENV.lower() not in ("local", "dev", "development"):
            raise RuntimeError(
                "Refusing to start with insecure defaults: "
                + " | ".join(problems)
                + "  (set BSS_ENV=local to bypass for local development)"
            )
        log.warning("SECURITY: continuing anyway because BSS_ENV=%s", BSS_ENV)


def _reconcile_orphaned_runs() -> None:
    """Close out runs left mid-flight by a previous pod.

    Run state lives in this process (subprocesses + in-memory dicts), so
    anything still marked queued/running in the DB at startup belongs to a pod
    that no longer exists. Without this they linger forever and the UI keeps
    polling them as if they were live.
    """
    conn = get_db()
    now = datetime.now().isoformat()
    items = conn.execute(
        "UPDATE test_run_items SET status='cancelled', finished_at=? WHERE status IN ('running','pending','queued')",
        (now,),
    ).rowcount
    runs = conn.execute(
        "UPDATE test_runs SET status='cancelled', finished_at=? WHERE status IN ('running','queued')",
        (now,),
    ).rowcount
    conn.commit()
    conn.close()
    if runs or items:
        log.warning(
            "Startup: cancelled %d orphaned run(s) and %d test item(s) left behind by a previous pod.",
            runs,
            items,
        )


def _project_suites(project_id: int) -> Path:
    return SUITES_DIR / str(project_id)


def _project_results(project_id: int) -> Path:
    p = RESULTS_DIR / str(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


DOM_LISTENER = Path(__file__).parent / "rf_listener" / "brace_capture.py"
_AUDIT_SECRET_KEYS = ("password", "token", "api_key", "secret", "key")


def audit(
    user,
    action: str,
    project_id: Optional[int] = None,
    target: Optional[str] = None,
    **detail,
) -> None:
    import json as _json

    try:
        username = user.get("username") if isinstance(user, dict) else str(user or "")
        safe = {}
        for k, v in detail.items():
            if any((s in k.lower() for s in _AUDIT_SECRET_KEYS)):
                safe[k] = bool(v) if not isinstance(v, bool) else v
            else:
                safe[k] = v
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO audit_log (ts, username, action, project_id, target, detail) VALUES (?,?,?,?,?,?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    username,
                    action,
                    project_id,
                    None if target is None else str(target),
                    _json.dumps(safe, default=str) if safe else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.debug("Audit write failed for %s: %s", action, exc)


def _contained(base: Path, target: Path) -> bool:
    """True only if `target` is `base` or sits inside it.

    A plain str.startswith() is NOT sufficient: base '/opt/rf/suites/1' would
    match '/opt/rf/suites/12/...', letting one project reach another's files.
    """
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


_SAFE_PATH_RE = re.compile("^[A-Za-z0-9._ ()\\[\\]+&,/-]+$")


def _validate_rel_path(rel: str) -> None:
    if not rel or len(rel) > 400:
        raise HTTPException(400, "Path is empty or too long")
    if not _SAFE_PATH_RE.match(rel):
        raise HTTPException(
            400,
            "Path may only contain letters, digits, spaces and . _ - ( ) [ ] + & , /",
        )


def _safe_path(project_id: int, rel: str) -> Path:
    _validate_rel_path(rel)
    base = _project_suites(project_id).resolve()
    target = (base / rel).resolve()
    if not _contained(base, target):
        raise HTTPException(400, "Path traversal denied")
    return target


_TAG_RE = re.compile("^[a-z0-9][a-z0-9._-]{0,39}$")


def _norm_tag(tag: str) -> str:
    """Lower-case, strip a leading '@'. Returns '' if it isn't a usable tag."""
    t = (tag or "").strip().lstrip("@").lower()
    return t if _TAG_RE.match(t) else ""


def _norm_tags(raw: str) -> str:
    """Normalise a comma/space separated tag string for storage.

    Stored as ',a,b,c,' — the wrapping commas let a LIKE '%,tag,%' match a whole
    tag without also matching 'smoke' inside 'smoketest'.
    """
    parts = [_norm_tag(p) for p in re.split("[,\\s]+", raw or "") if p.strip()]
    seen, out = (set(), [])
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return f",{','.join(out)}," if out else ""


def _split_args(raw: str) -> list:
    """Split robot arguments, honouring quotes.

    Falls back to a plain split when the string has unbalanced quotes, so a
    malformed extra_args degrades the way it always did instead of raising and
    failing the test case outright.
    """
    import shlex

    try:
        return shlex.split(raw)
    except ValueError:
        log.warning("Could not parse extra_args %r — falling back to plain split", raw)
        return raw.split()


def _db_write(statements: list) -> None:
    """Run (sql, params) pairs in one transaction. Blocking — call via to_thread.

    SQLite waits up to busy_timeout for a lock. Executed directly inside an
    async function that wait is spent on the event loop, so with several tests
    finishing at once the whole server stops answering — including /health,
    which used to get the pod restarted mid-run.
    """
    with database() as conn:
        for sql, params in statements:
            conn.execute(sql, params)


RESULTS_COOKIE = "brace_results"
