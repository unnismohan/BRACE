"""
BRACE v2 — housekeeping: retention, orphan sweep, SQLite maintenance.

Why this exists: the database grows by (runs x test cases). A 1200-case suite on
a nightly schedule writes 36,000 test_run_items a month, and every one of those
cases also leaves a log.html, a report.html and possibly a screenshot on the
results volume. Nobody notices until the PVC is full mid-run and every test
starts failing for reasons that have nothing to do with the test.

Retention is OFF by default (BRACE_RETENTION_DAYS=0). A deployment that silently
started deleting run history on upgrade would be a far worse surprise than a
full disk, so switching it on is a deliberate act.
"""
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from db import DB_PATH, get_db

log = logging.getLogger(__name__)

RESULTS_DIR = Path(os.getenv("RESULTS_DIR", "/opt/rf/results"))

# 0 = keep forever. Any positive value is "delete runs that finished more than
# N days ago", subject to the floor below.
RETENTION_DAYS = max(0, int(os.getenv("BRACE_RETENTION_DAYS", "0")))

# Never let retention empty a project. A project that runs monthly would
# otherwise lose its entire history the first time the job fires.
RETENTION_KEEP_MIN = max(0, int(os.getenv("BRACE_RETENTION_KEEP_MIN", "20")))

# Audit rows are small; keep them far longer than run data. Same 0 = forever.
AUDIT_RETENTION_DAYS = max(0, int(os.getenv("BRACE_AUDIT_RETENTION_DAYS", "365")))

MAINT_CRON = os.getenv("BRACE_MAINT_CRON", "30 2 * * *").strip() or "30 2 * * *"

# Delete in batches so a purge of tens of thousands of rows never holds the
# write lock long enough to make a concurrent run's status update time out.
_BATCH = 500

# Only worth reclaiming space if there is a meaningful amount to reclaim —
# VACUUM rewrites the whole file and blocks writers for its duration.
_VACUUM_MIN_FREE_RATIO = 0.10

# Last run's outcome, for the admin UI. Process-local: it resets on restart,
# which is fine — it is a diagnostic, not a record (the audit log is the record).
_last_result: dict = {}


def _dir_bytes(path: Path) -> int:
    try:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return -1


def disk_usage() -> dict:
    """What is actually on disk right now. Used by the admin card and /metrics."""
    try:
        db_bytes = DB_PATH.stat().st_size
    except OSError:
        db_bytes = -1
    return {
        "db_bytes":      db_bytes,
        "results_bytes": _dir_bytes(RESULTS_DIR),
        "results_dir":   str(RESULTS_DIR),
    }


def _purge_candidates(conn, cutoff: str, skip_run_ids: set) -> list:
    """Runs older than the cutoff, minus the newest KEEP_MIN of each project.

    The per-project floor is applied with a window function rather than a
    subquery per project so this stays one statement regardless of project count.
    """
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT run_id, project_id, status, finished_at, started_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY project_id
                       ORDER BY COALESCE(finished_at, started_at) DESC, id DESC
                   ) AS rn
            FROM test_runs
        )
        SELECT run_id, project_id FROM ranked
        WHERE rn > ?
          AND status NOT IN ('running', 'queued')
          AND COALESCE(finished_at, started_at) < ?
        ORDER BY project_id, run_id
        """,
        (RETENTION_KEEP_MIN, cutoff),
    ).fetchall()
    # An in-flight run can be older than the cutoff (long-running suite) and its
    # DB status may not have caught up yet, so filter on live state too.
    return [(r["run_id"], r["project_id"]) for r in rows
            if r["run_id"] not in skip_run_ids]


def purge_old_runs(skip_run_ids=None, dry_run: bool = False) -> dict:
    """Delete runs past the retention window, DB rows first, then result dirs.

    Order matters. Files deleted first would leave rows pointing at reports that
    404; rows deleted first leaves at worst orphaned directories, which the
    sweep below reclaims. One is a broken UI, the other is a tidy-up.
    """
    skip_run_ids = set(skip_run_ids or ())
    result = {"enabled": RETENTION_DAYS > 0, "dry_run": dry_run,
              "retention_days": RETENTION_DAYS, "keep_min": RETENTION_KEEP_MIN,
              "runs": 0, "items": 0, "freed_bytes": 0, "run_ids": [], "errors": []}
    if RETENTION_DAYS <= 0:
        return result

    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    result["cutoff"] = cutoff

    conn = get_db()
    try:
        candidates = _purge_candidates(conn, cutoff, skip_run_ids)
        result["runs"]    = len(candidates)
        result["run_ids"] = [r for r, _ in candidates][:200]   # capped for the API response
        if dry_run or not candidates:
            return result

        for i in range(0, len(candidates), _BATCH):
            chunk = candidates[i:i + _BATCH]
            ids   = [r for r, _ in chunk]
            marks = ",".join("?" * len(ids))
            n = conn.execute(
                f"DELETE FROM test_run_items WHERE run_id IN ({marks})", ids).rowcount
            result["items"] += max(0, n)
            # notify_state is keyed by scope, not run_id, so it survives on
            # purpose — only_on_change must keep working across a purge.
            conn.execute(f"DELETE FROM test_runs WHERE run_id IN ({marks})", ids)
            conn.commit()
    finally:
        conn.close()

    if not dry_run:
        for run_id, project_id in candidates:
            d = RESULTS_DIR / str(project_id) / run_id
            if d.is_dir():
                result["freed_bytes"] += max(0, _dir_bytes(d))
                try:
                    shutil.rmtree(d)
                except OSError as exc:
                    result["errors"].append(f"{run_id}: {exc}")
        log.info("Retention purge complete", extra={
            "runs": result["runs"], "items": result["items"],
            "freed_bytes": result["freed_bytes"], "cutoff": cutoff})
    return result


def sweep_orphan_dirs(skip_run_ids=None, dry_run: bool = False) -> dict:
    """Delete result directories with no matching run row.

    Catches a crash between the DB delete and the rmtree above, and any run whose
    row was removed by the older per-project purge endpoint.
    """
    skip_run_ids = set(skip_run_ids or ())
    out = {"dirs": 0, "freed_bytes": 0, "errors": []}
    if not RESULTS_DIR.is_dir():
        return out
    conn = get_db()
    try:
        known = {r["run_id"] for r in conn.execute("SELECT run_id FROM test_runs").fetchall()}
    finally:
        conn.close()

    # Editor "Run File" results (qr-*) have no database row at all, so they can
    # only be aged out by mtime — and never below a day, or the sweep would
    # delete the directory of a quick run that is executing right now.
    qr_cutoff = time.time() - max(1, RETENTION_DAYS or 7) * 86400

    for proj_dir in RESULTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        for run_dir in proj_dir.iterdir():
            if not run_dir.is_dir():
                continue
            name = run_dir.name
            if name.startswith("qr-"):
                try:
                    if run_dir.stat().st_mtime >= qr_cutoff:
                        continue
                except OSError:
                    continue
            # Only ever touch directories that look like a run id. Anything else
            # in there was put there by a human or another feature.
            elif not name.startswith("run-") or name in known or name in skip_run_ids:
                continue
            out["dirs"] += 1
            out["freed_bytes"] += max(0, _dir_bytes(run_dir))
            if not dry_run:
                try:
                    shutil.rmtree(run_dir)
                except OSError as exc:
                    out["errors"].append(f"{run_dir.name}: {exc}")
    if out["dirs"] and not dry_run:
        log.info("Orphan result sweep removed %d directorie(s), %d bytes",
                 out["dirs"], out["freed_bytes"])
    return out


def purge_old_audit(dry_run: bool = False) -> dict:
    out = {"rows": 0, "retention_days": AUDIT_RETENTION_DAYS}
    if AUDIT_RETENTION_DAYS <= 0:
        return out
    cutoff = (datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)).isoformat(timespec="seconds")
    conn = get_db()
    try:
        if dry_run:
            out["rows"] = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE ts < ?", (cutoff,)).fetchone()[0]
        else:
            out["rows"] = max(0, conn.execute(
                "DELETE FROM audit_log WHERE ts < ?", (cutoff,)).rowcount)
            conn.commit()
    except Exception as exc:                          # noqa: BLE001 — table may predate this build
        log.debug("Audit purge skipped: %s", exc)
    finally:
        conn.close()
    return out


def optimise_db(allow_vacuum: bool = True) -> dict:
    """ANALYZE always; VACUUM only when there is real space to reclaim.

    VACUUM takes an exclusive lock for the length of a full file rewrite. On a
    busy pod that is exactly the stall that makes test status updates time out,
    so the caller passes allow_vacuum=False whenever anything is executing.
    """
    out = {"analyzed": False, "vacuumed": False, "freed_bytes": 0}
    conn = get_db()
    try:
        conn.execute("ANALYZE")
        conn.commit()
        out["analyzed"] = True

        page_size  = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        ratio = (free_pages / page_count) if page_count else 0.0
        out["free_ratio"] = round(ratio, 3)
        if allow_vacuum and ratio >= _VACUUM_MIN_FREE_RATIO:
            before = DB_PATH.stat().st_size if DB_PATH.exists() else 0
            t0 = time.monotonic()
            conn.execute("VACUUM")
            conn.commit()
            after = DB_PATH.stat().st_size if DB_PATH.exists() else 0
            out["vacuumed"]    = True
            out["freed_bytes"] = max(0, before - after)
            out["seconds"]     = round(time.monotonic() - t0, 1)
            log.info("VACUUM reclaimed %d bytes in %ss", out["freed_bytes"], out["seconds"])
        elif free_pages:
            out["skipped_vacuum"] = ("busy" if not allow_vacuum
                                     else f"only {ratio:.1%} free, below threshold")
        _ = page_size   # read for completeness; ratio is what decides
    except Exception as exc:                          # noqa: BLE001 — never fail the job on this
        out["error"] = str(exc)[:200]
        log.warning("SQLite optimise failed: %s", exc)
    finally:
        conn.close()
    return out


def run_maintenance(skip_run_ids=None, allow_vacuum: bool = True,
                    dry_run: bool = False) -> dict:
    """The whole nightly job. Safe to call by hand from the admin UI."""
    started = datetime.now()
    out = {"started_at": started.isoformat(timespec="seconds"), "dry_run": dry_run}
    try:
        out["runs"]   = purge_old_runs(skip_run_ids, dry_run=dry_run)
        out["orphans"] = sweep_orphan_dirs(skip_run_ids, dry_run=dry_run)
        out["audit"]  = purge_old_audit(dry_run=dry_run)
        # Only worth compacting if something was actually removed.
        touched = (out["runs"]["runs"] or out["orphans"]["dirs"] or out["audit"]["rows"])
        out["db"] = (optimise_db(allow_vacuum) if (touched and not dry_run)
                     else {"skipped": "nothing was deleted"})
    except Exception as exc:                          # noqa: BLE001 — a scheduled job must not die
        out["error"] = str(exc)[:300]
        log.exception("Maintenance job failed")
    out["disk"] = disk_usage()
    out["seconds"] = round((datetime.now() - started).total_seconds(), 1)
    if not dry_run:
        global _last_result
        _last_result = out
    return out


def last_result() -> dict:
    return dict(_last_result)


def config() -> dict:
    """The knobs, for the admin UI. All env-driven — read-only from the browser."""
    return {
        "retention_days":       RETENTION_DAYS,
        "retention_keep_min":   RETENTION_KEEP_MIN,
        "audit_retention_days": AUDIT_RETENTION_DAYS,
        "maint_cron":           MAINT_CRON,
        "enabled":              RETENTION_DAYS > 0,
    }
