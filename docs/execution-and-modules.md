# Execution, profiles, and module guide

## Module ownership

`controller/main.py` composes FastAPI routers, starts/stops application services,
and serves the UI. Authentication and authorization live in `authentication.py`;
execution orchestration lives in `execution_engine.py`; process limits and fair
admission live in `execution.py`. Artifact extraction lives in `reporting.py`,
DOM diagnostics in `diagnostics.py`, scheduled/notification jobs in `jobs.py`,
and shared configuration/live state in `runtime.py`. HTTP handlers are grouped by
resource under `controller/routes/`. Import the owner module in tests and new code.
Existing HTTP paths and methods are covered by `tests/api_contract.json`.

The controller still requires one process/worker and one active replica. The
queue and scheduler are in memory; startup reconciles interrupted controller
runs. This change does not introduce a distributed durable queue.

## Fair queueing

At each free run slot, admit the project with the fewest active runs, breaking
ties by least recently served. Each project's own submissions remain FIFO.
Already running work is never preempted. Run and test concurrency remain bounded
by the existing configuration. The UI shows total queued submissions rather than
claiming an exact FIFO position: completion times and newly arriving projects can
change who receives the next slot.

## Environment profiles

Project administrators manage profiles in Settings → General. The form accepts one NAME=value per line for environment, Robot and new secret
variables, plus secret names to remove and an Enabled checkbox. Secret input is
cleared when the dialog closes. The equivalent API JSON supports `name`, `environment`, `variables`, `secret_variables`, `remove_secrets`,
and `enabled`. Example:

```json
{
  "name": "QA",
  "environment": {"TARGET_REGION": "qa"},
  "variables": {"BASE_URL": "https://qa.example.test"},
  "secret_variables": {"TEST_PASSWORD": "replace-with-scoped-test-credential"},
  "remove_secrets": [],
  "enabled": true
}
```

Secret variable values require a valid `BRACE_ENCRYPT_KEY`. API reads return only
secret names. Leaving the new-secret field blank (an empty API `secret_variables` object) retains
existing secrets; use Secret names to remove (`remove_secrets` in the API) to delete them.
Use the API for variable values containing newlines; the form supports one-line values. Disabling a profile prevents new
selections. A run snapshots public configuration and encrypted secrets before it
queues, so subsequent profile edits do not change queued runs. Rerun-failed uses
the currently enabled configuration of the original profile; it refuses a disabled
or removed profile. Profile variable names use letters, numbers and underscores,
starting with a letter or underscore. Values are limited to 16 KiB each.

Select the profile in the Run dialog. Robot receives variables through a variable
file and a child-only environment; secret values are not put on command lines.
Controller JWT, SMTP, Git and runner credentials are excluded from inherited test
environments. System interpreter/browser keys cannot be overridden by a profile.
Robot scripts can read selected test secrets and can print them in artifacts.
Use scoped testing credentials and protect artifact access; encryption at rest
is not log redaction.

## Retry and quarantine

The Run dialog allows zero (default), one or two retries after a failed test case
invocation. Each retry repeats the complete Robot file represented by the case,
including its setup, teardown and side effects. Do not enable retries for operations
that cannot safely repeat. The final verdict is used in the run totals; a pass after
retry is marked **Flaky pass** and counted separately in project Overview.
Expand the item to inspect every attempt's log and duration. Attempts are retained
under `attempt-1`, `attempt-2`, etc.; top-level report files show the final attempt.

Quarantine a case with a required reason using its row action. It stays searchable
and keeps its history, but normal manual and scheduled case runs exclude it.
Select **Include quarantined cases** for an intentional manual diagnostic run.
Rerun-failed is also an explicit diagnostic action and retains the requested failed
subset. Restore the case once its instability is fixed. Quarantine is manual;
BRACE never silently quarantines a case merely because it retried successfully.

## UI behavior

Cases, suites and their lazily loaded members, runs, report run history, user administration, case histories, suite-add pickers, suite selectors and run-case
pickers have bounded pages. Case/run/user searches execute on the server across
all matching rows. Run-item and audit pagination remain available. Case/run/user
filters and page offsets persist in local storage under the signed-in username
and project where relevant. Selections persist while navigating pages and filters,
are reset when changing project, and are not stored across reloads. No profile
secrets are written into filter persistence.

Overview shows execution coverage, quarantine and flaky counts, live queues,
recent failures and upcoming schedules. Failure details provide deterministic
next-check hints and links to attempt logs. History statistics describe the
current page, not all-time reliability. Membership/profile/project metadata and
coverage aggregates retain their existing response contracts.

## Isolated runner deployment

Use `docker-compose.isolated.yml` as the hardened deployment example:

1. Set `JWT_SECRET`, `BRACE_ENCRYPT_KEY`, `BRACE_ADMIN_PASSWORD`, and a random
   `BRACE_RUNNER_TOKEN` of at least 32 characters. Keep these outside Git.
2. Set `BRACE_RUNNER_PROJECT_ID` to the actual project id (default 1).
3. Run `docker compose -f docker-compose.isolated.yml up --build -d`.
4. For additional projects, deploy a separate worker service/container per project
   and add its URL/token to controller `BRACE_RUNNER_ENDPOINTS` JSON. Restart the
   controller after changing this deployment configuration.

Remote mode (`BRACE_RUNNER_MODE=remote`) never falls back to local execution when
an endpoint is missing. Production `BRACE_REQUIRE_ISOLATION=true` fails startup
unless remote mode is selected. A worker accepts only its configured project id.
Workers receive frozen sources and selected test variables, execute Robot, and
return bounded ZIP artifacts over authenticated HTTP. Archive traversal, links,
expanded size and entry-count limits are checked. Redirects are refused; HTTPS
uses normal certificate validation. Use HTTPS or a private protected transport
network. Never expose worker ports publicly.

The example worker has no controller volumes or Docker socket, runs as non-root,
uses a read-only image with temporary storage, drops capabilities, and has CPU,
memory and PID limits. Its internal network deliberately has no external test-target
access. Add only the networks/egress required by your test targets; in Kubernetes
use dedicated project workers plus explicit egress/ingress NetworkPolicies and
separate scoped secrets. Workers are project-isolated, not a new VM/container for
every attempt. Jobs from the same project share a worker UID and are trusted as
one project boundary. Do not co-locate mutually untrusted projects or mount host
secrets into a worker. Local mode is available for trusted development and has no
filesystem isolation.

Transferred archives are limited to 64 MiB compressed / 256 MiB expanded and
10,000 entries; workers hold at most 20 jobs and clean expired completed jobs.
Artifacts transfer to the controller before the worker job is deleted. Retry
history follows existing run retention cascades. Temporary worker state does not
survive a container restart; controller startup reconciliation remains required.

## Validation and reproducibility

Run `python -m unittest discover -s tests -v` in the repository virtual environment.
This includes actual Robot/rebot, fail-then-pass retry, cancellation, project fairness,
archive rejection, profile redaction, pagination, authorization, preserved API
contracts, and a real HTTP worker subprocess with transferred artifacts.

Run `python tests/benchmark.py` to write `docs/benchmark-results.json`. It creates
its own temporary database with 10,000 cases, 2,000 runs, 1,000 suites with 19,990 memberships and a 1,200-item run.
Numbers are local in-process SQL/serialization/parser measurements and exclude
HTTP, concurrent load and Chrome. The committed results include OS, Python,
repetitions, payload sizes, median and p95 timings. They are a baseline for future
comparisons, not a claimed before/after production throughput improvement.

Run `./tests/validate-production.ps1` on a Docker-capable Windows host. It builds
`Dockerfile.optimized` and starts headed Chrome under Xvfb, checks DOM access,
runs SeleniumLibrary through Robot and rebot, and reports the actual image Python/Chrome/UID. The same
smoke script is shipped at `/opt/rf/controller/production_smoke.py`. This smoke
requires Docker, image registries and dependency downloads; it does not deploy
the application or contact an external test target.

2026-10-05 validation: 32 local Python 3.12.14/Robot 7.5 regression and HTTP runner
checks passed. Production Python 3.14/pinned dependencies, Docker image build,
Chrome/Xvfb and Kubernetes deployment remain **unverified on this workstation**:
Docker and Podman executables are absent. The production validation harness is
implemented; its successful result must be recorded on a Docker-capable host.

Shell entrypoints have enforced LF endings in .gitattributes so a Windows checkout remains runnable in the Linux image.

The Compose controller publishes `0.0.0.0:8080:8080` for access through WSL and host network interfaces. The runner port remains internal.

Docker startup fixes: the optimized image explicitly installs `xorg-x11-server-Xvfb` and `xorg-x11-utils`, checks that `/usr/bin/Xvfb` exists during the build, and creates `/opt/rf/config` before assigning UID 1001 ownership. Fresh named config volumes inherit this directory ownership. Existing volumes retain their old ownership; repair them with a one-off Compose controller command using `--user 0 --entrypoint /bin/bash` and `chown -R 1001:0 /opt/rf/config`. Rebuild the image and recreate both services after applying the image fixes. These fixes address the reported missing Xvfb and SQLite startup errors; the full image build still requires validation on the Docker host.

2026-10-05 browser review: inspected the running Docker UI's project overview, testcase search and history, suites, run setup, failure details, reports and profile settings. Reviewed screens produced no browser console warnings or errors. Existing run details showed Chrome 151 executing, with target hostname resolution failures; this does not establish full production smoke validation. Local browser verification confirmed these subsequent fixes:

- Filtered testcase searches show “No test cases match” with a clear-filter action, rather than implying the project is empty.
- Recent failure entries include their run name and start time, distinguishing repeated failures of the same case. The overview API adds `started_at` to each entry.
- Browser hostname resolution errors provide guidance about the URL, runner DNS and permitted outbound networks.
- History with no passing executions suggests inspecting logs, environment and data without asserting that the test or application is broken.
- Empty run-picker pages display `0–0 of 0`. Frontend cache versions advance so rebuilt images serve the updated scripts.

Validation: frontend syntax checks and overview Python compilation pass. Changes were reviewed in a disposable local UI with 125 cases; the running Docker deployment needs an image rebuild to receive them.

2026-10-06 overview redesign: replaced the wide shared metric block with six individual cards with contextual labels and icons. Recent failures use separated, keyboard-accessible rows that open run details; schedules occupy a side panel with a useful empty state. A coverage panel explains the percentage of cases with any recorded execution, without presenting it as a pass rate. Added refresh, run-list and testcase-list navigation, loading state and retry-on-error controls. Responsive layouts use six, three or two metric columns and stack the activity panels at narrower widths. Styling uses existing light/dark theme tokens and sprite icons, with no new dependencies or API changes. Frontend cache versions advance to `20261006-1`.

Validation: local browser review at desktop and narrow widths, light and dark themes; no overview horizontal overflow at 390px or browser console errors. Refresh, failure-row navigation, view-runs and view-testcases actions verified. JavaScript syntax and Git whitespace checks pass. Preview screenshot uses disposable local data; rebuild the Docker image to deploy this redesign.
