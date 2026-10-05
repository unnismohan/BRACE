"""BRACE jobs — extracted application responsibility."""

import asyncio
from datetime import datetime, timedelta
from db import decrypt_token, get_db, rows_to_list
from scheduler import build_trigger, reload_schedules
import mailer
import maintenance


def _notify_recipients(cfg: dict, run: dict) -> list:
    """Configured list, optionally plus whoever triggered the run."""
    to = mailer.parse_recipients(cfg.get("recipients") or "")
    if cfg.get("notify_triggerer") and run.get("triggered_by"):
        conn = get_db()
        row = conn.execute(
            "SELECT email FROM users WHERE username=?", (run["triggered_by"],)
        ).fetchone()
        conn.close()
        addr = (row["email"] or "").strip() if row else ""
        if addr and addr not in to and mailer.parse_recipients(addr):
            to.append(addr)
    return to


def _should_notify(cfg: dict, run: dict, status: str, scheduled: bool) -> bool:
    """Event filter plus the only_on_change gate.

    only_on_change is what keeps this feature alive: a nightly suite that has
    been failing for three weeks otherwise sends 21 identical emails, everyone
    filters BRACE to a folder, and the next real regression is missed.
    """
    import runtime as mod_runtime

    if not cfg.get("enabled"):
        return False
    scope = f"p{run['project_id']}:" + (
        f"g{run['group_id']}" if run.get("group_id") else "adhoc"
    )
    conn = get_db()
    row = conn.execute(
        "SELECT last_status FROM notify_state WHERE scope=?", (scope,)
    ).fetchone()
    prev = row["last_status"] if row else None
    conn.execute(
        "INSERT INTO notify_state (scope, last_status, last_sent_at) VALUES (?,?,?) ON CONFLICT(scope) DO UPDATE SET last_status=excluded.last_status, last_sent_at=excluded.last_sent_at",
        (scope, status, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    wanted = (
        status == "failed"
        and cfg.get("on_scheduled_failed")
        and scheduled
        or (status == "failed" and cfg.get("on_run_failed") and (not scheduled))
        or (status == "passed" and cfg.get("on_run_passed"))
    )
    if not wanted:
        return False
    if not cfg.get("only_on_change"):
        return True
    if prev == status:
        mod_runtime.log.info(
            "Notification suppressed — unchanged outcome",
            extra={"run_id": run.get("run_id"), "scope": scope, "status": status},
        )
        return False
    return True


async def _notify_run_finished(run_id: str, status: str) -> None:
    """Email one summary for a finished run. Never raises.

    Called from the executor: a broken relay, a DNS failure or a typo in the
    recipient list must not affect the run that just completed.
    """
    import runtime as mod_runtime

    try:
        smtp = _smtp_row(decrypt=True)
        if not (smtp.get("enabled") and (smtp.get("host") or "").strip()):
            return
        conn = get_db()
        run = conn.execute(
            "SELECT tr.*, p.name AS project_name FROM test_runs tr JOIN projects p ON p.id = tr.project_id WHERE tr.run_id=?",
            (run_id,),
        ).fetchone()
        if not run:
            conn.close()
            return
        run = dict(run)
        items = rows_to_list(
            conn.execute(
                "SELECT tc_code, tc_name, status, fail_summary, fail_detail FROM test_run_items WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
        )
        conn.close()
        cfg = _notify_cfg(run["project_id"])
        scheduled = run.get("triggered_by") == "scheduler"
        if not _should_notify(cfg, run, status, scheduled):
            return
        to = _notify_recipients(cfg, run)
        if not to:
            mod_runtime.log.warning(
                "Notifications on but no valid recipients",
                extra={"project_id": run["project_id"], "run_id": run_id},
            )
            return
        if run.get("started_at") and run.get("finished_at"):
            try:
                secs = (
                    datetime.fromisoformat(run["finished_at"])
                    - datetime.fromisoformat(run["started_at"])
                ).total_seconds()
                run["duration_txt"] = (
                    f"{int(secs)}s"
                    if secs < 60
                    else f"{int(secs // 60)}m {int(secs % 60)}s"
                )
            except ValueError:
                pass
        subject, text, html_body = mailer.run_email(run, items, run["project_name"])
        for attempt in (1, 2):
            try:
                await asyncio.to_thread(
                    mailer.send_mail, smtp, to, subject, text, html_body
                )
                mod_runtime.log.info(
                    "Notification sent",
                    extra={"run_id": run_id, "recipients": len(to), "status": status},
                )
                return
            except Exception as exc:
                if attempt == 1:
                    await asyncio.sleep(5)
                    continue
                mod_runtime.log.error(
                    "Notification failed: %s",
                    mailer.friendly_error(exc),
                    extra={"run_id": run_id},
                )
    except Exception as exc:
        mod_runtime.log.error(
            "Notification dispatch error: %s", exc, extra={"run_id": run_id}
        )


def _send_digest(project_id: int) -> None:
    """Called by APScheduler from a worker thread — plain blocking code."""
    from routes import reports as mod_routes_reports
    import runtime as mod_runtime

    try:
        smtp = _smtp_row(decrypt=True)
        if not (smtp.get("enabled") and (smtp.get("host") or "").strip()):
            return
        cfg = _notify_cfg(project_id)
        if not (cfg.get("enabled") and cfg.get("weekly_digest")):
            return
        to = mailer.parse_recipients(cfg.get("recipients") or "")
        if not to:
            return
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        conn.close()
        if not row:
            return
        fake = {"username": "scheduler", "role": "admin"}
        cov = mod_routes_reports.coverage_report(project_id, stale_days=14, user=fake)
        week = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        stats = mod_routes_reports.report_stats(
            project_id,
            limit=100,
            date_from=week,
            date_to=datetime.now().strftime("%Y-%m-%d"),
            user=fake,
        )
        subject, text, html_body = mailer.digest_email(
            row["name"], project_id, cov, stats["summary"]
        )
        mailer.send_mail(smtp, to, subject, text, html_body)
        mod_runtime.log.info(
            "Weekly digest sent",
            extra={"project_id": project_id, "recipients": len(to)},
        )
    except Exception as exc:
        mod_runtime.log.error(
            "Weekly digest failed for project %s: %s",
            project_id,
            mailer.friendly_error(exc) if isinstance(exc, Exception) else exc,
        )


def _reload_all_jobs() -> None:
    """Re-register every scheduled job.

    reload_schedules() calls remove_all_jobs(), which would silently delete the
    digest and maintenance jobs too. Always go through this so they cannot drift.
    """
    reload_schedules(get_db, _trigger_group_run)
    _reload_digests()
    _reload_sync_jobs()
    _register_maintenance_job()


def _scheduled_tc_sync(project_id: int) -> None:
    """Automatic reconcile. Runs in an APScheduler worker thread — plain blocking
    code, no event loop involved."""
    from routes import projects as mod_routes_projects
    import runtime as mod_runtime

    try:
        mod_routes_projects._run_tc_sync(project_id, "scheduler")
    except Exception as exc:
        mod_runtime.log.warning(
            "Scheduled test case sync failed for project %s: %s", project_id, exc
        )


def _reload_sync_jobs() -> None:
    """Re-register per-project git sync jobs, under their own id prefix."""
    import runtime as mod_runtime
    from scheduler import scheduler

    try:
        for job in scheduler.get_jobs():
            if job.id.startswith("tcsync_"):
                scheduler.remove_job(job.id)
        conn = get_db()
        rows = conn.execute(
            "SELECT id, sync_cron FROM projects WHERE sync_mode='git' AND sync_cron IS NOT NULL AND sync_cron != ''"
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                scheduler.add_job(
                    _scheduled_tc_sync,
                    trigger=build_trigger(r["sync_cron"]),
                    args=[r["id"]],
                    id=f"tcsync_{r['id']}",
                    replace_existing=True,
                )
            except Exception as exc:
                mod_runtime.log.warning(
                    "Bad sync cron for project %s: %s", r["id"], exc
                )
    except Exception as exc:
        mod_runtime.log.warning("Could not reload sync jobs: %s", exc)


def _run_maintenance_job() -> None:
    """Nightly housekeeping. Called by APScheduler from a worker thread.

    VACUUM is suppressed while anything is executing: it takes an exclusive lock
    for a full file rewrite, and a test finishing during that window would block
    on its status update long enough to matter.
    """
    import runtime as mod_runtime

    busy = bool(mod_runtime._active_runs) or bool(mod_runtime._active_procs)
    res = maintenance.run_maintenance(
        skip_run_ids=set(mod_runtime._active_runs), allow_vacuum=not busy
    )
    if res.get("runs", {}).get("runs") or res.get("orphans", {}).get("dirs"):
        mod_runtime.audit(
            "scheduler",
            "maintenance.purge",
            runs=res["runs"]["runs"],
            items=res["runs"]["items"],
            orphan_dirs=res["orphans"]["dirs"],
            freed_bytes=res["runs"]["freed_bytes"] + res["orphans"]["freed_bytes"],
        )


def _register_maintenance_job() -> None:
    import runtime as mod_runtime
    from scheduler import scheduler

    try:
        for job in scheduler.get_jobs():
            if job.id == "brace_maintenance":
                scheduler.remove_job(job.id)
        scheduler.add_job(
            _run_maintenance_job,
            trigger=build_trigger(maintenance.MAINT_CRON),
            id="brace_maintenance",
            replace_existing=True,
        )
    except Exception as exc:
        mod_runtime.log.warning(
            "Could not register the maintenance job (cron %r): %s",
            maintenance.MAINT_CRON,
            exc,
        )


def _reload_digests() -> None:
    """Re-register digest jobs. Uses its own id prefix so it cannot collide with
    the suite schedules, which reload_schedules() clears wholesale."""
    import runtime as mod_runtime
    from scheduler import scheduler

    try:
        for job in scheduler.get_jobs():
            if job.id.startswith("digest_"):
                scheduler.remove_job(job.id)
        conn = get_db()
        rows = conn.execute(
            "SELECT project_id, digest_cron FROM notify_config WHERE enabled=1 AND weekly_digest=1"
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                scheduler.add_job(
                    _send_digest,
                    trigger=build_trigger(r["digest_cron"]),
                    args=[r["project_id"]],
                    id=f"digest_{r['project_id']}",
                    replace_existing=True,
                )
            except Exception as exc:
                mod_runtime.log.warning(
                    "Bad digest cron for project %s: %s", r["project_id"], exc
                )
    except Exception as exc:
        mod_runtime.log.warning("Could not reload digest jobs: %s", exc)


def _smtp_row(decrypt: bool = False) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM smtp_config WHERE id=1").fetchone()
    conn.close()
    d = dict(row) if row else {}
    d["password"] = decrypt_token(d.get("password") or "") if decrypt else ""
    return d


def _notify_cfg(project_id: int) -> dict:
    from routes import admin as mod_routes_admin

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM notify_config WHERE project_id=?", (project_id,)
    ).fetchone()
    conn.close()
    d = dict(mod_routes_admin._NOTIFY_DEFAULTS)
    d["project_id"] = project_id
    if row:
        d.update({k: v for k, v in dict(row).items() if v is not None})
    return d


def _trigger_group_run(group_id: int, overlap_policy: str = "queue"):
    """Called by APScheduler from a worker thread.

    Must hand the work to the main event loop. The previous version spun up a
    throwaway loop, ran trigger_run in it (which only schedules the execution
    task) and then closed that loop — destroying the task before it ran, so
    scheduled runs were created as 'queued' and never executed.
    """
    import runtime as mod_runtime

    conn = get_db()
    g = conn.execute("SELECT * FROM test_groups WHERE id=?", (group_id,)).fetchone()
    if not g:
        conn.close()
        return
    members = conn.execute(
        "\n        SELECT tc.* FROM test_cases tc\n        JOIN group_test_cases gtc ON tc.id = gtc.test_case_id\n        WHERE gtc.group_id=? AND tc.project_id=? AND tc.quarantined=0 ORDER BY gtc.order_idx\n    ",
        (group_id, g["project_id"]),
    ).fetchall()
    conn.close()
    tcs = rows_to_list(members)
    if not tcs:
        mod_runtime.log.warning(
            "Scheduled run for suite '%s' skipped — it has no test cases.", g["name"]
        )
        return
    if mod_runtime._main_loop is None or mod_runtime._main_loop.is_closed():
        mod_runtime.log.error(
            "Scheduled run for '%s' skipped — no running event loop.", g["name"]
        )
        return

    def launch():
        import execution_engine as mod_execution_engine
        import runtime as mod_runtime

        if overlap_policy == "skip" and any(
            (
                state.get("group_id") == group_id
                and state["status"] in {"queued", "running"}
                for state in mod_runtime._active_runs.values()
            )
        ):
            mod_runtime.log.info(
                "Scheduled suite %s skipped: an execution is already active", group_id
            )
            mod_runtime.audit(
                "scheduler",
                "schedule.overlap_skipped",
                project_id=g["project_id"],
                target=group_id,
            )
            return
        mod_execution_engine._start_run(
            g["project_id"],
            tcs,
            f"Scheduled: {g['name']}",
            "scheduler",
            None,
            group_id=group_id,
        )

    mod_runtime._main_loop.call_soon_threadsafe(launch)
    mod_runtime.log.info(
        "Scheduled run queued for suite '%s' (%d test cases).", g["name"], len(tcs)
    )
