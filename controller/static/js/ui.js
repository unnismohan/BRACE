// BRACE v2 — ui.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Themed prompt / confirm (replaces the browser's native dialogs) ──
document.body.insertAdjacentHTML('beforeend', `
  <div class="dlg-overlay" id="dlg-overlay">
    <div class="dlg">
      <div class="dlg-title" id="dlg-title"></div>
      <div class="dlg-msg" id="dlg-msg"></div>
      <input class="dlg-input" id="dlg-input" spellcheck="false" autocomplete="off">
      <div class="dlg-err" id="dlg-err"></div>
      <div class="dlg-btns">
        <button class="dlg-btn" id="dlg-cancel">Cancel</button>
        <button class="dlg-btn primary" id="dlg-ok">OK</button>
      </div>
    </div>
  </div>`);

let _dlgResolve = null;

function _dlgClose(value) {
  document.getElementById('dlg-overlay').classList.remove('open');
  const r = _dlgResolve; _dlgResolve = null;
  if (r) r(value);
}

let _dlgAllowSlash = false;

function _dlgOpen({ title, msg = '', input = false, value = '', placeholder = '',
                    okText = 'OK', danger = false, allowSlash = false }) {
  return new Promise(resolve => {
    _dlgResolve = resolve;
    _dlgAllowSlash = allowSlash;
    document.getElementById('dlg-title').textContent = title;
    const msgEl = document.getElementById('dlg-msg');
    msgEl.textContent = msg;
    msgEl.style.display = msg ? 'block' : 'none';
    document.getElementById('dlg-err').textContent = '';

    const inp = document.getElementById('dlg-input');
    inp.style.display = input ? 'block' : 'none';
    inp.value = value;
    inp.placeholder = placeholder;

    const ok = document.getElementById('dlg-ok');
    ok.textContent = okText;
    ok.classList.toggle('danger', danger);

    document.getElementById('dlg-overlay').classList.add('open');
    if (input) {
      inp.focus();
      // Preselect the basename so the extension survives a straight retype
      const dot = value.lastIndexOf('.');
      dot > 0 ? inp.setSelectionRange(0, dot) : inp.select();
    } else {
      ok.focus();
    }
  });
}

function askInput(title, opts = {}) {
  return _dlgOpen({ title, input: true, ...opts });
}
function askConfirm(title, msg, opts = {}) {
  return _dlgOpen({ title, msg, input: false, okText: 'Delete', danger: true, ...opts })
    .then(v => v !== null);
}

(function wireDialog() {
  const inp = document.getElementById('dlg-input');
  const submit = () => {
    if (inp.style.display !== 'none') {
      const v = inp.value.trim();
      const err = document.getElementById('dlg-err');
      if (!v) { err.textContent = 'Name cannot be empty'; return; }
      if (!_dlgAllowSlash && /[\\/]/.test(v)) {
        err.textContent = 'Name cannot contain / or \\'; return;
      }
      if (v.includes('..')) { err.textContent = 'Name cannot contain ".."'; return; }
      _dlgClose(v);
    } else _dlgClose(true);
  };
  document.getElementById('dlg-ok').onclick = submit;
  document.getElementById('dlg-cancel').onclick = () => _dlgClose(null);
  document.getElementById('dlg-overlay').onclick = e => {
    if (e.target.id === 'dlg-overlay') _dlgClose(null);
  };
  inp.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
    if (e.key === 'Escape') { e.preventDefault(); _dlgClose(null); }
  };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && _dlgResolve) _dlgClose(null);
  });
})();

// ── AI Debug Assistant ─────────────────────────────────
let _aidReq = null, _aidPrompt = '', _aidAbort = null;

// ── Analysis cache — avoid re-spending tokens on a debug already run ──
// Keyed per project+run+item, persisted in localStorage so it survives reloads.
function aidCacheKey(req) {
  return `brace-aid-cache:${_curProj.id}:${req.run_id}:${req.rf_run_id||''}:${req.suite_path||''}`;
}
function aidCacheGet(req) {
  try { return JSON.parse(localStorage.getItem(aidCacheKey(req)) || 'null'); } catch { return null; }
}
function aidCacheSet(req, text) {
  try {
    localStorage.setItem(aidCacheKey(req), JSON.stringify({ text, at: new Date().toISOString() }));
  } catch { /* storage full or unavailable — analysis still shown, just not cached */ }
}

async function openAIDebug(runId, rfRunId, suitePath, label) {
  _aidReq = { run_id: runId, rf_run_id: rfRunId || null, suite_path: suitePath || null };
  _aidPrompt = '';
  document.getElementById('aid-output').innerHTML = '<span style="color:#585b70">Gathering failure context…</span>';
  document.getElementById('aid-prompt').value = '';
  document.getElementById('aid-meta').innerHTML = `<span>${esc(label || runId)}</span>`;
  aidSwitch('analyze');
  showModal('modal-aidebug');

  try {
    const r = await api('POST', `/projects/${_curProj.id}/ai-debug/prompt`, _aidReq);
    _aidPrompt = r.prompt;
    document.getElementById('aid-prompt').value = r.prompt;
    document.getElementById('aid-prompt-size').textContent =
      `${(r.prompt.length/1024).toFixed(1)} KB · paste into any AI chat`;
    let meta = `<span>${esc(label || runId)}</span>`;
    if (r.suite_path) meta += `<code>${esc(r.suite_path)}</code>`;
    if (!r.has_failures) meta += `<span style="color:var(--c-warn)">⚠ no parsed failures in output.xml</span>`;
    if (r.model) meta += `<span>model: <code>${esc(r.model)}</code></span>`;
    document.getElementById('aid-meta').innerHTML = meta;

    document.getElementById('aid-unavailable').style.display = r.ai_available ? 'none' : 'block';
    if (r.ai_available) aidAnalyze(false);
    else {
      document.getElementById('aid-output').innerHTML = '';
      aidSwitch('prompt');
    }
  } catch(e) {
    document.getElementById('aid-output').textContent = 'Failed to build context: ' + e.message;
  }
}

function aidSwitch(pane) {
  document.getElementById('aid-tab-analyze').classList.toggle('active', pane === 'analyze');
  document.getElementById('aid-tab-prompt').classList.toggle('active', pane === 'prompt');
  document.getElementById('aid-pane-analyze').style.display = pane === 'analyze' ? 'flex' : 'none';
  document.getElementById('aid-pane-prompt').style.display  = pane === 'prompt'  ? 'flex' : 'none';
}

function aidCachedBanner(at) {
  const when = new Date(at).toLocaleString();
  return `<div class="aid-cached-banner">
    <span>📦 Showing a saved analysis from <b>${esc(when)}</b> — no API call made.</span>
    <button class="etbtn" style="color:#89b4fa;font-weight:700" onclick="aidAnalyze(true)">🔄 Re-run Analysis</button>
  </div>`;
}

async function aidAnalyze(force) {
  const out = document.getElementById('aid-output');

  if (!force) {
    const cached = aidCacheGet(_aidReq);
    if (cached) {
      out.classList.remove('aid-cursor');
      out.innerHTML = aidCachedBanner(cached.at) + `<div class="aid-body">${aidMarkdown(cached.text)}</div>`;
      return;
    }
  }

  out.classList.remove('aid-cursor');
  out.innerHTML = `<div class="aid-loading">
      <div class="aid-spinner"></div>
      <div>Sending failure context to the model…</div>
      <div style="font-size:11.5px;color:#6c7086">This can take up to a minute for a full trace.</div>
    </div>`;

  let raw = '';
  let firstChunk = true;
  try {
    _aidAbort = new AbortController();
    const r = await fetch(`/api/projects/${_curProj.id}/ai-debug/analyze`, {
      method:'POST',
      headers:{ 'Content-Type':'application/json', Authorization:`Bearer ${_token}` },
      body: JSON.stringify(_aidReq),
      signal: _aidAbort.signal,
    });
    if (!r.ok) { out.textContent = await r.text(); return; }
    const reader = r.body.getReader(), dec = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = dec.decode(value, { stream:true });
      if (!chunk) continue;
      if (firstChunk) { out.innerHTML = ''; out.classList.add('aid-cursor'); firstChunk = false; }
      raw += chunk;
      out.innerHTML = aidMarkdown(raw);
      out.scrollTop = out.scrollHeight;
    }
    if (raw.trim() && !raw.includes('[AI ERROR')) aidCacheSet(_aidReq, raw);
  } catch(e) {
    if (e.name !== 'AbortError') { if (firstChunk) out.innerHTML = ''; out.textContent += '\n[error] ' + e.message; }
  } finally {
    out.classList.remove('aid-cursor');
    _aidAbort = null;
  }
}

// Minimal markdown → HTML. Fenced code first so its content is never re-parsed.
function aidMarkdown(md) {
  const blocks = [];
  // Pull fenced code out first so its contents are never treated as markdown.
  // The sentinel uses chars esc() leaves alone and that models don't emit.
  let s = md.replace(/```(\w*)\n?([\s\S]*?)(?:```|$)/g, (_, lang, code) => {
    blocks.push('<pre>' + esc(code.replace(/\n$/, '')) + '</pre>');
    return '@@BRACEBLOCK' + (blocks.length - 1) + '@@';
  });
  s = esc(s)
    .replace(/^#{3,6} (.+)$/gm, '<h3>$1</h3>')
    .replace(/^#{1,2} (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>');
  return s.replace(/@@BRACEBLOCK(\d+)@@/g, (_, i) => blocks[+i] ?? '');
}

function aidCopyPrompt() {
  if (!_aidPrompt) return;
  copyToClipboard(_aidPrompt).then(
    () => toast('Prompt copied — paste into any AI chat', 's'),
    () => { document.getElementById('aid-prompt').select(); toast('Select-all done — press Ctrl+C', 'i'); }
  );
}

function aidDownloadPrompt() {
  if (!_aidPrompt) return;
  saveBlob(new Blob([_aidPrompt], { type:'text/markdown' }),
           `brace-debug-${_aidReq.rf_run_id || _aidReq.run_id}.md`);
}
