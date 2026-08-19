// BRACE v2 — core.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.


'use strict';
// ── State ──────────────────────────────────────────────
let _token     = localStorage.getItem('brace_token') || '';
let _user      = JSON.parse(localStorage.getItem('brace_user') || 'null');
let _projects  = [];
let _curProj   = null;
let _curTab    = 'scripts';
let _tcs       = [];
let _groups    = [];
let _runs      = [];
let _curFile   = null;
let _basePath  = '';
let _dirty     = false;
let _grpId     = null;
let _preSel    = [];
let _tcPicked  = new Set();   // ticked TC ids — kept out of the DOM so filtering keeps them
let _runsTimer = null;

// ── API ────────────────────────────────────────────────
async function api(method, path, body, isForm) {
  const opts = { method, headers: _token ? { Authorization: `Bearer ${_token}` } : {} };
  if (body && !isForm) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  else if (isForm) opts.body = body;

  let r;
  try {
    r = await fetch('/api' + path, opts);
  } catch (netErr) {
    // fetch only rejects for network-level failures — server down, DNS, offline.
    throw new Error('Cannot reach the BRACE server. Check your connection, '
                  + 'and that the service is still running.');
  }

  // A 401 means two completely different things depending on where it came from,
  // and both used to end up as `return undefined` — so the caller crashed on the
  // missing field ("Cannot read properties of undefined") instead of showing why.
  if (r.status === 401) {
    const detail = await r.json().catch(() => ({}));
    if (path === '/auth/login') {
      // Signing in: the credentials were wrong. Do not log out, there is no
      // session yet, and the user needs to see the reason.
      throw new Error(detail.detail === 'Invalid credentials'
        ? 'Incorrect username or password.'
        : (detail.detail || 'Incorrect username or password.'));
    }
    // Anywhere else: the token is missing, expired or no longer valid.
    logout();
    throw new Error('Your session has expired. Please sign in again.');
  }

  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    let msg = e.detail || r.statusText || `Request failed (${r.status})`;
    // FastAPI validation errors arrive as a list of objects, which stringify to
    // "[object Object]" — useless in a toast.
    if (Array.isArray(msg)) {
      msg = msg.map(x => (x && x.msg) ? `${(x.loc || []).slice(-1)[0]}: ${x.msg}` : String(x)).join('; ');
    } else if (typeof msg === 'object') {
      msg = JSON.stringify(msg);
    }
    if (r.status === 403) msg = msg || 'You do not have permission to do that.';
    if (r.status >= 500)  msg = `Server error: ${msg}`;
    throw new Error(msg);
  }

  if (r.status === 204) return null;

  // Guard the JSON parse. If something returns HTML where JSON was expected —
  // the SPA catch-all for a mistyped path, or a proxy/login portal in front of
  // BRACE — the raw failure is "Unexpected token '<'", which sends people
  // looking in entirely the wrong place.
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('json')) {
    throw new Error('The server returned an unexpected response for ' + path
                  + '. If BRACE sits behind a proxy or SSO gateway, check it is '
                  + 'not intercepting /api requests.');
  }
  try {
    return await r.json();
  } catch (parseErr) {
    throw new Error('The server sent a malformed response for ' + path + '.');
  }
}

// ── Utilities ──────────────────────────────────────────
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
// Safe JS string literal for embedding in an inline handler attribute.
// esc() alone is NOT enough there: the HTML parser decodes entities before the
// JS parser runs, so &#39; becomes a real quote and can still break out.
// JSON.stringify quotes/escapes the value, then esc() protects the attribute.
function jsArg(v) { return esc(JSON.stringify(v === undefined || v === null ? '' : String(v))); }
function timeAgo(iso) {
  const d = (Date.now() - new Date(iso)) / 1000;
  if (d < 60) return Math.round(d)+'s ago';
  if (d < 3600) return Math.round(d/60)+'m ago';
  if (d < 86400) return Math.round(d/3600)+'h ago';
  return Math.round(d/86400)+'d ago';
}
function toast(msg, type='i') {
  const el = document.createElement('div');
  el.className = `toast ${type}`; el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
function showView(n) { document.querySelectorAll('.view').forEach(v => v.classList.remove('active')); document.getElementById('view-'+n).classList.add('active'); }
function showModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('.overlay').forEach(o => o.addEventListener('click', e => { if (e.target===o) o.classList.remove('open'); }));

// ── Auth ───────────────────────────────────────────────
async function doLogin() {
  const u = document.getElementById('li-user').value.trim();
  const p = document.getElementById('li-pass').value;
  if (!u || !p) { liErr('Enter username and password'); return; }
  const btn = document.getElementById('btn-login');
  btn.disabled = true;
  liErr('');                      // clear any previous failure before retrying
  try {
    const r = await api('POST', '/auth/login', { username: u, password: p });
    // Defensive: api() throws on every error path, so this only trips if the
    // server ever returns 200 with an unexpected shape (a proxy, say). Better a
    // sentence than "Cannot read properties of undefined".
    if (!r || !r.access_token) {
      throw new Error('Signed in, but the server did not return a session token. '
                    + 'If there is a proxy in front of BRACE, check it is not '
                    + 'rewriting the response.');
    }
    _token = r.access_token; _user = { username: r.username, role: r.system_role };
    localStorage.setItem('brace_token', _token);
    localStorage.setItem('brace_user', JSON.stringify(_user));
    afterLogin(r);
  } catch(e) { liErr(e.message); } finally { btn.disabled = false; }
}
function liErr(msg) {
  const el = document.getElementById('li-err');
  el.textContent = msg || '';
  el.style.display = msg ? 'block' : 'none';
}

function afterLogin(r) {
  const name = r.username || _user.username;
  document.getElementById('user-display').textContent = name;
  document.getElementById('user-avatar').textContent  = name[0].toUpperCase();
  document.getElementById('li-err').style.display     = 'none';
  document.getElementById('sidebar').style.display      = 'flex';
  document.getElementById('sidebar').style.flexDirection = 'column';
  document.getElementById('user-controls').style.display = 'flex';
  if (_user.role === 'admin') {
    document.getElementById('admin-navitem').style.display = 'flex';
    document.getElementById('dash-newbtn').style.display   = 'inline-flex';
    document.getElementById('btn-new-proj').style.display  = 'block';
  } else {
    document.getElementById('btn-new-proj').style.display  = 'none';
  }
  if (r.must_change_password) showModal('modal-changepw');
  loadProjects();
  showView('dashboard');
}

function logout() {
  _token = ''; _user = null; _curProj = null;
  localStorage.removeItem('brace_token'); localStorage.removeItem('brace_user');
  clearInterval(_runsTimer);
  document.getElementById('sidebar').style.display       = 'none';
  document.getElementById('user-controls').style.display = 'none';
  showView('login');
}

async function changePassword() {
  try {
    await api('PUT', '/auth/change-password', { old_password: document.getElementById('cpw-old').value, new_password: document.getElementById('cpw-new').value });
    toast('Password updated', 's'); closeModal('modal-changepw');
    document.getElementById('cpw-old').value = ''; document.getElementById('cpw-new').value = '';
  } catch(e) { toast(e.message, 'e'); }
}

// ── Projects ───────────────────────────────────────────
async function loadProjects() {
  try { _projects = await api('GET', '/projects'); renderProjNav(); renderProjCards(); }
  catch(e) { toast(e.message, 'e'); }
}

function renderProjNav() {
  // Help/Admin live outside #project-nav, so clear their highlight explicitly
  ['help-navitem','admin-navitem'].forEach(id =>
    document.getElementById(id)?.classList.remove('active'));
  const nav = document.getElementById('project-nav');
  nav.innerHTML = '';
  _projects.forEach(p => {
    const d = document.createElement('div');
    d.className = 'nav-item' + (_curProj?.id === p.id ? ' active' : '');
    d.innerHTML = `<span class="ico">📁</span><span>${esc(p.name)}</span>`;
    d.onclick = () => openProject(p.id);
    nav.appendChild(d);
  });
}

function renderProjCards() {
  const c = document.getElementById('proj-cards');
  c.innerHTML = '';
  document.getElementById('no-projects').style.display = _projects.length ? 'none' : 'block';
  _projects.forEach(p => {
    const pct = p.tc_count ? Math.round(p.tc_passed/p.tc_count*100) : 0;
    const bar = p.tc_count ? `<div class="pbar"><div class="fill" style="width:${(p.tc_passed+p.tc_failed)/p.tc_count*100}%;background:linear-gradient(90deg,var(--c-ok) ${pct}%,var(--c-err) 0)"></div></div>` : '';
    const d = document.createElement('div');
    d.className = 'card';
    d.innerHTML = `
      <div class="card-title">${esc(p.name)}</div>
      <div class="card-meta">${esc(p.description||'No description')}</div>
      <div class="card-stats">
        <span class="pill pill-total">${p.tc_count} TCs</span>
        ${p.tc_passed ? `<span class="pill pill-pass">✓ ${p.tc_passed}</span>` : ''}
        ${p.tc_failed ? `<span class="pill pill-fail">✗ ${p.tc_failed}</span>` : ''}
        ${p.last_run_status==='running' ? `<span class="pill pill-run">Running…</span>` : ''}
      </div>
      ${bar}
      <div class="card-meta">
        <span>${p.last_run_at ? 'Last run: '+timeAgo(p.last_run_at) : 'No runs yet'}</span>
        ${p.has_git ? '<span>🔗 Git linked</span>' : ''}
      </div>`;
    d.onclick = () => openProject(p.id);
    c.appendChild(d);
  });
}

async function createProject() {
  const name = document.getElementById('np-name').value.trim();
  if (!name) { toast('Project name required', 'e'); return; }
  try {
    await api('POST', '/projects', {
      name, description: document.getElementById('np-desc').value.trim()||null,
      git_url:      document.getElementById('np-giturl').value.trim()||null,
      git_branch:   document.getElementById('np-gitbranch').value.trim()||'main',
      git_username: document.getElementById('np-gituser').value.trim()||null,
      git_token:    document.getElementById('np-gittoken').value||null,
    });
    closeModal('modal-newproj'); toast('Project created','s');
    ['np-name','np-desc','np-giturl','np-gituser','np-gittoken'].forEach(id => document.getElementById(id).value='');
    await loadProjects();
  } catch(e) { toast(e.message,'e'); }
}

function showDashboard() {
  _curProj = null; clearInterval(_runsTimer);
  renderProjNav(); showView('dashboard');
}

async function openProject(pid) {
  clearInterval(_runsTimer);
  _curProj = _projects.find(p => p.id === pid);
  if (!_curProj) { await loadProjects(); _curProj = _projects.find(p => p.id === pid); }
  document.getElementById('proj-title').textContent = _curProj.name;
  document.getElementById('bc-proj').textContent    = _curProj.name;
  // Team activity is project_admin-only — hide the tab for everyone else
  document.getElementById('tabt-team').style.display =
    _curProj.my_role === 'project_admin' ? '' : 'none';
  applyRoleUI();
  renderProjNav(); showView('project'); switchTab('scripts');
  api('GET', `/projects/${pid}/base-path`).then(r => { _basePath = r.base_path; }).catch(() => { _basePath = ''; });
}

// ── Role-aware UI ──────────────────────────────────────
// Mirrors ROLE_CAPS on the server. This only hides controls; every one of them
// is enforced server-side as well, so a stale or missing role here costs a
// confusing 403, never unauthorised access.
const ROLE_CAPS = {
  viewer:        ['view'],
  tester:        ['view', 'run', 'edit'],
  project_admin: ['view', 'run', 'edit', 'manage'],
};

function myCaps() {
  const role = (_curProj && _curProj.my_role) || '';
  // A system admin is project_admin everywhere, which is what the API returns —
  // but guard anyway so an unknown role degrades to read-only rather than
  // silently granting everything.
  return ROLE_CAPS[role] || (_user && _user.role === 'admin' ? ROLE_CAPS.project_admin : []);
}

function can(cap) { return myCaps().indexOf(cap) >= 0; }

function applyRoleUI() {
  const caps = myCaps();
  ['run', 'edit', 'manage'].forEach(c =>
    document.body.classList.toggle('no-' + c, caps.indexOf(c) < 0));
  const note = document.getElementById('role-note');
  if (note) {
    const viewer = !can('run') && !can('edit');
    note.style.display = viewer ? '' : 'none';
    if (viewer) note.innerHTML =
      '👁 <span>You have <b>Viewer</b> access to this project — you can read runs, '
      + 'reports and test cases, but not start runs or change anything.</span>';
  }
}

// ── Tabs ───────────────────────────────────────────────
function switchTab(name) {
  _curTab = name;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.getElementById('tabt-'+name).classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
  clearInterval(_runsTimer);
  if (name==='scripts')  loadFiles();
  if (name==='cases')    loadTCs();
  if (name==='groups')   loadGroups();
  if (name==='runs')     { loadRuns(); startRunsPoller(); }
  if (name==='reports')  { loadRuns().then(renderReports); }
  if (name==='team')     loadTeamActivity();
  if (name==='settings') loadSettings();
}
