# BRACE improvement log

This file records implementation details, compatibility changes, validation,
and remaining work from the repository review. An item is implemented only when
listed in the implemented table; the roadmap is not a release promise.

## Implemented: security and execution foundation

| Change | Behavior and reason | Validation |
|---|---|---|
| Live account authorization | API, report, and new SSE requests look up the current account and role rather than trusting the JWT role. Deleted accounts are rejected. | Deleted-admin and demotion regression tests. |
| Session invalidation | JWTs include the persisted `session_version`. Role changes, password changes/resets, and logout increment it. Logout currently revokes all sessions for that account. | Password rotation, demotion, and logout regression tests. |
| Server password-change enforcement | Accounts requiring a password change can only change their password or log out. New accounts and administrator resets require a change. The UI stores the flag across refreshes and installs the replacement token after a successful change. | API, report, and SSE denial tests; replacement token accepted. |
| Password validation | New/reset passwords require 12 characters and at most 72 UTF-8 bytes, matching bcrypt's input limit. Existing passwords remain usable. Malformed bcrypt input fails authentication instead of causing an internal error. | Short-password rejection test. |
| Login throttling | At most ten attempts per peer address per rolling minute, returning HTTP 429 and Retry-After. Entries are bounded to 10,000 peers. | Sliding-window expiration test. |
| Production encryption check | Startup fails outside local/dev/development when Fernet encryption is unavailable, including malformed keys or missing cryptography. | Production preflight regression test. |
| Git TLS verification | Clone and fetch no longer force `http.sslVerify=false`. Private certificate authorities must be trusted by the container or Git configuration. | Source inspection; external Git-server integration not run. |
| Database transaction helper | `database()` commits successful blocks, rolls back exceptions, and always closes. Authentication, logout, and hot-path execution writes use it. | Rollback regression test. |
| Atomic run creation | One commit persists the run and its items, replacing a commit for every test case. | Real run creation/cancellation integration test. |
| Bounded worker scheduling | Fixed worker tasks replace one task per test case. Results and failures stay in submission order. Existing global browser and run limits remain in force. | 1,000-item test checks concurrency and result ordering. |
| Process-tree cleanup | Robot starts in a separate POSIX session; cancellation/timeout terminates its process group, escalating to SIGKILL. Windows uses taskkill /T /F with a parent-kill fallback. Robot and rebot use the controller's Python executable. | Real Robot termination and full cancellation integration tests on Windows. Linux process-group behavior still requires container verification. |
| Cancellation correctness | Workers do not publish a verdict after cancellation. Final item updates require running status, and cancelled runs do not merge/publish a passed or failed completion. | Real Robot cancellation retains cancelled item and run statuses. |
| Batched project statistics | Project listing uses grouped case statistics, latest-run window queries, and one membership lookup instead of repeated queries per project. | Regression suite; latency benchmarking remains pending. |
| Latest-run index | Idempotent migration adds `(project_id, started_at DESC, id DESC)` to support project history lookup. | Existing database initialization in repeated regression tests. |
| API pagination | Runs accept limit 1–500 and nonnegative offset; default stays 100. Cases accept optional limit 1–500 and offset; omitted limit preserves existing array clients. | Invalid-limit rejection test. UI-wide pagination remains pending. |
| Content-based parser cache | Existing Robot parsing cache uses SHA-256 rather than mtime alone; cache cleanup uses path containment rather than string prefixes. | Same-size, same-timestamp content change test. |
| Queue visibility | Run summary includes queue position and busy/total run slots; detail header displays them while queued. | JavaScript syntax checks; browser interaction verification remains pending. |
| Unsaved editor protection | Closing/reloading a page with modified scripts or spreadsheets triggers the browser's unsaved-change prompt. Existing file-switch protection is retained. | JavaScript syntax checks; browser interaction verification remains pending. |

### Upgrade and operational notes

- Back up `brace.db` before upgrading. Schema migrations are additive and rerunnable.
- Existing JWTs lack `session_version` and are rejected after upgrade: sign in again.
- Existing database encryption keys must be preserved. Do not replace a key merely
  to satisfy startup validation; replacing it makes existing ciphertext unreadable.
- Password changes return `access_token`; custom clients must install that token.
- A password reset forces the recipient to change it at the next login.
- Logout revokes all sessions for the account, including report cookies on other devices.
- The limiter uses the ASGI peer address. Configure trusted proxy handling deliberately;
  clients sharing one proxy address share its quota. It is process-local and resets on restart.
- Already-open SSE connections are not forcibly disconnected when sessions are revoked;
  new requests and reconnects are checked. Per-connection revalidation remains pending.
- This does not isolate arbitrary Robot/Python execution from controller credentials.
- Paginated endpoints continue returning arrays; an empty page marks the end.
- Content hashing adds file reads on warm syncs in exchange for reliable invalidation.
- No performance speedup figures are claimed without a representative benchmark.

### Reproduce validation

Install the backend requirements and the development-only `httpx` dependency in
a virtual environment, then run:

```text
python -m unittest discover -s tests -v
```

The tests use a temporary database and directories; they do not modify local_data.
The execution integration tests start and terminate real Robot processes and need
permission to terminate their child processes. No Chrome is required for these tests.
Run Python AST parsing and `node --check` for all frontend files as well.
Container builds, browser flows, and Linux descendant cleanup need separate validation.

## Remaining implementation roadmap

| Area | Remaining work |
|---|---|
| Runner isolation | Separate restricted workers, restricted filesystem/network access, and credentials scoped to each execution. |
| Reproducible runs | Git commit recording, script/resource snapshots, environment and argument provenance. |
| Queue management | Per-project fairness, durable coordination, estimated waiting time, explicit cancelling state. |
| Environment profiles | Per-project environment selection, controlled variables, encrypted secrets, run snapshots. |
| Scheduling | Configurable skip/queue overlap policies and regression coverage. |
| Git sync preview | Review additions, changes, and missing cases before committing reconciliation. |
| Flaky tests | Explicit retry results, passed-after-retry distinction, and quarantine controls. |
| Pagination | Connect pagination to case/run screens; validate histories and audit filtering consistently. |
| Profiling | Representative database benchmarks, EXPLAIN plans, memory/load measurements. |
| Dashboard and failures | Project overview, actionable failure layout, clearer empty/loading/error states. |
| Editor and accessibility | Search/line navigation improvements, modal focus management, keyboard/contrast audit. |
| UI state | Persist filters/selections and reduce shared global state through namespaces. |
| Backend modularity | Continue extracting auth, reporting, and routers; execution utilities and security policy are extracted already. |
| Regression breadth | Cross-project isolation, scheduling, timeout descendants, and browser tests. |

## Commit notes

Commit messages should describe the user-visible problem, implementation,
compatibility changes, checks performed, and validation limitations. Keep this log
and the in-app manual updated alongside behavior changes. Do not describe roadmap
items as completed functionality.
