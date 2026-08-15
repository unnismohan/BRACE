"""
BRACE APScheduler wrapper — cron-based test group execution.
"""
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

# Cron expressions are interpreted in this zone. Defaults to UTC, but a team in
# IST writing "0 2 * * *" means 2am *their* time — set BRACE_SCHEDULER_TZ to
# e.g. Asia/Kolkata so schedules mean what they look like.
SCHEDULER_TZ = os.getenv("BRACE_SCHEDULER_TZ", "UTC").strip() or "UTC"

scheduler = BackgroundScheduler(timezone=SCHEDULER_TZ)


def build_trigger(cron_expr: str) -> CronTrigger:
    """Parse a 5-field cron expression, raising ValueError if it is invalid.

    The previous check only counted fields, so '99 * * * *' was accepted and
    then silently failed to schedule.
    """
    parts = (cron_expr or "").split()
    if len(parts) != 5:
        raise ValueError("cron expression must have exactly 5 fields: "
                         "minute hour day month day_of_week")
    return CronTrigger(minute=parts[0], hour=parts[1], day=parts[2],
                       month=parts[3], day_of_week=parts[4], timezone=SCHEDULER_TZ)


def next_run_times(cron_expr: str, count: int = 3) -> list:
    """Next N fire times as ISO strings — lets the UI show what a cron means."""
    from datetime import datetime
    try:
        trigger = build_trigger(cron_expr)
    except Exception:
        return []
    out, prev = [], None
    try:
        now = datetime.now(scheduler.timezone)
        for _ in range(count):
            nxt = trigger.get_next_fire_time(prev, now if prev is None else prev)
            if not nxt:
                break
            out.append(nxt.isoformat(timespec="seconds"))
            prev = nxt
    except Exception as exc:                       # noqa: BLE001 — preview only
        log.debug("Could not compute next run times: %s", exc)
    return out


def scheduled_job_ids() -> list:
    """What APScheduler currently holds — for the admin diagnostic view."""
    try:
        return [{"id": j.id,
                 "next_run": j.next_run_time.isoformat(timespec="seconds") if j.next_run_time else None}
                for j in scheduler.get_jobs()]
    except Exception:
        return []


def start_scheduler():
    scheduler.start()
    log.info("APScheduler started")


def stop_scheduler():
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass


def reload_schedules(get_db_fn, trigger_group_fn):
    """Remove all jobs and re-load enabled schedules from DB."""
    scheduler.remove_all_jobs()
    try:
        conn = get_db_fn()
        rows = conn.execute("""
            SELECT s.id, s.group_id, s.cron_expr, tg.name AS group_name
            FROM   schedules  s
            JOIN   test_groups tg ON s.group_id = tg.id
            WHERE  s.enabled = 1
        """).fetchall()
        conn.close()
    except Exception as exc:
        log.warning("Could not load schedules: %s", exc)
        return

    for row in rows:
        try:
            trigger = build_trigger(row["cron_expr"])
            scheduler.add_job(
                trigger_group_fn,
                trigger=trigger,
                args=[row["group_id"]],
                id=f"sched_{row['id']}",
                replace_existing=True,
            )
            log.info("Scheduled group %s (%s) — cron: %s", row["group_id"], row["group_name"], row["cron_expr"])
        except Exception as exc:
            log.warning("Bad schedule id=%s: %s", row["id"], exc)
