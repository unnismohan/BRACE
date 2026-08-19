"""
BRACE v2 — SQLite database layer
"""
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import bcrypt

log = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/opt/rf/config"))
DB_PATH    = CONFIG_DIR / "brace.db"

# ── Fernet encryption for git tokens ─────────────────────────────
try:
    from cryptography.fernet import Fernet
    _FERNET_KEY = os.getenv("BRACE_ENCRYPT_KEY", "").strip()
    if not _FERNET_KEY:
        _fernet = None
        log.warning("SECURITY: BRACE_ENCRYPT_KEY not set — git tokens and the AI "
                    "API key are stored in plain text.")
    else:
        try:
            _fernet = Fernet(_FERNET_KEY.encode())
        except Exception as exc:                     # noqa: BLE001 — must not be silent
            _fernet = None
            # Previously a malformed key fell into a bare `except` and silently
            # disabled encryption, so a typo looked identical to a good key.
            log.error("SECURITY: BRACE_ENCRYPT_KEY is set but invalid (%s) — secrets "
                      "will be stored in PLAIN TEXT. It must be a url-safe base64 "
                      "32-byte key: python -c \"from cryptography.fernet import Fernet;"
                      "print(Fernet.generate_key().decode())\"", exc)
except ImportError:
    _fernet = None
    log.error("SECURITY: cryptography package missing — secrets stored in plain text.")


def encrypt_token(token: str) -> str:
    if not token:
        return ""
    if _fernet:
        return _fernet.encrypt(token.encode()).decode()
    return token


def decrypt_token(enc: str) -> str:
    if not enc:
        return ""
    if _fernet:
        try:
            return _fernet.decrypt(enc.encode()).decode()
        except Exception:
            return enc
    return enc


# ── Password hashing ─────────────────────────────────────────────
class _PwdContext:
    def hash(self, secret: str) -> str:
        return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()

    def verify(self, secret: str, hashed: str) -> bool:
        return bcrypt.checkpw(secret.encode(), hashed.encode())


pwd_context = _PwdContext()


# ── Connection factory ───────────────────────────────────────────
def get_db() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ── Schema ───────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    UNIQUE NOT NULL,
    password_hash        TEXT    NOT NULL,
    system_role          TEXT    NOT NULL DEFAULT 'user',
    full_name            TEXT,
    email                TEXT,
    must_change_password INTEGER DEFAULT 0,
    created_at           TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    description  TEXT,
    git_url      TEXT,
    git_branch   TEXT DEFAULT 'main',
    git_username TEXT,
    git_token    TEXT,
    status       TEXT DEFAULT 'active',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    project_role TEXT    NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS test_cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tc_code         TEXT UNIQUE,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    suite_path      TEXT,
    extra_args      TEXT,
    tags            TEXT,
    last_run_status TEXT,
    last_run_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tc_project ON test_cases(project_id);

CREATE TABLE IF NOT EXISTS test_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS group_test_cases (
    group_id     INTEGER NOT NULL REFERENCES test_groups(id) ON DELETE CASCADE,
    test_case_id INTEGER NOT NULL REFERENCES test_cases(id)  ON DELETE CASCADE,
    order_idx    INTEGER DEFAULT 0,
    PRIMARY KEY (group_id, test_case_id)
);

CREATE TABLE IF NOT EXISTS test_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT UNIQUE NOT NULL,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    group_id     INTEGER REFERENCES test_groups(id),
    run_name     TEXT,
    triggered_by TEXT,
    status       TEXT DEFAULT 'running',
    total        INTEGER DEFAULT 0,
    passed       INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    started_at   TEXT DEFAULT (datetime('now')),
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON test_runs(project_id);

CREATE TABLE IF NOT EXISTS test_run_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
    test_case_id INTEGER REFERENCES test_cases(id),
    tc_code      TEXT,
    tc_name      TEXT,
    rf_run_id    TEXT,
    status       TEXT DEFAULT 'pending',
    started_at   TEXT,
    finished_at  TEXT,
    fail_summary    TEXT,
    fail_detail     TEXT,
    fail_screenshot TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_run ON test_run_items(run_id);

CREATE TABLE IF NOT EXISTS schedules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    group_id   INTEGER NOT NULL REFERENCES test_groups(id)   ON DELETE CASCADE,
    cron_expr  TEXT NOT NULL,
    enabled    INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Single-row config (id always 1) for the AI debug assistant
CREATE TABLE IF NOT EXISTS ai_config (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER DEFAULT 0,
    api_base    TEXT DEFAULT 'https://openrouter.ai/api/v1',
    api_key     TEXT,
    model       TEXT DEFAULT 'anthropic/claude-sonnet-4',
    verify_ssl  INTEGER DEFAULT 1,
    updated_at  TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO ai_config (id, enabled) VALUES (1, 0);

-- Single-row SMTP settings (id always 1). Shared by every project; who gets
-- mailed about what is per-project, in notify_config below.
CREATE TABLE IF NOT EXISTS smtp_config (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER DEFAULT 0,
    host        TEXT,
    port        INTEGER DEFAULT 587,
    -- starttls (587, Gmail/M365) | ssl (465, implicit TLS) | none (internal relay)
    security    TEXT    DEFAULT 'starttls',
    username    TEXT,
    password    TEXT,               -- Fernet-encrypted, never returned to the browser
    from_addr   TEXT,
    from_name   TEXT    DEFAULT 'BRACE',
    verify_ssl  INTEGER DEFAULT 1,
    timeout_sec INTEGER DEFAULT 20,
    updated_at  TEXT    DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO smtp_config (id, enabled) VALUES (1, 0);

-- Per-project: which events mail, and to whom.
CREATE TABLE IF NOT EXISTS notify_config (
    project_id          INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    enabled             INTEGER DEFAULT 0,
    on_run_failed       INTEGER DEFAULT 1,
    on_scheduled_failed INTEGER DEFAULT 1,
    on_run_passed       INTEGER DEFAULT 0,   -- usually noise; off by default
    -- Mail only when the outcome differs from last time for the same suite.
    -- Without this a nightly suite that has been red for three weeks sends 21
    -- identical emails and everyone filters BRACE into a folder.
    only_on_change      INTEGER DEFAULT 1,
    notify_triggerer    INTEGER DEFAULT 0,   -- also mail whoever started the run
    weekly_digest       INTEGER DEFAULT 0,
    digest_cron         TEXT    DEFAULT '0 8 * * 1',
    recipients          TEXT,                -- comma/newline separated
    updated_at          TEXT    DEFAULT (datetime('now'))
);

-- Last outcome per notification scope, for only_on_change.
CREATE TABLE IF NOT EXISTS notify_state (
    scope        TEXT PRIMARY KEY,   -- e.g. 'p1:g3' (project 1, suite 3) or 'p1:adhoc'
    last_status  TEXT,
    last_sent_at TEXT
);

-- Who changed what. Deliberately denormalised (username as text, not a user_id)
-- so the trail survives the user being deleted — an audit log that loses its
-- subject when someone leaves is not an audit log.
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT,        -- local time, written by the app (see below)
    username   TEXT,
    action     TEXT,        -- 'run.trigger', 'tc.delete', 'user.role_change', …
    project_id INTEGER,
    target     TEXT,        -- run_id / tc_code / schedule id / affected username
    detail     TEXT         -- small JSON blob; NEVER secrets
);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user   ON audit_log(username);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""


def _add_column(cursor, table: str, column: str, col_type: str):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
    except Exception:
        pass


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript(_SCHEMA)
    conn.commit()

    # Safe migrations for existing DBs
    for col, typ in [
        ("full_name",            "TEXT"),
        ("email",                "TEXT"),
        ("system_role",          "TEXT DEFAULT 'user'"),
    ]:
        _add_column(c, "users", col, typ)
    for col, typ in [
        ("git_url",     "TEXT"),
        ("git_branch",  "TEXT DEFAULT 'main'"),
        ("git_username","TEXT"),
        ("git_token",   "TEXT"),
    ]:
        _add_column(c, "projects", col, typ)
    for col, typ in [
        ("tc_code",    "TEXT"),
        ("description","TEXT"),
    ]:
        _add_column(c, "test_cases", col, typ)
    _add_column(c, "test_groups", "description", "TEXT")
    _add_column(c, "schedules",   "project_id",  "INTEGER")
    _add_column(c, "ai_config",   "verify_ssl",  "INTEGER DEFAULT 1")
    _add_column(c, "test_runs",   "rerun_of",    "TEXT")   # parent run for re-run-failed
    # Free-text tags, stored as ',smoke,regression,' so LIKE '%,smoke,%' matches
    # a whole tag and not 'smoke' inside 'smoketest'.
    _add_column(c, "test_cases",  "tags",        "TEXT")
    # Why a case failed, parsed out of output.xml at run time so the UI can
    # show it without anyone opening log.html.
    for col in ("fail_summary", "fail_detail", "fail_screenshot"):
        _add_column(c, "test_run_items", col, "TEXT")
    # Email notifications — added after the tables above shipped, so existing
    # databases get the columns without a rebuild.
    for col, typ in [("from_name", "TEXT DEFAULT 'BRACE'"),
                     ("timeout_sec", "INTEGER DEFAULT 20"),
                     ("security", "TEXT DEFAULT 'starttls'")]:
        _add_column(c, "smtp_config", col, typ)
    for col, typ in [("weekly_digest", "INTEGER DEFAULT 0"),
                     ("digest_cron", "TEXT DEFAULT '0 8 * * 1'"),
                     ("only_on_change", "INTEGER DEFAULT 1"),
                     ("notify_triggerer", "INTEGER DEFAULT 0")]:
        _add_column(c, "notify_config", col, typ)
    # Git-native test cases. A project stays in 'manual' mode until someone opts
    # in, so an upgrade never starts rewriting test case metadata on its own.
    for col, typ in [("sync_mode",        "TEXT DEFAULT 'manual'"),
                     ("sync_cron",        "TEXT"),
                     ("last_sync_at",     "TEXT"),
                     ("last_sync_result", "TEXT")]:
        _add_column(c, "projects", col, typ)
    # Where a synced test case came from. NULL source_path = created by hand in
    # the UI, which the sync must never touch.
    for col, typ in [("source_path",  "TEXT"),
                     ("source_test",  "TEXT"),
                     ("sync_status",  "TEXT")]:
        _add_column(c, "test_cases", col, typ)
    # Retention purges by finished_at, and the run list sorts by started_at.
    c.execute("CREATE INDEX IF NOT EXISTS idx_runs_finished ON test_runs(finished_at)")
    # Per-test-case history filters on tc_code across every run; this table grows
    # by (runs x cases) so the scan gets expensive without an index.
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_tc_code ON test_run_items(tc_code)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_tcid    ON test_run_items(test_case_id)")
    conn.commit()

    # Migrate old role → system_role
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "role" in cols:
            c.execute("UPDATE users SET system_role='admin' WHERE system_role='user' AND role='admin'")
            conn.commit()
    except Exception:
        pass

    # Bootstrap admin user. BRACE_ADMIN_PASSWORD lets a deployment seed a real
    # password instead of the well-known default; the change-password flag is
    # only forced when we fall back to that default.
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seeded = os.getenv("BRACE_ADMIN_PASSWORD", "").strip()
        c.execute(
            "INSERT INTO users (username, password_hash, system_role, must_change_password) VALUES (?,?,?,?)",
            ("admin", pwd_context.hash(seeded or "admin"), "admin", 0 if seeded else 1),
        )
        conn.commit()
        if not seeded:
            log.warning("SECURITY: bootstrap admin created with the default password 'admin'. "
                        "Set BRACE_ADMIN_PASSWORD, or change it at first login.")

    conn.close()


# ── TC code generation ───────────────────────────────────────────
def next_tc_code(conn) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(tc_code, 4) AS INTEGER)) FROM test_cases WHERE tc_code IS NOT NULL"
    ).fetchone()
    n = (row[0] or 0) + 1
    return f"TC-{n:04d}"


# ── Helpers ──────────────────────────────────────────────────────
def row_to_dict(row) -> Optional[dict]:
    return dict(row) if row else None


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]
