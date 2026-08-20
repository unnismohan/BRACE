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
// Three different things can be true, and they used to read the same. A run
// that begins immediately should not be announced as waiting for a slot.
function runStartMsg(r) {
  if (!r) return 'Run started';
  const n = r.total ? `${r.total} case${r.total === 1 ? '' : 's'}` : 'Run';
  // "6 cases, 6 at a time" says the same thing twice; when the width covers the
  // whole run they simply all go at once.
  const par = r.parallel > 1
    ? (r.parallel >= r.total ? ' in parallel' : `, ${r.parallel} at a time`)
    : '';
  if (r.starts_immediately) return `Running ${n}${par}…`;
  if (r.queued_ahead)
    return `Queued behind ${r.queued_ahead} run${r.queued_ahead === 1 ? '' : 's'} — `
         + 'it starts on its own.';
  const slots = r.slots_total || 0;
  return slots === 1
    ? 'Queued — the execution slot is busy. It starts as soon as it frees.'
    : `Queued — all ${slots} execution slots are busy. It starts as soon as one frees.`;
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
  if (!_runs.length) { tbody.innerHTML = `<tr><td colspan="7"><div class="empty"><div class="eico">${ico('run')}</div><h4>Nothing has run yet</h4><p>Pick a suite or a few test cases and start a run — results, reports and failure detail all land here.</p>${can('run') ? '<button class="btn btn-a btn-sm" onclick="openRunModal()">'+ico('run')+' Start a run</button>' : ''}</div></td></tr>`; return; }
  if (!rows.length)  { tbody.innerHTML = `<tr><td colspan="7"><div class="empty"><div class="eico">${ico('search')}</div><h4>No runs match</h4><p>Nothing here fits the current filters.</p><button class="btn btn-o btn-sm" onclick="runsClearFilters()">Clear filters</button></div></td></tr>`; return; }
  rows.forEach(r => {
    const done = r.passed+r.failed;
    const pct  = r.total ? Math.round(r.passed/r.total*100) : 0;
    const fpct = r.total ? Math.round(r.failed/r.total*100) : 0;
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td data-label="Run ID"><code style="font-size:11px">${esc(r.run_id)}</code></td>
      <td data-label="Name">${esc(r.run_name||'—')}</td>
      <td data-label="By">${esc(r.triggered_by||'—')}</td>
      <td data-label="Status"><span class="sbadge ${r.status}">${r.status}</span></td>
      <td data-label="Results" style="min-width:140px">
        <div style="font-size:11px;margin-bottom:2px">✓${r.passed} ✗${r.failed} / ${r.total}</div>
        <div class="pbar"><div class="fill" style="width:${r.total?done/r.total*100:0}%;background:linear-gradient(90deg,var(--c-ok) ${pct}%,var(--c-err) 0)"></div></div>
      </td>
      <td data-label="Started" style="white-space:nowrap">${r.started_at?r.started_at.replace('T',' ').slice(0,16):'—'}</td>
      <td><div class="bgrp">
        <button class="bico" onclick="viewRunDetail(${jsArg(r.run_id)})" title="Details">${ico('search')}</button>
        ${(r.status==='running'||r.status==='queued')?`<button class="bico" onclick="cancelRun(${jsArg(r.run_id)})" title="Cancel">${ico('stop')}</button>`:''}
        ${(r.failed>0 && r.status!=='running' && r.status!=='queued')?`<button class="bico" onclick="rerunFailed(${jsArg(r.run_id)})" title="Re-run the ${r.failed} failed test case(s)">${ico('retry')}</button>`:''}
      </div></td>`;
    tbody.appendChild(tr);
  });
}

// ── Run detail ───────────────────────────────────────────────────────────────
// Built to survive a 1200-case run. The rules that follow from that:
//   • the 3s poller fetches the summary only (~1 KB), never the case list
//   • cases are paged in from /runs/{id}/items, PAGE at a time
//   • failure detail (stack traces, page source) is fetched per row on expand
//   • the list is filtered to failures by default — on a big run that is the
//     only part anyone is looking for
const RD_PAGE = 50;
let _rdTimer = null;
let _rdStreaming = false;
let _rd = null;          // per-open state; null when the modal is closed
let _rdQTimer = null;
let _rdES = null;        // EventSource while a run is live

async function viewRunDetail(runId) {
  rdStopWatch();
  _rdStreaming = false;
  try {
    const r = await api('GET', `/runs/${runId}`);
    _rd = { runId, run: r, q: '', loaded: 0, total: 0, items: [],
            open: {}, detail: {}, headSig: '', busy: false, live: false,
            // Land on the failures when there are any; there is no reason to
            // make someone scroll past 700 passes to find the 400 that broke.
            status: (r.status_counts && r.status_counts.failed) ? 'failed' : '' };
    document.getElementById('rd-body').innerHTML = `
      <div id="rd-head"></div>
      <div class="rd-bar">
        <div id="rd-chips" class="rd-chips"></div>
        <input id="rd-q" class="rd-q" type="search" placeholder="Search code, name or failure…"
               oninput="rdSearchInput(this.value)" autocomplete="off">
      </div>
      <div id="rd-items"></div>
      <div id="rd-more"></div>`;
    renderRunHead(r);
    await loadRunItems(true);
    showModal('modal-rundetail');
    updateRunConsole(r);
    rdStartWatch(r);
  } catch(e) { toast(e.message,'e'); }
}

// Live updates. SSE when it connects, the 3s poll when it does not — a proxy
// that buffers event streams degrades to the old behaviour instead of leaving
// the window frozen.
function rdStartWatch(r) {
  if (r.status !== 'running' && r.status !== 'queued') return;
  if (!window.EventSource) { _rdTimer = setInterval(pollRunDetail, 3000); return; }
  const runId = _rd.runId;
  try {
    // EventSource cannot send headers, so the token rides in the query string.
    _rdES = new EventSource(
      `/api/runs/${encodeURIComponent(runId)}/events?token=${encodeURIComponent(_token||'')}`);
  } catch(e) {
    _rdTimer = setInterval(pollRunDetail, 3000);
    return;
  }
  _rdES.addEventListener('summary', ev => rdApplySummary(JSON.parse(ev.data)));
  _rdES.addEventListener('item',    ev => rdApplyItem(JSON.parse(ev.data)));
  _rdES.addEventListener('done',    ev => {
    rdApplySummary(JSON.parse(ev.data));
    rdStopWatch();
    // One last read so the header, chips and combined-report buttons reflect
    // the committed run rather than the last event.
    pollRunDetail();
  });
  _rdES.onopen  = () => { if (_rd) { _rd.live = true; clearInterval(_rdTimer); } };
  _rdES.onerror = () => {
    // Fires on network loss AND on a clean server close. Either way, stop the
    // stream and fall back to polling; if the run is finished the first poll
    // clears its own timer.
    if (_rdES) { _rdES.close(); _rdES = null; }
    if (_rd && !_rdTimer) _rdTimer = setInterval(pollRunDetail, 3000);
    if (_rd) _rd.live = false;
  };
  // Safety net: if the stream never opens, poll anyway after 4s.
  _rdTimer = setInterval(pollRunDetail, 4000);
}

function rdStopWatch() {
  clearInterval(_rdTimer);
  _rdTimer = null;
  if (_rdES) { _rdES.close(); _rdES = null; }
  if (_rd) _rd.live = false;
}

// A pushed summary carries only what changed; merge rather than replace so the
// fields the event does not send (run_name, has_combined_report…) survive.
function rdApplySummary(d) {
  if (!_rd) return;
  Object.assign(_rd.run, d);
  renderRunHead(_rd.run);
  updateRunConsole(_rd.run);
}

function rdApplyItem(d) {
  if (!_rd) return;
  const counts = _rd.run.status_counts || (_rd.run.status_counts = {});
  const row = _rd.items.find(i => i.id === d.id);
  if (typeof d.passed === 'number') { _rd.run.passed = d.passed; _rd.run.failed = d.failed; }
  if (row) {
    // Keep the chip counts honest as a case moves pending -> running -> verdict.
    if (row.status !== d.status) {
      counts[row.status] = Math.max(0, (counts[row.status] || 1) - 1);
      counts[d.status]   = (counts[d.status] || 0) + 1;
    }
    Object.assign(row, {status: d.status, rf_run_id: d.rf_run_id,
                        fail_summary: d.fail_summary || row.fail_summary});
    document.getElementById('rd-items').dataset.sig = '';   // contents changed, count did not
    renderRunItems();
  } else if (d.status !== 'running') {
    // A case that finished outside the loaded page: counts still need updating,
    // and the filtered list may now be missing a row the user should see.
    counts.pending = Math.max(0, (counts.pending || 1) - 1);
    counts[d.status] = (counts[d.status] || 0) + 1;
    if (!_rd.busy && _rd.status && _rd.status === d.status) refreshRunItems();
  }
  renderRunHead(_rd.run);
}

function closeRunDetail() {
  rdStopWatch();
  clearTimeout(_rdQTimer);
  _rdStreaming = false;
  _rd = null;
  closeModal('modal-rundetail');
}

// Summary poll. Cheap by design — no items in the response, and the case list
// is only re-fetched when a count actually moved.
async function pollRunDetail() {
  if (!_rd) return;
  let r;
  try { r = await api('GET', `/runs/${_rd.runId}`); }
  catch(e) { return; }                       // transient; the next tick retries
  if (!_rd) return;
  const prev = _rd.run;
  _rd.run = r;
  renderRunHead(r);
  updateRunConsole(r);
  const moved = prev.passed !== r.passed || prev.failed !== r.failed
             || prev.status !== r.status;
  if (moved) refreshRunItems();
  if (r.status !== 'running' && r.status !== 'queued') rdStopWatch();
}

function renderRunHead(r) {
  document.getElementById('rd-title').textContent = r.run_name || _rd.runId;
  const runId  = _rd.runId;
  const failed = (r.status_counts && r.status_counts.failed) || 0;
  const pct    = r.total ? Math.round(r.passed / r.total * 100) : 0;
  const barPct = r.total ? (r.passed + r.failed) / r.total * 100 : 0;
  const html = `
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
      <span class="sbadge ${r.status}">${r.status}</span>
      <span style="font-size:12px">By: <b>${esc(r.triggered_by||'—')}</b></span>
      <span style="font-size:12px">✓ ${r.passed} &nbsp; ✗ ${r.failed} &nbsp; / ${r.total}</span>
      ${r.rerun_of?`<span style="font-size:12px;color:var(--c-muted)">re-run of <a href="#" onclick="viewRunDetail(${jsArg(r.rerun_of)});return false">${esc(r.rerun_of)}</a></span>`:''}
    </div>
    <div class="pbar" style="margin-bottom:12px"><div class="fill" style="width:${barPct}%;background:linear-gradient(90deg,var(--c-ok) ${pct}%,var(--c-err) 0)"></div></div>
    ${(r.has_combined_report || failed)?`<div class="bgrp" style="margin-bottom:12px">
      ${r.has_combined_report?`<button class="btn btn-sm btn-p" onclick="openReport('/results/${_curProj.id}/${runId}/combined/report.html','Combined Report')">${ico('chart')} Combined Report</button>
      <button class="btn btn-sm btn-o" onclick="openReport('/results/${_curProj.id}/${runId}/combined/log.html','Combined Log')">${ico('log')} Combined Log</button>`:''}
      ${(failed && r.status!=='running' && r.status!=='queued')
        ?`<button class="btn btn-sm btn-a" onclick="rerunFailed(${jsArg(runId)})">${ico('retry')} Re-run Failed (${failed})</button>`:''}
    </div>`:''}`;
  // Only touch the DOM when something changed — otherwise a 3s repaint kills
  // text selection and any :hover in the header.
  const head = document.getElementById('rd-head');
  if (head && head.dataset.sig !== html.length + ':' + barPct + ':' + r.status) {
    head.innerHTML = html;
    head.dataset.sig = html.length + ':' + barPct + ':' + r.status;
  }
  renderRunChips(r);
}

// Status filter. The counts come from a GROUP BY, so they describe the whole
// run — not just the page currently loaded.
function renderRunChips(r) {
  const el = document.getElementById('rd-chips');
  if (!el) return;
  const c = r.status_counts || {};
  const order = ['failed','passed','error','skipped','running','queued','cancelled'];
  const seen  = order.filter(s => c[s]).concat(
                  Object.keys(c).filter(s => order.indexOf(s) < 0));
  const icon  = { passed:'✓', failed:'✗' };
  const chips = [{ k:'', label:'All', n:r.item_count||0 }].concat(
    seen.map(s => ({ k:s, label:`${icon[s]||''} ${s}`.trim(), n:c[s] })));
  const html = chips.map(ch =>
    `<button class="rd-chip ${ch.k===_rd.status?'on':''} ${ch.k}"
             onclick="rdSetStatus(${jsArg(ch.k)})">${esc(ch.label)} <b>${ch.n}</b></button>`
  ).join('');
  if (el.dataset.sig !== html.length + ':' + _rd.status) {
    el.innerHTML = html;
    el.dataset.sig = html.length + ':' + _rd.status;
  }
}

function rdSetStatus(s) {
  if (!_rd || _rd.status === s) return;
  _rd.status = s;
  renderRunChips(_rd.run);
  loadRunItems(true);
}

function rdSearchInput(v) {
  if (!_rd) return;
  clearTimeout(_rdQTimer);
  // Debounced — typing "AUTH_TOKEN" should be one query, not eight.
  _rdQTimer = setTimeout(() => { if (_rd) { _rd.q = v; loadRunItems(true); } }, 300);
}

// reset=true starts a fresh page 1; otherwise append the next page.
async function loadRunItems(reset) {
  if (!_rd || _rd.busy) return;
  _rd.busy = true;
  const runId = _rd.runId;
  const offset = reset ? 0 : _rd.loaded;
  const qs = `?status=${encodeURIComponent(_rd.status)}&q=${encodeURIComponent(_rd.q)}`
           + `&offset=${offset}&limit=${RD_PAGE}`;
  try {
    const p = await api('GET', `/runs/${runId}/items${qs}`);
    if (!_rd || _rd.runId !== runId) return;   // modal closed or switched runs
    _rd.total  = p.total;
    _rd.items  = reset ? p.items : _rd.items.concat(p.items);
    _rd.loaded = _rd.items.length;
    if (reset) { _rd.open = {}; }
    renderRunItems();
  } catch(e) {
    toast(e.message,'e');
  } finally { if (_rd) _rd.busy = false; }
}

// Re-fetch exactly the rows already on screen, so a running run updates without
// collapsing what the reader has expanded or losing their place.
async function refreshRunItems() {
  if (!_rd || _rd.busy || !_rd.loaded) return;
  _rd.busy = true;
  const runId = _rd.runId, want = Math.min(_rd.loaded, 500);
  const qs = `?status=${encodeURIComponent(_rd.status)}&q=${encodeURIComponent(_rd.q)}`
           + `&offset=0&limit=${want}`;
  try {
    const p = await api('GET', `/runs/${runId}/items${qs}`);
    if (!_rd || _rd.runId !== runId) return;
    _rd.total  = p.total;
    _rd.items  = p.items;
    _rd.loaded = p.items.length;
    renderRunItems();
  } catch(e) { /* transient — the next count change retries */ }
  finally { if (_rd) _rd.busy = false; }
}

function rdItemRow(it) {
  const runId  = _rd.runId;
  const expand = it.status === 'failed';
  const open   = !!_rd.open[it.id];
  return `<div class="tc-rrow">
      ${expand?`<button class="rd-x" onclick="rdToggle(${it.id})"
                 title="${open?'Hide':'Show'} failure detail">${open?'▾':'▸'}</button>`
              :`<span class="rd-x-sp"></span>`}
      <span class="tc-code">${esc(it.tc_code||'')}</span>
      <span class="name">${esc(it.tc_name||'')}${
        (!open && it.fail_summary)?`<span class="rd-fs"> — ${esc(it.fail_summary)}</span>`:''}</span>
      <span class="sbadge ${it.status}">${it.status}</span>
      <div class="bgrp">
        ${it.has_report?`<button class="btn btn-sm btn-o" onclick="openReport(${jsArg(`/results/${_curProj.id}/${runId}/${it.rf_run_id}/report.html`)},${jsArg((it.tc_name||'')+' Report')})">Report</button>`:''}
        ${it.has_log   ?`<button class="btn btn-sm btn-o" onclick="openReport(${jsArg(`/results/${_curProj.id}/${runId}/${it.rf_run_id}/log.html`)},${jsArg((it.tc_name||'')+' Log')})">Log</button>`:''}
        ${it.status==='failed'?`<button class="btn btn-sm btn-a" onclick="openAIDebug(${jsArg(runId)},${jsArg(it.rf_run_id||'')},null,${jsArg(it.tc_name||'')})" title="AI-assisted debugging">${ico('robot')} Debug</button>`:''}
      </div>
    </div>${open?failureBlock(_rd.detail[it.id]):''}`;
}

function renderRunItems() {
  const box = document.getElementById('rd-items');
  if (!box) return;
  const html = _rd.items.length
    ? _rd.items.map(rdItemRow).join('')
    : `<div style="padding:26px;text-align:center;color:var(--c-muted);font-size:12px">
         No test cases match this filter.</div>`;
  if (box.dataset.sig !== html.length + ':' + _rd.items.length) {
    const scroller = document.querySelector('#modal-rundetail .modal');
    const top = scroller ? scroller.scrollTop : 0;
    box.innerHTML = html;
    box.dataset.sig = html.length + ':' + _rd.items.length;
    if (scroller && top) scroller.scrollTop = top;
  }
  const more = document.getElementById('rd-more');
  const left = _rd.total - _rd.loaded;
  more.innerHTML = _rd.total
    ? `<div class="rd-more">
         <span>Showing ${_rd.loaded} of ${_rd.total}</span>
         ${left>0?`<button class="btn btn-sm btn-o" onclick="loadRunItems(false)">
            Load ${Math.min(left, RD_PAGE)} more</button>`:''}
       </div>`
    : '';
}

// Expanding is what pulls fail_detail and the screenshot over the wire — one
// row's worth, not the whole run's.
async function rdToggle(id) {
  if (!_rd) return;
  if (_rd.open[id]) { delete _rd.open[id]; renderRunItems(); return; }
  _rd.open[id] = true;
  renderRunItems();
  if (!_rd.detail[id]) {
    try {
      const d = await api('GET', `/runs/${_rd.runId}/items/${id}`);
      if (!_rd) return;
      _rd.detail[id] = d;
      // Force the signature to differ — the row count did not change, only its
      // contents did.
      const box = document.getElementById('rd-items');
      if (box) box.dataset.sig = '';
      renderRunItems();
    } catch(e) { toast(e.message,'e'); }
  }
}

// Why a case failed, straight from output.xml — the point is that nobody has to
// open log.html and hunt for the red keyword to find out.
function failureBlock(it) {
  if (!it) return `<div class="failbox"><div class="fb-msg">Loading failure detail…</div></div>`;
  if (!(it.fail_summary || it.fail_detail))
    return `<div class="failbox"><div class="fb-msg">No failure detail was captured for this case — open the Log for the full trace.</div></div>`;
  // <img> cannot send the Authorization header, so the results route's
  // query-param token is the only way this loads — same trick as the report
  // iframe. Without it every thumbnail 401s.
  const shot = it.fail_screenshot
    ? `/results/${_curProj.id}/${_rd.runId}/${it.rf_run_id}/`
      + `${encodeURIComponent(it.fail_screenshot)}?token=${encodeURIComponent(_token||'')}`
    : null;
  return `<div class="failbox">
    <div class="fb-kw">${esc(it.fail_summary||'Failed')}</div>
    ${it.fail_detail?`<div class="fb-msg">${esc(it.fail_detail)}</div>`:''}
    ${shot?`<a href="${esc(shot)}" target="_blank" rel="noopener" title="Open full screenshot">
      <img class="fb-shot" src="${esc(shot)}" alt="Screenshot at failure"></a>`:''}
  </div>`;
}

function updateRunConsole(r) {
  const cons = document.getElementById('run-console');
  if (!cons) return;
  if (r.status === 'queued') {
    // Keep polling — it will flip to running when an execution slot frees up
    cons.textContent = 'Waiting for a free execution slot…\n\n'
      + 'BRACE limits how many suites run at once so the server is not overloaded. '
      + 'This run starts automatically as soon as a slot is available.';
  } else if (r.status === 'running') {
    if (!_rdStreaming) { cons.textContent=''; _rdStreaming = true; streamLog(_rd.runId, cons); }
  } else if (!cons.textContent) {
    cons.textContent = '(Run complete — open Report/Log links above)';
  }
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
      <span class="srow-num" style="color:var(--c-ok-text)">✓ ${r.passed}</span>
      <span class="srow-num" style="color:var(--c-err-text);min-width:44px">✗ ${r.failed}</span>
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
