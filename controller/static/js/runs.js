// BRACE v2 — runs.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Run Modal ──────────────────────────────────────────
async function openRunModal() {
  if (!_tcs.length) await loadTCs();
  if (!_groups.length) await loadGroups();
  // TC list
  const list = document.getElementById('run-tc-list'); list.innerHTML='';
  _tcs.forEach(tc => {
    const d=document.createElement('div'); d.className='tc-sitem';
    const pre = _preSel.includes(tc.id)?'checked':'';
    d.innerHTML=`<input type="checkbox" class="run-chk" id="rtc-${tc.id}" value="${tc.id}" ${pre}><label for="rtc-${tc.id}"><span class="tc-code">${esc(tc.tc_code||'')}</span> ${esc(tc.name)}</label>`;
    list.appendChild(d);
  });
  _preSel=[];
  // Group list
  const sel=document.getElementById('run-grp-sel'); sel.innerHTML='';
  _groups.forEach(g => { const o=document.createElement('option'); o.value=g.id; o.textContent=`${g.name} (${g.tc_count} TCs)`; sel.appendChild(o); });
  // Tag list — options come from tags actually in use, so nobody has to
  // remember exact spelling.
  const tsel=document.getElementById('run-tag-sel'); tsel.innerHTML='';
  try {
    const tags = await api('GET', `/projects/${_curProj.id}/tags`);
    if (!tags.length) {
      tsel.innerHTML='<option value="">No tags yet — add them on a test case</option>';
    } else {
      tags.forEach(t => { const o=document.createElement('option');
        o.value=t.tag; o.textContent=`${t.tag} (${t.count})`; tsel.appendChild(o); });
    }
  } catch(e) { tsel.innerHTML='<option value="">Could not load tags</option>'; }

  await loadRunConfig();
  const p=document.getElementById('run-parallel');
  p.value = _runCfg.default_parallel; p.max = _runCfg.max_parallel;
  document.getElementById('run-parallel-hint').textContent =
    `Test cases at once (1–${_runCfg.max_parallel}). Use 1 if the suite's cases must run in order.`;

  document.getElementById('run-name').value=''; document.getElementById('run-xargs').value='';
  document.getElementById('run-mode').value='tcs'; onRunModeChange();
  showModal('modal-run');
}

// Server-side execution limits. Cached — they only change on a redeploy.
let _runCfg = { max_parallel: 1, default_parallel: 1 };
let _runCfgLoaded = false;
async function loadRunConfig() {
  if (_runCfgLoaded) return _runCfg;
  try { _runCfg = await api('GET', '/run-config'); _runCfgLoaded = true; }
  catch(e) { /* keep the safe 1x default */ }
  return _runCfg;
}

function onRunModeChange() {
  const m = document.getElementById('run-mode').value;
  document.getElementById('run-tcs-panel').style.display = m==='tcs'   ? 'block' : 'none';
  document.getElementById('run-grp-panel').style.display = m==='group' ? 'block' : 'none';
  document.getElementById('run-tag-panel').style.display = m==='tag'   ? 'block' : 'none';
}

async function triggerRun() {
  const mode = document.getElementById('run-mode').value;
  const body = { run_name:document.getElementById('run-name').value.trim()||null, extra_args:document.getElementById('run-xargs').value.trim()||null };
  const par = parseInt(document.getElementById('run-parallel').value, 10);
  if (par >= 1) body.parallel = par;
  if (mode==='tcs') {
    body.tc_ids = [...document.querySelectorAll('.run-chk:checked')].map(c=>+c.value);
    if (!body.tc_ids.length) { toast('Select at least one TC','e'); return; }
  } else if (mode==='tag') {
    body.tag = document.getElementById('run-tag-sel').value;
    if (!body.tag) { toast('Select a tag','e'); return; }
  } else {
    body.group_id = +document.getElementById('run-grp-sel').value;
    if (!body.group_id) { toast('Select a suite','e'); return; }
  }
  try {
    const r = await api('POST', `/projects/${_curProj.id}/runs`, body);
    toast(runStartMsg(r),'i'); closeModal('modal-run'); switchTab('runs');
  } catch(e) { toast(e.message,'e'); }
}

// ── Runs ───────────────────────────────────────────────
// Runs may queue when the server is at its concurrency limit — say so, rather
// than leaving the user wondering why nothing is executing.
function runStartMsg(r) {
  const par = (r && r.parallel > 1) ? ` (${r.parallel} cases in parallel)` : '';
  if (r && r.status === 'queued') {
    return r.queued_ahead
      ? `Run queued — ${r.queued_ahead} run(s) ahead of it. It starts automatically.`
      : `Run queued${par} — starts as soon as an execution slot is free.`;
  }
  return 'Run started: ' + (r ? r.run_id : '');
}

async function loadRuns() {
  if (!_curProj) return;
  try { _runs = await api('GET', `/projects/${_curProj.id}/runs`); renderRuns(); return _runs; }
  catch(e) { toast(e.message,'e'); }
}

function startRunsPoller() { clearInterval(_runsTimer); _runsTimer = setInterval(loadRuns, 5000); }

function runsClearFilters() {
  document.getElementById('runs-search').value = '';
  document.getElementById('runs-from').value = '';
  document.getElementById('runs-to').value = '';
  document.getElementById('runs-status').value = '';
  document.getElementById('runs-user').value = '';
  renderRuns();
}

// Fill the "By" dropdown from whoever actually appears in the loaded runs.
// renderRuns() runs every 5s from the poller, so rebuild only when the set of
// names really changed — otherwise an open dropdown closes under the user and
// a selection made mid-poll is lost.
function syncRunUserFilter() {
  const sel = document.getElementById('runs-user');
  if (!sel) return;
  const names = [...new Set(_runs.map(r => r.triggered_by).filter(Boolean))].sort();
  if (sel.dataset.names === names.join(' ')) return;
  sel.dataset.names = names.join(' ');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Anyone</option>';
  names.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
  // Keep the current pick if that user still has runs; otherwise fall back to
  // Anyone rather than filtering against a name that no longer exists.
  sel.value = names.includes(cur) ? cur : '';
}

function renderRuns() {
  const tbody = document.getElementById('runs-tbody');
  const q      = (document.getElementById('runs-search')?.value || '').trim().toLowerCase();
  const from   = document.getElementById('runs-from')?.value || '';
  const to     = document.getElementById('runs-to')?.value || '';
  const status = document.getElementById('runs-status')?.value || '';
  syncRunUserFilter();
  const who    = document.getElementById('runs-user')?.value || '';

  let rows = _runs;
  if (q)      rows = rows.filter(r => (r.run_name||'').toLowerCase().includes(q) || (r.run_id||'').toLowerCase().includes(q));
  if (status) rows = rows.filter(r => r.status === status);
  if (who)    rows = rows.filter(r => r.triggered_by === who);
  if (from)   rows = rows.filter(r => r.started_at && r.started_at.slice(0,10) >= from);
  if (to)     rows = rows.filter(r => r.started_at && r.started_at.slice(0,10) <= to);

  const active = !!(q || from || to || status || who);
  const cnt = document.getElementById('runs-filter-count');
  if (cnt) cnt.textContent = active ? `${rows.length} of ${_runs.length} runs`
                                    : `${_runs.length} run${_runs.length===1?'':'s'}`;
  const clr = document.getElementById('runs-clear');
  if (clr) clr.hidden = !active;

  tbody.innerHTML='';
  if (!_runs.length) { tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--c-muted)">No runs yet.</td></tr>'; return; }
  if (!rows.length)  { tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--c-muted)">No runs match these filters.</td></tr>'; return; }
  rows.forEach(r => {
    const done = r.passed+r.failed;
    const pct  = r.total ? Math.round(r.passed/r.total*100) : 0;
    const fpct = r.total ? Math.round(r.failed/r.total*100) : 0;
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td><code style="font-size:11px">${esc(r.run_id)}</code></td>
      <td>${esc(r.run_name||'—')}</td>
      <td>${esc(r.triggered_by||'—')}</td>
      <td><span class="sbadge ${r.status}">${r.status}</span></td>
      <td style="min-width:140px">
        <div style="font-size:11px;margin-bottom:2px">✓${r.passed} ✗${r.failed} / ${r.total}</div>
        <div class="pbar"><div class="fill" style="width:${r.total?done/r.total*100:0}%;background:linear-gradient(90deg,var(--c-ok) ${pct}%,var(--c-err) 0)"></div></div>
      </td>
      <td style="white-space:nowrap">${r.started_at?r.started_at.replace('T',' ').slice(0,16):'—'}</td>
      <td><div class="bgrp">
        <button class="bico" onclick="viewRunDetail(${jsArg(r.run_id)})" title="Details">🔍</button>
        ${(r.status==='running'||r.status==='queued')?`<button class="bico" onclick="cancelRun(${jsArg(r.run_id)})" title="Cancel">✕</button>`:''}
        ${(r.failed>0 && r.status!=='running' && r.status!=='queued')?`<button class="bico" onclick="rerunFailed(${jsArg(r.run_id)})" title="Re-run the ${r.failed} failed test case(s)">↻</button>`:''}
      </div></td>`;
    tbody.appendChild(tr);
  });
}

let _rdTimer = null;
let _rdStreaming = false;

async function viewRunDetail(runId) {
  clearInterval(_rdTimer);
  _rdStreaming = false;
  try {
    await renderRunDetail(runId);
    showModal('modal-rundetail');
    _rdTimer = setInterval(() => renderRunDetail(runId), 3000);
  } catch(e) { toast(e.message,'e'); }
}

// Why a case failed, straight from output.xml — the point is that nobody has to
// open log.html and hunt for the red keyword to find out.
function failureBlock(it, runId) {
  if (it.status !== 'failed' || !(it.fail_summary || it.fail_detail)) return '';
  // <img> cannot send the Authorization header, so the results route's
  // query-param token is the only way this loads — same trick as the report
  // iframe. Without it every thumbnail 401s.
  const shot = it.fail_screenshot
    ? `/results/${_curProj.id}/${runId}/${it.rf_run_id}/`
      + `${encodeURIComponent(it.fail_screenshot)}?token=${encodeURIComponent(_token||'')}`
    : null;
  return `<div class="failbox">
    <div class="fb-kw">${esc(it.fail_summary||'Failed')}</div>
    ${it.fail_detail?`<div class="fb-msg">${esc(it.fail_detail)}</div>`:''}
    ${shot?`<a href="${esc(shot)}" target="_blank" rel="noopener" title="Open full screenshot">
      <img class="fb-shot" src="${esc(shot)}" alt="Screenshot at failure"></a>`:''}
  </div>`;
}

async function renderRunDetail(runId) {
  const r = await api('GET', `/runs/${runId}`);
  document.getElementById('rd-title').textContent = r.run_name||runId;
  const pct = r.total ? Math.round(r.passed/r.total*100) : 0;
  const barPct = r.total ? (r.passed+r.failed)/r.total*100 : 0;
  let html=`
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
      <span class="sbadge ${r.status}">${r.status}</span>
      <span style="font-size:12px">By: <b>${esc(r.triggered_by||'—')}</b></span>
      <span style="font-size:12px">✓ ${r.passed} &nbsp; ✗ ${r.failed} &nbsp; / ${r.total}</span>
      ${r.rerun_of?`<span style="font-size:12px;color:var(--c-muted)">re-run of <a href="#" onclick="viewRunDetail(${jsArg(r.rerun_of)});return false">${esc(r.rerun_of)}</a></span>`:''}
    </div>
    <div class="pbar" style="margin-bottom:12px"><div class="fill" style="width:${barPct}%;background:linear-gradient(90deg,var(--c-ok) ${pct}%,var(--c-err) 0)"></div></div>`;
  const failedCount = (r.items||[]).filter(i => i.status==='failed').length;
  if (r.has_combined_report || failedCount) {
    html+=`<div class="bgrp" style="margin-bottom:12px">
      ${r.has_combined_report?`<button class="btn btn-sm btn-p" onclick="openReport('/results/${_curProj.id}/${runId}/combined/report.html','Combined Report')">📊 Combined Report</button>
      <button class="btn btn-sm btn-o" onclick="openReport('/results/${_curProj.id}/${runId}/combined/log.html','Combined Log')">📋 Combined Log</button>`:''}
      ${(failedCount && r.status!=='running' && r.status!=='queued')
        ?`<button class="btn btn-sm btn-a" onclick="rerunFailed(${jsArg(runId)})">↻ Re-run Failed (${failedCount})</button>`:''}
    </div>`;
  }
  html+=`<div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--c-muted);margin-bottom:8px">Test Case Results</div>`;
  r.items.forEach(it => {
    html+=`<div class="tc-rrow">
      <span class="tc-code">${esc(it.tc_code||'')}</span>
      <span class="name">${esc(it.tc_name||'')}</span>
      <span class="sbadge ${it.status}">${it.status}</span>
      <div class="bgrp">
        ${it.has_report?`<button class="btn btn-sm btn-o" onclick="openReport(${jsArg(`/results/${_curProj.id}/${runId}/${it.rf_run_id}/report.html`)},${jsArg((it.tc_name||'')+' Report')})">Report</button>`:''}
        ${it.has_log   ?`<button class="btn btn-sm btn-o" onclick="openReport(${jsArg(`/results/${_curProj.id}/${runId}/${it.rf_run_id}/log.html`)},${jsArg((it.tc_name||'')+' Log')})">Log</button>`:''}
        ${it.status==='failed'?`<button class="btn btn-sm btn-a" onclick="openAIDebug(${jsArg(runId)},${jsArg(it.rf_run_id||'')},null,${jsArg(it.tc_name||'')})" title="AI-assisted debugging">🤖 Debug</button>`:''}
      </div>
    </div>${failureBlock(it, runId)}`;
  });
  document.getElementById('rd-body').innerHTML=html;

  const cons=document.getElementById('run-console');
  if (r.status==='queued') {
    // Keep polling — it will flip to running when an execution slot frees up
    cons.textContent = 'Waiting for a free execution slot…\n\n'
      + 'BRACE limits how many suites run at once so the server is not overloaded. '
      + 'This run starts automatically as soon as a slot is available.';
  } else if (r.status==='running') {
    if (!_rdStreaming) { cons.textContent=''; _rdStreaming = true; streamLog(runId, cons); }
  } else {
    clearInterval(_rdTimer);
    if (!cons.textContent) cons.textContent='(Run complete — open Report/Log links above)';
  }
}

function closeRunDetail() {
  clearInterval(_rdTimer);
  _rdStreaming = false;
  closeModal('modal-rundetail');
}

function streamLog(runId, el) {
  (async () => {
    try {
      const r = await fetch(`/api/runs/${runId}/log`, { headers:{ Authorization:`Bearer ${_token}` } });
      const reader=r.body.getReader(); const dec=new TextDecoder();
      let buf='';
      while(true) {
        const {done,value}=await reader.read(); if(done) break;
        buf += dec.decode(value,{stream:true});
        // Keep the trailing fragment — a line can straddle a chunk boundary
        const lines = buf.split('\n');
        buf = lines.pop();
        lines.forEach(line => {
          if (!line.startsWith('data: ')) return;
          const msg=line.slice(6);
          if (msg==='__DONE__'||msg==='__TIMEOUT__') return;
          el.textContent+=msg+'\n'; el.scrollTop=el.scrollHeight;
        });
      }
    } catch {}
  })();
}

async function cancelRun(runId) {
  if (!await askConfirm('Cancel Run', 'Stop this run?\n\nTest cases still queued will not execute.',
      { okText: 'Cancel Run' })) return;
  try { await api('POST', `/runs/${runId}/cancel`); toast('Cancelled','i'); loadRuns(); }
  catch(e) { toast(e.message,'e'); }
}

function openReport(url, title) {
  document.getElementById('rpt-title').textContent=title;
  const sep = url.includes('?') ? '&' : '?';
  document.getElementById('rpt-frame').src = _token ? `${url}${sep}token=${encodeURIComponent(_token)}` : url;
  showModal('modal-report');
}

// ── Reports tab ────────────────────────────────────────
function fmtDur(sec) {
  if (sec === null || sec === undefined) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

let _rptPreset = 'today';

function _rptTodayStr() { return _localDate(new Date()); }

// Local calendar date. NOT toISOString(), which converts to UTC first: at
// 02:00 IST that returns yesterday, so a "last 7 days" window would start a day
// early while its end date came from local time — an inconsistent range.
function _localDate(d) {
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

// n days ago, local.
function _daysAgo(n) { const d = new Date(); d.setDate(d.getDate() - n); return _localDate(d); }

function rptSetPreset(p) {
  _rptPreset = p;
  document.querySelectorAll('.rpt-preset').forEach(b => b.classList.toggle('active', b.dataset.p === p));
  document.getElementById('rpt-from').value = '';
  document.getElementById('rpt-to').value = '';
  renderReports();
}

function rptCustomRange() {
  const from = document.getElementById('rpt-from').value;
  const to = document.getElementById('rpt-to').value;
  if (!from && !to) return;
  _rptPreset = 'custom';
  document.querySelectorAll('.rpt-preset').forEach(b => b.classList.remove('active'));
  renderReports();
}

function rptDateParams() {
  const today = _rptTodayStr();
  if (_rptPreset === 'today') return { date_from: today, date_to: today };
  if (_rptPreset === '7d')  return { date_from: _daysAgo(6),  date_to: today };
  if (_rptPreset === '30d') return { date_from: _daysAgo(29), date_to: today };
  if (_rptPreset === 'all') return { date_from: '0001-01-01', date_to: '9999-12-31' };
  // custom
  const from = document.getElementById('rpt-from').value;
  const to = document.getElementById('rpt-to').value;
  return { date_from: from || '0001-01-01', date_to: to || today };
}

async function renderReports() {
  try {
    const { date_from, date_to } = rptDateParams();
    const st = await api('GET', `/projects/${_curProj.id}/report-stats?date_from=${date_from}&date_to=${date_to}&limit=100`);
    renderKPIs(st.summary);
    renderTrend(st.trend);
    renderTCStats('rpt-failing', st.top_failing, 'No failures recorded.');
    renderTCStats('rpt-flaky',   st.flaky,       'No flaky test cases.');
    renderRunHistory(st.trend.slice().reverse());
  } catch(e) { toast(e.message,'e'); }
}

function renderKPIs(s) {
  const rateCls = s.pass_rate >= 90 ? 'ok' : s.pass_rate >= 70 ? 'warn' : 'err';
  document.getElementById('rpt-kpis').innerHTML = `
    <div class="kpi ${rateCls}">
      <span class="kpi-lbl">Pass Rate</span>
      <span class="kpi-val">${s.pass_rate}%</span>
      <span class="kpi-sub">${s.total_passed} of ${s.total_tests} tests</span>
    </div>
    <div class="kpi">
      <span class="kpi-lbl">Total Runs</span>
      <span class="kpi-val">${s.total_runs}</span>
      <span class="kpi-sub">across this project</span>
    </div>
    <div class="kpi ok">
      <span class="kpi-lbl">Passed</span>
      <span class="kpi-val">${s.total_passed}</span>
      <span class="kpi-sub">test executions</span>
    </div>
    <div class="kpi err">
      <span class="kpi-lbl">Failed</span>
      <span class="kpi-val">${s.total_failed}</span>
      <span class="kpi-sub">test executions</span>
    </div>
    <div class="kpi">
      <span class="kpi-lbl">Avg Duration</span>
      <span class="kpi-val" style="font-size:22px">${fmtDur(s.avg_duration)}</span>
      <span class="kpi-sub">per run</span>
    </div>
    <div class="kpi ${s.flaky_count ? 'warn' : ''}">
      <span class="kpi-lbl">Flaky TCs</span>
      <span class="kpi-val">${s.flaky_count}</span>
      <span class="kpi-sub">inconsistent results</span>
    </div>`;
}

function renderTrend(runs) {
  const wrap = document.getElementById('rpt-trend-wrap');
  if (!runs || !runs.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  document.getElementById('rpt-trend-sub').textContent = `last ${runs.length} run${runs.length>1?'s':''}`;

  const maxTot = Math.max(...runs.map(r => r.total || 0), 1);
  let bars = '<div class="trend-bars">';
  let axis = '<div class="trend-axis">';
  runs.forEach(r => {
    const tot = r.total || 0;
    const pH = tot ? (r.passed / maxTot) * 100 : 0;
    const fH = tot ? (r.failed / maxTot) * 100 : 0;
    const when = r.started_at ? r.started_at.replace('T',' ').slice(5,16) : '';
    const pct = tot ? Math.round(r.passed / tot * 100) : 0;
    bars += `<div class="tbar" onclick="viewRunDetail(${jsArg(r.run_id)})">
        <div class="tip"><b>${esc(r.run_name || r.run_id)}</b><br>${when}<br>
          ✓ ${r.passed} &nbsp; ✗ ${r.failed} &nbsp; (${pct}%)<br>${fmtDur(r.duration_sec)}</div>
        <div class="seg-f" style="height:${fH}%"></div>
        <div class="seg-p" style="height:${pH}%"></div>
      </div>`;
    axis += `<span>${when.slice(0,5)}</span>`;
  });
  bars += '</div>'; axis += '</div>';
  document.getElementById('rpt-trend').innerHTML = bars + axis +
    `<div class="trend-legend">
       <span><i style="background:var(--c-ok)"></i>Passed</span>
       <span><i style="background:var(--c-err)"></i>Failed</span>
       <span style="margin-left:auto">click a bar for run details</span>
     </div>`;
}

function renderTCStats(elId, items, emptyMsg) {
  const wrap = document.getElementById(elId + '-wrap');
  wrap.style.display = 'block';
  const el = document.getElementById(elId);
  if (!items || !items.length) { el.innerHTML = `<div class="rpt-empty">${emptyMsg}</div>`; return; }
  el.innerHTML = items.map(t => `
    <div class="srow">
      <span class="srow-code">${esc(t.tc_code || '—')}</span>
      <span class="srow-name" title="${esc(t.tc_name || '')}">${esc(t.tc_name || 'Unnamed')}</span>
      <span class="srow-bar"><i style="width:${t.pass_rate}%"></i></span>
      <span class="srow-num">${t.pass_rate}% · ${t.runs} run${t.runs>1?'s':''}</span>
    </div>`).join('');
}

function renderRunHistory(runs) {
  const list = document.getElementById('reports-list');
  if (!runs.length) { list.innerHTML = '<div class="rpt-empty">No runs in this date range.</div>'; return; }
  list.innerHTML = runs.map(r => {
    const pct = r.total ? Math.round(r.passed / r.total * 100) : 0;
    const when = r.started_at ? r.started_at.replace('T',' ').slice(0,16) : '';
    return `<div class="srow" style="cursor:pointer;padding:11px 16px" onclick="viewRunDetail(${jsArg(r.run_id)})">
      <span class="sbadge ${esc(r.status)}">${esc(r.status)}</span>
      <span class="srow-name"><b>${esc(r.run_name || r.run_id)}</b></span>
      <span class="srow-num" style="min-width:auto">${r.total} TC${r.total===1?'':'s'}</span>
      <span class="srow-bar" title="${pct}% passed"><i style="width:${pct}%;background:${r.failed?'linear-gradient(90deg,var(--c-ok) '+pct+'%,var(--c-err) '+pct+'%)':'var(--c-ok)'};width:100%"></i></span>
      <span class="srow-num" style="color:#15803d">✓ ${r.passed}</span>
      <span class="srow-num" style="color:#b91c1c;min-width:44px">✗ ${r.failed}</span>
      <span class="srow-num" style="min-width:104px">${when}</span>
    </div>`;
  }).join('');
}

// ── Coverage view ──────────────────────────────────────
// Answers "how much of what we have is actually tested?", which the execution
// dashboard cannot: a 100% pass rate over three cases looks identical to a 100%
// pass rate over eighty until you count the ones that never ran.
let _rptViewMode = 'exec';

function rptView(v) {
  _rptViewMode = v;
  document.querySelectorAll('.rpt-vtab').forEach(b =>
    b.classList.toggle('active', b.dataset.v === v));
  document.getElementById('rpt-view-exec').style.display = v === 'exec' ? 'block' : 'none';
  document.getElementById('rpt-view-cov').style.display  = v === 'cov'  ? 'block' : 'none';
  if (v === 'cov') loadCoverage();
}

async function loadCoverage() {
  const days = parseInt(document.getElementById('cov-stale-days').value, 10) || 14;
  try {
    const c = await api('GET', `/projects/${_curProj.id}/coverage?stale_days=${days}`);
    renderCoverage(c);
  } catch(e) { toast(e.message, 'e'); }
}

function renderCoverage(c) {
  const t = c.test_cases, s = c.scripts;
  const cls = p => p >= 80 ? 'ok' : p >= 50 ? 'warn' : 'err';

  document.getElementById('cov-kpis').innerHTML = `
    <div class="kpi ${cls(t.coverage_pct)}">
      <span class="kpi-lbl">Execution Coverage</span>
      <span class="kpi-val">${t.coverage_pct}%</span>
      <span class="kpi-sub">${t.executed} of ${t.total} cases have ever run</span>
    </div>
    <div class="kpi ${cls(t.health_pct)}">
      <span class="kpi-lbl">Currently Passing</span>
      <span class="kpi-val">${t.health_pct}%</span>
      <span class="kpi-sub">of the ${t.executed} that have run</span>
    </div>
    <div class="kpi err">
      <span class="kpi-lbl">Currently Failing</span>
      <span class="kpi-val">${t.failed}</span>
      <span class="kpi-sub">${t.failed_pct}% of all cases</span>
    </div>
    <div class="kpi ${t.never ? 'warn' : 'ok'}">
      <span class="kpi-lbl">Never Executed</span>
      <span class="kpi-val">${t.never}</span>
      <span class="kpi-sub">${t.never_pct}% of all cases</span>
    </div>
    <div class="kpi ${t.stale ? 'warn' : ''}">
      <span class="kpi-lbl">Stale</span>
      <span class="kpi-val">${t.stale}</span>
      <span class="kpi-sub">no run in ${c.stale_days} days</span>
    </div>
    <div class="kpi ${cls(s.onboarded_pct)}">
      <span class="kpi-lbl">Scripts Onboarded</span>
      <span class="kpi-val">${s.onboarded_pct}%</span>
      <span class="kpi-sub">${s.onboarded} of ${s.on_disk} .robot files</span>
    </div>`;

  // One bar, whole project: the three states always sum to 100%.
  const seg = (n, p, k, label) => n
    ? `<span class="cseg ${k}" style="width:${p}%" title="${label}: ${n} (${p}%)">${p >= 8 ? p + '%' : ''}</span>`
    : '';
  document.getElementById('cov-bar').innerHTML = t.total ? `
    <div class="cbar">
      ${seg(t.passed, t.passed_pct, 'ok',    'Passing')}
      ${seg(t.failed, t.failed_pct, 'err',   'Failing')}
      ${seg(t.never,  t.never_pct,  'never', 'Never executed')}
    </div>
    <div class="clegend">
      <span><i class="ok"></i> Passing ${t.passed} (${t.passed_pct}%)</span>
      <span><i class="err"></i> Failing ${t.failed} (${t.failed_pct}%)</span>
      <span><i class="never"></i> Never executed ${t.never} (${t.never_pct}%)</span>
    </div>` : '<div class="rpt-empty">No test cases yet.</div>';

  // Scripts on disk with no test case are invisible everywhere else in BRACE —
  // they cannot run, and nothing reports them as missing.
  const orph = s.orphan_list || [], miss = s.missing_list || [];
  document.getElementById('cov-scripts').innerHTML = `
    <div class="cbar">
      ${seg(s.onboarded, s.onboarded_pct, 'ok', 'Onboarded')}
      ${s.orphan ? `<span class="cseg never" style="width:${(100 - s.onboarded_pct).toFixed(1)}%"
          title="Not onboarded: ${s.orphan}"></span>` : ''}
    </div>
    <div class="clegend">
      <span><i class="ok"></i> Linked to a test case ${s.onboarded}</span>
      <span><i class="never"></i> On disk, no test case ${s.orphan}</span>
    </div>
    ${orph.length ? `<div class="cov-sub">Not onboarded — these scripts can never run:</div>
      <div class="cov-list">${orph.map(p => `<code>${esc(p)}</code>`).join('')}</div>` : ''}
    ${miss.length ? `<div class="cov-sub err">Test cases pointing at a file that is not on disk
        — these will fail every time:</div>
      <div class="cov-list">${miss.map(p => `<code>${esc(p)}</code>`).join('')}</div>` : ''}`;

  document.getElementById('cov-suites').innerHTML = (c.by_suite || []).map(g => `
    <tr>
      <td>${esc(g.suite)}</td>
      <td>${g.total}</td>
      <td>${g.executed}</td>
      <td><div class="pbar" title="${g.coverage_pct}% executed"><div class="fill"
            style="width:${g.coverage_pct}%;background:var(--c-${g.coverage_pct >= 80 ? 'ok' : g.coverage_pct >= 50 ? 'warn' : 'err'})"></div></div>
          <span class="cov-pct">${g.coverage_pct}%</span></td>
      <td>${g.passed}</td>
      <td>${g.failed}</td>
      <td>${g.never}</td>
    </tr>`).join('') ||
    '<tr><td colspan="7" class="rpt-empty">No suites yet.</td></tr>';

  covList('cov-never', c.never_run, 'Every test case has run at least once.');
  covList('cov-stale', c.stale_list, `Nothing older than ${c.stale_days} days.`);
}

function covList(elId, items, emptyMsg) {
  const el = document.getElementById(elId);
  if (!items || !items.length) { el.innerHTML = `<div class="rpt-empty">${emptyMsg}</div>`; return; }
  el.innerHTML = items.map(t => `
    <div class="srow">
      <span class="srow-code">${esc(t.tc_code || '—')}</span>
      <span class="srow-name" title="${esc(t.suite_path || 'No suite path set')}">${esc(t.name || 'Unnamed')}</span>
      <span class="srow-num">${t.last_run_at ? esc(t.last_run_at.replace('T',' ').slice(0,10)) : 'never'}</span>
    </div>`).join('');
}
