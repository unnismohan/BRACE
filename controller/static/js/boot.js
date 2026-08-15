// BRACE v2 — boot.js
// Part of the app bundle. Files are plain classic scripts loaded in a
// fixed order by index.html (no modules, no build step — the runtime is
// air-gapped). They share one global scope, so ORDER MATTERS: keep the
// <script> tags in index.html in the same sequence as this list.

// ── Boot ───────────────────────────────────────────────
(async () => {
  try { const h=await fetch('/health').then(r=>r.json()); document.getElementById('env-badge').textContent=h.bss_env||'staging'; } catch {}
  if (_token && _user) afterLogin(_user);
})();
