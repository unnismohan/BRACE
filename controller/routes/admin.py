"""BRACE routes.admin — extracted application responsibility."""

import asyncio
import shutil
from datetime import datetime
from typing import Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from db import encrypt_token, get_db, rows_to_list
from scheduler import build_trigger
import mailer
import maintenance
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()


class AIConfigReq(BaseModel):
    enabled: bool = False
    api_base: str = "https://openrouter.ai/api/v1"
    api_key: Optional[str] = None
    model: str = "anthropic/claude-sonnet-4"
    verify_ssl: bool = True


class SmtpConfigReq(BaseModel):
    enabled: Optional[bool] = None
    host: Optional[str] = None
    port: Optional[int] = None
    security: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    from_addr: Optional[str] = None
    from_name: Optional[str] = None
    verify_ssl: Optional[bool] = None
    timeout_sec: Optional[int] = None


@router.get("/api/admin/smtp-presets")
def smtp_presets(user=Depends(mod_authentication._require_sys_admin)):
    """Host/port/security for common providers. Never credentials."""
    return mailer.SMTP_PRESETS


@router.get("/api/admin/smtp-config")
def get_smtp_config(user=Depends(mod_authentication._require_sys_admin)):
    import jobs as mod_jobs

    d = mod_jobs._smtp_row()
    stored = mod_jobs._smtp_row(decrypt=True).get("password")
    d.pop("password", None)
    d["has_password"] = bool(stored)
    d["public_url"] = mailer.PUBLIC_URL or ""
    return d


@router.put("/api/admin/smtp-config")
def put_smtp_config(
    req: SmtpConfigReq, user=Depends(mod_authentication._require_sys_admin)
):
    import jobs as mod_jobs
    import runtime as mod_runtime

    cur = mod_jobs._smtp_row(decrypt=True)
    sec = (req.security or cur.get("security") or "starttls").lower()
    if sec not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "security must be starttls, ssl or none")
    pwd = (
        cur.get("password")
        if req.password in (None, "")
        else mailer.normalise_password(req.password)
    )
    conn = get_db()
    conn.execute(
        "\n        UPDATE smtp_config SET enabled=?, host=?, port=?, security=?, username=?,\n               password=?, from_addr=?, from_name=?, verify_ssl=?, timeout_sec=?,\n               updated_at=? WHERE id=1",
        (
            1 if (cur.get("enabled") if req.enabled is None else req.enabled) else 0,
            (req.host if req.host is not None else cur.get("host")) or "",
            int(req.port if req.port is not None else cur.get("port") or 587),
            sec,
            (req.username if req.username is not None else cur.get("username")) or "",
            encrypt_token(pwd or ""),
            (req.from_addr if req.from_addr is not None else cur.get("from_addr"))
            or "",
            (req.from_name if req.from_name is not None else cur.get("from_name"))
            or "BRACE",
            (
                1
                if (
                    cur.get("verify_ssl", 1)
                    if req.verify_ssl is None
                    else req.verify_ssl
                )
                else 0
            ),
            max(
                5,
                int(
                    req.timeout_sec
                    if req.timeout_sec is not None
                    else cur.get("timeout_sec") or 20
                ),
            ),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    mod_runtime.log.info("SMTP config updated", extra={"user": user["username"]})
    mod_runtime.audit(
        user,
        "smtp.update",
        target=req.host or cur.get("host"),
        enabled=req.enabled,
        security=sec,
        password=bool(req.password),
    )
    return get_smtp_config(user)


class SmtpTestReq(BaseModel):
    to: str


@router.post("/api/admin/smtp-config/test")
async def test_smtp_config(
    req: SmtpTestReq, user=Depends(mod_authentication._require_sys_admin)
):
    """Send a real email. Reports the actual error, which is the whole point —
    SMTP misconfiguration is otherwise invisible until an alert silently fails.
    """
    import jobs as mod_jobs
    import runtime as mod_runtime

    to = mailer.parse_recipients(req.to)
    if not to:
        raise HTTPException(400, "Enter a valid destination address")
    cfg = mod_jobs._smtp_row(decrypt=True)
    if not (cfg.get("host") or "").strip():
        raise HTTPException(400, "Configure the SMTP host first")
    text = f"This is a test message from BRACE.\n\nIf you received it, notifications are correctly configured.\nSent {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} from pod {mod_runtime.POD_NAME}.\n"
    html_body = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;padding:20px"><h2 style='color:#0F3278;margin:0 0 8px'>BRACE test email</h2><p style='font-size:14px;color:#1a2340'>If you received this, notifications are correctly configured.</p><p style='font-size:12px;color:#6b7a99'>Sent {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} from pod {mod_runtime.POD_NAME}.</p></div>"""
    try:
        await asyncio.to_thread(
            mailer.send_mail, cfg, to, "BRACE test email", text, html_body
        )
    except Exception as exc:
        mod_runtime.log.warning("SMTP test failed: %s", type(exc).__name__)
        raise HTTPException(400, mailer.friendly_error(exc))
    mod_runtime.audit(user, "smtp.test", recipients=len(to))
    return {"ok": True, "sent_to": to}


class NotifyConfigReq(BaseModel):
    enabled: Optional[bool] = None
    on_run_failed: Optional[bool] = None
    on_scheduled_failed: Optional[bool] = None
    on_run_passed: Optional[bool] = None
    only_on_change: Optional[bool] = None
    notify_triggerer: Optional[bool] = None
    weekly_digest: Optional[bool] = None
    digest_cron: Optional[str] = None
    recipients: Optional[str] = None


_NOTIFY_DEFAULTS = {
    "enabled": 0,
    "on_run_failed": 1,
    "on_scheduled_failed": 1,
    "on_run_passed": 0,
    "only_on_change": 1,
    "notify_triggerer": 0,
    "weekly_digest": 0,
    "digest_cron": "0 8 * * 1",
    "recipients": "",
}


@router.get("/api/projects/{project_id}/notify-config")
def get_notify_config(project_id: int, user=Depends(mod_authentication._proj_admin)):
    import jobs as mod_jobs

    d = mod_jobs._notify_cfg(project_id)
    d["recipient_list"] = mailer.parse_recipients(d.get("recipients") or "")
    smtp = mod_jobs._smtp_row()
    d["smtp_ready"] = bool(smtp.get("enabled") and (smtp.get("host") or "").strip())
    d["public_url"] = mailer.PUBLIC_URL or ""
    return d


@router.put("/api/projects/{project_id}/notify-config")
def put_notify_config(
    project_id: int, req: NotifyConfigReq, user=Depends(mod_authentication._proj_admin)
):
    import jobs as mod_jobs
    import runtime as mod_runtime

    cur = mod_jobs._notify_cfg(project_id)
    if req.digest_cron:
        try:
            build_trigger(req.digest_cron)
        except Exception as exc:
            raise HTTPException(400, f"Invalid digest schedule: {exc}")
    bad = mailer.invalid_recipients(req.recipients or "")
    if bad:
        raise HTTPException(400, "Not valid email addresses: " + ", ".join(bad[:5]))
    pick = lambda new, key: cur.get(key) if new is None else 1 if new else 0
    conn = get_db()
    conn.execute(
        "\n        INSERT INTO notify_config (project_id, enabled, on_run_failed, on_scheduled_failed,\n            on_run_passed, only_on_change, notify_triggerer, weekly_digest, digest_cron,\n            recipients, updated_at)\n        VALUES (?,?,?,?,?,?,?,?,?,?,?)\n        ON CONFLICT(project_id) DO UPDATE SET\n            enabled=excluded.enabled, on_run_failed=excluded.on_run_failed,\n            on_scheduled_failed=excluded.on_scheduled_failed,\n            on_run_passed=excluded.on_run_passed, only_on_change=excluded.only_on_change,\n            notify_triggerer=excluded.notify_triggerer, weekly_digest=excluded.weekly_digest,\n            digest_cron=excluded.digest_cron, recipients=excluded.recipients,\n            updated_at=excluded.updated_at",
        (
            project_id,
            pick(req.enabled, "enabled"),
            pick(req.on_run_failed, "on_run_failed"),
            pick(req.on_scheduled_failed, "on_scheduled_failed"),
            pick(req.on_run_passed, "on_run_passed"),
            pick(req.only_on_change, "only_on_change"),
            pick(req.notify_triggerer, "notify_triggerer"),
            pick(req.weekly_digest, "weekly_digest"),
            req.digest_cron or cur.get("digest_cron") or "0 8 * * 1",
            cur.get("recipients") if req.recipients is None else req.recipients,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    mod_jobs._reload_digests()
    mod_runtime.audit(
        user,
        "notify.update",
        project_id=project_id,
        enabled=req.enabled,
        recipients=len(mailer.parse_recipients(req.recipients or "")),
        weekly_digest=req.weekly_digest,
    )
    return get_notify_config(project_id, user)


@router.get("/api/admin/ai-config")
def get_ai_config(user=Depends(mod_authentication._require_sys_admin)):
    from routes import ai as mod_routes_ai

    cfg = mod_routes_ai._get_ai_config()
    key = cfg.get("api_key") or ""
    return {
        "enabled": cfg["enabled"],
        "api_base": cfg["api_base"],
        "model": cfg["model"],
        "verify_ssl": cfg["verify_ssl"],
        "has_key": bool(key),
        "key_hint": "…" + key[-4:] if len(key) > 4 else "",
    }


@router.put("/api/admin/ai-config")
def put_ai_config(
    req: AIConfigReq, user=Depends(mod_authentication._require_sys_admin)
):
    import runtime as mod_runtime

    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute("INSERT OR IGNORE INTO ai_config (id, enabled) VALUES (1, 0)")
    if req.api_key:
        conn.execute(
            "UPDATE ai_config SET enabled=?, api_base=?, api_key=?, model=?, verify_ssl=?, updated_at=? WHERE id=1",
            (
                int(req.enabled),
                req.api_base,
                encrypt_token(req.api_key),
                req.model,
                int(req.verify_ssl),
                now,
            ),
        )
    else:
        conn.execute(
            "UPDATE ai_config SET enabled=?, api_base=?, model=?, verify_ssl=?, updated_at=? WHERE id=1",
            (int(req.enabled), req.api_base, req.model, int(req.verify_ssl), now),
        )
    conn.commit()
    saved = conn.execute("SELECT enabled, api_key FROM ai_config WHERE id=1").fetchone()
    conn.close()
    mod_runtime.audit(
        user,
        "ai.config_update",
        enabled=req.enabled,
        model=req.model,
        api_base=req.api_base,
        api_key=bool(req.api_key),
    )
    return {
        "ok": True,
        "enabled": bool(saved["enabled"]),
        "has_key": bool(saved["api_key"]),
    }


@router.post("/api/admin/ai-config/test")
def test_ai_config(user=Depends(mod_authentication._require_sys_admin)):
    """Make a real minimal call so the admin gets a definitive answer, not a guess."""
    from routes import ai as mod_routes_ai
    import requests

    cfg = mod_routes_ai._get_ai_config()
    if not cfg["api_key"]:
        raise HTTPException(400, "No API key saved")
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    mod_routes_ai._hush_insecure_warning(cfg["verify_ssl"])
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "user", "content": "Reply with the single word: ok"}
                ],
                "max_tokens": 8,
            },
            timeout=30,
            verify=cfg["verify_ssl"],
        )
    except Exception as exc:
        raise HTTPException(502, f"Cannot reach {url} — {type(exc).__name__}: {exc}")
    r.encoding = "utf-8"
    if r.status_code != 200:
        raise HTTPException(502, f"HTTP {r.status_code} from provider: {r.text[:400]}")
    try:
        data = r.json()
        reply = data["choices"][0]["message"]["content"]
        model = data.get("model", cfg["model"])
    except (ValueError, KeyError, IndexError):
        raise HTTPException(502, f"Unexpected response shape: {r.text[:400]}")
    return {"ok": True, "model": model, "reply": (reply or "").strip()[:120]}


@router.get("/api/projects/{project_id}/data-summary")
def data_summary(project_id: int, user=Depends(mod_authentication._proj_viewer)):
    """Counts backing the Danger Zone, so the admin sees the blast radius first."""
    import runtime as mod_runtime

    conn = get_db()
    q = lambda sql: conn.execute(sql, (project_id,)).fetchone()[0]
    out = {
        "test_cases": q("SELECT COUNT(*) FROM test_cases  WHERE project_id=?"),
        "suites": q("SELECT COUNT(*) FROM test_groups WHERE project_id=?"),
        "runs": q("SELECT COUNT(*) FROM test_runs   WHERE project_id=?"),
        "schedules": q("SELECT COUNT(*) FROM schedules   WHERE project_id=?"),
    }
    conn.close()
    results_dir = mod_runtime._project_results(project_id)
    if results_dir.exists():
        out["result_dirs"] = sum((1 for p in results_dir.iterdir() if p.is_dir()))
        out["result_bytes"] = sum(
            (p.stat().st_size for p in results_dir.rglob("*") if p.is_file())
        )
    else:
        out["result_dirs"] = 0
        out["result_bytes"] = 0
    return out


@router.delete("/api/projects/{project_id}/runs")
def purge_runs(
    project_id: int, keep_last: int = 0, user=Depends(mod_authentication._current_user)
):
    """Delete run records and their result files. keep_last>0 retains the newest N."""
    import authentication as mod_authentication
    import runtime as mod_runtime

    mod_authentication._require_proj_admin(project_id, user)
    conn = get_db()
    if keep_last > 0:
        rows = conn.execute(
            "SELECT run_id FROM test_runs WHERE project_id=? AND run_id NOT IN\n               (SELECT run_id FROM test_runs WHERE project_id=?\n                ORDER BY started_at DESC, id DESC LIMIT ?)",
            (project_id, project_id, keep_last),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT run_id FROM test_runs WHERE project_id=?", (project_id,)
        ).fetchall()
    run_ids = [r["run_id"] for r in rows if r["run_id"] not in mod_runtime._active_runs]
    skipped = len(rows) - len(run_ids)
    for rid in run_ids:
        conn.execute("DELETE FROM test_run_items WHERE run_id=?", (rid,))
        conn.execute("DELETE FROM test_runs     WHERE run_id=?", (rid,))
    conn.commit()
    conn.close()
    results_dir = mod_runtime._project_results(project_id)
    freed = 0
    for rid in run_ids:
        d = results_dir / rid
        if d.exists() and d.is_dir():
            freed += sum((p.stat().st_size for p in d.rglob("*") if p.is_file()))
            shutil.rmtree(d, ignore_errors=True)
    mod_runtime.audit(
        user,
        "data.purge_runs",
        project_id=project_id,
        deleted=len(run_ids),
        keep_last=keep_last,
        freed_bytes=freed,
    )
    return {
        "deleted_runs": len(run_ids),
        "freed_bytes": freed,
        "skipped_active": skipped,
    }


@router.delete("/api/projects/{project_id}/test-cases")
def purge_test_cases(project_id: int, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import runtime as mod_runtime

    mod_authentication._require_proj_admin(project_id, user)
    conn = get_db()
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM test_cases WHERE project_id=?", (project_id,)
        ).fetchall()
    ]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE test_run_items SET test_case_id=NULL WHERE test_case_id IN ({marks})",
            ids,
        )
        conn.execute(
            f"DELETE FROM group_test_cases WHERE test_case_id IN ({marks})", ids
        )
        conn.execute("DELETE FROM test_cases WHERE project_id=?", (project_id,))
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user, "data.purge_test_cases", project_id=project_id, deleted=len(ids)
    )
    return {"deleted_test_cases": len(ids)}


@router.delete("/api/projects/{project_id}/groups")
def purge_suites(project_id: int, user=Depends(mod_authentication._current_user)):
    import authentication as mod_authentication
    import runtime as mod_runtime

    mod_authentication._require_proj_admin(project_id, user)
    conn = get_db()
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM test_groups WHERE project_id=?", (project_id,)
        ).fetchall()
    ]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE test_runs SET group_id=NULL WHERE group_id IN ({marks})", ids
        )
        conn.execute(f"DELETE FROM schedules        WHERE group_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM group_test_cases WHERE group_id IN ({marks})", ids)
        conn.execute("DELETE FROM test_groups WHERE project_id=?", (project_id,))
    conn.commit()
    conn.close()
    mod_runtime.audit(
        user, "data.purge_suites", project_id=project_id, deleted=len(ids)
    )
    return {"deleted_suites": len(ids)}


@router.get("/api/admin/maintenance")
async def get_maintenance(
    refresh: bool = False, user=Depends(mod_authentication._require_sys_admin)
):
    """Retention settings, current disk usage, and the last job's outcome.

    Disk size comes from the shared cache, so opening Administration is instant
    instead of waiting on a walk of the whole results volume. refresh=1 forces a
    fresh measurement for the operator who wants the live number.
    """
    import runtime as mod_runtime

    disk = await asyncio.to_thread(maintenance.disk_usage, refresh)
    return {
        "config": maintenance.config(),
        "disk": disk,
        "last": maintenance.last_result(),
        "busy": bool(mod_runtime._active_runs) or bool(mod_runtime._active_procs),
    }


@router.post("/api/admin/maintenance/run")
async def run_maintenance_now(
    dry_run: bool = True, user=Depends(mod_authentication._require_sys_admin)
):
    """Run housekeeping on demand.

    Defaults to dry_run=True. Deleting run history is irreversible, so the
    caller has to ask for it explicitly — the UI shows what a dry run found and
    makes the operator confirm before the real thing.
    """
    import runtime as mod_runtime

    busy = bool(mod_runtime._active_runs) or bool(mod_runtime._active_procs)
    res = await asyncio.to_thread(
        maintenance.run_maintenance, set(mod_runtime._active_runs), not busy, dry_run
    )
    if not dry_run:
        mod_runtime.audit(
            user,
            "maintenance.run_now",
            runs=res.get("runs", {}).get("runs", 0),
            orphan_dirs=res.get("orphans", {}).get("dirs", 0),
            freed_bytes=res.get("runs", {}).get("freed_bytes", 0)
            + res.get("orphans", {}).get("freed_bytes", 0),
        )
    return res


@router.get("/api/admin/audit")
def list_audit(
    username: str = "",
    action: str = "",
    project_id: int = 0,
    date_from: str = "",
    date_to: str = "",
    offset: int = 0,
    limit: int = 50,
    user=Depends(mod_authentication._require_sys_admin),
):
    """Paged, filtered audit trail. Admin only — it names who did what."""
    limit = max(1, min(500, limit))
    offset = max(0, offset)
    where, params = ([], [])
    if username:
        where.append("username=?")
        params.append(username)
    if action:
        where.append("(action=? OR action LIKE ?)")
        params += [action, f"{action}.%"]
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if date_from:
        where.append("substr(ts,1,10) >= ?")
        params.append(date_from[:10])
    if date_to:
        where.append("substr(ts,1,10) <= ?")
        params.append(date_to[:10])
    clause = " WHERE " + " AND ".join(where) if where else ""
    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM audit_log{clause}", params
        ).fetchone()["n"]
        rows = rows_to_list(
            conn.execute(
                f"SELECT * FROM audit_log{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        )
        users = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT username FROM audit_log ORDER BY username"
            ).fetchall()
            if r[0]
        ]
        actions = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT action FROM audit_log ORDER BY action"
            ).fetchall()
            if r[0]
        ]
    finally:
        conn.close()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "entries": rows,
        "usernames": users,
        "actions": actions,
    }
