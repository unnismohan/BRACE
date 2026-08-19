// BRACE v2 — settings.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Settings ───────────────────────────────────────────
async function loadSettings() {
  const p=_curProj;
  document.getElementById('s-name').value=p.name;
  document.getElementById('s-desc').value=p.description||'';
  try {
    const gc=await api('GET', `/projects/${p.id}/git-config`);
    document.getElementById('s-giturl').value=gc.git_url||'';
    document.getElementById('s-gitbranch').value=gc.git_branch||'main';
    document.getElementById('s-gituser').value=gc.git_username||'';
  } catch {}
  loadMembers();
  loadDangerZone();
  loadNotifyConfig();
  loadSyncConfig();
  // The schedule dialog picks a suite from _groups, which is only populated by
  // the Suites tab — load it here so Settings works as a first stop.
  if (!_groups.length) loadGroups().finally(loadSchedules);
  else loadSchedules();
}

// ── User manual ────────────────────────────────────────
const R = { v:'<span class="help-role v">Viewer</span>',
            t:'<span class="help-role t">Tester</span>',
            pa:'<span class="help-role pa">Project Admin</span>',
            sa:'<span class="help-role sa">System Admin</span>' };

const HELP_SECTIONS = [
{ id:'start', title:'Getting Started', body:`
  <p class="help-lead">BRACE runs your Robot Framework test suites, stores the results,
  and shows you what passed, what failed, and why.</p>
  <h4>Signing in</h4>
  <p>Use the username and password your administrator issued. On a brand-new
  installation the first account is <code>admin</code>; if it still has its initial
  password you are asked to change it before continuing.</p>
  <h4>How work is organised</h4>
  <table><thead><tr><th>Term</th><th>What it is</th></tr></thead><tbody>
    <tr><td><b>Project</b></td><td>A workspace with its own scripts, test cases, runs and members. Nothing is shared between projects.</td></tr>
    <tr><td><b>Script</b></td><td>A <code>.robot</code> file on disk, usually pulled from Git.</td></tr>
    <tr><td><b>Test Case</b></td><td>A named entry pointing at one script — the thing you actually run and report on.</td></tr>
    <tr><td><b>Suite</b></td><td>An ordered group of test cases you run together (a regression pack, a smoke set).</td></tr>
    <tr><td><b>Run</b></td><td>One execution of a suite or a selection of test cases, with its results and logs.</td></tr>
    <tr><td><b>Tag</b></td><td>A free label on a test case (<code>smoke</code>, <code>wip</code>). Cuts across suites, so you can run every tagged case at once.</td></tr>
    <tr><td><b>Schedule</b></td><td>A timetable that runs a suite automatically, e.g. nightly.</td></tr>
  </tbody></table>
  <h4>A typical first session</h4>
  <ol>
    <li>Open your project from the left sidebar.</li>
    <li><b>Settings → Git Repository</b> — point at your repo and press <b>Sync Now</b>.</li>
    <li><b>Scripts</b> — confirm your <code>.robot</code> files arrived.</li>
    <li><b>Test Cases</b> — add them individually, or upload a CSV in one go.</li>
    <li><b>Suites</b> — group the ones you want to run together.</li>
    <li><b>Run</b> — execute, then read the outcome under <b>Runs</b> and <b>Reports</b>.</li>
  </ol>`},

{ id:'roles', title:'Roles & Permissions', body:`
  <p>Your role is set <b>per project</b>, so you may be a Tester on one and a Viewer on
  another. A System Admin has Project Admin rights everywhere.</p>
  <table><thead><tr><th>Action</th><th>${R.v}</th><th>${R.t}</th><th>${R.pa}</th><th>${R.sa}</th></tr></thead><tbody>
    <tr><td>View scripts, cases, runs, reports</td><td>✓</td><td>✓</td><td>✓</td><td>✓</td></tr>
    <tr><td>Edit scripts, Git sync, run tests</td><td>—</td><td>✓</td><td>✓</td><td>✓</td></tr>
    <tr><td>Create / edit test cases and suites</td><td>—</td><td>✓</td><td>✓</td><td>✓</td></tr>
    <tr><td>Cancel a run, re-run failed cases</td><td>—</td><td>✓</td><td>✓</td><td>✓</td></tr>
    <tr><td>Create / edit schedules</td><td>—</td><td>✓</td><td>✓</td><td>✓</td></tr>
    <tr><td>Delete a test case</td><td>—</td><td>—</td><td>✓</td><td>✓</td></tr>
    <tr><td>Manage members, Danger Zone, Team tab</td><td>—</td><td>—</td><td>✓</td><td>✓</td></tr>
    <tr><td>Create projects and user accounts</td><td>—</td><td>—</td><td>—</td><td>✓</td></tr>
  </tbody></table>
  <h4>What a Viewer sees</h4>
  <p>Controls you cannot use are hidden rather than shown and refused: a ${R.v} gets no Run,
  Add, Edit or Delete buttons, and scripts open read-only. A banner at the top of the project
  says so, and everything else — runs, reports, coverage, failure detail, history — is fully
  readable. Use this role for managers, auditors and anyone who should never accidentally
  start a run.</p>
  <div class="help-note"><p>Hiding is a courtesy, not the control. Every one of these actions
  is checked on the server as well, so a stale browser tab cannot get around it.</p></div>`},

{ id:'scripts', title:'Scripts & the Editor', body:`
  <p>The <b>Scripts</b> tab is a file explorer and code editor over your project's script folder.</p>
  <h4>Getting scripts in</h4>
  <ul>
    <li><b>Git Sync</b> (<span class="help-kbd">↻</span>) — clones or updates from the repo in Settings. This is the normal route.</li>
    <li><b>Upload</b> (<span class="help-kbd">⬆</span>) — individual files, or a <code>.zip</code> that is unpacked for you.</li>
    <li><b>New File</b> (<span class="help-kbd">＋</span>) — create one directly.</li>
  </ul>
  <div class="help-note warn"><p><b>Git sync overwrites.</b> Files that also exist in the
  repository are replaced with the repo's copy. Commit local edits before syncing.</p></div>
  <h4>Getting scripts out</h4>
  <p>The <span class="help-kbd">⬇</span> button in the Explorer toolbar downloads every
  script in the project — the whole folder tree — as a single <code>.zip</code>. Useful for
  a local backup, sharing a snapshot outside BRACE, or moving scripts to another project by
  hand. The zip name includes the project name and a timestamp, e.g.
  <code>brace-UPC-scripts-20260811-143000.zip</code>.</p>
  <h4>The file tree</h4>
  <p>Folders start collapsed — click to expand. The search box filters by filename.
  <b>Right-click</b> any file or folder for: New File, New Folder, Rename, Delete,
  Copy Relative Path, and Copy Full Container Path (the on-disk path, useful when a
  script needs an absolute location).</p>
  <h4>Editing</h4>
  <ul>
    <li>Syntax highlighting for <code>.robot</code>, <code>.py</code>, <code>.yaml</code> and friends.</li>
    <li><span class="help-kbd">Ctrl</span>+<span class="help-kbd">F</span> or <span class="help-kbd">Ctrl</span>+<span class="help-kbd">H</span> — find &amp; replace, with case and regex options.</li>
    <li>A dot (<b>●</b>) on the tab means unsaved changes. <b>💾 Save</b> writes to disk.</li>
    <li><code>.xlsx</code> and <code>.csv</code> open in a spreadsheet grid with sheet tabs, not as raw text.</li>
  </ul>
  <h4>Run a single file</h4>
  <p>With a <code>.robot</code> file open, <b>▶ Run</b> executes just that file and streams the
  console output underneath. It saves first if there are unsaved changes. This is for quick
  verification while editing — it does not create a tracked run or appear in Reports.</p>`},

{ id:'cases', title:'Test Cases', body:`
  <p>A test case gives a script a name, a description, and a stable ID
  (<code>TC-0001</code>) that results are reported against.</p>
  <h4>Adding one</h4>
  <p><b>＋ Add TC</b>, then fill in:</p>
  <ul>
    <li><b>Name</b> — required; what appears in reports.</li>
    <li><b>Suite Path</b> — the <code>.robot</code> file. Start typing to search; only files inside a
        <code>Testcases</code> folder are offered, since resource and keyword files aren't runnable on their own.</li>
    <li><b>Extra Args</b> — optional Robot Framework arguments, e.g. <code>--include smoke</code>.</li>
    <li><b>Tags</b> — optional labels, space or comma separated (<code>smoke regression upc</code>).</li>
  </ul>
  <h4>Tags</h4>
  <p>A case belongs to one suite structure, but can carry any number of tags. Tags cut across
  suites, so <code>smoke</code> can mean “part of the smoke set” regardless of which suite the
  case sits in — then <b>Trigger Run → By Tag</b> runs all of them together.</p>
  <p>Tags are lower-cased automatically and a leading <code>@</code> is stripped, so
  <code>@Smoke</code> and <code>smoke</code> are the same tag. They match whole words only —
  filtering on <code>smoke</code> will not pull in <code>smoketest</code>. Click any tag chip in
  the table to filter to it, or use the <b>Tag</b> dropdown, which lists only tags in use.</p>
  <h4>Bulk upload by CSV</h4>
  <p><b>⬆ Bulk CSV</b> creates many at once. Columns:</p>
  <pre><code>name,description,suite_path,extra_args,suite
TC_001 Product Family,Creates a product family,UPC/Testcases/TC_001_Product_Family.robot,,UPC Regression
TC_002 Offer Group,Base plan offer group,UPC/Testcases/TC_002_Offer_Group.robot,,UPC Regression|Smoke</code></pre>
  <p>Only <code>name</code> is required. The optional <code>suite</code> column adds each case to that
  suite, creating it if it doesn't exist; separate multiple suites with <code>|</code>. An optional
  <code>tags</code> column sets tags on each case (space or comma separated).</p>
  <div class="help-note warn"><p><b>Uploading always inserts — it never updates.</b> Uploading
  the same file twice gives you two copies of every case. To reload a list cleanly, delete the
  existing cases first (<b>Settings → Danger Zone → All test cases</b>).</p></div>
  <h4>Finding a test case</h4>
  <p>The search box matches on <b>name, TC ID, description, suite path and tags</b> — so
  <code>TC-0042</code>, <code>Bulk Upload</code> and <code>UPC/Testcases</code> all work.
  Combine it with the <b>Status</b> filter to answer questions like “which of my bulk-upload
  cases are failing?”. <b>Never run</b> lists cases that have no result yet — useful for
  spotting gaps after a CSV import. The counter shows how many of the total are matching.</p>
  <h4>History of one case (🕒)</h4>
  <p><b>🕒</b> on a row opens that case's execution timeline — every run it appeared in, newest
  first, with duration and a link to each run's log.</p>
  <p>Above it: pass rate, number of executions, average duration, and the current streak, plus a
  pass/fail strip you can click to jump to a run. The banner answers the question the aggregate
  Reports lists cannot:</p>
  <ul>
    <li><b>“Failing since &lt;date&gt;”</b> — it used to pass, so something changed. Look at what
        was deployed or edited around that date.</li>
    <li><b>“Never passed”</b> — it has failed every time it ever ran. Usually a broken test or an
        unfinished script rather than a regression in the application.</li>
    <li><b>Passing streak</b> — currently healthy.</li>
  </ul>
  <h4>Running from this tab</h4>
  <p><b>▶</b> on a row runs that one case. Tick several and use <b>▶ Run Selected</b> for an
  ad-hoc run without creating a suite; the button shows how many are selected.</p>
  <p>Selections are kept while you change the filter, so you can search one term, tick a few,
  search another and tick more, then run them all together. The header checkbox selects
  everything <i>currently visible</i> — filter first, then tick it, to select a whole subset
  at once. Selections reset when the list reloads.</p>`},

{ id:'suites', title:'Suites', body:`
  <p>A suite is an ordered group of test cases — a regression pack, a smoke set, a
  release candidate list. A test case can belong to several suites.</p>
  <ul>
    <li><b>＋ New Suite</b> — create one, then <b>＋ TCs</b> to add cases.</li>
    <li><b>Adding many at once:</b> the ＋ TCs picker has a filter box and
        <b>Select all</b> / <b>Clear</b>. Select all applies to whatever the filter is
        currently showing — so filtering on <code>Bulk_Upload</code> and pressing Select all
        adds just those. Ticks survive changing the filter, so you can build a selection
        across several searches before adding. Cases already in the suite are never listed,
        and re-adding one is harmless.</li>
    <li>Click a suite header to expand or collapse it; <b>Expand All</b> / <b>Collapse All</b> act on every suite.</li>
    <li>Cases run in the order listed. <b>✕</b> removes a case from the suite without deleting it.</li>
    <li><b>▶ Run</b> executes the whole suite as one tracked run.</li>
  </ul>
  <div class="help-note"><p>Deleting a suite never deletes the test cases inside it.</p></div>`},

{ id:'runs', title:'Running Tests', body:`
  <h4>Starting a run</h4>
  <ul>
    <li><b>Runs → ▶ Trigger Run</b> — pick a suite, select individual cases, or choose <b>By Tag</b>.</li>
    <li><b>▶ Run</b> on a suite, or <b>▶</b> on a single test case.</li>
  </ul>
  <p>Optionally name the run so it's easy to find later, and pass extra Robot Framework
  arguments that apply to the whole run.</p>
  <p><b>By Tag</b> runs every case carrying that tag, wherever it lives — the selection a
  suite cannot express, e.g. all <code>smoke</code> cases across five different suites.
  See <a href="#" onclick="helpGo('cases');return false">Test Cases</a> for how to tag.</p>
  <h4>Parallel execution</h4>
  <p>Cases inside a run execute <b>in parallel</b>. The <b>Parallel</b> box sets how many at
  once; it is pre-filled with the server's default and cannot exceed the server limit, because
  each case running is another browser on the pod.</p>
  <div class="help-note warn"><p><b>Set Parallel to 1 if the cases depend on each other's
  order</b> — e.g. one case creates the subscriber a later case then modifies. Cases that
  share test data or a login session are not safe to run at the same time.</p></div>
  <h4>Watching progress</h4>
  <p>Open <b>🔍 Details</b> to watch live: the progress bar, per-case status and console output
  update as each case finishes — no need to reopen the window. The server pushes those updates
  rather than the page asking for them, so a case flipping to failed appears at once, and a
  1000-case run costs no more to watch than a 10-case one. Where a proxy blocks event streams
  the window falls back to refreshing every few seconds instead.</p>
  <h4>Finding an old run</h4>
  <p>The Runs tab filters by name or run ID, by date range, by status, and by <b>who started
  it</b> (the By dropdown, which also lists <code>scheduler</code> for scheduled runs). The
  counter shows how many of the total are matching.</p>
  <h4>Reading results</h4>
  <table><thead><tr><th>Status</th><th>Meaning</th></tr></thead><tbody>
    <tr><td><b>queued</b></td><td>Waiting for a free execution slot. It starts on its own — nothing to do.</td></tr>
    <tr><td><b>running</b></td><td>Still executing.</td></tr>
    <tr><td><b>passed</b></td><td>Every test case passed.</td></tr>
    <tr><td><b>failed</b></td><td>At least one case failed. Open Details to see which.</td></tr>
    <tr><td><b>cancelled</b></td><td>Stopped by a user, or interrupted by a server restart; remaining cases did not execute.</td></tr>
  </tbody></table>
  <h4>Queued runs</h4>
  <p>The server caps how much runs at once so it is not overloaded. Past that cap, a run sits
  at <b>queued</b> and starts automatically when a slot frees — the message when you start it
  tells you how many are ahead. Nothing is lost by queueing.</p>
  <div class="help-note"><p>If the server restarts, runs that were queued or running are marked
  <b>cancelled</b> rather than being left stuck forever. Start them again.</p></div>
  <h4>Stopping a run</h4>
  <p><b>✕</b> on a running or queued run cancels it. Cases already finished keep their results;
  the one executing is terminated and the rest never start.</p>
  <h4>Re-running only what failed</h4>
  <p>After a failed run, <b>↻ Re-run Failed</b> — in run details, or the <b>↻</b> icon on the
  Runs list — starts a new run containing <i>only</i> the failed cases. On a 44-case regression
  where 3 failed, that is 3 cases instead of 44.</p>
  <p>The new run is named after the original with a retry count, e.g.
  <code>UPC Regression run (retry 3)</code>, and its details link back to the run it came from,
  so the chain stays traceable. Re-running a retry does not stack the suffix.</p>
  <h4>Finding a case in a large run</h4>
  <p>The case list in <b>🔍 Details</b> is filtered and paged, so a run with a thousand cases
  opens as fast as one with ten.</p>
  <ul>
    <li>The chips at the top — <b>All</b>, <b>✗ failed</b>, <b>✓ passed</b> — filter the list.
    Their counts are for the whole run, not the page on screen.</li>
    <li>A run with failures <b>opens on the failures</b>. Click <b>All</b> to see everything.</li>
    <li>The search box matches test case code, name and failure reason.</li>
    <li>50 cases load at a time; <b>Load 50 more</b> pulls the next page.</li>
  </ul>
  <h4>Why a case failed</h4>
  <p>Each failed row shows the failing keyword on the row itself. Click <b>▸</b> to expand it for
  the full reason — the library the keyword came from, Robot's own message, and the screenshot
  taken at that moment if the test captured one. Click the screenshot for the full-size image.</p>
  <p>Expanded rows stay open while the run is still executing, so you can read a failure without
  the auto-refresh closing it.</p>
  <p>That is usually enough to triage without opening anything else. <b>Report</b> is the Robot
  Framework summary; <b>Log</b> is the full step-by-step trace — open it when the inline summary
  isn't enough. <b>Combined Report</b> merges every case in the run into a single view.</p>`},

{ id:'schedules', title:'Scheduled Runs', body:`
  <p>A schedule runs a <b>suite</b> automatically on a repeating timetable — a nightly
  regression, a smoke set every four hours. Set them up under
  <b>Settings → ⏰ Schedules</b>. Everyone can see them; <span class="help-role t">Tester</span>
  and above can create and change them.</p>
  <h4>Creating one</h4>
  <ul>
    <li><b>Suite</b> — what runs. Schedules always run a whole suite, never a loose selection.</li>
    <li><b>Cron expression</b> — when. Use a preset (<b>Daily 02:00</b>, <b>Weekdays 02:00</b>,
        <b>Every 4 hours</b>, <b>Weekly Sun 03:00</b>) or type your own.</li>
    <li><b>Enabled</b> — untick to keep the schedule but stop it firing.</li>
  </ul>
  <h4>Reading the cron box</h4>
  <p>Five fields: <code>minute hour day month day-of-week</code>.</p>
  <pre><code>0 2 * * *      02:00 every day
0 2 * * 1-5    02:00 Monday to Friday
30 6 * * 1     06:30 every Monday
0 */4 * * *    every 4 hours, on the hour</code></pre>
  <p>As you type, BRACE validates the expression and shows it back in plain English with the
  <b>next three fire times</b>. If it turns red the expression is invalid and cannot be saved —
  check the preview before saving rather than discovering at 2am that nothing ran.</p>
  <div class="help-note warn"><p><b>Times are in the server's timezone</b>, shown next to the
  preview — not your browser's. If the preview says a time you did not expect, that is the
  timezone, and the next-run times are the truth.</p></div>
  <h4>Watching them</h4>
  <p>Each schedule lists its next run times and the outcome of its last one, so a schedule that
  has quietly been failing is visible without digging through the Runs tab. Scheduled runs appear
  in <b>Runs</b> like any other, named <code>Scheduled: &lt;suite&gt;</code> and triggered by
  <code>scheduler</code> — the <b>By</b> filter on that tab isolates them.</p>
  <div class="help-note warn"><p><b>Nobody is notified when a scheduled run fails.</b> A 2am
  failure is recorded and visible, but no email or message is sent — someone has to look.
  Check the Schedules card or filter Runs by <code>scheduler</code> as part of your morning.</p></div>
  <h4>Deleting</h4>
  <p>Deleting a schedule keeps the suite — it just stops running automatically. Deleting the
  <i>suite</i> deletes its schedules too.</p>`},

{ id:'notify', title:'Email Notifications', body:`
  <p>BRACE can email you when runs fail, so a 2am scheduled failure does not sit unnoticed
  until someone happens to open the dashboard.</p>
  <div class="help-note"><p>Two levels: a <span class="help-role sa">System Admin</span>
  configures <b>one mail account for the whole server</b> under Administration → Email
  Notifications; each <span class="help-role pa">Project Admin</span> then chooses what
  their own project sends, under <b>Settings → Notifications</b>.</p></div>
  <h4>Setting up the mail account (admin)</h4>
  <p>Pick your provider from the list to fill in host, port and security automatically —
  Gmail/Google Workspace, Microsoft 365, Outlook, Yahoo, Zoho, SendGrid, Amazon SES, or an
  internal relay. Then enter the account and a From address, and press <b>Send Test</b>.</p>
  <div class="help-note warn"><p><b>Gmail and Google Workspace need an App Password</b>, not
  your normal Google password — that will always be rejected. Turn on 2-Step Verification,
  then create one at <i>myaccount.google.com → Security → App passwords</i> and paste the
  16-character value. Outlook and Yahoo work the same way.</p></div>
  <p>The password is stored encrypted and never shown again; leave it blank when saving to
  keep the existing one. <b>Send Test</b> makes a real connection and reports the exact
  reason if it fails — the quickest way to tell a wrong password from a blocked port.</p>
  <h4>Choosing what a project sends</h4>
  <ul>
    <li><b>A scheduled run fails</b> — the main reason to switch this on.</li>
    <li><b>Any run fails</b> — includes runs people start by hand.</li>
    <li><b>A run passes</b> — off by default; usually noise.</li>
    <li><b>Also email whoever started the run</b> — uses the address on their user account.</li>
  </ul>
  <p><b>Recipients</b> is a plain list separated by commas, semicolons or new lines. An
  address that is not valid is rejected when you save, rather than silently never mailed.</p>
  <h4>Only when the outcome changes</h4>
  <p>On by default, and worth keeping. A suite that has been failing for three weeks would
  otherwise email you 21 times; people build a filter, and then miss the genuine regression.
  With it on you are told when a suite <b>starts</b> failing, not every night it stays
  broken. Each suite is tracked separately, so one noisy suite cannot mask another.</p>
  <h4>Weekly digest</h4>
  <p>A summary of the <a href="#" onclick="helpGo('reports');return false">Coverage</a>
  figures plus the last seven days of runs, on a cron schedule of your choosing (default
  Monday 08:00). Coverage is a report worth receiving rather than remembering to open.</p>
  <h4>What the email contains</h4>
  <p>One email per run — never one per failed case. It lists the failed cases with the
  failing keyword and Robot's own message, so it often answers the question without opening
  anything, plus a link straight to the run.</p>
  <div class="help-note"><p>If that link is missing, the server does not know its own
  external address — an admin sets <code>BRACE_PUBLIC_URL</code> in the deployment.</p></div>
  <div class="help-note warn"><p>Emails carry test names and failure messages. If that is
  sensitive, keep recipients internal — and note that a public provider such as Gmail means
  those details leave your network.</p></div>`},

{ id:'reports', title:'Reports & Analytics', body:`
  <p>The <b>Reports</b> tab summarises quality over a time window. It defaults to
  <b>today</b>; switch to 7 / 30 days, all time, or a custom range.</p>
  <ul>
    <li><b>Pass Rate</b> — green ≥90%, amber ≥70%, red below.</li>
    <li><b>Pass / Fail Trend</b> — one stacked bar per run, oldest to newest. Hover for detail, click to open the run.</li>
    <li><b>Top Failing Test Cases</b> — ranked by failure count: your consistent problems.</li>
    <li><b>Flaky Test Cases</b> — cases that have both passed and failed. Usually the highest-value list: a test that always fails is a known bug, but a flaky one means you can't trust the result either way.</li>
    <li><b>Run History</b> — every run in the range; click a row for details.</li>
  </ul>`},

{ id:'aidebug', title:'AI Debug Assistant', body:`
  <p>When a run fails, <b>🤖 Debug</b> collects the evidence and produces a diagnosis.
  It appears next to failed cases in run details, and in the editor after a failed <b>▶ Run</b>.</p>
  <p>It gathers the parsed failure and failing keyword chain from <code>output.xml</code>, the
  console output, the test suite source, and the resource files that suite imports.</p>
  <h4>Two ways to use it</h4>
  <ul>
    <li><b>✨ Analyze</b> — if your administrator has connected a model, the diagnosis streams
        directly into the window.</li>
    <li><b>📋 Copy Prompt</b> — always available. Copies (or downloads) a complete,
        self-contained prompt you can paste into any AI chat on your own machine. This is
        the option on an air-gapped server.</li>
  </ul>
  <p>A completed analysis is remembered. Reopening the same failure shows the saved result
  instantly without calling the model again; <b>🔄 Re-run Analysis</b> forces a fresh one —
  worth doing after you've changed the script.</p>
  <div class="help-note warn"><p>With in-app analysis enabled, the prompt — including your
  test source and logs — is sent to the configured provider. If that provider is outside your
  network, use <b>Copy Prompt</b> instead for anything sensitive.</p></div>
  <div class="help-note"><p>Treat the output as a knowledgeable suggestion, not a verdict.
  It sees the failure and the source, but not your application's behaviour. Verify before acting.</p></div>`},

{ id:'team', title:'Team Activity', body:`
  <p><span class="help-role pa">Project Admin</span> only. Execution activity per person
  for this project, over a chosen date range.</p>
  <p>Shows runs triggered, tests executed, unique cases touched, pass rate, active days,
  tests per day and execution time — plus a stacked daily chart and CSV export.</p>
  <div class="help-note warn"><p><b>“Execution Time” is not time worked.</b> It measures how long
  the test runner was busy, including waits when nobody was at a keyboard. Script writing,
  debugging and reviewing are not recorded at all. Use this for throughput and coverage
  trends, not to judge individual effort.</p></div>`},

{ id:'settings', title:'Project Settings', body:`
  <h4>Git Repository</h4>
  <p>Set the repository URL, branch, username and access token, then <b>Save Git Config</b>.
  <b>Sync Now</b> pulls immediately. The token is stored encrypted and never shown again —
  leave the field blank when saving to keep the existing one.</p>
  <h4>Test Cases from Git</h4>
  <p>Two modes, per project:</p>
  <ul>
    <li><b>Manual</b> (default) — you create and edit test cases in BRACE. Nothing changes.</li>
    <li><b>Git</b> — the repository decides which test cases exist. Every test in a
      <code>Testcases</code> folder becomes a BRACE test case; its name, <code>[Tags]</code> and
      <code>[Documentation]</code> come from the file.</li>
  </ul>
  <p><b>🔍 Preview</b> reports exactly what would change without writing anything — worth
  running first on a project that already has hand-made cases. <b>↻ Sync Test Cases</b> applies
  it, and in git mode a Git Sync from the Scripts tab reconciles the cases too, so pulling
  scripts and updating the case list are one action.</p>
  <p>Three guarantees make this safe to run repeatedly:</p>
  <ul>
    <li><b>Nothing is deleted.</b> A test removed from the repo is flagged
      <span class="srcbadge missing">not in repo</span> and kept — its run history is real
      history. Delete it yourself when you are ready.</li>
    <li><b>Hand-made cases are never touched.</b> Only cases that came from a file are managed.</li>
    <li><b>Each case runs only its own test.</b> A file with five tests produces five cases,
      each pinned with <code>--test</code>, not five cases that each run all five.</li>
  </ul>
  <h4>Pinning a test case code</h4>
  <p>Identity is normally the file path plus the test name, so renaming a test reads as one
  removed and one added. To keep a code across renames, put it in the repository:</p>
  <pre><code>*** Test Cases ***
Verify Successful Login
    [Tags]    smoke    braceid:VDRC_LOGIN_01</code></pre>
  <p>That tag sets the BRACE test case code and is not shown as a tag. If the same code is
  already used by another project the sync assigns a generated one instead and says so —
  codes are unique across the whole server.</p>
  <h4>Schedules</h4>
  <p>Run a suite automatically on a timetable — see
  <a href="#" onclick="helpGo('schedules');return false">Scheduled Runs</a>.
  Git mode has its own optional cron for reconciling test cases.</p>
  <h4>Notifications</h4>
  <p>Email alerts for failed and scheduled runs, plus a weekly digest — see
  <a href="#" onclick="helpGo('notify');return false">Email Notifications</a>.</p>
  <h4>Members</h4>
  <p><span class="help-role pa">Project Admin</span> only. Add people, change roles inline,
  or remove them. Filter by name or role when the list grows.</p>
  <p><b>＋ Add Member</b> lets you add several people at once: tick as many users as you
  like — with a filter box and <b>Select all</b> / <b>Clear</b> — pick one role, and add
  them together. The role applies to everyone in that batch; to mix roles, add one batch
  per role, or change individuals afterwards using the dropdown in the members table.
  People who are already members are not listed.</p>
  <div class="help-note"><p>If any username in the batch is unrecognised, nothing is added —
  the whole batch is rejected so you never end up with a half-applied change.</p></div>
  <h4>Danger Zone</h4>
  <p><span class="help-role pa">Project Admin</span> only. Bulk deletion, permanent, with the
  affected count shown before you act:</p>
  <ul>
    <li><b>Run history</b> — all runs plus their report and log files. Frees disk space.</li>
    <li><b>Old runs only</b> — the same, keeping the 10 most recent.</li>
    <li><b>All test cases</b> — every case; run history is kept and stays readable.</li>
    <li><b>All suites</b> — every suite and its schedules; the cases themselves are kept.</li>
  </ul>
  <div class="help-note danger"><p>None of this can be undone. It never touches your
  <code>.robot</code> files on disk — scripts are only ever changed from the Scripts tab or by Git sync.</p></div>`},

{ id:'admin', title:'Administration', body:`
  <p><span class="help-role sa">System Admin</span> only, from the sidebar.</p>
  <h4>Users</h4>
  <p>Create accounts individually or by CSV (<code>username,password,system_role,full_name,email</code>).
  New users must change their password at first login. Creating an account does not grant
  access to anything — add them to a project under that project's Settings → Members.</p>
  <h4>AI Debug Assistant</h4>
  <p>Connect any OpenAI-compatible endpoint (OpenRouter, LiteLLM, a self-hosted vLLM):</p>
  <ul>
    <li><b>Enable in-app AI analysis</b> — leave off and users still get the copy-prompt option.</li>
    <li><b>API Base URL</b> and <b>Model</b> — as issued by your provider.</li>
    <li><b>API Key</b> — stored encrypted, never returned to the browser. Blank on re-save keeps the current key.</li>
    <li><b>Verify TLS certificate</b> — untick only for an internal endpoint with a self-signed certificate.</li>
    <li><b>🔌 Test Connection</b> — makes a real call and reports the exact error if it fails.</li>
  </ul>
  <h4>Email Notifications</h4>
  <p>One SMTP account for the whole server, with presets for Gmail, Microsoft 365, SendGrid,
  SES and others, and a <b>Send Test</b> button that reports the real error. Detail in
  <a href="#" onclick="helpGo('notify');return false">Email Notifications</a>.</p>
  <h4>Housekeeping</h4>
  <p>The database grows by runs × test cases, and every case leaves a log and a report on the
  results volume. A nightly job keeps that bounded:</p>
  <ul>
    <li><b>Retention</b> — deletes runs that finished more than <code>BRACE_RETENTION_DAYS</code>
      ago, with their reports. <b>0 means keep forever</b>, and that is the default, so an
      upgrade never starts deleting history on its own.</li>
    <li><b>Always keep</b> — <code>BRACE_RETENTION_KEEP_MIN</code> runs per project survive
      however old they are, so a project that runs monthly keeps its history.</li>
    <li><b>Orphan sweep</b> — result folders with no run behind them, and stale editor
      quick-run output. Runs even with retention off.</li>
    <li><b>Compaction</b> — reclaims freed database space, and only when something was actually
      deleted. It is skipped automatically while any test is executing.</li>
  </ul>
  <p>The card shows current database and disk usage. <b>🔍 Preview (dry run)</b> reports what
  would be removed without touching anything; <b>🧹 Run Now</b> does it. A run that is still
  executing is never purged, whatever its age.</p>
  <div class="help-note warn"><p>Retention is set in the Deployment, not in this screen —
  a value editable in two places drifts. The card reports what the pod is configured with.</p></div>
  <h4>Audit Log</h4>
  <p>Who changed what: runs started and cancelled, test cases and suites created and deleted,
  scripts saved, schedules and settings changed, members added and roles altered. Filter by
  user, action, or date; an action family such as <code>run</code> matches
  <code>run.trigger</code> and <code>run.cancel</code> together.</p>
  <p>Reads are not recorded — logging page views would bury the interesting lines. Passwords,
  tokens and API keys are recorded as <i>changed</i>, never as their value.</p>
  <h4>Monitoring the server</h4>
  <p>For whoever operates the deployment rather than uses it:</p>
  <ul>
    <li><code>/metrics</code> — Prometheus format, no login required (it is scraped by the
        monitoring stack). Active and queued runs, free execution slots, run and test outcomes,
        and <b>results disk usage</b> — the one worth alerting on, since a full volume stops
        every run.</li>
    <li><b>/api/health-detail</b> — <span class="help-role sa">System Admin</span> only. Live
        execution limits, how many tests are running right now, and the image tag actually
        deployed, which settles “is my rebuild live?”.</li>
    <li><b>/api/admin/scheduler-jobs</b> — what the scheduler currently holds and when each job
        next fires. Use it when a schedule looks right in the UI but isn't firing.</li>
  </ul>
  <h4>Execution capacity</h4>
  <p>Three settings, applied at deployment, decide throughput:</p>
  <table><thead><tr><th>Setting</th><th>Controls</th></tr></thead><tbody>
    <tr><td><code>BRACE_MAX_CONCURRENT_RUNS</code></td><td>How many runs are admitted at once. Extras queue.</td></tr>
    <tr><td><code>BRACE_MAX_CONCURRENT_TESTS</code></td><td><b>Total test cases — i.e. browsers — executing across all runs.</b> This is what must match the pod's CPU and memory.</td></tr>
    <tr><td><code>BRACE_RUN_PARALLEL</code></td><td>Default cases one run may use, capped by the above. Testers can lower it per run.</td></tr>
  </tbody></table>
  <div class="help-note warn"><p>Raise <code>BRACE_MAX_CONCURRENT_TESTS</code> only together with
  the pod's CPU/memory limits. Each concurrent case is another browser; too many and the pod is
  OOM-killed, which loses every run in flight, not just the excess.</p></div>`},

{ id:'faq', title:'Troubleshooting', body:`
  <h4>My script isn't in the Suite Path dropdown</h4>
  <p>Only <code>.robot</code> files inside a folder named <code>Testcases</code> are listed, because
  resource and keyword files can't run standalone. Move the file, or type the path by hand.</p>
  <h4>Git sync reports 0 files</h4>
  <p>Check the branch name and that the token is still valid. The sync log shows git's own
  error; credentials are masked. An expired token usually appears as “Authentication failed”.</p>
  <h4>A test passes locally but fails in BRACE</h4>
  <p>Tests here run headless in a Linux container. Common causes: a hard-coded Windows path,
  a missing wait on an element that was fast locally, or test data already consumed by an
  earlier run. The <b>Log</b> link has screenshots at the point of failure.</p>
  <h4>Every test in a suite failed at the first step</h4>
  <p>Usually environment rather than test logic — the application under test is down, or
  credentials have expired. Check one Log; if they all fail identically at login, it is almost
  certainly the environment rather than your tests.</p>
  <h4>Times shown are hours out from my clock</h4>
  <p>The server records times using its own clock, and the pages show them exactly as recorded.
  If the server's timezone is not yours, everything reads consistently offset — commonly 5½ hours
  behind for an India-based team on a server left in UTC. It is a server setting
  (<code>TZ</code>), not something you can change per user; ask an administrator. Times written
  before the fix keep the old zone, so a run list can briefly show a mix.</p>
  <h4>Tests pass one at a time but fail when run together</h4>
  <p>Almost always shared state: two cases using the same login, the same subscriber, or the
  same test-data row at the same time. Set <b>Parallel</b> to 1 for that run, or give each case
  its own data. A case that only passes when nothing else is running is worth fixing — it will
  fail again the moment the suite grows.</p>
  <h4>My run says “queued” and nothing is happening</h4>
  <p>The server is at its execution limit; your run starts automatically when a slot frees.
  Check <b>Runs</b> for what is currently running. If a run has been stuck for far longer than
  a test should take, cancel it — that releases its slot for everything behind it.</p>
  <h4>My schedule didn't run</h4>
  <p>In order: is it <b>Enabled</b>? Does the suite still have test cases in it — an empty suite
  is skipped. And check the next-run times shown under the schedule: cron is read in the
  <b>server's</b> timezone, so <code>0 2 * * *</code> may not be 2am where you are.</p>
  <h4>No screenshot on a failed case</h4>
  <p>BRACE shows the screenshot the test itself captured. If your suite doesn't take one on
  failure, there is nothing to show — the failing keyword and message are still there. Browser
  tests using SeleniumLibrary normally capture one automatically.</p>
  <h4>“Permission denied”</h4>
  <p>Your role on this project doesn't allow that action — see <a href="#" onclick="helpGo('roles');return false">Roles &amp; Permissions</a>.
  Ask a Project Admin.</p>
  <h4>I deleted something by mistake</h4>
  <p>Deletions are permanent; there is no undo. Test cases and suites can be recreated
  (re-upload your CSV). Deleted run history and report files are gone for good.</p>`},
];

function openHelp() {
  showView('help');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('help-navitem').classList.add('active');
  if (!document.getElementById('help-body').dataset.rendered) renderHelp();
}

function renderHelp() {
  document.getElementById('help-toc-list').innerHTML =
    HELP_SECTIONS.map(s => `<a href="#" data-sec="${s.id}" onclick="helpGo('${s.id}');return false">${esc(s.title)}</a>`).join('');
  const body = document.getElementById('help-body');
  body.innerHTML = HELP_SECTIONS.map(s =>
    `<section class="help-sec" id="help-${s.id}"><h3>${esc(s.title)}</h3>${s.body}</section>`).join('');
  body.dataset.rendered = '1';

  // Highlight the section currently in view
  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (!e.isIntersecting) return;
      const id = e.target.id.replace('help-', '');
      document.querySelectorAll('#help-toc-list a').forEach(a =>
        a.classList.toggle('active', a.dataset.sec === id));
    });
  }, { rootMargin: '-10% 0px -70% 0px' });
  body.querySelectorAll('.help-sec').forEach(s => obs.observe(s));
}

function helpGo(id) {
  if (!document.getElementById('help-body').dataset.rendered) renderHelp();
  showView('help');
  const el = document.getElementById('help-' + id);
  if (el) el.scrollIntoView({ behavior:'smooth', block:'start' });
  document.querySelectorAll('#help-toc-list a').forEach(a =>
    a.classList.toggle('active', a.dataset.sec === id));
}

function helpSearch(q) {
  const body = document.getElementById('help-body');
  if (!body.dataset.rendered) renderHelp();
  const term = q.trim().toLowerCase();

  // Re-render to drop any previous highlighting
  body.innerHTML = HELP_SECTIONS.map(s =>
    `<section class="help-sec" id="help-${s.id}"><h3>${esc(s.title)}</h3>${s.body}</section>`).join('');

  if (!term) {
    document.querySelectorAll('#help-toc-list a').forEach(a => a.style.display = '');
    return;
  }
  let hits = 0;
  HELP_SECTIONS.forEach(s => {
    const sec = document.getElementById('help-' + s.id);
    const match = sec.textContent.toLowerCase().includes(term);
    sec.style.display = match ? '' : 'none';
    const link = document.querySelector(`#help-toc-list a[data-sec="${s.id}"]`);
    if (link) link.style.display = match ? '' : 'none';
    if (match) { hits++; helpHighlight(sec, term); }
  });
  if (!hits) body.innerHTML = `<div class="help-nores">Nothing in the manual matches “${esc(q)}”.</div>`;
}

// Wrap matches in <mark>, walking text nodes only so markup is never corrupted
function helpHighlight(root, term) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: n => (n.parentElement.tagName === 'PRE' || !n.nodeValue.toLowerCase().includes(term))
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const targets = [];
  while (walker.nextNode()) targets.push(walker.currentNode);
  targets.forEach(node => {
    const frag = document.createDocumentFragment();
    let rest = node.nodeValue, i;
    while ((i = rest.toLowerCase().indexOf(term)) !== -1) {
      frag.append(rest.slice(0, i));
      const m = document.createElement('mark');
      m.textContent = rest.slice(i, i + term.length);
      frag.append(m);
      rest = rest.slice(i + term.length);
    }
    frag.append(rest);
    node.parentNode.replaceChild(frag, node);
  });
}
