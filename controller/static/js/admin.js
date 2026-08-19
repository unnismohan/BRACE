// BRACE v2 — admin.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Team activity (project_admin) ──────────────────────
let _teamPreset = '30d', _teamData = null;

function teamPreset(p) {
  _teamPreset = p;
  document.querySelectorAll('.rpt-preset[data-tp]').forEach(b => b.classList.toggle('active', b.dataset.tp === p));
  document.getElementById('team-from').value = '';
  document.getElementById('team-to').value = '';
  loadTeamActivity();
}

function teamCustom() {
  if (!document.getElementById('team-from').value && !document.getElementById('team-to').value) return;
  _teamPreset = 'custom';
  document.querySelectorAll('.rpt-preset[data-tp]').forEach(b => b.classList.remove('active'));
  loadTeamActivity();
}

function teamRange() {
  const today = _rptTodayStr();
  const back = _daysAgo;      // local dates; see _localDate in runs.js
  if (_teamPreset === '7d')  return { date_from: back(6),  date_to: today };
  if (_teamPreset === '30d') return { date_from: back(29), date_to: today };
  if (_teamPreset === '90d') return { date_from: back(89), date_to: today };
  if (_teamPreset === 'all') return { date_from: '0001-01-01', date_to: '9999-12-31' };
  return {
    date_from: document.getElementById('team-from').value || '0001-01-01',
    date_to:   document.getElementById('team-to').value   || today,
  };
}

async function loadTeamActivity() {
  const { date_from, date_to } = teamRange();
  try {
    const d = await api('GET', `/projects/${_curProj.id}/tester-activity?date_from=${date_from}&date_to=${date_to}`);
    _teamData = d;
    document.getElementById('team-range').textContent = `${d.date_from} → ${d.date_to}`;
    renderTeamKPIs(d.totals);
    renderTeamTable(d.testers);
    renderTeamChart(d.daily, d.testers);
  } catch(e) { toast(e.message,'e'); }
}

function renderTeamKPIs(t) {
  document.getElementById('team-kpis').innerHTML = `
    <div class="kpi"><span class="kpi-lbl">Active Testers</span>
      <span class="kpi-val">${t.testers}</span><span class="kpi-sub">triggered ≥1 run</span></div>
    <div class="kpi"><span class="kpi-lbl">Runs</span>
      <span class="kpi-val">${t.runs}</span><span class="kpi-sub">in this period</span></div>
    <div class="kpi"><span class="kpi-lbl">Tests Executed</span>
      <span class="kpi-val">${t.tests}</span><span class="kpi-sub">across all runs</span></div>
    <div class="kpi"><span class="kpi-lbl">Execution Time</span>
      <span class="kpi-val" style="font-size:22px">${fmtDur(t.exec_seconds)}</span>
      <span class="kpi-sub">machine time, not hours worked</span></div>`;
}

function renderTeamTable(rows) {
  const tbl = document.getElementById('team-table');
  if (!rows.length) {
    tbl.innerHTML = '<tbody><tr><td class="rpt-empty">No activity in this period.</td></tr></tbody>';
    return;
  }
  const maxTests = Math.max(...rows.map(r => r.tests), 1);
  tbl.innerHTML = `
    <thead><tr>
      <th>Tester</th><th title="Runs triggered">Runs</th>
      <th title="Total test cases executed">Tests</th>
      <th title="Distinct test cases touched">Unique TCs</th>
      <th title="Share of executed tests that passed">Pass Rate</th>
      <th title="Days with at least one run">Active Days</th>
      <th title="Tests executed per active day">Tests/Day</th>
      <th title="Runner busy time — not time worked">Exec Time</th>
      <th>Last Active</th>
    </tr></thead>
    <tbody>${rows.map(r => {
      const pc = r.pass_rate >= 90 ? 'var(--c-ok-text)' : r.pass_rate >= 70 ? 'var(--c-warn-text)' : 'var(--c-err-text)';
      const bar = Math.round(r.tests / maxTests * 100);
      return `<tr>
        <td><b>${esc(r.user)}</b>${r.cancelled ? `<span class="kpi-sub"> · ${r.cancelled} cancelled</span>` : ''}</td>
        <td>${r.runs}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-variant-numeric:tabular-nums;min-width:34px">${r.tests}</span>
            <span class="srow-bar" style="width:56px"><i style="width:${bar}%;background:var(--c-blue)"></i></span>
          </div>
        </td>
        <td>${r.unique_tcs}</td>
        <td style="color:${pc};font-weight:700">${r.pass_rate}%</td>
        <td>${r.active_days}</td>
        <td>${r.tests_per_day}</td>
        <td>${fmtDur(r.exec_seconds)}</td>
        <td style="font-size:12px;color:var(--c-muted)">${esc(r.last_seen || '—')}</td>
      </tr>`;
    }).join('')}</tbody>`;
}

const TEAM_COLORS = ['var(--c-blue)','var(--c-accent)','var(--c-ok)','#a855f7','var(--c-warn)','#06b6d4','#ec4899','#84cc16'];

function renderTeamChart(daily, testers) {
  const el = document.getElementById('team-chart');
  if (!daily.length) { el.innerHTML = '<div class="rpt-empty">No activity to chart.</div>'; return; }
  const users = testers.map(t => t.user);
  const color = u => TEAM_COLORS[users.indexOf(u) % TEAM_COLORS.length];
  const maxDay = Math.max(...daily.map(d => Object.values(d.by_user).reduce((a,b)=>a+b,0)), 1);

  let bars = '<div class="trend-bars">', axis = '<div class="trend-axis">';
  daily.forEach(d => {
    const tot = Object.values(d.by_user).reduce((a,b)=>a+b,0);
    const tip = Object.entries(d.by_user).sort((a,b)=>b[1]-a[1])
      .map(([u,n]) => `${esc(u)}: ${n}`).join('<br>');
    bars += `<div class="tbar"><div class="tip"><b>${d.date}</b><br>${tip}<br>total ${tot}</div>`;
    Object.entries(d.by_user).forEach(([u,n]) => {
      bars += `<div style="height:${n/maxDay*100}%;background:${color(u)}" title="${esc(u)}: ${n}"></div>`;
    });
    bars += '</div>';
    axis += `<span>${d.date.slice(5)}</span>`;
  });
  bars += '</div>'; axis += '</div>';

  const legend = users.map(u =>
    `<span><i style="background:${color(u)}"></i>${esc(u)}</span>`).join('');
  el.innerHTML = bars + axis + `<div class="trend-legend">${legend}</div>`;
}

function teamExportCSV() {
  if (!_teamData || !_teamData.testers.length) { toast('Nothing to export','e'); return; }
  const cols = ['user','runs','tests','unique_tcs','passed','failed','cancelled',
                'pass_rate','active_days','runs_per_day','tests_per_day',
                'exec_seconds','first_seen','last_seen'];
  const esc2 = v => /[",\n]/.test(String(v ?? '')) ? `"${String(v).replace(/"/g,'""')}"` : String(v ?? '');
  const csv = [cols.join(',')]
    .concat(_teamData.testers.map(t => cols.map(c => esc2(t[c])).join(',')))
    .join('\n');
  saveBlob(new Blob([csv], { type:'text/csv' }),
           `${_curProj.name}-tester-activity-${_teamData.date_from}_${_teamData.date_to}.csv`);
  toast('CSV downloaded','s');
}

// ── Schedules ──────────────────────────────────────────
let _schedEditId = null, _schedTz = 'UTC';

// Plain-English rendering of the common cron shapes, so nobody has to decode
// "0 2 * * 1-5" in their head.
function cronDescribe(expr) {
  const p = (expr||'').trim().split(/\s+/);
  if (p.length !== 5) return null;
  const [mi, hr, dom, mon, dow] = p;
  const DOW = {'0':'Sunday','1':'Monday','2':'Tuesday','3':'Wednesday','4':'Thursday','5':'Friday','6':'Saturday','7':'Sunday'};
  const t = (h, m) => `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
  const num = s => /^\d+$/.test(s);
  if (num(mi) && num(hr) && dom==='*' && mon==='*' && dow==='*') return `Every day at ${t(hr,mi)}`;
  if (num(mi) && num(hr) && dom==='*' && mon==='*' && dow==='1-5') return `Weekdays at ${t(hr,mi)}`;
  if (num(mi) && num(hr) && dom==='*' && mon==='*' && DOW[dow])   return `Every ${DOW[dow]} at ${t(hr,mi)}`;
  if (num(mi) && hr==='*'  && dom==='*' && mon==='*' && dow==='*') return `Every hour at :${String(mi).padStart(2,'0')}`;
  if (num(mi) && /^\*\/\d+$/.test(hr) && dom==='*' && dow==='*')
    return `Every ${hr.slice(2)} hours at :${String(mi).padStart(2,'0')}`;
  if (num(mi) && num(hr) && num(dom) && mon==='*' && dow==='*')
    return `Monthly on day ${dom} at ${t(hr,mi)}`;
  return null;   // valid but not a shape we phrase — the preview times still show
}

async function loadSchedules() {
  const box = document.getElementById('sched-list');
  try {
    const d = await api('GET', `/projects/${_curProj.id}/schedules`);
    _schedTz = d.timezone || 'UTC';
    document.getElementById('sched-tz').textContent = _schedTz;
    const rows = d.schedules || [];
    if (!rows.length) {
      box.innerHTML = '<div class="picker-empty">No schedules. Suites run only when triggered manually.</div>';
      return;
    }
    // A table, not stacked rows. The old markup put the last-run badge inside
    // a `.sched-info span`, which the stylesheet forces to display:block — so
    // the status stretched into a full-width pink bar. Columns also let the
    // eye compare "next run" down the list, which is the actual question.
    box.innerHTML = `<div class="tbl-wrap"><table class="stackable sched-tbl">
      <thead><tr>
        <th>Suite</th><th>Schedule</th><th>Next run</th><th>Last run</th>
        <th style="width:150px"></th>
      </tr></thead>
      <tbody>${rows.map(s => {
        const desc = cronDescribe(s.cron_expr);
        const next = (s.next_runs||[])[0];
        const lr = s.last_run;
        return `<tr class="${s.enabled?'':'sched-off'}">
          <td data-label="Suite"><b>${esc(s.group_name)}</b>${
            s.enabled ? '' : ' <span class="sbadge pending">disabled</span>'}</td>
          <td data-label="Schedule">
            ${desc ? esc(desc) : 'Custom'}
            <code class="sched-cron">${esc(s.cron_expr)}</code>
          </td>
          <td data-label="Next run" class="u-nowrap">${
            s.enabled && next
              ? esc(next.replace('T',' ').slice(0,16))
              : '<span class="u-muted">—</span>'}</td>
          <td data-label="Last run">${
            lr ? `<span class="sbadge ${esc(lr.status)}">${esc(lr.status)}</span>
                  <span class="sched-lr">✓${lr.passed} ✗${lr.failed} ·
                  ${esc((lr.started_at||'').replace('T',' ').slice(0,16))}</span>`
               : '<span class="u-muted">never</span>'}</td>
          <td data-label=""><div class="bgrp">
            <button class="btn btn-o btn-sm" onclick="toggleSchedule(${s.id},${s.enabled?0:1})"
                    title="${s.enabled?'Stop running this on a timetable':'Resume this schedule'}"
              >${s.enabled?'Disable':'Enable'}</button>
            <button class="bico" title="Edit" onclick='openSchedModal(${jsArg(JSON.stringify(s))})'>${ico('edit')}</button>
            <button class="bico" title="Delete" onclick="deleteSchedule(${s.id})">${ico('trash')}</button>
          </div></td>
        </tr>`;
      }).join('')}</tbody></table></div>`;
  } catch(e) {
    box.innerHTML = `<div class="picker-empty">Could not load schedules — ${esc(e.message)}</div>`;
  }
}

function openSchedModal(raw) {
  const s = typeof raw === 'string' ? JSON.parse(raw) : null;
  _schedEditId = s ? s.id : null;
  document.getElementById('sched-title').textContent = s ? 'Edit Schedule' : 'New Schedule';
  const sel = document.getElementById('sched-group');
  sel.innerHTML = _groups.length
    ? _groups.map(g => `<option value="${g.id}" ${s && s.group_id===g.id?'selected':''}>${esc(g.name)} (${g.tc_count} TCs)</option>`).join('')
    : '<option value="">— no suites yet —</option>';
  document.getElementById('sched-cron').value = s ? s.cron_expr : '0 2 * * *';
  document.getElementById('sched-enabled').checked = s ? !!s.enabled : true;
  schedPreview();
  showModal('modal-sched');
}

function schedPreset(expr) {
  document.getElementById('sched-cron').value = expr;
  schedPreview();
}

let _schedPreviewT = null;
function schedPreview() {
  clearTimeout(_schedPreviewT);
  _schedPreviewT = setTimeout(async () => {
    const expr = document.getElementById('sched-cron').value.trim();
    const box = document.getElementById('sched-preview');
    if (!expr) { box.className='sched-preview'; box.textContent='Enter a cron expression.'; return; }
    try {
      const r = await api('POST', `/projects/${_curProj.id}/schedules/preview`, { cron_expr: expr });
      const desc = cronDescribe(expr);
      box.className = 'sched-preview ok';
      box.innerHTML = (desc ? `<b>${esc(desc)}</b> (${esc(r.timezone)})<br>` : `Valid (${esc(r.timezone)})<br>`)
        + 'Next: ' + (r.next_runs||[]).slice(0,3)
            .map(t => esc(t.replace('T',' ').slice(0,16))).join(' · ');
    } catch(e) {
      box.className = 'sched-preview err';
      box.textContent = e.message;
    }
  }, 250);
}

async function saveSchedule() {
  const gid  = +document.getElementById('sched-group').value;
  const cron = document.getElementById('sched-cron').value.trim();
  const on   = document.getElementById('sched-enabled').checked;
  if (!gid)  { toast('Select a suite','e'); return; }
  if (!cron) { toast('Enter a cron expression','e'); return; }
  try {
    if (_schedEditId) {
      await api('PUT', `/schedules/${_schedEditId}`, { cron_expr: cron, enabled: on });
    } else {
      const r = await api('POST', `/projects/${_curProj.id}/schedules`, { group_id: gid, cron_expr: cron });
      if (!on) await api('PUT', `/schedules/${r.id}`, { enabled: false });
    }
    toast('Schedule saved','s');
    closeModal('modal-sched');
    loadSchedules();
  } catch(e) { toast(e.message,'e'); }
}

async function toggleSchedule(sid, enable) {
  try { await api('PUT', `/schedules/${sid}`, { enabled: !!enable }); loadSchedules(); }
  catch(e) { toast(e.message,'e'); }
}

async function deleteSchedule(sid) {
  if (!await askConfirm('Delete Schedule',
      'Delete this schedule?\n\nThe suite itself is kept — it just stops running automatically.')) return;
  try { await api('DELETE', `/schedules/${sid}`); toast('Schedule deleted','s'); loadSchedules(); }
  catch(e) { toast(e.message,'e'); }
}

// ── Danger Zone (bulk cleanup) ─────────────────────────
function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B','KB','MB','GB']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

async function loadDangerZone() {
  const el = document.getElementById('dz-rows');
  try {
    const s = await api('GET', `/projects/${_curProj.id}/data-summary`);
    const row = (label, hint, count, badge, fn, disabled) => `
      <div class="dz-row">
        <div class="dz-info"><b>${label}</b><span>${hint}</span></div>
        <div class="dz-act">
          <span class="dz-count ${count ? '' : 'zero'}">${badge}</span>
          <button class="btn btn-sm btn-d" onclick="${fn}" ${disabled ? 'disabled style="opacity:.45;cursor:not-allowed"' : ''}>Delete</button>
        </div>
      </div>`;
    el.innerHTML =
      row('Run history', 'Removes run records, per-TC results, and report/log files on disk.',
          s.runs, `${s.runs} runs · ${fmtBytes(s.result_bytes)}`, 'dzPurgeRuns(0)', !s.runs) +
      row('Old runs only', 'Same as above but keeps the 10 most recent runs.',
          s.runs, `keep last 10`, 'dzPurgeRuns(10)', s.runs <= 10) +
      row('All test cases', 'Deletes every test case. Run history is kept and stays readable.',
          s.test_cases, `${s.test_cases} TCs`, 'dzPurgeTCs()', !s.test_cases) +
      row('All suites', 'Deletes every suite and its schedules. Test cases themselves are kept.',
          s.suites, `${s.suites} suites`, 'dzPurgeSuites()', !s.suites);
  } catch(e) {
    el.innerHTML = `<div style="font-size:12.5px;color:var(--c-muted)">Unavailable — ${esc(e.message)}</div>`;
  }
}

async function dzPurgeRuns(keep) {
  const msg = keep
    ? `Delete all run history except the ${keep} most recent?\n\nReport and log files are removed from disk. This cannot be undone.`
    : 'Delete ALL run history for this project?\n\nEvery run record, result, report and log file is removed. This cannot be undone.';
  if (!await askConfirm('Delete Run History', msg)) return;
  try {
    const r = await api('DELETE', `/projects/${_curProj.id}/runs?keep_last=${keep}`);
    let m = `Deleted ${r.deleted_runs} run(s), freed ${fmtBytes(r.freed_bytes)}`;
    if (r.skipped_active) m += ` · ${r.skipped_active} still running, skipped`;
    toast(m, 's');
    loadDangerZone(); loadRuns();
  } catch(e) { toast(e.message,'e'); }
}

async function dzPurgeTCs() {
  if (!await askConfirm('Delete All Test Cases',
      'Delete every test case in this project?\n\nRun history is preserved. Scripts on disk are untouched. This cannot be undone.')) return;
  try {
    const r = await api('DELETE', `/projects/${_curProj.id}/test-cases`);
    toast(`Deleted ${r.deleted_test_cases} test case(s)`, 's');
    loadDangerZone();
  } catch(e) { toast(e.message,'e'); }
}

async function dzPurgeSuites() {
  if (!await askConfirm('Delete All Suites',
      'Delete every suite in this project?\n\nThe test cases inside them are kept. Schedules on these suites are removed. This cannot be undone.')) return;
  try {
    const r = await api('DELETE', `/projects/${_curProj.id}/groups`);
    toast(`Deleted ${r.deleted_suites} suite(s)`, 's');
    loadDangerZone();
  } catch(e) { toast(e.message,'e'); }
}

async function saveProjSettings() {
  try {
    await api('PUT', `/projects/${_curProj.id}`, { name:document.getElementById('s-name').value, description:document.getElementById('s-desc').value||null });
    _curProj.name=document.getElementById('s-name').value;
    document.getElementById('proj-title').textContent=_curProj.name;
    document.getElementById('bc-proj').textContent=_curProj.name;
    toast('Saved','s'); renderProjNav();
  } catch(e) { toast(e.message,'e'); }
}

async function saveGitConfig() {
  const token=document.getElementById('s-gittoken').value;
  try {
    await api('PUT', `/projects/${_curProj.id}/git-config`, {
      git_url:document.getElementById('s-giturl').value.trim()||null,
      git_branch:document.getElementById('s-gitbranch').value.trim()||'main',
      git_username:document.getElementById('s-gituser').value.trim()||null,
      git_token:token||null,
    });
    document.getElementById('s-gittoken').value=''; toast('Git config saved','s');
  } catch(e) { toast(e.message,'e'); }
}

// ── Git-native test cases ──────────────────────────────
// Which projects have been synced in this browser session, so the first-run
// 'this will take a minute' notice is only shown when it is true.
let _syncSeen = {};

async function loadSyncConfig() {
  const el = document.getElementById('sy-status');
  if (!el) return;
  try {
    const c = await api('GET', `/projects/${_curProj.id}/sync-config`);
    document.getElementById('sy-mode').value = c.sync_mode || 'manual';
    document.getElementById('sy-cron').value = c.sync_cron || '';
    document.getElementById('sy-cron-desc').textContent =
      c.sync_cron ? cronDescribe(c.sync_cron) : '';
    const k = c.counts || {};
    el.innerHTML = `
      <span style="color:var(--c-muted)">
        ${k.synced || 0} of ${k.total || 0} test case(s) come from the repository${
          k.missing ? ` · <b style="color:var(--c-warn-text)">${k.missing} no longer in the repo</b>` : ''}.
        ${c.last_sync_at ? `Last synced ${esc(c.last_sync_at.replace('T',' '))}.` : 'Never synced.'}
      </span>
      ${!c.has_git ? `<div class="fhint" style="color:var(--c-warn-text)">No git repository configured
         for this project — sync reads the .robot files already in the suites folder.</div>` : ''}`;
  } catch(e) { el.innerHTML = `<span style="color:var(--c-muted)">${esc(e.message)}</span>`; }
}

async function saveSyncConfig() {
  try {
    await api('PUT', `/projects/${_curProj.id}/sync-config`, {
      sync_mode: document.getElementById('sy-mode').value,
      sync_cron: document.getElementById('sy-cron').value.trim() || null,
    });
    toast('Saved','s');
    loadSyncConfig();
  } catch(e) { toast(e.message,'e'); }
}

async function runTcSync(dry) {
  const el = document.getElementById('sy-status');
  // The first sync parses every .robot file — a minute on a large repo. After
  // that only changed files are re-read, so say which one this is likely to be
  // rather than leaving a spinner with no explanation.
  const first = !(_syncSeen && _syncSeen[_curProj.id]);
  el.innerHTML = `<span class="u-muted">${dry ? 'Reading' : 'Syncing'} the repository…`
    + (first ? ' The first pass reads every <code>.robot</code> file and can take a '
             + 'minute on a large repository; later ones only re-read what changed.' : '')
    + '</span>';
  try {
    const r = await api('POST', `/projects/${_curProj.id}/sync?dry_run=${dry ? 'true' : 'false'}`);
    const s = r.summary;
    _syncSeen = _syncSeen || {}; _syncSeen[_curProj.id] = true;
    // A repository can hold far more tests than a hand-curated project does.
    // Preview is the only place that gap is visible, so make it unmissable
    // rather than a number in a sentence.
    const existing = (r.counts_before != null) ? r.counts_before : _tcs.length;
    const bigAdd = dry && s.added >= 100;
    el.innerHTML = `<span style="color:${dry ? 'var(--c-muted)' : 'var(--c-ok-text)'}">
        ${dry ? '<b>Preview</b> — nothing was written.' : '<b>Synced.</b>'}
        Parsed <b>${s.parsed}</b> test(s) — added <b>${s.added}</b>,
        updated <b>${s.updated}</b>, unchanged <b>${s.unchanged}</b>,
        no longer in the repo <b>${s.missing}</b>.
      </span>
      ${bigAdd ? `<div class="help-note warn" style="margin-top:8px">
        <p>This would create <b>${s.added}</b> test cases${existing ? ` alongside the
        ${existing} already in this project` : ''}. That is what the repository
        contains — every test in a <code>Testcases</code> folder becomes a case.
        Nothing you created by hand is touched, and nothing is deleted, but this
        is a large change: confirm the number looks right before syncing.</p></div>` : ''}
      ${syncDetail('Added', r.added, t => `${t.tc_code} — ${t.name}`)}
      ${syncDetail('Updated', r.updated, t => `${t.tc_code} — ${t.name} (${t.fields.join(', ')})`)}
      ${syncDetail('Missing from the repo', r.missing, t => `${t.tc_code} — ${t.name}`)}
      ${r.duplicates && r.duplicates.length ? `<div class="fhint" style="color:var(--c-err-text)">
        Duplicate <code>braceid:</code> tags in the repository — these tests were skipped:
        ${esc(r.duplicates.map(d => d.braceid + ' (' + d.tests.join(', ') + ')').join('; '))}</div>` : ''}
      ${r.errors && r.errors.length ? `<div class="fhint" style="color:var(--c-warn-text)">
        ${r.errors.length} file(s) could not be parsed: ${esc(r.errors.slice(0,3).join('; '))}</div>` : ''}`;
    if (!dry) { loadSyncConfig(); if (_curTab === 'cases') loadTCs(); }
  } catch(e) { el.innerHTML = `<span style="color:var(--c-err-text)">${esc(e.message)}</span>`; }
}

// Long lists get truncated — a first sync of a big repo reports hundreds of
// additions and the point of the panel is the summary, not the enumeration.
function syncDetail(label, rows, fmt) {
  if (!rows || !rows.length) return '';
  const shown = rows.slice(0, 8).map(fmt).map(esc).join('<br>');
  return `<div class="fhint" style="margin-top:6px"><b>${label}:</b><br>${shown}${
    rows.length > 8 ? `<br>…and ${rows.length - 8} more` : ''}</div>`;
}

let _members = [];

async function loadMembers() {
  try {
    _members = await api('GET', `/projects/${_curProj.id}/members`);
    renderMembers();
  } catch(e) { toast(e.message,'e'); }
}

function renderMembers() {
  const q = (document.getElementById('mem-search')?.value || '').trim().toLowerCase();
  const rows = q
    ? _members.filter(m => (m.username||'').toLowerCase().includes(q) ||
                           (m.full_name||'').toLowerCase().includes(q) ||
                           (m.project_role||'').toLowerCase().includes(q))
    : _members;

  const cnt = document.getElementById('mem-count');
  if (cnt) cnt.textContent = q ? `${rows.length} of ${_members.length}`
                               : `${_members.length}`;

  const tbody = document.getElementById('members-tbody');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;padding:22px;color:var(--c-muted)">${
      _members.length ? 'No members match this filter.' : 'No members yet.'}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(m => `
    <tr>
      <td><strong>${esc(m.username)}</strong>${m.full_name?` <span style="color:var(--c-muted);font-size:12px">${esc(m.full_name)}</span>`:''}</td>
      <td><select class="rsel" onchange="updateMemberRole(${m.id},this.value)">
        <option value="viewer"        ${m.project_role==='viewer'        ?'selected':''}>Viewer</option>
        <option value="tester"        ${m.project_role==='tester'        ?'selected':''}>Tester</option>
        <option value="project_admin" ${m.project_role==='project_admin' ?'selected':''}>Project Admin</option>
      </select></td>
      <td><button class="btn btn-sm btn-d" onclick="removeMember(${m.id})">Remove</button></td>
    </tr>`).join('');
}

let _memAvail = [];          // users not already in the project
let _memPicked = new Set();  // kept outside the DOM so filtering can't drop ticks

async function openAddMemberModal() {
  _memPicked = new Set();
  document.getElementById('mem-search').value = '';
  document.getElementById('mem-picker').innerHTML =
    '<div class="picker-empty">Loading users…</div>';
  showModal('modal-addmember');
  try {
    const [allUsers, members] = await Promise.all([
      api('GET', '/users'),
      api('GET', `/projects/${_curProj.id}/members`),
    ]);
    const memberNames = new Set(members.map(m => m.username));
    _memAvail = allUsers.filter(u => !memberNames.has(u.username));
    renderMemPicker();
  } catch(e) {
    toast(e.message,'e');
    document.getElementById('mem-picker').innerHTML =
      `<div class="picker-empty">Could not load users — ${esc(e.message)}</div>`;
  }
}

function memVisible() {
  const q = (document.getElementById('mem-search')?.value || '').trim().toLowerCase();
  return !q ? _memAvail
            : _memAvail.filter(u => (u.username||'').toLowerCase().includes(q) ||
                                    (u.full_name||'').toLowerCase().includes(q) ||
                                    (u.email||'').toLowerCase().includes(q));
}

function renderMemPicker() {
  const box = document.getElementById('mem-picker');
  const vis = memVisible();
  if (!_memAvail.length) {
    box.innerHTML = '<div class="picker-empty">Every user is already a member of this project.</div>';
  } else if (!vis.length) {
    box.innerHTML = '<div class="picker-empty">No users match that filter.</div>';
  } else {
    box.innerHTML = vis.map(u => `
      <div class="tc-sitem">
        <input type="checkbox" class="mem-chk" id="mu-${esc(u.username)}" value="${esc(u.username)}"
               ${_memPicked.has(u.username)?'checked':''}
               onchange="memToggle(${jsArg(u.username)},this.checked)">
        <label for="mu-${esc(u.username)}"><b>${esc(u.username)}</b>${
          u.full_name?` <span style="color:var(--c-muted);font-size:12px">${esc(u.full_name)}</span>`:''}</label>
      </div>`).join('');
  }
  memCount();
}

function memToggle(name, on) { on ? _memPicked.add(name) : _memPicked.delete(name); memCount(); }

function memSelectAll(on) {
  memVisible().forEach(u => on ? _memPicked.add(u.username) : _memPicked.delete(u.username));
  renderMemPicker();
}

function memCount() {
  const el = document.getElementById('mem-count-sel');
  if (!el) return;
  const total = _memAvail.length, vis = memVisible().length;
  el.textContent = `${_memPicked.size} selected`
    + (vis !== total ? ` · showing ${vis} of ${total}` : ` of ${total}`);
}

async function addMember() {
  const usernames = [..._memPicked];
  if (!usernames.length) { toast('Select at least one user','e'); return; }
  const role = document.getElementById('mem-role').value;
  try {
    const r = await api('POST', `/projects/${_curProj.id}/members/bulk`, { usernames, project_role: role });
    toast(`Added ${r.added} member(s) as ${r.role.replace('_',' ')}`,'s');
    closeModal('modal-addmember');
    loadMembers();
  } catch(e) { toast(e.message,'e'); }
}

async function updateMemberRole(uid, role) {
  try { await api('PUT', `/projects/${_curProj.id}/members/${uid}`, { username:'', project_role:role }); toast('Role updated','s'); }
  catch(e) { toast(e.message,'e'); loadMembers(); }
}

async function removeMember(uid) {
  if (!await askConfirm('Remove Member', 'Remove this member from the project?',
      { okText: 'Remove' })) return;
  try { await api('DELETE', `/projects/${_curProj.id}/members/${uid}`); toast('Removed','s'); loadMembers(); }
  catch(e) { toast(e.message,'e'); }
}

// ── Admin ──────────────────────────────────────────────
// ── Administration ─────────────────────────────────────
// Five stacked cards was a long scroll, and openAdmin() used to fire all five
// loads at once — including the results-volume walk behind Housekeeping. Tabs
// mean one request for the panel you are actually looking at.
// Wrapped rather than referenced directly, for the same reason as
// SETTINGS_PANES in settings.js: these all happen to live in this file today,
// but a bare reference silently breaks the moment one moves.
const ADMIN_TABS = {
  users: () => loadUsers(),
  ai:    () => loadAIConfig(),
  email: () => loadSmtpConfig(),
  house: () => loadHousekeeping(),
  audit: () => loadAudit(true),
};
let _adminTab = null;

function switchAdminTab(name, force) {
  if (!ADMIN_TABS[name]) name = 'users';
  _adminTab = name;
  localStorage.setItem('brace_admin_tab', name);
  Object.keys(ADMIN_TABS).forEach(k => {
    document.getElementById('atab-'  + k).classList.toggle('active', k === name);
    document.getElementById('apane-' + k).classList.toggle('active', k === name);
  });
  // Reload every time it is opened: users get added, audit rows accumulate, and
  // a stale panel is worse than a second request.
  try { ADMIN_TABS[name](); } catch (e) { /* a panel failing must not break the tabs */ }
}

function openAdmin() {
  showView('admin');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('admin-navitem').classList.add('active');
  switchAdminTab(localStorage.getItem('brace_admin_tab') || 'users');
}

// ── Housekeeping ───────────────────────────────────────
// Retention is env-driven, so this card reports rather than edits: an operator
// who can change BRACE_RETENTION_DAYS is changing the Deployment, and a value
// editable in two places would drift.
async function loadHousekeeping(refresh) {
  const box = document.getElementById('hk-body');
  if (!box) return;
  if (refresh) box.innerHTML = '<div class="u-hint">Measuring the results volume…</div>';
  try {
    const h = await api('GET', '/admin/maintenance' + (refresh ? '?refresh=true' : ''));
    const c = h.config, d = h.disk, last = h.last;
    box.innerHTML = `
      <div class="hk-grid">
        <div class="hk-stat"><span class="hk-lbl">Database</span><b>${fmtBytes(d.db_bytes)}</b></div>
        <div class="hk-stat"><span class="hk-lbl">Results on disk</span><b>${fmtBytes(d.results_bytes)}</b>
          <span class="hk-age">${hkAge(d)}</span></div>
        <div class="hk-stat"><span class="hk-lbl">Retention</span><b>${
          c.enabled ? c.retention_days + ' days' : 'off — keep forever'}</b></div>
        <div class="hk-stat"><span class="hk-lbl">Always keep</span><b>${c.retention_keep_min} runs/project</b></div>
        <div class="hk-stat"><span class="hk-lbl">Nightly job</span><b>${esc(c.maint_cron)}</b></div>
        <div class="hk-stat"><span class="hk-lbl">Audit kept</span><b>${
          c.audit_retention_days ? c.audit_retention_days + ' days' : 'forever'}</b></div>
      </div>
      ${c.enabled ? '' : `<div class="fhint" style="margin-top:10px">
        Retention is off. Set <code>BRACE_RETENTION_DAYS</code> in the Deployment to switch it on —
        the orphan sweep and database compaction run nightly either way.</div>`}
      ${last && last.started_at ? `<div class="fhint" style="margin-top:8px">
        Last run ${esc(String(last.started_at).replace('T',' '))} —
        ${last.runs ? last.runs.runs : 0} run(s) deleted,
        ${fmtBytes((last.runs?last.runs.freed_bytes:0)+(last.orphans?last.orphans.freed_bytes:0))} reclaimed
        in ${last.seconds}s.</div>` : ''}`;
  } catch(e) { box.innerHTML = `<div class="fhint">${esc(e.message)}</div>`; }
}

// Says how old the disk figure is, and offers a live re-measure. Measuring a
// large volume takes seconds, so the card samples rather than blocking — but a
// stale number presented as live would be worse than a slow one.
function hkAge(d) {
  const a = d.results_age_sec;
  if (a == null) return '';
  const when = a < 5 ? 'just now'
             : a < 90 ? `${Math.round(a)}s ago`
             : `${Math.round(a / 60)}m ago`;
  return `measured ${when}`
       + (d.results_measure_sec ? ` · took ${d.results_measure_sec}s` : '')
       + ` · <a href="#" onclick="loadHousekeeping(true);return false">re-measure</a>`;
}

async function runHousekeeping(dry) {
  const el = document.getElementById('hk-status');
  if (!dry) {
    if (!await askConfirm('Run housekeeping',
        'This permanently deletes runs past the retention window and their reports. '
        + 'Preview first if you have not already.', { okText: 'Delete now', danger: true })) return;
  }
  el.innerHTML = dry ? 'Checking what would be removed…' : 'Running…';
  try {
    const r = await api('POST', `/admin/maintenance/run?dry_run=${dry ? 'true' : 'false'}`);
    const runs = r.runs || {}, orph = r.orphans || {}, audit = r.audit || {}, db = r.db || {};
    const freed = (runs.freed_bytes || 0) + (orph.freed_bytes || 0);
    el.innerHTML = `<span style="color:${dry ? 'var(--c-muted)' : 'var(--c-ok-text)'}">
      ${dry ? '<b>Dry run</b> — nothing was deleted.' : '<b>Done.</b>'}
      Runs ${dry ? 'that would be' : ''} deleted: <b>${runs.runs || 0}</b>
      ${runs.enabled === false ? '(retention off)' : ''} ·
      Orphan folders: <b>${orph.dirs || 0}</b> ·
      Audit rows: <b>${audit.rows || 0}</b> ·
      Space: <b>${fmtBytes(freed)}</b>
      ${db.vacuumed ? ` · database compacted, ${fmtBytes(db.freed_bytes)} reclaimed` : ''}
      ${db.skipped_vacuum ? ` · compaction skipped (${esc(String(db.skipped_vacuum))})` : ''}
      </span>
      ${(runs.errors||[]).concat(orph.errors||[]).length
        ? `<div class="fhint" style="color:var(--c-warn-text)">Some folders could not be removed: ${
            esc(((runs.errors||[]).concat(orph.errors||[])).slice(0,3).join('; '))}</div>` : ''}`;
    if (!dry) loadHousekeeping();
  } catch(e) { el.innerHTML = `<span style="color:var(--c-err-text)">${esc(e.message)}</span>`; }
}

// ── Audit log ──────────────────────────────────────────
let _audit = { offset: 0, limit: 50, total: 0, rows: [] };

function clearAuditFilters() {
  ['audit-user','audit-action','audit-from','audit-to'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  loadAudit(true);
}

async function loadAudit(reset) {
  const tb = document.getElementById('audit-tbody');
  if (!tb) return;
  const qs = new URLSearchParams({
    username:  document.getElementById('audit-user').value || '',
    action:    document.getElementById('audit-action').value || '',
    date_from: document.getElementById('audit-from').value || '',
    date_to:   document.getElementById('audit-to').value || '',
    offset:    reset ? 0 : _audit.rows.length,
    limit:     _audit.limit,
  });
  try {
    const r = await api('GET', `/admin/audit?${qs}`);
    _audit.total = r.total;
    _audit.rows  = reset ? r.entries : _audit.rows.concat(r.entries);
    // Rebuild the dropdowns only on a reset, or typing a filter would reset itself
    if (reset) {
      fillAuditSelect('audit-user',   r.usernames, 'All users');
      fillAuditSelect('audit-action', auditFamilies(r.actions), 'All actions');
    }
    renderAudit();
  } catch(e) {
    tb.innerHTML = `<tr><td colspan="5" style="padding:20px;color:var(--c-muted)">${esc(e.message)}</td></tr>`;
  }
}

// 'run.trigger' and 'run.cancel' collapse to a single 'run' option — the server
// treats a bare family name as a prefix match.
function auditFamilies(actions) {
  const fam = new Set();
  (actions || []).forEach(a => { fam.add(a.split('.')[0]); fam.add(a); });
  return [...fam].sort();
}

function fillAuditSelect(id, values, allLabel) {
  const el = document.getElementById(id);
  const cur = el.value;
  el.innerHTML = `<option value="">${allLabel}</option>`
    + (values || []).map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if (cur) el.value = cur;
}

function renderAudit() {
  const tb = document.getElementById('audit-tbody');
  document.getElementById('audit-count').textContent =
    _audit.total ? `${_audit.rows.length} of ${_audit.total}` : '';
  if (!_audit.rows.length) {
    tb.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:26px;color:var(--c-muted)">
      No audit entries match these filters.</td></tr>`;
    document.getElementById('audit-more').innerHTML = '';
    return;
  }
  tb.innerHTML = _audit.rows.map(e => `<tr>
      <td style="white-space:nowrap">${esc((e.ts||'').replace('T',' '))}</td>
      <td>${esc(e.username||'—')}</td>
      <td><code style="font-size:11px">${esc(e.action||'')}</code></td>
      <td>${esc(e.target||'—')}</td>
      <td style="font-size:11px;color:var(--c-muted);word-break:break-word">${esc(auditDetail(e.detail))}</td>
    </tr>`).join('');
  const left = _audit.total - _audit.rows.length;
  document.getElementById('audit-more').innerHTML = left > 0
    ? `<div class="rd-more"><span>Showing ${_audit.rows.length} of ${_audit.total}</span>
         <button class="btn btn-sm btn-o" onclick="loadAudit(false)">Load ${Math.min(left, _audit.limit)} more</button></div>`
    : '';
}

function auditDetail(raw) {
  if (!raw) return '';
  try {
    const d = JSON.parse(raw);
    return Object.entries(d)
      .filter(([, v]) => v !== null && v !== undefined && v !== '' &&
                         !(Array.isArray(v) && !v.length))
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`)
      .join('  ');
  } catch (e) { return String(raw); }
}

function aiStatus(html, kind) {
  const c = kind === 'ok' ? 'var(--c-ok-text)' : kind === 'err' ? 'var(--c-err-text)' : 'var(--c-muted)';
  document.getElementById('ai-status').innerHTML = `<span style="color:${c}">${html}</span>`;
}

async function loadAIConfig() {
  try {
    const c = await api('GET', '/admin/ai-config');
    document.getElementById('ai-enabled').checked = !!c.enabled;
    document.getElementById('ai-base').value  = c.api_base || '';
    document.getElementById('ai-model').value = c.model || '';
    document.getElementById('ai-verify').checked = c.verify_ssl !== false;
    document.getElementById('ai-key').value   = '';
    document.getElementById('ai-key-hint').textContent =
      c.has_key ? `(saved, ending ${c.key_hint})` : '(not set)';

    if (!c.has_key)      aiStatus('No API key saved. In-app analysis is unavailable — users get the copy-prompt option.', 'idle');
    else if (!c.enabled) aiStatus(ico('warn') + ' Key is saved but <b>Enable in-app AI analysis</b> is off, so nothing uses it. Tick the box and save.', 'err');
    else                 aiStatus('✓ Configured and enabled. Use <b>Test Connection</b> to verify the endpoint responds.'
                                  + (c.verify_ssl === false ? ' <span style="color:var(--c-warn-text)">TLS verification is off.</span>' : ''), 'ok');
  } catch(e) { /* non-admins simply don't see this section populated */ }
}

async function saveAIConfig() {
  const body = {
    enabled:  document.getElementById('ai-enabled').checked,
    api_base: document.getElementById('ai-base').value.trim() || 'https://openrouter.ai/api/v1',
    model:    document.getElementById('ai-model').value.trim() || 'anthropic/claude-sonnet-4',
    verify_ssl: document.getElementById('ai-verify').checked,
  };
  const key = document.getElementById('ai-key').value.trim();
  if (key) body.api_key = key;
  try {
    const r = await api('PUT', '/admin/ai-config', body);
    // Report what the server actually persisted, not what we hoped it did
    toast(`Saved — key ${r.has_key ? 'stored' : 'NOT stored'}, AI ${r.enabled ? 'enabled' : 'disabled'}`,
          r.has_key ? 's' : 'e');
    loadAIConfig();
  } catch(e) { toast(e.message,'e'); }
}

async function testAIConfig() {
  aiStatus('Testing endpoint…', 'idle');
  try {
    const r = await api('POST', '/admin/ai-config/test');
    aiStatus(`✓ Connection OK — model <code>${esc(r.model)}</code> replied: “${esc(r.reply)}”`, 'ok');
    toast('AI endpoint reachable','s');
  } catch(e) {
    aiStatus('✗ ' + esc(e.message), 'err');
  }
}

async function loadUsers() {
  try {
    const users=await api('GET','/users');
    const tbody=document.getElementById('users-tbody'); tbody.innerHTML='';
    users.forEach(u => {
      const tr=document.createElement('tr');
      tr.innerHTML=`
        <td><strong>${esc(u.username)}</strong></td>
        <td>${esc(u.full_name||'—')}</td>
        <td>${esc(u.email||'—')}</td>
        <td><span class="sbadge ${u.system_role}">${u.system_role}</span></td>
        <td><button class="btn btn-sm btn-d" onclick="deleteUser(${u.id},${jsArg(u.username)})">Delete</button></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { toast(e.message,'e'); }
}

async function createUser() {
  const u=document.getElementById('nu-user').value.trim(), p=document.getElementById('nu-pass').value;
  if (!u||!p) { toast('Username and password required','e'); return; }
  try {
    await api('POST','/users', { username:u, password:p, system_role:document.getElementById('nu-role').value, full_name:document.getElementById('nu-name').value.trim()||null, email:document.getElementById('nu-email').value.trim()||null });
    toast('User created','s'); closeModal('modal-newuser');
    ['nu-user','nu-pass','nu-name','nu-email'].forEach(id=>document.getElementById(id).value='');
    loadUsers();
  } catch(e) { toast(e.message,'e'); }
}

async function deleteUser(id, name) {
  if (!await askConfirm('Delete User', `Delete user "${name}"?\n\nThis cannot be undone.`)) return;
  try { await api('DELETE', `/users/${id}`); toast('Deleted','s'); loadUsers(); }
  catch(e) { toast(e.message,'e'); }
}

async function bulkUploadUsers() {
  const file=document.getElementById('bulk-users-file').files[0];
  if (!file) { toast('Select CSV','e'); return; }
  const fd=new FormData(); fd.append('file',file);
  try {
    const r=await api('POST','/users/bulk-csv',fd,true);
    toast(`Created ${r.created} users`,'s'); closeModal('modal-bulkusers'); loadUsers();
  } catch(e) { toast(e.message,'e'); }
}

// Populated on right-click by showPathMenu()
document.body.insertAdjacentHTML('beforeend',
  '<div id="path-menu" class="path-menu" style="display:none"></div>');

// ── SMTP configuration (system admin) ──────────────────
let _smtpPresets = [];

async function loadSmtpConfig() {
  try {
    if (!_smtpPresets.length) {
      _smtpPresets = await api('GET', '/admin/smtp-presets');
      const sel = document.getElementById('smtp-preset');
      _smtpPresets.forEach(p => {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.label;
        sel.appendChild(o);
      });
    }
    const c = await api('GET', '/admin/smtp-config');
    document.getElementById('smtp-enabled').checked = !!c.enabled;
    document.getElementById('smtp-host').value      = c.host || '';
    document.getElementById('smtp-port').value      = c.port || 587;
    document.getElementById('smtp-security').value  = c.security || 'starttls';
    document.getElementById('smtp-user').value      = c.username || '';
    document.getElementById('smtp-from').value      = c.from_addr || '';
    document.getElementById('smtp-fromname').value  = c.from_name || 'BRACE';
    document.getElementById('smtp-verify').checked  = c.verify_ssl !== 0;
    document.getElementById('smtp-pass').value      = '';
    document.getElementById('smtp-pass-hint').textContent =
      c.has_password ? '— a password is stored' : '— none stored yet';
    // Emails can only deep-link to a run if the pod knows its external URL.
    document.getElementById('smtp-url-warn').innerHTML = c.public_url
      ? `Emails link to <code>${esc(c.public_url)}</code>`
      : ico('warn') + ' <b>BRACE_PUBLIC_URL is not set</b>, so emails cannot link back to the run. '
        + 'Set it in the Deployment to the route users actually browse to.';
  } catch(e) { /* non-admins never see this section */ }
}

function applySmtpPreset() {
  const id = document.getElementById('smtp-preset').value;
  const p = _smtpPresets.find(x => x.id === id);
  document.getElementById('smtp-preset-note').textContent = p ? (p.note || '') : '';
  if (!p) return;
  if (p.host) document.getElementById('smtp-host').value = p.host;
  document.getElementById('smtp-port').value     = p.port;
  document.getElementById('smtp-security').value = p.security;
  // Gmail rejects the account password outright; say so before they try.
  document.getElementById('smtp-pass-hint2').textContent =
    id.startsWith('gmail') ? 'Use a 16-character App Password — your normal Google password will be rejected.' : '';
}

async function saveSmtpConfig() {
  const body = {
    enabled:    document.getElementById('smtp-enabled').checked,
    host:       document.getElementById('smtp-host').value.trim(),
    port:       parseInt(document.getElementById('smtp-port').value, 10) || 587,
    security:   document.getElementById('smtp-security').value,
    username:   document.getElementById('smtp-user').value.trim(),
    from_addr:  document.getElementById('smtp-from').value.trim(),
    from_name:  document.getElementById('smtp-fromname').value.trim() || 'BRACE',
    verify_ssl: document.getElementById('smtp-verify').checked,
  };
  const pw = document.getElementById('smtp-pass').value;
  if (pw) body.password = pw;                 // blank keeps the stored one
  try {
    await api('PUT', '/admin/smtp-config', body);
    toast('Email settings saved', 's');
    document.getElementById('smtp-status').innerHTML =
      '<span style="color:var(--c-ok)">Saved. Send a test email to confirm it works.</span>';
    loadSmtpConfig();
  } catch(e) { toast(e.message, 'e'); }
}

async function testSmtpConfig() {
  const to = document.getElementById('smtp-test-to').value.trim();
  if (!to) { toast('Enter an address to send the test to', 'e'); return; }
  const box = document.getElementById('smtp-status');
  box.innerHTML = '<span style="color:var(--c-muted)">Sending…</span>';
  try {
    const r = await api('POST', '/admin/smtp-config/test', { to });
    box.innerHTML = `<span style="color:var(--c-ok)">✓ Sent to ${esc(r.sent_to.join(', '))}. `
      + 'If it does not arrive, check the spam folder.</span>';
  } catch(e) {
    box.innerHTML = `<span style="color:var(--c-err)">✗ ${esc(e.message)}</span>`;
  }
}

// ── Per-project notifications ──────────────────────────
async function loadNotifyConfig() {
  try {
    const c = await api('GET', `/projects/${_curProj.id}/notify-config`);
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.checked = !!v; };
    set('nt-enabled', c.enabled);
    set('nt-run-failed', c.on_run_failed);
    set('nt-sched-failed', c.on_scheduled_failed);
    set('nt-run-passed', c.on_run_passed);
    set('nt-onchange', c.only_on_change);
    set('nt-triggerer', c.notify_triggerer);
    set('nt-digest', c.weekly_digest);
    document.getElementById('nt-recipients').value = c.recipients || '';
    document.getElementById('nt-digest-cron').value = c.digest_cron || '0 8 * * 1';
    document.getElementById('nt-digest-desc').textContent =
      cronDescribe(c.digest_cron || '0 8 * * 1') || '';
    // Enabling notifications with no working transport produces silence, which
    // looks identical to "nothing failed". Say so up front.
    document.getElementById('notify-smtp-warn').innerHTML = c.smtp_ready
      ? 'Email is configured server-wide. Choose what this project should send.'
      : ico('warn') + ' <b>No SMTP server is configured yet</b>, so nothing will be sent. '
        + 'A system admin sets it up under Administration → Email Notifications.';
  } catch(e) { /* project_admin only */ }
}

async function saveNotifyConfig() {
  const g = id => document.getElementById(id).checked;
  try {
    const r = await api('PUT', `/projects/${_curProj.id}/notify-config`, {
      enabled:             g('nt-enabled'),
      on_run_failed:       g('nt-run-failed'),
      on_scheduled_failed: g('nt-sched-failed'),
      on_run_passed:       g('nt-run-passed'),
      only_on_change:      g('nt-onchange'),
      notify_triggerer:    g('nt-triggerer'),
      weekly_digest:       g('nt-digest'),
      digest_cron:         document.getElementById('nt-digest-cron').value.trim() || '0 8 * * 1',
      recipients:          document.getElementById('nt-recipients').value,
    });
    toast('Notification settings saved', 's');
    const n = (r.recipient_list || []).length;
    document.getElementById('nt-status').innerHTML = r.enabled
      ? `<span style="color:var(--c-ok)">Enabled — ${n} recipient${n === 1 ? '' : 's'}.</span>`
      : '<span style="color:var(--c-muted)">Disabled — no email will be sent.</span>';
    document.getElementById('nt-digest-desc').textContent =
      cronDescribe(r.digest_cron || '') || '';
  } catch(e) { toast(e.message, 'e'); }
}
