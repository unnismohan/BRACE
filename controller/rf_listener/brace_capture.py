"""
BRACE — failure-time page capture listener.

Robot Framework loads this with `--listener <path>/brace_capture.py:<out_dir>`,
injected by the controller. Nothing in the user's suites changes, and nothing
here is importable by them: the file lives in the image, not in SUITES_DIR.

Why a listener and not post-run analysis of output.xml: when a locator fails,
the browser is still alive. Robot tears it down moments later and the page is
gone forever. This is the only point where the DOM can still be read.

The markup is pruned in-process before it ever reaches disk — a real enterprise
page is 1-3 MB of which the overwhelming majority is script bodies, inline SVG
and base64 images that no selector will ever be built from.

Self-contained on purpose. It runs inside the robot process, whose sys.path is
the suites directory, so it cannot import anything from the controller package.

THE RULE: this must never fail a test. A capture feature that breaks runs gets
switched off on day one and never switched back on. Every entry point is
wrapped, and every failure is a printed line and nothing more.
"""
import json
import os
import re
import time

ROBOT_LISTENER_API_VERSION = 3

# Only capture when the message looks like the browser could not reach an
# element. An assertion failure on an API test has no page worth storing, and
# capturing on every failure would write a page dump per failed assert.
_LOCATOR_FAIL_MARKERS = (
    "not found",
    "not visible",
    "not open",
    "not enabled",
    "not interactable",
    "not clickable",
    "not selected",
    "no such element",
    "did not appear",
    "did not become",
    "stale element",
    "element is not attached",
    "could not be scrolled into view",
    "element click intercepted",
    "unable to locate",
)

# The value inside SeleniumLibrary's "Element with locator 'x' not found".
_LOCATOR_IN_MSG = re.compile(r"locator\s+'([^']{1,200})'", re.I)

# Pruning. Ordered cheapest-first; each one runs over the whole document.
_RE_SCRIPT  = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_RE_STYLE   = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.I | re.S)
_RE_NOSCRIPT= re.compile(r"<noscript\b[^>]*>.*?</noscript\s*>", re.I | re.S)
_RE_SVG     = re.compile(r"<svg\b[^>]*>.*?</svg\s*>", re.I | re.S)
_RE_COMMENT = re.compile(r"<!--(?!\[if).*?-->", re.S)
# Inline handlers and javascript: URLs. Scripts are already gone, but the
# capture is served back into a browser later and these are the remaining way
# markup can execute.
_RE_ONATTR  = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_RE_JSHREF  = re.compile(r"(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", re.I)
# Any attribute value over this is a data: URI, an inline SVG path or a base64
# blob. None of them help identify an element.
_RE_LONGATTR = re.compile(r"=\s*([\"'])([^\"']{300,})\1")
_RE_WS      = re.compile(r"[ \t]{2,}")

MAX_PRUNED_BYTES = 512_000


def _prune(html):
    """Strip everything a selector can never be built from. Best-effort regex.

    Not a parser, deliberately: this runs inside the user's test process on a
    failure path, and an html5 parser on a 3 MB document is both a dependency
    and a stall. The output only has to be good enough to identify elements by
    their attributes, and it is never rendered as a live page.
    """
    out = html
    for rx in (_RE_SCRIPT, _RE_STYLE, _RE_NOSCRIPT, _RE_SVG, _RE_COMMENT):
        out = rx.sub("", out)
    out = _RE_ONATTR.sub("", out)
    out = _RE_JSHREF.sub(r"\1=\2#\2", out)
    out = _RE_LONGATTR.sub(lambda m: "=%s[brace:truncated]%s" % (m.group(1), m.group(1)), out)
    out = _RE_WS.sub(" ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# A locator, as opposed to a message, a timeout or a piece of test data.
_LOCATOR_SHAPE = re.compile(
    r"^\s*(//|\(//|\./|"
    r"(id|name|css|xpath|link|partial link|tag|class|text|data|dom|jquery|sizzle)"
    r"\s*[:=])", re.I)


def _looks_like_locator(val):
    v = (val or "").strip()
    if not v or len(v) > 400:
        return False
    # An unresolved variable identifies nothing, and matching on the literal
    # text '${locator}' is how the shortlist ends up scoring the word 'locator'
    # against the page.
    if "${" in v or "@{" in v or "%{" in v:
        return False
    if _LOCATOR_SHAPE.match(v):
        return True
    # A bare value with no spaces is what an unprefixed id/name lookup takes.
    # A sentence is not, and neither is a timeout — '5s' and '10' are the other
    # arguments the same keywords are usually given.
    if " " in v or not (2 < len(v) <= 80):
        return False
    return not re.match(r"^\d+(\.\d+)?\s*(m?s|min|sec|seconds?|minutes?)?$", v, re.I)


class brace_capture:                                    # noqa: N801 — Robot matches the filename
    """Listener v3. `out_dir` is the run item's results directory."""

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, out_dir, max_per_test="3"):
        self.dir = os.path.join(out_dir, "dom")
        try:
            self.max_per_test = max(1, int(max_per_test))
        except (TypeError, ValueError):
            self.max_per_test = 3
        self._n = 0
        self._seen = set()

    # ── Robot callbacks ──────────────────────────────────────────
    def start_test(self, data, result):
        # Per test, not per run: a suite of 30 cases that all fail the same way
        # should still yield one capture each, not three for the whole file.
        self._n = 0
        self._seen = set()

    def end_keyword(self, data, result):
        try:
            self._maybe_capture(data, result)
        except BaseException as exc:                    # noqa: BLE001 — see module docstring
            print("[BRACE] page capture skipped: %s" % exc)

    # ── Internals ────────────────────────────────────────────────
    def _maybe_capture(self, data, result):
        if getattr(result, "status", None) != "FAIL":
            return
        if self._n >= self.max_per_test:
            return

        msg = (getattr(result, "message", "") or "").strip()
        low = msg.lower()
        if not any(m in low for m in _LOCATOR_FAIL_MARKERS):
            return

        # end_keyword fires innermost-first and every enclosing keyword fails
        # with the same message, so without this one failure would be captured
        # once per level of nesting.
        key = low[:200]
        if key in self._seen:
            return
        self._seen.add(key)

        drv = self._driver()
        if drv is None:
            return

        src = drv.page_source or ""
        original = len(src)
        pruned = _prune(src)
        truncated = len(pruned) > MAX_PRUNED_BYTES
        if truncated:
            pruned = pruned[:MAX_PRUNED_BYTES] + "\n<!-- [brace] truncated -->"

        self._n += 1
        seq = "%03d" % self._n
        os.makedirs(self.dir, exist_ok=True)

        with open(os.path.join(self.dir, seq + ".html"), "w",
                  encoding="utf-8", errors="replace") as f:
            f.write(pruned)

        meta = {
            "seq":       self._n,
            "captured":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "keyword":   getattr(result, "name", None) or getattr(data, "name", None),
            "args":      [str(a) for a in (getattr(data, "args", None) or [])][:10],
            "locator":   self._locator(data, msg),
            "message":   msg[:2000],
            "url":       self._safe(lambda: drv.current_url),
            "title":     self._safe(lambda: drv.title),
            "viewport":  self._safe(lambda: drv.get_window_size()),
            "html_file": seq + ".html",
            "bytes_original": original,
            "bytes_pruned":   len(pruned),
            "truncated": truncated,
        }
        with open(os.path.join(self.dir, seq + ".json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)

        print("[BRACE] captured page at failure -> dom/%s.html (%d -> %d bytes)"
              % (seq, original, len(pruned)))

    def _locator(self, data, msg):
        """The locator that failed, resolved.

        The message is authoritative when it names one. Otherwise the keyword's
        arguments are searched — and they arrive as written in the source, so
        `${locator}` has to be resolved first or the whole shortlist ends up
        matching on the word "locator".

        Only an argument that looks like a locator is accepted. The failing
        keyword is often a wrapper whose first argument is a human message, and
        feeding that in produces confident nonsense.
        """
        m = _LOCATOR_IN_MSG.search(msg)
        if m:
            return m.group(1)
        for raw in (getattr(data, "args", None) or [])[:6]:
            val = self._resolve(str(raw))
            if val and _looks_like_locator(val):
                return val
        return None

    @staticmethod
    def _resolve(text):
        """Substitute Robot variables. Returns the text unchanged if it cannot."""
        if "${" not in text and "@{" not in text and "%{" not in text:
            return text.strip()
        try:
            from robot.libraries.BuiltIn import BuiltIn
            return str(BuiltIn().replace_variables(text)).strip()
        except Exception:                               # noqa: BLE001 — unresolvable is not fatal
            return text.strip()

    def _driver(self):
        """The live WebDriver, or None if this is not a browser test."""
        try:
            from robot.libraries.BuiltIn import BuiltIn
        except ImportError:
            return None
        try:
            sl = BuiltIn().get_library_instance("SeleniumLibrary")
        except Exception:                               # noqa: BLE001 — library not in use
            return None
        # SeleniumLibrary 4+ exposes .driver; older versions only the private
        # accessor. Both return None when no browser is open.
        drv = getattr(sl, "driver", None)
        if drv is None:
            drv = self._safe(lambda: sl._current_browser())     # noqa: SLF001
        return drv

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:                               # noqa: BLE001 — metadata is optional
            return None
