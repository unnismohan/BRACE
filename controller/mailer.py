"""
BRACE v2 — email notifications.

Transport plus templates. Deliberately stdlib-only (smtplib/email/ssl): the
runtime is air-gapped and adding a dependency to send mail is not worth it.

Everything here is blocking. Callers on the event loop must wrap sends in
asyncio.to_thread, the same as the rebot merge and the zip builder.
"""
import html as _html
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

log = logging.getLogger(__name__)

# The pod has no idea what URL users reach it on, so a "view the run" link is
# impossible without being told. Unset simply means the emails carry no links.
PUBLIC_URL = (os.getenv("BRACE_PUBLIC_URL", "") or "").strip().rstrip("/")


# ── Provider presets ─────────────────────────────────────────────
# Host/port/security only. Credentials are never presets.
SMTP_PRESETS = [
    {"id": "gmail", "label": "Gmail / Google Workspace",
     "host": "smtp.gmail.com", "port": 587, "security": "starttls",
     "note": "Requires a 16-character App Password, not your normal Google "
             "password. Create one at myaccount.google.com → Security → "
             "2-Step Verification → App passwords. Username is the full "
             "address; it must match the From address unless the account has "
             "'Send mail as' configured."},
    {"id": "gmail_ssl", "label": "Gmail (SSL, port 465)",
     "host": "smtp.gmail.com", "port": 465, "security": "ssl",
     "note": "Same App Password requirement. Use this only if outbound 587 is "
             "blocked by your firewall."},
    {"id": "m365", "label": "Microsoft 365",
     "host": "smtp.office365.com", "port": 587, "security": "starttls",
     "note": "SMTP AUTH is disabled by default on M365 tenants — an admin must "
             "enable it for the mailbox."},
    {"id": "outlook", "label": "Outlook.com",
     "host": "smtp-mail.outlook.com", "port": 587, "security": "starttls",
     "note": "App password required when 2FA is on."},
    {"id": "yahoo", "label": "Yahoo Mail",
     "host": "smtp.mail.yahoo.com", "port": 587, "security": "starttls",
     "note": "Requires an app password."},
    {"id": "zoho", "label": "Zoho Mail",
     "host": "smtp.zoho.com", "port": 587, "security": "starttls", "note": ""},
    {"id": "sendgrid", "label": "SendGrid",
     "host": "smtp.sendgrid.net", "port": 587, "security": "starttls",
     "note": "Username is the literal string 'apikey'; password is the API key."},
    {"id": "ses", "label": "Amazon SES",
     "host": "email-smtp.us-east-1.amazonaws.com", "port": 587, "security": "starttls",
     "note": "Change the region in the host. Credentials are SES SMTP "
             "credentials, not your AWS access keys."},
    {"id": "relay", "label": "Internal relay (no auth)",
     "host": "", "port": 25, "security": "none",
     "note": "For an IP-allowlisted corporate relay. Leave username and "
             "password blank."},
]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Google shows an App Password as four groups of four, e.g. "abcd efgh ijkl mnop".
# Pasted verbatim the spaces go over the wire and authentication fails with a
# message that says nothing about spacing.
_APP_PW_RE = re.compile(r"^[a-z]{4}( [a-z]{4}){3}$", re.I)


def normalise_password(pw: str) -> str:
    """Strip the display spacing from a Google-style App Password.

    Only when the value matches that exact shape, so a passphrase that
    legitimately contains spaces is left alone.
    """
    pw = (pw or "").strip()
    return pw.replace(" ", "") if _APP_PW_RE.match(pw) else pw


def parse_recipients(raw: str) -> list:
    """Split a comma/semicolon/newline separated list into valid addresses."""
    out, seen = [], set()
    for part in re.split(r"[,;\n\r]+", raw or ""):
        addr = part.strip()
        if addr and _EMAIL_RE.match(addr) and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def invalid_recipients(raw: str) -> list:
    """Addresses that were supplied but are not usable — surfaced in the UI so
    a typo is visible at save time rather than silently never mailed."""
    bad = []
    for part in re.split(r"[,;\n\r]+", raw or ""):
        addr = part.strip()
        if addr and not _EMAIL_RE.match(addr):
            bad.append(addr)
    return bad


# ── Transport ────────────────────────────────────────────────────
def send_mail(cfg: dict, to: list, subject: str, text: str,
              html_body: Optional[str] = None) -> None:
    """Send one message. Raises on failure — callers decide how loud to be.

    cfg carries a decrypted password; never log it.
    """
    if not to:
        raise ValueError("No recipients")
    host = (cfg.get("host") or "").strip()
    if not host:
        raise ValueError("SMTP host is not configured")

    from_addr = (cfg.get("from_addr") or cfg.get("username") or "").strip()
    if not from_addr:
        raise ValueError("From address is not configured")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("from_name") or "BRACE", from_addr))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1] or "brace.local")
    # Alerts are transactional. Marking them as such keeps them out of bulk
    # folders and stops "out of office" storms bouncing back.
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    port = int(cfg.get("port") or 587)
    security = (cfg.get("security") or "starttls").lower()
    timeout = max(5, int(cfg.get("timeout_sec") or 20))

    ctx = ssl.create_default_context()
    if not cfg.get("verify_ssl", 1):
        # Internal relays commonly present a self-signed certificate. Opt-in
        # only, and mirrored in the UI with an explicit warning.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        user, pwd = (cfg.get("username") or "").strip(), cfg.get("password") or ""
        if user and pwd:
            server.login(user, pwd)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:                      # noqa: BLE001 — already sent
            pass


def friendly_error(exc: Exception) -> str:
    """Turn an smtplib exception into something a tester can act on.

    The raw errors are cryptic — Gmail's auth failure in particular is a wall of
    text ending in a URL, and the actual cause (an App Password is required) is
    never stated.
    """
    s = str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        if "gmail" in s.lower() or "534" in s or "535" in s:
            return ("Authentication failed. For Gmail/Workspace you must use a "
                    "16-character App Password, not the account password — and "
                    "2-Step Verification must be on. For Microsoft 365, an admin "
                    "must enable SMTP AUTH for the mailbox. (" + s[:200] + ")")
        return "Authentication failed: " + s[:250]
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return ("The server rejected the From address. Most relays only accept a "
                "sender they recognise — it usually has to match the login "
                "account. (" + s[:200] + ")")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Every recipient was rejected: " + s[:250]
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return ("The server does not support that security mode. Try STARTTLS on "
                "587, or SSL on 465. (" + s[:200] + ")")
    if isinstance(exc, ssl.SSLError):
        return ("TLS handshake failed. If this is an internal relay with a "
                "self-signed certificate, untick 'Verify TLS certificate'. "
                "(" + s[:200] + ")")
    if isinstance(exc, (TimeoutError, OSError)) and "timed out" in s.lower():
        return ("Connection timed out. The pod could not reach the SMTP server — "
                "check egress rules and that the host resolves from inside the "
                "cluster. (" + s[:150] + ")")
    if isinstance(exc, OSError):
        return ("Could not connect: " + s[:200] +
                " — check the host, port, and that outbound SMTP is permitted "
                "from this namespace.")
    return s[:300]


# ── Templates ────────────────────────────────────────────────────
def _run_url(project_id: int, run_id: str) -> Optional[str]:
    return f"{PUBLIC_URL}/?project={project_id}&run={run_id}" if PUBLIC_URL else None


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def run_email(run: dict, items: list, project_name: str) -> tuple:
    """(subject, text, html) for a finished run.

    The subject has to be readable in a phone notification preview, so the
    verdict and the counts come first — not the word "BRACE".
    """
    failed = [i for i in items if i.get("status") == "failed"]
    passed = sum(1 for i in items if i.get("status") == "passed")
    total = run.get("total") or len(items)
    verdict = "PASSED" if not failed else "FAILED"
    name = run.get("run_name") or run.get("run_id")
    # ASCII only. A non-ASCII character forces RFC 2047 encoding of part of the
    # header, which some clients and phone previews render as raw =?utf-8?b?…?=.
    subject = (f"[{verdict}] {name} - {len(failed)}/{total} failed - {project_name}"
               if failed else f"[PASSED] {name} - {total}/{total} - {project_name}")

    url = _run_url(run.get("project_id"), run.get("run_id"))
    started = (run.get("started_at") or "").replace("T", " ")[:16]
    dur = run.get("duration_txt") or "—"

    lines = [
        f"{verdict}: {name}",
        f"Project   : {project_name}",
        f"Started   : {started}     Duration: {dur}",
        f"Result    : {passed} passed, {len(failed)} failed, of {total}",
        f"Triggered : {run.get('triggered_by') or '—'}",
        "",
    ]
    if failed:
        lines.append(f"Failed test cases ({len(failed)}):")
        for i in failed[:25]:
            lines.append(f"  - {i.get('tc_code') or ''} {i.get('tc_name') or ''}")
            if i.get("fail_summary"):
                lines.append(f"      {i['fail_summary']}")
            if i.get("fail_detail"):
                lines.append(f"      {str(i['fail_detail'])[:200]}")
        if len(failed) > 25:
            lines.append(f"  ...and {len(failed) - 25} more")
        lines.append("")
    if url:
        lines.append(f"Open the run: {url}")
    text = "\n".join(lines)

    colour = "#ef4444" if failed else "#22c55e"
    rows = "".join(
        f"""<tr>
              <td style="padding:8px 10px;border-bottom:1px solid #eee;vertical-align:top;
                         font-family:Consolas,monospace;font-size:12px;color:#0F3278;
                         white-space:nowrap">{_esc(i.get('tc_code'))}</td>
              <td style="padding:8px 10px;border-bottom:1px solid #eee;font-size:13px">
                <b>{_esc(i.get('tc_name'))}</b>
                {f'<div style="color:#991b1b;font-size:12px;margin-top:3px">{_esc(i.get("fail_summary"))}</div>' if i.get('fail_summary') else ''}
                {f'<div style="color:#7f1d1d;font-size:11px;font-family:Consolas,monospace;margin-top:2px">{_esc(str(i.get("fail_detail"))[:300])}</div>' if i.get('fail_detail') else ''}
              </td>
            </tr>""" for i in failed[:25])

    html_body = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;background:#f0f3f8;padding:22px">
  <div style="max-width:660px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;
              box-shadow:0 2px 12px rgba(15,50,120,.10)">
    <div style="background:#0F3278;color:#fff;padding:16px 22px">
      <div style="font-size:12px;letter-spacing:1px;opacity:.8">BRACE · {_esc(project_name)}</div>
      <div style="font-size:19px;font-weight:700;margin-top:3px">{_esc(name)}</div>
    </div>
    <div style="padding:18px 22px">
      <div style="display:inline-block;background:{colour};color:#fff;border-radius:12px;
                  padding:4px 14px;font-size:13px;font-weight:700">{verdict}</div>
      <table style="width:100%;margin-top:16px;font-size:13px;color:#1a2340;border-collapse:collapse">
        <tr><td style="padding:3px 0;color:#6b7a99;width:110px">Result</td>
            <td><b>{passed} passed</b>, <b style="color:#b91c1c">{len(failed)} failed</b> of {total}</td></tr>
        <tr><td style="padding:3px 0;color:#6b7a99">Started</td><td>{_esc(started)}</td></tr>
        <tr><td style="padding:3px 0;color:#6b7a99">Duration</td><td>{_esc(dur)}</td></tr>
        <tr><td style="padding:3px 0;color:#6b7a99">Triggered by</td><td>{_esc(run.get('triggered_by') or '—')}</td></tr>
      </table>
      {f'''<div style="margin-top:20px;font-size:12px;font-weight:700;text-transform:uppercase;
                       letter-spacing:.8px;color:#6b7a99">Failed test cases</div>
           <table style="width:100%;border-collapse:collapse;margin-top:6px">{rows}</table>
           {f'<div style="font-size:12px;color:#6b7a99;padding:8px 2px">…and {len(failed)-25} more</div>' if len(failed) > 25 else ''}
        ''' if failed else '<div style="margin-top:16px;font-size:13px;color:#15803d">Every test case passed.</div>'}
      {f'''<div style="margin-top:22px">
             <a href="{_esc(url)}" style="background:#ee743b;color:#fff;text-decoration:none;
                padding:10px 20px;border-radius:7px;font-size:13px;font-weight:600;
                display:inline-block">Open the run in BRACE</a></div>''' if url else ''}
    </div>
    <div style="padding:12px 22px;background:#f7f9fd;color:#6b7a99;font-size:11px">
      Sent by BRACE. Change which events email you under Project&nbsp;→&nbsp;Settings&nbsp;→&nbsp;Notifications.
    </div>
  </div>
</div>"""
    return subject, text, html_body


def digest_email(project_name: str, project_id: int, cov: dict, runs_7d: dict) -> tuple:
    """Weekly coverage + activity digest."""
    t, s = cov["test_cases"], cov["scripts"]
    subject = (f"[BRACE] Weekly digest - {project_name} - "
               f"{t['coverage_pct']}% covered, {t['failed']} failing")
    link = f"{PUBLIC_URL}/?project={project_id}" if PUBLIC_URL else None
    text = "\n".join([
        f"Weekly digest — {project_name}",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "COVERAGE",
        f"  Test cases        : {t['total']}",
        f"  Ever executed     : {t['executed']} ({t['coverage_pct']}%)",
        f"  Currently passing : {t['passed']} ({t['passed_pct']}%)",
        f"  Currently failing : {t['failed']} ({t['failed_pct']}%)",
        f"  Never executed    : {t['never']} ({t['never_pct']}%)",
        f"  Stale (>{cov['stale_days']}d)    : {t['stale']}",
        f"  Scripts onboarded : {s['onboarded']}/{s['on_disk']} ({s['onboarded_pct']}%)",
        "",
        "LAST 7 DAYS",
        f"  Runs      : {runs_7d.get('total_runs', 0)}",
        f"  Executions: {runs_7d.get('total_tests', 0)}"
        f" ({runs_7d.get('pass_rate', 0)}% passed)",
        "",
        (f"Open BRACE: {link}" if link else ""),
    ])

    def kpi(label, val, sub, colour="#0F3278"):
        return f"""<td style="padding:10px 12px;background:#f7f9fd;border-radius:8px;vertical-align:top">
            <div style="font-size:10px;letter-spacing:.8px;text-transform:uppercase;color:#6b7a99">{label}</div>
            <div style="font-size:22px;font-weight:700;color:{colour};margin-top:2px">{val}</div>
            <div style="font-size:11px;color:#6b7a99">{sub}</div></td>"""

    cov_colour = "#15803d" if t["coverage_pct"] >= 80 else "#b45309" if t["coverage_pct"] >= 50 else "#b91c1c"
    html_body = f"""<div style="font-family:'Segoe UI',Arial,sans-serif;background:#f0f3f8;padding:22px">
  <div style="max-width:660px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;
              box-shadow:0 2px 12px rgba(15,50,120,.10)">
    <div style="background:#0F3278;color:#fff;padding:16px 22px">
      <div style="font-size:12px;letter-spacing:1px;opacity:.8">BRACE · WEEKLY DIGEST</div>
      <div style="font-size:19px;font-weight:700;margin-top:3px">{_esc(project_name)}</div>
    </div>
    <div style="padding:18px 22px">
      <table style="width:100%;border-collapse:separate;border-spacing:6px">
        <tr>{kpi('Coverage', str(t['coverage_pct']) + '%', f"{t['executed']} of {t['total']} ever ran", cov_colour)}
            {kpi('Passing', t['passed'], f"{t['passed_pct']}% of all cases", '#15803d')}
            {kpi('Failing', t['failed'], f"{t['failed_pct']}% of all cases", '#b91c1c')}</tr>
        <tr>{kpi('Never run', t['never'], f"{t['never_pct']}% of all cases", '#b45309')}
            {kpi('Stale', t['stale'], f"no run in {cov['stale_days']} days", '#b45309')}
            {kpi('Scripts onboarded', str(s['onboarded_pct']) + '%', f"{s['onboarded']} of {s['on_disk']} files")}</tr>
      </table>
      <div style="margin-top:18px;font-size:13px;color:#1a2340">
        Last 7 days: <b>{runs_7d.get('total_runs', 0)}</b> runs,
        <b>{runs_7d.get('total_tests', 0)}</b> executions,
        <b>{runs_7d.get('pass_rate', 0)}%</b> passed.
      </div>
      {f'''<div style="margin-top:20px"><a href="{_esc(link)}"
            style="background:#ee743b;color:#fff;text-decoration:none;padding:10px 20px;
            border-radius:7px;font-size:13px;font-weight:600;display:inline-block">Open BRACE</a></div>'''
        if link else ''}
    </div>
    <div style="padding:12px 22px;background:#f7f9fd;color:#6b7a99;font-size:11px">
      Sent by BRACE. Turn this off under Project&nbsp;→&nbsp;Settings&nbsp;→&nbsp;Notifications.
    </div>
  </div>
</div>"""
    return subject, text, html_body
