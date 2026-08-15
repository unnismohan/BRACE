// BRACE v2 — testcases.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Suite path searchable dropdown ─────────────────────
let _suiteFiles = [];

async function loadSuiteFiles() {
  if (!_curProj) return;
  try { _suiteFiles = await api('GET', `/projects/${_curProj.id}/suites`); }
  catch { _suiteFiles = []; }
}

function filterSuites(q) {
  const dd = document.getElementById('suite-dropdown');
  const fl = q.toLowerCase();
  const matches = _suiteFiles.filter(s => !fl || s.toLowerCase().includes(fl));
  if (!matches.length) { dd.style.display='none'; return; }
  dd.innerHTML = '';
  matches.slice(0,100).forEach(s => {
    const item = document.createElement('div');
    item.style.cssText = 'padding:7px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid #f0f0f0;font-family:monospace';
    item.textContent = s;
    item.onmousedown = () => {
      document.getElementById('tc-suite').value = s;
      dd.style.display = 'none';
    };
    item.onmouseover = () => item.style.background = '#e8f0fe';
    item.onmouseout  = () => item.style.background = '';
    dd.appendChild(item);
  });
  dd.style.display = 'block';
}

function hideSuiteDD() {
  const dd = document.getElementById('suite-dropdown');
  if (dd) dd.style.display = 'none';
}

async function uploadFiles(input) {
  const fd = new FormData();
  for (const f of input.files) fd.append('files', f);
  try { const r = await api('POST', `/projects/${_curProj.id}/files/upload`, fd, true); toast('Uploaded: '+r.uploaded.join(', '),'s'); loadFiles(); }
  catch(e) { toast(e.message,'e'); }
  input.value='';
}

async function gitSync() {
  const log = document.getElementById('git-log');
  log.style.display='block'; log.textContent='Starting git sync…\n';
  try {
    const r = await fetch(`/api/projects/${_curProj.id}/git-sync`, { method:'POST', headers:{ Authorization:`Bearer ${_token}` } });
    const reader = r.body.getReader(); const dec = new TextDecoder();
    while(true) { const {done,value}=await reader.read(); if(done) break; log.textContent+=dec.decode(value,{stream:true}); log.scrollTop=log.scrollHeight; }
    toast('Git sync complete','s'); loadFiles();
  } catch(e) { toast(e.message,'e'); }
}

// Save a Blob to disk. The anchor MUST be in the document and the object URL
// must outlive the click — revoking it immediately silently kills the download
// for anything large enough that the browser hasn't started reading it yet.
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 60000);
}

async function downloadAllScripts(btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  toast('Preparing zip — this can take a moment…','i');
  try {
    const r = await fetch(`/api/projects/${_curProj.id}/files/download-all`,
      { headers: { Authorization:`Bearer ${_token}` } });
    if (r.status===401) { logout(); return; }
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    const blob = await r.blob();
    if (!blob.size) throw new Error('Server returned an empty file');
    const m = (r.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/);
    saveBlob(blob, m ? m[1] : `brace-${_curProj.name}-scripts.zip`);
    toast(`Downloaded ${(blob.size/1048576).toFixed(1)} MB`,'s');
  } catch(e) {
    toast('Download failed: ' + (e.message || 'server unreachable'), 'e');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬇'; }
  }
}

// ── Test Cases ─────────────────────────────────────────
async function openNewTCModal() {
  ['tc-name','tc-desc','tc-suite','tc-args','tc-tags'].forEach(id => document.getElementById(id).value='');
  resetTCBtn();
  await loadSuiteFiles();
  showModal('modal-newtc');
}

async function loadTCs() {
  try {
    _tcs = await api('GET', `/projects/${_curProj.id}/test-cases`);
    _tcPicked.clear();            // ids may no longer exist after a reload
    renderTCs();
    onChkChange();
  }
  catch(e) { toast(e.message,'e'); }
}

function tcClearFilters() {
  document.getElementById('tc-search').value = '';
  document.getElementById('tc-status').value = '';
  document.getElementById('tc-tag').value = '';
  renderTCs();
}

// Tags are stored ',smoke,wip,' — strip the wrapping commas for display.
function tcTagList(tc) {
  return (tc.tags || '').split(',').filter(Boolean);
}

// Populate the tag filter from the tags currently in use. Rebuilt only when the
// set changes, so a selection is not lost when the table re-renders.
function syncTCTagFilter() {
  const sel = document.getElementById('tc-tag');
  if (!sel) return;
  const names = [...new Set(_tcs.flatMap(tcTagList))].sort();
  if (sel.dataset.names === names.join(' ')) return;
  sel.dataset.names = names.join(' ');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All</option>';
  names.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = n;
    sel.appendChild(o);
  });
  sel.value = names.includes(cur) ? cur : '';
}

function renderTCs() {
  const tbody = document.getElementById('tc-tbody');
  const q      = (document.getElementById('tc-search')?.value || '').trim().toLowerCase();
  const status = document.getElementById('tc-status')?.value || '';
  syncTCTagFilter();
  const tag    = document.getElementById('tc-tag')?.value || '';

  let rows = _tcs;
  if (q) rows = rows.filter(tc =>
    (tc.name||'').toLowerCase().includes(q) ||
    (tc.tc_code||'').toLowerCase().includes(q) ||
    (tc.description||'').toLowerCase().includes(q) ||
    (tc.suite_path||'').toLowerCase().includes(q) ||
    (tc.tags||'').toLowerCase().includes(q));
  if (tag) rows = rows.filter(tc => tcTagList(tc).includes(tag));
  if (status) rows = rows.filter(tc =>
    status === 'never' ? !tc.last_run_status : tc.last_run_status === status);

  const active = !!(q || status || tag);
  const cnt = document.getElementById('tc-filter-count');
  if (cnt) cnt.textContent = active ? `${rows.length} of ${_tcs.length} test cases`
                                    : `${_tcs.length} test case${_tcs.length===1?'':'s'}`;
  const clr = document.getElementById('tc-clear');
  if (clr) clr.hidden = !active;

  tbody.innerHTML = '';
  if (!_tcs.length) { tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--c-muted)">No test cases yet.</td></tr>'; return; }
  if (!rows.length) { tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:32px;color:var(--c-muted)">No test cases match these filters.</td></tr>'; return; }
  rows.forEach(tc => {
    const tr = document.createElement('tr');
    const suiteTitle = tc.suite_path ? `Suite: ${tc.suite_path}` : 'No suite path set';
    tr.innerHTML=`
      <td><input type="checkbox" class="tc-chk" data-id="${tc.id}" ${_tcPicked.has(tc.id)?'checked':''} onchange="tcToggle(${tc.id},this.checked)"></td>
      <td><span class="tc-code">${esc(tc.tc_code||'—')}</span></td>
      <td class="tc-name-cell" title="${esc(suiteTitle)}">
        ${esc(tc.name)}${tc.suite_path?'<span class="tc-suite-dot" title="'+esc(suiteTitle)+'">🔗</span>':''}
      </td>
      <td class="tc-desc-cell" title="${esc(tc.description||'')}">${esc(tc.description||'—')}</td>
      <td>${tcTagList(tc).map(t =>
            `<span class="tagchip" title="Filter by ${esc(t)}" onclick="filterByTag(${jsArg(t)})">${esc(t)}</span>`
          ).join('') || '<span style="color:var(--c-muted)">—</span>'}</td>
      <td>${tc.last_run_status?`<span class="sbadge ${tc.last_run_status}">${tc.last_run_status}</span>`:'<span style="color:var(--c-muted)">—</span>'}</td>
      <td><div class="bgrp">
        <button class="bico" onclick="quickRunTC(${tc.id})" title="Run">▶</button>
        <button class="bico" onclick="openTCHistory(${tc.id})" title="Execution history">🕒</button>
        <button class="bico" onclick="editTC(${tc.id})"    title="Edit">✏️</button>
        <button class="bico" onclick="deleteTC(${tc.id})"  title="Delete">🗑</button>
      </div></td>`;
    tbody.appendChild(tr);
  });
}

// Selection is held outside the DOM so filtering the table cannot silently
// discard ticks the user already made.
function tcToggle(id, on) { on ? _tcPicked.add(id) : _tcPicked.delete(id); onChkChange(); }

function onChkChange() {
  const btn = document.getElementById('btn-runsel');
  btn.style.display = _tcPicked.size ? 'inline-flex' : 'none';
  btn.textContent = `▶ Run Selected (${_tcPicked.size})`;
  // header checkbox reflects only what is currently visible
  const vis = [...document.querySelectorAll('.tc-chk')];
  const all = document.getElementById('chk-all');
  if (all) {
    all.checked = vis.length > 0 && vis.every(c => c.checked);
    all.indeterminate = !all.checked && vis.some(c => c.checked);
  }
}

// Applies to the filtered view — "select all" means what you can see
function toggleAllTCs(v) {
  document.querySelectorAll('.tc-chk').forEach(c => {
    c.checked = v;
    const id = +c.dataset.id;
    v ? _tcPicked.add(id) : _tcPicked.delete(id);
  });
  onChkChange();
}

async function createTC() {
  const name = document.getElementById('tc-name').value.trim();
  if (!name) { toast('Name required','e'); return; }
  try {
    await api('POST', `/projects/${_curProj.id}/test-cases`, {
      name, description:document.getElementById('tc-desc').value.trim()||null,
      suite_path:document.getElementById('tc-suite').value.trim()||null,
      extra_args:document.getElementById('tc-args').value.trim()||null,
      tags:document.getElementById('tc-tags').value.trim(),
    });
    closeModal('modal-newtc'); toast('Test case created','s'); loadTCs();
    ['tc-name','tc-desc','tc-suite','tc-args','tc-tags'].forEach(id=>document.getElementById(id).value='');
  } catch(e) { toast(e.message,'e'); }
}

async function bulkUploadTCs() {
  const file = document.getElementById('tc-csvfile').files[0];
  if (!file) { toast('Select a CSV','e'); return; }
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await api('POST', `/projects/${_curProj.id}/test-cases/bulk-csv`, fd, true);
    let msg = `Created ${r.created} test cases`;
    if (r.suites_used)          msg += ` · linked to ${r.suites_used} suite(s)`;
    if (r.suites_created?.length) msg += ` · new: ${r.suites_created.join(', ')}`;
    toast(msg, 's');
    closeModal('modal-bulktc');
    loadTCs();
  } catch(e) { toast(e.message,'e'); }
}

async function deleteTC(id) {
  if (!await askConfirm('Delete Test Case', 'Delete this test case?\n\nThis cannot be undone.')) return;
  try { await api('DELETE', `/test-cases/${id}`); toast('Deleted','s'); loadTCs(); }
  catch(e) { toast(e.message,'e'); }
}

async function editTC(id) {
  const tc = _tcs.find(x=>x.id===id); if (!tc) return;
  document.getElementById('tc-name').value  = tc.name;
  document.getElementById('tc-desc').value  = tc.description||'';
  document.getElementById('tc-suite').value = tc.suite_path||'';
  document.getElementById('tc-args').value  = tc.extra_args||'';
  document.getElementById('tc-tags').value  = tcTagList(tc).join(' ');
  await loadSuiteFiles();
  showModal('modal-newtc');
  const btn = document.getElementById('btn-savetc');
  btn.textContent = 'Update'; btn.onclick = async () => {
    try {
      await api('PUT', `/test-cases/${id}`, {
        name:document.getElementById('tc-name').value,
        description:document.getElementById('tc-desc').value||null,
        suite_path:document.getElementById('tc-suite').value||null,
        extra_args:document.getElementById('tc-args').value||null,
        // Sent even when empty — '' is how a tester clears every tag, whereas
        // null would mean "leave them alone".
        tags:document.getElementById('tc-tags').value,
      });
      toast('Updated','s'); closeModal('modal-newtc'); resetTCBtn(); loadTCs();
    } catch(e) { toast(e.message,'e'); }
  };
}
function resetTCBtn() { const b=document.getElementById('btn-savetc'); b.textContent='Add Test Case'; b.onclick=createTC; }
document.getElementById('modal-newtc').addEventListener('click', e => { if(e.target.classList.contains('mclose')) resetTCBtn(); });

async function quickRunTC(id) {
  const tc = _tcs.find(x=>x.id===id);
  try {
    const r = await api('POST', `/projects/${_curProj.id}/runs`, { tc_ids:[id], run_name:`Quick — ${tc?.tc_code}` });
    toast(runStartMsg(r),'i'); switchTab('runs');
  } catch(e) { toast(e.message,'e'); }
}

function runSelectedTCs() {
  _preSel = [..._tcPicked];   // from the Set, so ticks hidden by a filter still count
  openRunModal();
}

// ── Per-test-case history ──────────────────────────────
async function openTCHistory(tcId) {
  document.getElementById('tch-title').textContent = 'Test Case History';
  document.getElementById('tch-stats').innerHTML = '<div class="picker-empty">Loading…</div>';
  document.getElementById('tch-strip-wrap').style.display = 'none';
  document.getElementById('tch-rows').innerHTML = '';
  showModal('modal-tchist');
  try {
    const d = await api('GET', `/test-cases/${tcId}/history?limit=50`);
    renderTCHistory(d);
  } catch(e) {
    document.getElementById('tch-stats').innerHTML =
      `<div class="picker-empty">Could not load history — ${esc(e.message)}</div>`;
  }
}

function renderTCHistory(d) {
  const s = d.stats, tc = d.test_case;
  document.getElementById('tch-title').textContent = `${tc.tc_code || ''} ${tc.name}`.trim();

  if (!s.executions) {
    document.getElementById('tch-stats').innerHTML =
      '<div class="picker-empty">This test case has never been executed.</div>';
    document.getElementById('tch-rows').innerHTML = '';
    return;
  }

  const rateCls = s.pass_rate >= 90 ? 'ok' : s.pass_rate >= 70 ? 'warn' : 'err';
  const stat = (label, val, cls='') =>
    `<div class="tch-stat ${cls}"><b>${val}</b><span>${label}</span></div>`;
  document.getElementById('tch-stats').innerHTML =
    stat('Pass rate', s.pass_rate + '%', rateCls) +
    stat('Executions', s.executions) +
    stat('Passed', s.passed, 'ok') +
    stat('Failed', s.failed, s.failed ? 'err' : '') +
    stat('Avg duration', fmtDur(s.avg_duration)) +
    stat('Current streak', `${s.streak}×`, s.streak_status === 'passed' ? 'ok' : 'err');

  // The verdict line — this is the point of the whole feature
  let banner = '';
  if (s.streak_status === 'failed' && s.streak === s.executions && s.executions > 2) {
    banner = `<div class="tch-banner err"><b>Never passed.</b> Failed all
      ${s.executions} recorded runs — likely a broken test or an unimplemented feature,
      rather than a regression.</div>`;
  } else if (s.streak_status === 'failed' && s.failing_since) {
    banner = `<div class="tch-banner err"><b>Failing since
      ${esc((s.failing_since||'').replace('T',' ').slice(0,16))}</b> —
      ${s.streak} consecutive failure(s) after previously passing. Look at what
      changed around that date.</div>`;
  } else if (s.passed && s.failed) {
    banner = `<div class="tch-banner warn"><b>Inconsistent.</b> ${s.passed} passed /
      ${s.failed} failed across ${s.executions} runs — treat results from this case
      with caution until it is stabilised.</div>`;
  } else if (s.streak_status === 'passed') {
    banner = `<div class="tch-banner ok"><b>Stable.</b> ${s.streak} consecutive pass(es).</div>`;
  }

  // Oldest left, newest right — reads like a timeline
  const strip = d.history.slice().reverse().map(h => {
    const when = (h.run_started||'').replace('T',' ').slice(0,16);
    return `<div class="tch-cell ${esc(h.status)}"
      title="${esc(h.run_name||h.run_id)} — ${esc(h.status)} — ${esc(when)}"
      onclick="closeModal('modal-tchist');viewRunDetail(${jsArg(h.run_id)})"></div>`;
  }).join('');
  document.getElementById('tch-strip-wrap').style.display = 'block';
  document.getElementById('tch-strip').innerHTML = strip;
  document.getElementById('tch-stats').insertAdjacentHTML('afterend', banner);

  document.getElementById('tch-rows').innerHTML = d.history.map(h => `
    <tr>
      <td><b>${esc(h.run_name || h.run_id)}</b><br>
          <span style="font-size:11px;color:var(--c-muted)">by ${esc(h.triggered_by||'—')}</span></td>
      <td><span class="sbadge ${esc(h.status)}">${esc(h.status)}</span></td>
      <td>${fmtDur(h.duration_sec)}</td>
      <td style="font-size:12px">${esc((h.run_started||'').replace('T',' ').slice(0,16))}</td>
      <td><div class="bgrp">
        <button class="bico" title="Open run" onclick="closeModal('modal-tchist');viewRunDetail(${jsArg(h.run_id)})">🔍</button>
        ${h.has_log?`<button class="bico" title="Robot log" onclick="openReport(${jsArg(`/results/${_curProj.id}/${h.run_id}/${h.rf_run_id}/log.html`)},${jsArg((tc.tc_code||'')+' log')})">📋</button>`:''}
      </div></td>
    </tr>`).join('');
}

async function rerunFailed(runId) {
  if (!await askConfirm('Re-run Failed',
      'Start a new run containing only the failed test cases from this run?',
      { okText: 'Re-run', danger: false })) return;
  try {
    const r = await api('POST', `/runs/${runId}/rerun-failed`, { include_cancelled: false });
    toast(runStartMsg(r), 'i');
    closeRunDetail();
    switchTab('runs');
    loadRuns();
  } catch(e) { toast(e.message, 'e'); }
}

// ── Groups ─────────────────────────────────────────────
async function loadGroups() {
  try { _groups = await api('GET', `/projects/${_curProj.id}/groups`); renderGroups(); }
  catch(e) { toast(e.message,'e'); }
}

let _grpOpen = {};   // group id → expanded? (collapsed by default)

function renderGroups() {
  const list = document.getElementById('groups-list');
  if (!_groups.length) {
    list.innerHTML='<div class="empty"><div class="eico">📋</div><p>No suites. Create one and add test cases.</p></div>';
    document.getElementById('grp-toolbar').style.display = 'none';
    return;
  }
  document.getElementById('grp-toolbar').style.display = 'flex';

  list.innerHTML = _groups.map(g => {
    const passC = g.test_cases.filter(t=>t.last_run_status==='passed').length;
    const failC = g.test_cases.filter(t=>t.last_run_status==='failed').length;
    const open  = _grpOpen[g.id] === true;
    return `
    <div class="rcard">
      <div class="rcard-hdr grp-hdr" onclick="toggleGroup(${g.id})" title="${open?'Collapse':'Expand'} suite">
        <span class="grp-caret ${open?'open':''}">▸</span>
        <div style="flex:1;min-width:0">
          <strong>${esc(g.name)}</strong>
          <span class="pill pill-total" style="margin-left:8px">${g.tc_count} TCs</span>
          ${passC?`<span class="pill pill-pass" style="margin-left:4px">✓ ${passC}</span>`:''}
          ${failC?`<span class="pill pill-fail" style="margin-left:4px">✗ ${failC}</span>`:''}
        </div>
        <div class="bgrp">
          <button class="btn btn-sm btn-a" onclick="event.stopPropagation();runGroup(${g.id})">▶ Run</button>
          <button class="btn btn-sm btn-o" onclick="event.stopPropagation();openGrpTCModal(${g.id})">＋ TCs</button>
          <button class="btn btn-sm btn-d" onclick="event.stopPropagation();deleteGroup(${g.id})">Delete</button>
        </div>
      </div>
      <div class="rcard-body grp-body" style="${open?'':'display:none'}">
        ${g.test_cases.length ? g.test_cases.map(tc=>`
          <div class="tc-rrow">
            <span class="tc-code">${esc(tc.tc_code||'')}</span>
            <span class="name">${esc(tc.name)}</span>
            ${tc.last_run_status?`<span class="sbadge ${tc.last_run_status}">${tc.last_run_status}</span>`:''}
            <button class="bico" onclick="rmTCFromGroup(${g.id},${tc.id})" title="Remove">✕</button>
          </div>`).join('') : '<div style="color:var(--c-muted);font-size:12px">No test cases added yet.</div>'}
      </div>
    </div>`;
  }).join('');
}

function toggleGroup(gid) {
  _grpOpen[gid] = !_grpOpen[gid];
  renderGroups();
}

function expandAllGroups(open) {
  _groups.forEach(g => { _grpOpen[g.id] = open; });
  renderGroups();
}

async function createGroup() {
  const name=document.getElementById('grp-name').value.trim();
  if (!name) { toast('Name required','e'); return; }
  try {
    await api('POST', `/projects/${_curProj.id}/groups`, { name, description:document.getElementById('grp-desc').value.trim()||null });
    closeModal('modal-newgrp'); toast('Suite created','s'); loadGroups();
    document.getElementById('grp-name').value=''; document.getElementById('grp-desc').value='';
  } catch(e) { toast(e.message,'e'); }
}

async function deleteGroup(id) {
  if (!await askConfirm('Delete Suite', 'Delete this suite?\n\nThe test cases inside it are not deleted.')) return;
  try { await api('DELETE', `/groups/${id}`); toast('Deleted','s'); loadGroups(); }
  catch(e) { toast(e.message,'e'); }
}

let _grpAvail = [];        // TCs not yet in the suite
let _grpPicked = new Set();  // survives filtering, so a search can't lose a tick

function openGrpTCModal(gid) {
  _grpId = gid;
  _grpPicked = new Set();
  const g = _groups.find(x=>x.id===gid);
  document.getElementById('grptcs-title').textContent = `Add TCs to "${g?.name || ''}"`;
  document.getElementById('grptc-search').value = '';
  const existIds = new Set(g?.test_cases.map(t=>t.id)||[]);
  _grpAvail = _tcs.filter(tc=>!existIds.has(tc.id));
  renderGrpTCList();
  showModal('modal-grptcs');
}

function grpTCVisible() {
  const q = (document.getElementById('grptc-search')?.value || '').trim().toLowerCase();
  return !q ? _grpAvail
            : _grpAvail.filter(tc => (tc.name||'').toLowerCase().includes(q) ||
                                     (tc.tc_code||'').toLowerCase().includes(q));
}

function renderGrpTCList() {
  const list = document.getElementById('grptc-list');
  const vis = grpTCVisible();
  if (!_grpAvail.length) {
    list.innerHTML = '<div class="picker-empty">All test cases are already in this suite.</div>';
  } else if (!vis.length) {
    list.innerHTML = '<div class="picker-empty">No test cases match that filter.</div>';
  } else {
    list.innerHTML = vis.map(tc => `
      <div class="tc-sitem">
        <input type="checkbox" class="grp-chk" id="gtc-${tc.id}" value="${tc.id}"
               ${_grpPicked.has(tc.id)?'checked':''} onchange="grpTCToggle(${tc.id},this.checked)">
        <label for="gtc-${tc.id}"><span class="tc-code">${esc(tc.tc_code||'')}</span> ${esc(tc.name)}</label>
      </div>`).join('');
  }
  grpTCCount();
}

function grpTCToggle(id, on) { on ? _grpPicked.add(id) : _grpPicked.delete(id); grpTCCount(); }

function grpTCSelectAll(on) {
  // Acts on what's currently filtered, which is what the user can see
  grpTCVisible().forEach(tc => on ? _grpPicked.add(tc.id) : _grpPicked.delete(tc.id));
  renderGrpTCList();
}

function grpTCCount() {
  const el = document.getElementById('grptc-count');
  if (!el) return;
  const total = _grpAvail.length, vis = grpTCVisible().length;
  el.textContent = `${_grpPicked.size} selected`
    + (vis !== total ? ` · showing ${vis} of ${total}` : ` of ${total}`);
}

async function addTCsToGroup() {
  const ids = [..._grpPicked];
  if (!ids.length) { toast('Select at least one test case','e'); return; }
  try {
    // One transactional call — the old loop fired a request per TC and could
    // leave the suite half-populated if one failed partway through.
    const r = await api('POST', `/groups/${_grpId}/test-cases/bulk`, { test_case_ids: ids });
    let msg = `Added ${r.added} test case(s)`;
    if (r.skipped_already_present) msg += ` · ${r.skipped_already_present} already present`;
    toast(msg,'s');
    closeModal('modal-grptcs');
    loadGroups();
  } catch(e) { toast(e.message,'e'); }
}

async function rmTCFromGroup(gid, tcId) {
  try { await api('DELETE', `/groups/${gid}/test-cases/${tcId}`); loadGroups(); }
  catch(e) { toast(e.message,'e'); }
}

async function runGroup(gid) {
  const g = _groups.find(x=>x.id===gid);
  try {
    const r = await api('POST', `/projects/${_curProj.id}/runs`, { group_id:gid, run_name:`${g?.name} run` });
    toast(runStartMsg(r),'i'); switchTab('runs');
  } catch(e) { toast(e.message,'e'); }
}

// Clicking a tag chip filters the table to that tag.
function filterByTag(tag) {
  const sel = document.getElementById('tc-tag');
  if (!sel) return;
  syncTCTagFilter();
  sel.value = tag;
  renderTCs();
}
