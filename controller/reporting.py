"""BRACE reporting — extracted application responsibility."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

_IMG_SRC_RE = re.compile('src="([^"]+)"')
_DATA_URI_RE = re.compile("^data:image/(png|jpe?g|gif|webp);base64,(.+)$", re.I | re.S)
MAX_EMBED_SHOT_BYTES = 8 * 1024 * 1024


def _write_embedded_shot(data_uri: str, item_dir: Path) -> Optional[str]:
    """Decode an embedded screenshot to a file so the UI can display it.

    `Capture Page Screenshot  EMBED` puts the image inline in output.xml rather
    than on disk. The failure box needs a URL, so the chosen one is written out
    once, here. Only the chosen one: a ten-iteration loop can embed ten
    full-page screenshots and writing them all would double the run's disk use
    for images nobody opens.
    """
    import runtime as mod_runtime

    m = _DATA_URI_RE.match(data_uri.strip())
    if not m:
        return None
    import base64
    import hashlib

    try:
        blob = base64.b64decode(m.group(2), validate=False)
    except Exception:
        return None
    if not blob or len(blob) > MAX_EMBED_SHOT_BYTES:
        return None
    ext = "jpg" if m.group(1).lower() in ("jpg", "jpeg") else m.group(1).lower()
    name = f"brace-embed-{hashlib.sha1(blob).hexdigest()[:12]}.{ext}"
    try:
        (item_dir / name).write_bytes(blob)
    except OSError as exc:
        mod_runtime.log.debug("Could not write embedded screenshot: %s", exc)
        return None
    return name


def _extract_failure(out_xml: Path, item_dir: Path):
    """Return (summary, detail, screenshot_name) for a failed output.xml.

    summary  — the failing keyword and its library, e.g.
               "Click Element (SeleniumLibrary)", suitable for one table cell.
    detail   — the failure message robot reported.
    screenshot_name — a file in item_dir, or None. Only names an existing file,
               so the UI never renders a broken thumbnail.
    """
    import runtime as mod_runtime

    root = ET.parse(str(out_xml)).getroot()
    detail = None
    for test in root.iter("test"):
        st = test.find("status")
        if st is not None and st.get("status") == "FAIL":
            detail = (st.text or "").strip() or None
            break
    kw_name = kw_owner = None
    fail_kw = None
    for kw in root.iter("kw"):
        st = kw.find("status")
        if st is None or st.get("status") != "FAIL":
            continue
        if any(
            (
                c.find("status") is not None
                and c.find("status").get("status") == "FAIL"
                for c in kw.findall("kw")
            )
        ):
            continue
        fail_kw = kw
        kw_name = kw.get("name") or kw_name
        kw_owner = kw.get("owner") or kw.get("library") or None
        if not detail:
            for msg in kw.findall("msg"):
                if msg.get("level") in ("FAIL", "ERROR") and (msg.text or "").strip():
                    detail = msg.text.strip()
                    break
    summary = kw_name or "Test failed"
    if kw_name and kw_owner:
        summary = f"{kw_name} ({kw_owner})"
    base = item_dir.resolve()
    shots = []
    fail_start = fail_end = None
    for i, el in enumerate(root.iter()):
        if el is fail_kw:
            fail_start = i
        if el.tag != "msg" or el.get("html") != "true" or (not el.text):
            continue
        m = _IMG_SRC_RE.search(el.text)
        if not m:
            continue
        src = m.group(1)
        if src.startswith("data:image/"):
            shots.append((i, "embed", src))
            continue
        name = src.split("/")[-1]
        cand = (base / name).resolve()
        if mod_runtime._contained(base, cand) and cand.is_file():
            shots.append((i, "file", name))
    if fail_start is not None and fail_kw is not None:
        fail_end = fail_start + sum((1 for _ in fail_kw.iter()))
    chosen = None
    if shots:
        upto = [s for s in shots if fail_end is None or s[0] < fail_end]
        chosen = (upto or shots)[-1]
    shot = None
    if chosen and chosen[1] == "file":
        shot = chosen[2]
    elif chosen:
        shot = _write_embedded_shot(chosen[2], item_dir)
    if detail and len(detail) > 2000:
        detail = detail[:2000] + " …"
    return (summary[:300], detail, shot)


def _item_files(run_dir, rf_run_id):
    """(has_log, has_report) for one item. Two stat() calls — only ever done for
    the page of items actually being returned, never for the whole run."""
    if not rf_run_id:
        return (False, False)
    d = run_dir / rf_run_id
    return ((d / "log.html").exists(), (d / "report.html").exists())
