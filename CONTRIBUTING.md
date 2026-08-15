# Contributing to BRACE

Thanks for taking the time. BRACE is a small, deliberately dependency-light
project, and that shapes most of the guidance below.

## Getting it running

You need Docker. Nothing else.

```bash
git clone <your-fork-url> && cd BRACE
docker compose -f docker-compose.local.yml up -d --build
```

Open <http://localhost:8080>, sign in as `admin` / `admin`.

The first build takes a while — it compiles a UBI9 image with Python, Robot
Framework and a pinned Chrome. Later builds are cached.

### Backend only, no Docker

Faster for API work. Browser-based tests will not run, but everything else does.

```bash
cd controller
pip install -r ../requirements_optimized.txt
CONFIG_DIR=/tmp/brace-cfg SUITES_DIR=/tmp/brace-suites RESULTS_DIR=/tmp/brace-res \
BSS_ENV=local JWT_SECRET=$(openssl rand -hex 32) \
python -m uvicorn main:app --reload --port 8080
```

## Project shape

```
controller/
  main.py        API, execution engine, auth, security  (single module by design)
  db.py          SQLite schema + idempotent migrations
  scheduler.py   APScheduler wrapper, cron parsing
  mailer.py      SMTP transport and email templates
  static/        index.html + css/ + js/  — no build step
k8s/             Deployment, PVCs, Secret template, helper scripts
```

## Constraints worth understanding before you open an MR

These are not style preferences — breaking them breaks real deployments.

**The frontend has no build step.** `static/js/*.js` are plain classic scripts
loaded in a fixed order by `index.html`, sharing one global scope. No modules, no
bundler, no npm. BRACE is designed to run in air-gapped environments where a
build pipeline is not available and a CDN cannot be reached. If you add a file,
add a `<script>` tag in the right position — order matters.

**No new runtime dependencies without a good reason.** Same rationale. The mailer
uses stdlib `smtplib` rather than a library, for example. If you genuinely need
one, say why in the MR.

**Single process, single worker.** Run state, the execution semaphores and the
scheduler live in memory. Anything that assumes multiple workers or replicas is
wrong — see the Architecture section of the README.

**Blocking calls belong in a thread.** The event loop serves every HTTP request.
Anything that blocks — a subprocess, `rebot`, zipping, SMTP — goes through
`asyncio.to_thread`.

**Timestamps are local, stored as ISO strings.** Compare on the date prefix
(`substr(started_at,1,10)`), never on the whole string: rows exist in both
`T`-separated and space-separated forms, and `'T' > ' '` silently excludes rows.

## Code style

Match the surrounding code. Beyond that:

- Comments explain **why**, not what. A comment saying what a line does is noise;
  one saying why it is written that way is what stops the next person "fixing" it.
- Escape anything user-supplied that reaches HTML — `esc()` in JS,
  `html.escape()` in Python. Test names end up in the UI and in emails.
- Any filesystem path from a request must be containment-checked against the
  project directory (`_contained()`), never merely prefix-matched.
- New tables and columns go through `db.py` as `CREATE TABLE IF NOT EXISTS` or
  `_add_column`, so upgrades are automatic and re-runnable.

## Testing

There is no formal test suite yet — contributions welcome. Until then, verify by
running the thing, and say in your MR what you actually checked. A real run
against a real `.robot` file is worth more than an assertion that it should work.

At minimum, before opening an MR:

```bash
python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('controller/*.py')]"
for f in controller/static/js/*.js; do node --check "$f"; done
```

If you touch the UI, click through the tab you changed and check the browser
console is clean.

## Opening a merge request

1. Branch from `main`.
2. Keep it focused — one change per MR reviews far faster.
3. Describe **what you verified**, not just what you changed.
4. If it changes behaviour anyone depends on, update the README and the in-app
   user manual (`controller/static/js/settings.js`, `HELP_SECTIONS`).

## Reporting bugs

Include the version from `/health`, the startup log line (`BRACE v2 started —
…`), and what you expected versus what happened. For anything execution-related,
the run's console log is usually the fastest route to a diagnosis.

## Security issues

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
