// BRACE v2 — scripts.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Syntax highlight ───────────────────────────────────
function highlight(code, ext) {
  // escape HTML first
  let s = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  if (['.robot','.resource'].includes(ext)) {
    // sections
    s = s.replace(/(^\*{3}[\w\s]+\*{3}.*$)/gm, '<span class="tok-sect">$1</span>');
    // comments
    s = s.replace(/(#.*)$/gm, '<span class="tok-cmt">$1</span>');
    // variables ${} @{} &{}
    s = s.replace(/(\$\{[^}]+\}|@\{[^}]+\}|&\{[^}]+\})/g, '<span class="tok-var">$1</span>');
    // tags/settings in brackets
    s = s.replace(/(\[[A-Za-z ]+\])/g, '<span class="tok-tag">$1</span>');
    // RF built-in keywords (common ones)
    s = s.replace(/\b(Log|Should Be Equal|Should Contain|Should Not Contain|Should Be True|Should Be False|Fail|Pass Execution|Run Keyword|Run Keyword If|Set Variable|Get Variable Value|Import Library|Import Resource|FOR|IN|IN RANGE|IF|ELSE IF|ELSE|END|TRY|EXCEPT|FINALLY|WHILE|BREAK|CONTINUE|Return From Keyword|Sleep|Wait Until|Evaluate)\b/g, '<span class="tok-bkw">$1</span>');
  } else if (['.py'].includes(ext)) {
    s = s.replace(/(#.*)$/gm, '<span class="tok-cmt">$1</span>');
    s = s.replace(/\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|in|not|and|or|is|None|True|False|pass|break|continue|raise|yield|lambda|async|await)\b/g, '<span class="tok-kw">$1</span>');
    s = s.replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')/g, '<span class="tok-str">$1</span>');
    s = s.replace(/\b(\d+\.?\d*)\b/g, '<span class="tok-num">$1</span>');
  } else if (['.yaml','.yml'].includes(ext)) {
    s = s.replace(/(#.*)$/gm, '<span class="tok-cmt">$1</span>');
    s = s.replace(/^(\s*[\w-]+):/gm, '<span class="tok-tag">$1</span>:');
    s = s.replace(/: (.+)$/gm, ': <span class="tok-str">$1</span>');
  }
  return s;
}

function updateEditor(content) {
  const pre   = document.getElementById('editor-pre');
  const linos = document.getElementById('editor-linenos');
  const ext   = _curFile ? _curFile.split('.').pop().toLowerCase() : '';
  const lines = (content+'\n').split('\n');
  linos.textContent = lines.map((_,i)=>i+1).join('\n');
  pre.innerHTML = highlight(content, '.'+ext) + '\n';
  document.getElementById('editor-linecount').textContent = `${lines.length-1} lines · ${content.length} chars`;
  // status bar lang
  const langMap = {robot:'Robot Framework',resource:'RF Resource',py:'Python',yaml:'YAML',yml:'YAML',txt:'Text',csv:'CSV'};
  document.getElementById('sb-lang').textContent = langMap[ext] || ext.toUpperCase() || '';
}

function updateCursorPos() {
  const ta = document.getElementById('editor-area');
  const txt = ta.value.slice(0, ta.selectionStart);
  const ln  = txt.split('\n').length;
  const col = txt.split('\n').pop().length + 1;
  document.getElementById('sb-position').textContent = `Ln ${ln}, Col ${col}`;
}

function onEditorInput() {
  _dirty = true;
  const content = document.getElementById('editor-area').value;
  updateEditor(content);
  // dot indicator on tab
  if (_curFile) {
    const fn = document.getElementById('editor-filename');
    const base = _curFile.split('/').pop();
    fn.innerHTML = `<span title="${esc(_curFile)}">● ${esc(base)}</span>`;
  }
}

function syncScroll() {
  const area = document.getElementById('editor-area');
  const pre  = document.getElementById('editor-pre');
  const lino = document.getElementById('editor-linenos');
  pre.scrollTop  = area.scrollTop;
  pre.scrollLeft = area.scrollLeft;
  lino.scrollTop = area.scrollTop;
}

function onEditorKey(e) {
  // Tab → insert 4 spaces
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const s = ta.selectionStart, en = ta.selectionEnd;
    ta.value = ta.value.slice(0,s) + '    ' + ta.value.slice(en);
    ta.selectionStart = ta.selectionEnd = s + 4;
    onEditorInput();
  }
  // Ctrl+S → save
  if ((e.ctrlKey||e.metaKey) && e.key==='s') { e.preventDefault(); saveFile(); }
}

// ── File tree ──────────────────────────────────────────
let _treeData = {};

// Build nested tree from flat {folderPath:[files]} API response
function buildNestedTree(flat) {
  const root = { __dirs: {}, __files: [] };
  Object.keys(flat).sort().forEach(folder => {
    const files = flat[folder];
    let node = root;
    if (folder) {
      folder.split('/').forEach(part => {
        if (!node.__dirs[part]) node.__dirs[part] = { __dirs: {}, __files: [] };
        node = node.__dirs[part];
      });
    }
    files.forEach(f => node.__files.push(f));
  });
  return root;
}

function renderTreeNode(node, depth, filter, openState, parentPath) {
  parentPath = parentPath || '';
  let html = '';
  const indent = depth * 16;
  // render dirs first
  Object.keys(node.__dirs).sort().forEach(name => {
    const child = node.__dirs[name];
    const folderPath = parentPath ? parentPath + '/' + name : name;
    // Key on the full path — name+depth collides (e.g. APIGw/Resources vs UPC/Resources)
    const key = 'td_' + folderPath;
    const isOpen = openState[key] === true; // default collapsed
    const arrowCls = isOpen ? 'open' : 'closed';
    const folderIco = isOpen ? '📂' : '📁';
    html += `<div class="tree-row folder-row" data-key="${esc(key)}" data-path="${esc(folderPath)}" onclick="toggleTreeDir(this)" oncontextmenu="return showPathMenu(event,this)">`;
    html += `<span style="width:${indent}px" class="tree-indent"></span>`;
    html += `<span class="tree-arrow ${arrowCls}">▾</span>`;
    html += `<span class="tree-icon">${folderIco}</span>`;
    html += `<span class="tree-label">${esc(name)}</span>`;
    html += `</div>`;
    html += `<div class="tree-children${isOpen ? '' : ' closed'}" data-key="${esc(key)}">`;
    html += renderTreeNode(child, depth + 1, filter, openState, folderPath);
    html += `</div>`;
  });
  // render files
  node.__files.forEach(f => {
    if (filter && !f.name.toLowerCase().includes(filter)) return;
    const ext = f.name.split('.').pop().toLowerCase();
    const active = f.path === _curFile ? ' active' : '';
    const ico = ext === 'robot' ? '🤖' : ext === 'py' ? '🐍' : ext === 'xlsx' || ext === 'csv' ? '📊' : ext === 'yaml' || ext === 'yml' ? '⚙' : '📄';
    html += `<div class="tree-row ext-${esc(ext)}${active}" data-path="${esc(f.path)}" onclick="openFile(this.dataset.path)" oncontextmenu="return showPathMenu(event,this)" title="${esc(f.path)}">`;
    html += `<span style="width:${indent}px" class="tree-indent"></span>`;
    html += `<span class="tree-arrow" style="visibility:hidden">▾</span>`;
    html += `<span class="tree-icon">${ico}</span>`;
    html += `<span class="tree-label">${esc(f.name)}</span>`;
    html += `</div>`;
  });
  return html;
}

let _treeOpenState = {};

function renderFileTree(tree, filter='') {
  _treeData = tree;
  const panel = document.getElementById('file-tree');
  if (!Object.keys(tree).length) {
    panel.innerHTML='<div style="padding:12px;font-size:12px;color:var(--c-muted)">No files. Upload or Git sync.</div>';
    return;
  }
  const nested = buildNestedTree(tree);
  const fl = filter.toLowerCase();
  panel.innerHTML = renderTreeNode(nested, 0, fl, _treeOpenState);
}

function toggleTreeDir(row) {
  const key = row.dataset.key;
  const childEl = row.nextElementSibling;
  const arrow = row.querySelector('.tree-arrow');
  const icon = row.querySelector('.tree-icon');
  const isOpen = !childEl.classList.contains('closed');
  childEl.classList.toggle('closed', isOpen);
  arrow.classList.toggle('open', !isOpen);
  arrow.classList.toggle('closed', isOpen);
  icon.textContent = isOpen ? '📁' : '📂';
  _treeOpenState[key] = !isOpen;
}

// ── Right-click "Copy Path" menu ────────────────────────
let _pathMenuTarget = null;

let _pathMenuIsDir = false;

function showPathMenu(evt, row) {
  evt.preventDefault();
  evt.stopPropagation();
  _pathMenuTarget = row.dataset.path;
  _pathMenuIsDir  = row.classList.contains('folder-row');
  const name = _pathMenuTarget.split('/').pop();

  let items = '';
  if (_pathMenuIsDir) {
    items += `<div class="path-menu-item" onclick="fsNewFile()">📄 New File here…</div>`;
    items += `<div class="path-menu-item" onclick="fsNewFolder()">📁 New Folder here…</div>`;
    items += `<div class="path-menu-sep"></div>`;
  }
  items += `<div class="path-menu-item" onclick="fsRename()">✏️ Rename…</div>`;
  items += `<div class="path-menu-item danger" onclick="fsDelete()">🗑 Delete ${_pathMenuIsDir ? 'Folder' : ''}</div>`;
  items += `<div class="path-menu-sep"></div>`;
  items += `<div class="path-menu-item" onclick="copyPath(false)">🔗 Copy Relative Path</div>`;
  items += `<div class="path-menu-item" onclick="copyPath(true)">💽 Copy Full Container Path</div>`;

  const menu = document.getElementById('path-menu');
  menu.innerHTML = `<div class="path-menu-hd">${esc(name)}</div>` + items;
  menu.style.display = 'block';
  menu.style.left = Math.min(evt.clientX, window.innerWidth  - 250) + 'px';
  menu.style.top  = Math.min(evt.clientY, window.innerHeight - menu.offsetHeight - 12) + 'px';
  return false;
}

function hidePathMenu() { document.getElementById('path-menu').style.display = 'none'; }

// Right-click on empty tree space → create at project root
function showRootMenu(evt) {
  if (evt.target.closest('.tree-row')) return true;   // row handler owns it
  evt.preventDefault();
  evt.stopPropagation();
  _pathMenuTarget = '';
  _pathMenuIsDir  = true;
  const menu = document.getElementById('path-menu');
  menu.innerHTML =
    '<div class="path-menu-hd">Project root</div>' +
    '<div class="path-menu-item" onclick="fsNewFile()">📄 New File…</div>' +
    '<div class="path-menu-item" onclick="fsNewFolder()">📁 New Folder…</div>';
  menu.style.display = 'block';
  menu.style.left = Math.min(evt.clientX, window.innerWidth  - 250) + 'px';
  menu.style.top  = Math.min(evt.clientY, window.innerHeight - menu.offsetHeight - 12) + 'px';
  return false;
}

// ── Explorer file operations ────────────────────────────
async function fsNewFile() {
  hidePathMenu();
  const dir = _pathMenuIsDir ? _pathMenuTarget : _pathMenuTarget.split('/').slice(0,-1).join('/');
  const name = await askInput('New File', {
    msg: dir ? `in ${dir}/` : 'at project root',
    placeholder: 'TC_010_Login.robot', okText: 'Create',
  });
  if (!name) return;
  const path = dir ? `${dir}/${name.trim()}` : name.trim();
  try {
    await api('PUT', `/projects/${_curProj.id}/files/${path}`, { content: '' });
    toast('Created ' + path, 's');
    await loadFiles();
    openFile(path);
  } catch(e) { toast(e.message, 'e'); }
}

async function fsNewFolder() {
  hidePathMenu();
  const dir = _pathMenuIsDir ? _pathMenuTarget : _pathMenuTarget.split('/').slice(0,-1).join('/');
  const name = await askInput('New Folder', {
    msg: dir ? `in ${dir}/` : 'at project root',
    placeholder: 'Testcases', okText: 'Create',
  });
  if (!name) return;
  const path = dir ? `${dir}/${name.trim()}` : name.trim();
  try {
    await api('POST', `/projects/${_curProj.id}/fs/mkdir`, { path });
    toast('Created folder ' + path, 's');
    if (dir) _treeOpenState['td_' + dir] = true;   // reveal the new folder
    _treeOpenState['td_' + path] = true;
    loadFiles();
  } catch(e) { toast(e.message, 'e'); }
}

async function fsRename() {
  hidePathMenu();
  const old = _pathMenuTarget;
  const parts = old.split('/');
  const cur = parts.pop();
  const name = await askInput(_pathMenuIsDir ? 'Rename Folder' : 'Rename File', {
    value: cur, okText: 'Rename',
  });
  if (!name || name.trim() === cur) return;
  const newPath = [...parts, name.trim()].join('/');
  try {
    await api('POST', `/projects/${_curProj.id}/fs/rename`, { old_path: old, new_path: newPath });
    toast(`Renamed to ${name.trim()}`, 's');
    if (_curFile === old) { _curFile = newPath; _dirty = false; }
    await loadFiles();
    if (_curFile === newPath) openFile(newPath);
  } catch(e) { toast(e.message, 'e'); }
}

async function fsDelete() {
  hidePathMenu();
  const target = _pathMenuTarget;
  const ok = await askConfirm(
    _pathMenuIsDir ? 'Delete Folder' : 'Delete File',
    _pathMenuIsDir
      ? `Delete "${target}" and everything inside it?\n\nThis cannot be undone.`
      : `Delete "${target}"?\n\nThis cannot be undone.`);
  if (!ok) return;
  try {
    if (_pathMenuIsDir) {
      const r = await api('DELETE', `/projects/${_curProj.id}/fs/rmdir/${target}`);
      toast(`Deleted folder (${r.files_removed} file(s))`, 's');
    } else {
      await api('DELETE', `/projects/${_curProj.id}/files/${target}`);
      toast('Deleted ' + target, 's');
    }
    if (_curFile === target || (_pathMenuIsDir && _curFile && _curFile.startsWith(target + '/'))) {
      _curFile = null; _dirty = false;
      document.getElementById('editor-area').value = '';
      document.getElementById('editor-filename').className = 'editor-no-tab';
      document.getElementById('editor-filename').textContent = 'No file open';
      ['btn-savefile','btn-delfile','btn-runfile','et-sep1','et-sep2'].forEach(id => {
        const el = document.getElementById(id); if (el) el.style.display = 'none';
      });
    }
    loadFiles();
  } catch(e) { toast(e.message, 'e'); }
}
document.addEventListener('click', hidePathMenu);
document.addEventListener('scroll', hidePathMenu, true);

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  // fallback for http:// non-localhost contexts where clipboard API is blocked
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try {
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      ok ? resolve() : reject();
    } catch(e) { document.body.removeChild(ta); reject(e); }
  });
}

function copyPath(full) {
  if (!_pathMenuTarget) return;
  const text = full ? `${_basePath}/${_pathMenuTarget}` : _pathMenuTarget;
  copyToClipboard(text).then(
    () => toast('Copied: ' + text, 's'),
    () => toast('Copy failed — select and copy manually: ' + text, 'e')
  );
  hidePathMenu();
}

function filterTree(q) { renderFileTree(_treeData, q); }

async function loadFiles() {
  try {
    const tree = await api('GET', `/projects/${_curProj.id}/files`);
    renderFileTree(tree);
    document.getElementById('btn-newfile').style.display = 'inline-flex';
  } catch(e) { toast(e.message,'e'); }
}

async function openFile(path) {
  if (_dirty && !await askConfirm('Unsaved Changes',
      `"${_curFile}" has unsaved edits.\n\nDiscard them and open the other file?`,
      { okText: 'Discard' })) return;
  const ext = path.split('.').pop().toLowerCase();
  if (ext === 'xlsx' || ext === 'csv') { openExcelFile(path); return; }
  if (_excelMode) resetExcelEditor();
  try {
    const opts = { method:'GET', headers: _token ? { Authorization:`Bearer ${_token}` } : {} };
    const r = await fetch(`/api/projects/${_curProj.id}/files/${path}`, opts);
    // Raw fetch (text/blob), so api()'s handling does not apply — say why
    // before dropping the user back to the login screen.
    if (r.status===401) { toast('Your session has expired. Please sign in again.','e'); logout(); return; }
    if (!r.ok) throw new Error(await r.text());
    const content = await r.text();
    _curFile = path; _dirty = false;
    const area = document.getElementById('editor-area');
    area.value = content;
    area.onclick = area.onkeyup = updateCursorPos;
    // filename tab styling
    const fn = document.getElementById('editor-filename');
    fn.className = 'editor-open-tab';
    fn.innerHTML = `<span title="${esc(path)}">${esc(path.split("/").pop())}</span>`;
    document.getElementById('btn-savefile').style.display = 'inline-flex';
    document.getElementById('et-sep1').style.display      = 'inline-block';
    const isRobot = path.endsWith('.robot') || path.endsWith('.resource');
    document.getElementById('btn-runfile').style.display  = isRobot ? 'inline-flex' : 'none';
    document.getElementById('et-sep2').style.display      = isRobot ? 'inline-block' : 'none';
    document.getElementById('btn-newfile').style.display  = 'inline-flex';
    document.getElementById('btn-delfile').style.display  = 'inline-flex';
    updateEditor(content);
    updateCursorPos();
    document.querySelectorAll('.tree-row[data-path]').forEach(el => el.classList.toggle('active', el.dataset.path===path));
  } catch(e) { toast(e.message,'e'); }
}

// ── Quick run from editor ─────────────────────────────
let _qrunAbort = null;
let _qrunLastId = null;

async function runEditorFile() {
  if (!_curFile || !_curProj) return;
  if (_dirty) {
    if (!await askConfirm('Unsaved Changes',
        'This file has unsaved edits. Save before running?',
        { okText: 'Save & Run', danger: false })) return;
    await saveFile();
  }
  const btn = document.getElementById('btn-runfile');
  const panel = document.getElementById('qrun-panel');
  const cons = document.getElementById('qrun-console');
  const status = document.getElementById('qrun-status');
  btn.classList.add('running');
  btn.textContent = '⏳ Running…';
  btn.disabled = true;
  cons.textContent = '';
  status.textContent = 'running';
  status.style.color = '#89b4fa';
  document.getElementById('btn-qrun-debug').style.display = 'none';
  panel.style.display = 'flex';
  panel.scrollIntoView({ behavior:'smooth', block:'nearest' });

  try {
    _qrunAbort = new AbortController();
    const r = await fetch(`/api/projects/${_curProj.id}/quick-run`, {
      method: 'POST',
      headers: { 'Content-Type':'application/json', Authorization:`Bearer ${_token}` },
      body: JSON.stringify({ suite_path: _curFile }),
      signal: _qrunAbort.signal
    });
    if (!r.ok) { cons.textContent = await r.text(); return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let all = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = dec.decode(value, { stream:true });
      all += chunk;
      cons.textContent += chunk;
      cons.scrollTop = cons.scrollHeight;
    }
    const passed = /\[BRACE\] Exit code: 0 — PASS/.test(all);
    status.textContent = passed ? '✓ PASS' : '✗ FAIL';
    status.style.color = passed ? '#a6e3a1' : '#f38ba8';
    // Offer AI debugging on failure — pull the quick-run id out of the report line
    const dbgBtn = document.getElementById('btn-qrun-debug');
    const m = all.match(/\[BRACE\] Report: \/results\/\d+\/([^/]+)\//);
    if (!passed && m) {
      _qrunLastId = m[1];
      dbgBtn.style.display = 'inline-flex';
    } else {
      dbgBtn.style.display = 'none';
    }
  } catch(e) {
    if (e.name !== 'AbortError') { status.textContent = 'error'; cons.textContent += '\n'+e.message; }
  } finally {
    btn.classList.remove('running');
    btn.textContent = '▶ Run';
    btn.disabled = false;
    _qrunAbort = null;
  }
}

function closeQrun() {
  if (_qrunAbort) { _qrunAbort.abort(); }
  document.getElementById('qrun-panel').style.display = 'none';
}

// ── Search & Replace ──────────────────────────────────
let _srMatches = [], _srIdx = 0;

function toggleSearchPanel() {
  const p = document.getElementById('sr-panel');
  const show = p.style.display === 'none';
  p.style.display = show ? 'block' : 'none';
  if (show) { document.getElementById('sr-find').focus(); srHighlight(); }
  else _srMatches = [];
}

function closeSR() {
  document.getElementById('sr-panel').style.display = 'none';
  _srMatches = [];
  document.getElementById('sr-count').textContent = '';
}

function srGetPattern() {
  const q = document.getElementById('sr-find').value;
  if (!q) return null;
  const useRegex = document.getElementById('sr-regex').checked;
  const caseSens = document.getElementById('sr-case').checked;
  try { return new RegExp(useRegex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), caseSens ? 'g' : 'gi'); }
  catch { return null; }
}

function srHighlight() {
  _srMatches = [];
  _srIdx = 0;
  const area = document.getElementById('editor-area');
  const re = srGetPattern();
  const cnt = document.getElementById('sr-count');
  if (!re) { cnt.textContent = ''; return; }
  let m;
  re.lastIndex = 0;
  while ((m = re.exec(area.value)) !== null) {
    _srMatches.push(m.index);
    if (_srMatches.length > 5000) break;
  }
  cnt.textContent = _srMatches.length ? `${_srIdx + 1}/${_srMatches.length}` : 'No results';
  if (_srMatches.length) srJumpTo(_srIdx);
}

function srJumpTo(idx) {
  _srIdx = (idx + _srMatches.length) % _srMatches.length;
  const area = document.getElementById('editor-area');
  const re = srGetPattern();
  if (!re) return;
  re.lastIndex = _srMatches[_srIdx];
  const m = re.exec(area.value);
  if (!m) return;
  area.focus();
  area.setSelectionRange(m.index, m.index + m[0].length);
  document.getElementById('sr-count').textContent = `${_srIdx + 1}/${_srMatches.length}`;
  // scroll line into view
  const linesBefore = area.value.slice(0, m.index).split('\n').length - 1;
  const lineHeight = parseInt(getComputedStyle(area).lineHeight) || 20;
  area.scrollTop = Math.max(0, linesBefore * lineHeight - area.clientHeight / 2);
}

function srNext() { if (_srMatches.length) srJumpTo(_srIdx + 1); }
function srPrev() { if (_srMatches.length) srJumpTo(_srIdx - 1); }

function srFindKey(e) {
  if (e.key === 'Enter') { e.shiftKey ? srPrev() : srNext(); e.preventDefault(); }
  if (e.key === 'Escape') closeSR();
}
function srReplaceKey(e) {
  if (e.key === 'Enter') { srReplaceOne(); e.preventDefault(); }
  if (e.key === 'Escape') closeSR();
}

function srReplaceOne() {
  if (!_srMatches.length) return;
  const area = document.getElementById('editor-area');
  const re = srGetPattern();
  const rep = document.getElementById('sr-replace').value;
  if (!re) return;
  re.lastIndex = _srMatches[_srIdx];
  const m = re.exec(area.value);
  if (!m) return;
  const newVal = area.value.slice(0, m.index) + rep + area.value.slice(m.index + m[0].length);
  area.value = newVal;
  onEditorInput();
  srHighlight();
}

function srReplaceAll() {
  const area = document.getElementById('editor-area');
  const re = srGetPattern();
  const rep = document.getElementById('sr-replace').value;
  if (!re || !_srMatches.length) return;
  const count = _srMatches.length;
  area.value = area.value.replace(re, rep);
  onEditorInput();
  srHighlight();
  toast(`Replaced ${count} occurrence(s)`, 's');
}

// Ctrl+H / Ctrl+F shortcut on editor
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'h' || e.key === 'H' || e.key === 'f' || e.key === 'F')) {
    const active = document.activeElement;
    if (active && active.id === 'editor-area') {
      e.preventDefault();
      toggleSearchPanel();
    }
  }
});

async function saveFile() {
  if (!_curFile) return;
  try {
    await api('PUT', `/projects/${_curProj.id}/files/${_curFile}`, { content: document.getElementById('editor-area').value });
    _dirty = false; toast('Saved ✓','s');
    const fn = document.getElementById('editor-filename');
    fn.innerHTML = `<span title="${esc(_curFile)}">${esc(_curFile.split("/").pop())}</span>`;
  } catch(e) { toast(e.message,'e'); }
}

async function deleteFile() {
  if (!_curFile) return;
  if (!await askConfirm('Delete File', `Delete "${_curFile}"?\n\nThis cannot be undone.`)) return;
  try {
    await api('DELETE', `/projects/${_curProj.id}/files/${_curFile}`);
    _curFile = null; _dirty = false;
    if (_excelMode) resetExcelEditor();
    document.getElementById('editor-area').value = '';
    document.getElementById('editor-pre').innerHTML = '';
    document.getElementById('editor-linenos').textContent = '1';
    const fn = document.getElementById('editor-filename');
    fn.className = 'editor-no-tab'; fn.innerHTML = 'No file open';
    document.getElementById('editor-linecount').textContent = '';
    document.getElementById('sb-lang').textContent = '';
    document.getElementById('sb-position').textContent = 'Ln 1, Col 1';
    document.getElementById('btn-savefile').style.display = 'none';
    document.getElementById('et-sep1').style.display      = 'none';
    document.getElementById('btn-newfile').style.display  = 'none';
    document.getElementById('btn-delfile').style.display  = 'none';
    toast('Deleted','s'); loadFiles();
  } catch(e) { toast(e.message,'e'); }
}

async function promptNewFile() {
  const name = await askInput('New File', {
    msg: 'Path relative to the project root. Folders are created as needed.',
    placeholder: 'Testcases/NewSuite.robot', okText: 'Create', allowSlash: true,
  });
  if (!name || !name.trim()) return;
  const path = name.trim();
  try {
    await api('PUT', `/projects/${_curProj.id}/files/${path}`, { content: '' });
    toast('Created '+path,'s');
    await loadFiles();
    openFile(path);
  } catch(e) { toast(e.message,'e'); }
}

// ── Excel / CSV editor ────────────────────────────────
let _excelData = null; // {sheets:{name:[[]]}, sheet_names:[], cur:''}
let _excelMode = false;

async function openExcelFile(path) {
  if (_dirty && !await askConfirm('Unsaved Changes',
      `"${_curFile}" has unsaved edits.\n\nDiscard them and open the other file?`,
      { okText: 'Discard' })) return;
  try {
    const r = await fetch(`/api/projects/${_curProj.id}/excel/${path}`,
      { headers: _token ? { Authorization:`Bearer ${_token}` } : {} });
    // Raw fetch (text/blob), so api()'s handling does not apply — say why
    // before dropping the user back to the login screen.
    if (r.status===401) { toast('Your session has expired. Please sign in again.','e'); logout(); return; }
    if (!r.ok) throw new Error(await r.text());
    _excelData = await r.json();
    _excelData.cur = _excelData.sheet_names[0] || 'Sheet1';
    _excelMode = true; _curFile = path; _dirty = false;
    // swap views
    document.getElementById('code-editor').style.display  = 'none';
    document.getElementById('excel-editor').style.display = 'flex';
    // toolbar
    const fn = document.getElementById('editor-filename');
    fn.className = 'editor-open-tab';
    fn.innerHTML = `<span title="${esc(path)}">${esc(path.split("/").pop())}</span>`;
    document.getElementById('btn-savefile').style.display = 'inline-flex';
    document.getElementById('btn-savefile').onclick = saveExcelFile;
    document.getElementById('et-sep1').style.display     = 'inline-block';
    document.getElementById('btn-newfile').style.display = 'none';
    document.getElementById('btn-delfile').style.display = 'inline-flex';
    document.getElementById('btn-delfile').onclick = deleteFile;
    // status bar
    document.getElementById('sb-lang').textContent = path.endsWith('.csv') ? 'CSV' : 'Excel (xlsx)';
    document.getElementById('sb-position').textContent = '';
    document.querySelectorAll('.tree-row[data-path]').forEach(el => el.classList.toggle('active', el.dataset.path===path));
    renderExcelTabs();
    renderExcelSheet(_excelData.cur);
  } catch(e) { toast(e.message,'e'); }
}

function renderExcelTabs() {
  const tabs = document.getElementById('excel-sheet-tabs');
  tabs.innerHTML = '';
  _excelData.sheet_names.forEach(name => {
    const t = document.createElement('div');
    t.className = 'xls-tab' + (name===_excelData.cur ? ' active' : '');
    t.textContent = name;
    t.onclick = () => { collectExcelSheet(_excelData.cur); _excelData.cur = name; renderExcelTabs(); renderExcelSheet(name); };
    tabs.appendChild(t);
  });
}

function renderExcelSheet(name) {
  const rows = _excelData.sheets[name] || [];
  const maxCols = Math.max(1, rows.reduce((m,r) => Math.max(m, r.length), 1));
  const tbl = document.getElementById('excel-table');

  // Build entire table as HTML string — 10-50× faster than DOM append per cell
  let html = '<thead><tr><th class="row-hdr">#</th>';
  for (let c = 0; c < maxCols; c++) html += `<th>${colLabel(c)}</th>`;
  html += '</tr></thead><tbody>';

  rows.forEach((row, ri) => {
    html += `<tr><td class="row-hdr">${ri+1}</td>`;
    for (let ci = 0; ci < maxCols; ci++) {
      const val = esc(row[ci] !== undefined ? row[ci] : '');
      html += `<td><input type="text" value="${val}" data-r="${ri}" data-c="${ci}" oninput="excelCellInput(this)" onkeydown="excelNav(event)"></td>`;
    }
    html += '</tr>';
  });
  // blank append row
  html += `<tr><td class="row-hdr">${rows.length+1}</td>`;
  for (let ci = 0; ci < maxCols; ci++) {
    html += `<td><input type="text" value="" data-r="${rows.length}" data-c="${ci}" oninput="excelCellInput(this)" onkeydown="excelNav(event)"></td>`;
  }
  html += '</tr></tbody>';

  tbl.innerHTML = html;
  document.getElementById('editor-linecount').textContent = `${rows.length} rows · ${maxCols} cols`;
}

function excelCellInput(inp) { _dirty = true; markExcelDirty(); }

function addExcelBlankRow(tbody, ri, maxCols) {
  const tr = tbody.insertRow();
  const rhdr = tr.insertCell(); rhdr.className='row-hdr'; rhdr.textContent = ri+1;
  for (let ci = 0; ci < maxCols; ci++) {
    const td = tr.insertCell();
    const inp = document.createElement('input');
    inp.type = 'text'; inp.value = '';
    inp.dataset.r = ri; inp.dataset.c = ci;
    inp.oninput = () => { _dirty = true; markExcelDirty(); };
    inp.onkeydown = excelNav;
    inp.onfocus = () => {
      // grow: add another blank row if focusing last row
      const rows = tbody.querySelectorAll('tr');
      if (inp.closest('tr') === rows[rows.length-1]) addExcelBlankRow(tbody, rows.length, maxCols);
    };
    td.appendChild(inp);
  }
}

function markExcelDirty() {
  const fn = document.getElementById('editor-filename');
  if (_curFile) fn.innerHTML = `<span title="${esc(_curFile)}">● ${esc(_curFile.split("/").pop())}</span>`;
}

function colLabel(n) {
  let s = ''; n++;
  while (n > 0) { s = String.fromCharCode(64 + (n % 26 || 26)) + s; n = Math.floor((n-1)/26); }
  return s;
}

function excelNav(e) {
  const inp = e.target;
  const r = +inp.dataset.r, c = +inp.dataset.c;
  const tbl = document.getElementById('excel-table');
  function focus(row, col) {
    const target = tbl.querySelector(`input[data-r="${row}"][data-c="${col}"]`);
    if (target) { target.focus(); target.select(); }
  }
  if (e.key === 'Tab') { e.preventDefault(); focus(r, c+1) || focus(r+1, 0); }
  else if (e.key === 'Enter') { e.preventDefault(); focus(r+1, c); }
  else if (e.key === 'ArrowRight' && inp.selectionStart === inp.value.length) focus(r, c+1);
  else if (e.key === 'ArrowLeft'  && inp.selectionStart === 0)               focus(r, c-1);
  else if (e.key === 'ArrowDown')  { e.preventDefault(); focus(r+1, c); }
  else if (e.key === 'ArrowUp')    { e.preventDefault(); focus(r-1, c); }
}

function collectExcelSheet(name) {
  const tbl = document.getElementById('excel-table');
  const inputs = tbl.querySelectorAll('tbody input');
  if (!inputs.length) return;
  const maxR = Math.max(...[...inputs].map(i => +i.dataset.r)) + 1;
  const maxC = Math.max(...[...inputs].map(i => +i.dataset.c)) + 1;
  const rows = Array.from({length: maxR}, () => Array(maxC).fill(''));
  inputs.forEach(inp => { rows[+inp.dataset.r][+inp.dataset.c] = inp.value; });
  // trim trailing empty rows
  while (rows.length && rows[rows.length-1].every(v => !v)) rows.pop();
  _excelData.sheets[name] = rows;
}

async function saveExcelFile() {
  if (!_curFile) return;
  collectExcelSheet(_excelData.cur);
  try {
    await api('PUT', `/projects/${_curProj.id}/excel/${_curFile}`, { sheets: _excelData.sheets });
    _dirty = false; toast('Saved ✓','s');
    const fn = document.getElementById('editor-filename');
    fn.innerHTML = `<span title="${esc(_curFile)}">${esc(_curFile.split("/").pop())}</span>`;
  } catch(e) { toast(e.message,'e'); }
}

function resetExcelEditor() {
  _excelMode = false; _excelData = null;
  document.getElementById('excel-editor').style.display = 'none';
  document.getElementById('code-editor').style.display  = 'block';
  document.getElementById('btn-savefile').onclick = saveFile;
}
