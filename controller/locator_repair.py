"""
BRACE — deterministic locator repair.

Given the page as it was when a locator failed, find the elements it most
plausibly meant. No model, no network, no dependency beyond the standard
library, which is the point: the majority of locator breakage is a renamed id
or a changed test hook, and that is a string-similarity problem, not a
reasoning one.

Everything here is a pure function over (html, failed_locator). It is testable
without a browser, without a database and without an API key, and it is the
stage that must stay correct — the model stage later only fills the gaps this
one leaves ambiguous.
"""
import difflib
import re
from html.parser import HTMLParser

# Elements a test can plausibly address. Everything else is layout.
_INTERESTING = {
    "a", "button", "input", "select", "textarea", "option", "label",
    "form", "table", "td", "th", "li", "h1", "h2", "h3", "img", "iframe",
    "summary", "dialog",
}
# ...plus any element carrying one of these, whatever its tag: a div with a
# test hook is exactly the element somebody was addressing.
_HOOK_ATTRS = ("data-testid", "data-test", "data-test-id", "data-cy",
               "data-qa", "data-automation-id", "id", "name", "role",
               "aria-label", "aria-labelledby", "placeholder", "title")

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}

# Never candidates: they are not on the page. A <link rel=stylesheet> with an
# id scores like any other element and is never what a test was clicking.
_NOT_RENDERED = {"link", "meta", "script", "style", "title", "base", "head", "html"}

# Locator strategies BRACE understands. SeleniumLibrary accepts both `id=x`
# and `id:x`; Browser library uses `id=x` and css/xpath shorthands.
_STRATEGIES = ("id", "name", "css", "xpath", "link", "partial link", "tag",
               "class", "text", "data", "aria-label", "identifier", "dom",
               "jquery", "sizzle", "default", "when", "element")

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# 'Price143' is a name and a number, not one word. Splitting them keeps the
# digits from padding out a similarity score.
_DIGIT_EDGE = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
_NONWORD = re.compile(r"[^a-z0-9]+")


# ── Locator parsing ──────────────────────────────────────────────
def parse_locator(raw):
    """Split a Robot locator into (strategy, value).

    Unprefixed values are reported as strategy 'default', which is what
    SeleniumLibrary does with them — it tries id, then name. A bare path is
    xpath, which is the one implicit strategy worth detecting.
    """
    s = (raw or "").strip()
    if not s:
        return ("default", "")
    if s.startswith(("//", "(//", "./", "..")):
        return ("xpath", s)
    m = re.match(r"^\s*([a-zA-Z][a-zA-Z \-]{0,18})\s*[:=]\s*(.+)$", s, re.S)
    if m:
        strat = m.group(1).strip().lower()
        if strat in _STRATEGIES:
            return (strat, m.group(2).strip())
    return ("default", s)


# Patterns that name a locator inside a failure message. The first two are
# SeleniumLibrary's own wording; the third catches the very common house style
# where a team's wrapper keyword appends the locator it was given.
# Greedy on purpose. An XPath routinely contains single quotes of its own —
# //p[contains(text(),'Saved')] — and a non-greedy match stops at the first of
# them and returns a fragment. Greedy runs to the closing quote, and the result
# is validated afterwards.
_MSG_LOCATOR_PATTERNS = (
    re.compile(r"locator\s*[:=]?\s*'(.{2,300})'", re.I),
    re.compile(r"locator\s*[:=]?\s*\"(.{2,300})\"", re.I),
)
# Last resort: a bare XPath sitting in the message text. Quotes are allowed
# inside it for the same reason; whitespace is what ends it.
_MSG_BARE_XPATH = re.compile(r"(\(?//[^\s<>]{3,300})")


def locator_from_message(message):
    """Pull a locator out of a failure message, or return None.

    The listener sees only the failing keyword's own arguments, and that
    keyword is frequently a wrapper that fails with a human-readable message
    while the locator it was actually looking for appears in the text. This
    recovers it after the fact from what Robot recorded.
    """
    msg = message or ""
    for rx in _MSG_LOCATOR_PATTERNS:
        m = rx.search(msg)
        if m and m.group(1).strip():
            return m.group(1).strip()
    m = _MSG_BARE_XPATH.search(msg)
    if m:
        # Trailing sentence punctuation, but never a bracket the expression
        # itself needs — count them before trimming.
        v = m.group(1).strip().rstrip(".,;")
        while v.endswith(")") and v.count(")") > v.count("("):
            v = v[:-1]
        return v
    return None


def locator_hint(strategy, value):
    """The identifying text to match on, pulled out of the strategy's syntax.

    An xpath or css locator is not itself a name, but it almost always contains
    one — `//button[@id='submit-btn']` is a failed lookup for 'submit-btn', and
    matching on that finds the renamed element where matching on the raw
    expression finds nothing.
    """
    v = value or ""
    if strategy in ("xpath", "css", "jquery", "sizzle", "dom"):
        # Quoted strings first, then #id / .class / @attr= fragments.
        quoted = re.findall(r"['\"]([^'\"]{2,80})['\"]", v)
        if quoted:
            return max(quoted, key=len)
        frag = re.findall(r"[#.]([\w\-]{2,80})", v)
        if frag:
            return max(frag, key=len)
        tail = re.findall(r"[\w\-]{3,80}", v)
        return tail[-1] if tail else v
    return v


# ── DOM extraction ───────────────────────────────────────────────
class _Collector(HTMLParser):
    """Flatten the document into candidate elements with their text.

    convert_charrefs is on, so text arrives decoded. Malformed markup is
    expected — captures come from real applications, not from fixtures.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        el = {"tag": tag, "attrs": a, "text": "", "depth": len(self._stack)}
        keep = (tag not in _NOT_RENDERED
                and (tag in _INTERESTING or any(k in a for k in _HOOK_ATTRS)))
        if keep:
            self.elements.append(el)
        if tag not in _VOID:
            self._stack.append(el if keep else None)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._stack:
            self._stack.pop()

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        t = data.strip()
        if not t:
            return
        # Attribute the text to every open element that we kept, so a button
        # wrapping a span still carries its own label.
        for el in self._stack:
            if el is not None and len(el["text"]) < 300:
                el["text"] = (el["text"] + " " + t).strip()


def extract_elements(html):
    p = _Collector()
    try:
        p.feed(html or "")
        p.close()
    except Exception:                                   # noqa: BLE001 — partial is fine
        pass
    return p.elements


# ── Similarity ───────────────────────────────────────────────────
def _norm(s):
    """Fold camelCase, digit boundaries, separators and case together.

    'submitBtn', 'submit-btn' and 'SUBMIT_BTN' are the same name written three
    ways, and a rename between them is the single most common breakage.
    """
    s = _CAMEL.sub(" ", s or "")
    s = _DIGIT_EDGE.sub(" ", s)
    return _NONWORD.sub(" ", s.lower()).strip()


def _tokens(s):
    return set(_norm(s).split())


def _ratio(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def common_tokens(elements):
    """Tokens that appear all over this page's names, and so identify nothing.

    Learned from the page rather than from a word list, because which words are
    meaningless is entirely application-specific. On a pricing screen every
    other id contains 'price', so 'AutomationPrice143' against 'minPrice' looks
    like a 64% match when the only thing they share is the noise word. On a
    checkout page 'submit' appears once and is the most identifying token there
    is. A fixed stoplist gets one of those two wrong.
    """
    freq = {}
    named = 0
    for el in elements:
        a = el["attrs"]
        vals = [a[k] for k in ("id", "name", "data-testid", "data-test",
                               "data-test-id", "data-cy", "data-qa") if a.get(k)]
        if not vals:
            continue
        named += 1
        seen = set()
        for v in vals:
            seen |= _tokens(v)
        for t in seen:
            freq[t] = freq.get(t, 0) + 1
    if named < 4:
        return set()
    cutoff = max(3, int(named * 0.12))
    return {t for t, n in freq.items() if n >= cutoff}


def _name_score(value, hint, common):
    """Similarity between two element names, discounted for coincidence.

    Character similarity alone is not enough. Two cases produce a confident
    number from nothing:

      * the only shared word is one this page uses everywhere — 'minPrice'
        against 'AutomationPrice143' scores 0.64 on the word 'price';
      * no word is shared at all and the overlap is an accident of spelling —
        'combinationType' against 'AutomationPrice143' scores 0.56 because both
        contain the letters 'omation'.

    Either way the answer should be "these are different names". A high raw
    ratio with no shared token is exempt, because that is what a separator or
    spelling variant looks like: 'cvvcode' against 'cvv_code'.
    """
    r = _ratio(value, hint)
    if r >= 0.85:
        return r
    shared = _tokens(value) & _tokens(hint)
    if shared and (shared - common):
        return r                        # shares a word this page finds distinctive
    return r * 0.4


# Locators that address an element by what it says rather than by what it is
# called: //p[contains(text(),'Saved')], link=Cancel, text=Submit.
_TEXT_LOCATOR = re.compile(r"text\s*\(\s*\)|normalize-space|contains\s*\(\s*\.", re.I)


def _is_text_locator(strategy, value):
    return strategy in ("link", "partial link", "text") or bool(
        _TEXT_LOCATOR.search(value or ""))


def score_element(el, strategy, value, hint, common=frozenset()):
    """0..1 confidence that `el` is what the failed locator meant.

    Signals are ordered by how much they actually tell you. A matching test
    hook is near-certain — nobody has two elements with the same data-testid.
    A matching id is nearly as good. Text and role are corroboration, not
    identification, so they cap lower. Every name comparison is then scaled by
    whether the two share anything this page treats as distinctive.
    """
    a = el["attrs"]
    hooks = [a[k] for k in ("data-testid", "data-test", "data-test-id",
                            "data-cy", "data-qa", "data-automation-id") if a.get(k)]
    best, why = 0.0, None

    for h in hooks:
        r = _name_score(h, hint, common)
        if r >= 0.99 and (best < 1.0):
            best, why = 1.0, "test hook matches exactly"
        elif r >= 0.45 and r * 0.95 > best:
            best, why = r * 0.95, "test hook is a partial match (%.2f)" % r

    # The floor here is deliberately low. 'submit-btn' against 'submit-payment'
    # scores about 0.65 — too weak to propose, but exactly the element a person
    # is looking for, and showing them nothing is the worse failure. Promotion
    # to a proposal is governed by AUTO_THRESHOLD, not by this cutoff.
    for key, label, weight in (("id", "id", 0.97), ("name", "name", 0.92)):
        if a.get(key):
            r = _name_score(a[key], hint, common)
            if r >= 0.99 and weight > best:
                best, why = weight, "%s matches ignoring case and separators" % label
            elif r >= 0.45 and r * weight > best:
                best, why = r * weight, "%s is a partial match (%.2f)" % (label, r)

    if a.get("aria-label"):
        r = _name_score(a["aria-label"], hint, common)
        if r >= 0.8 and r * 0.8 > best:
            best, why = r * 0.8, "aria-label is a near match"

    txt = el.get("text") or ""
    if txt:
        r = _ratio(txt[:80], hint)
        # Exact text identifies rather than corroborates when the locator was
        # addressing the element by its text in the first place — a text()
        # XPath or a link=/text= strategy — or on the controls whose text is
        # conventionally their name. Anything short of exact stays weak.
        text_addressed = (_is_text_locator(strategy, value)
                          or el["tag"] in ("a", "button", "label", "option"))
        if r >= 0.99 and text_addressed and 0.9 > best:
            best, why = 0.9, "visible text matches exactly"
        elif r >= 0.85 and r * 0.75 > best:
            best, why = r * 0.75, "visible text is a near match"

    for key in ("placeholder", "title", "value"):
        if a.get(key):
            r = _ratio(a[key], hint)
            if r >= 0.85 and r * 0.7 > best:
                best, why = r * 0.7, "%s matches" % key

    if best == 0.0:
        return 0.0, None

    # Corroboration only — never enough on its own to promote a weak match.
    if strategy == "tag" and el["tag"] == (value or "").lower():
        best = min(1.0, best + 0.03)
    if el["tag"] in ("button", "input", "a", "select", "textarea"):
        best = min(1.0, best + 0.02)
    return round(best, 4), why


# ── Selector suggestion ──────────────────────────────────────────
# Framework-generated class names — css-1a2b3c, sc-bdVaJa, jss123, tw hashes.
_GENERATED_CLASS = re.compile(r"^(css-|sc-|jss\d|makeStyles-|ng-|_[\w]{5,})|[0-9a-f]{6,}$")


def suggest_selector(el):
    """The most durable way to address this element, in preference order.

    Never an absolute XPath. Replacing one brittle locator with a more brittle
    one is worse than leaving the test broken, because the next failure is
    somebody else's problem and it looks like the fix worked.
    """
    a = el["attrs"]
    for k in ("data-testid", "data-test", "data-test-id", "data-cy",
              "data-qa", "data-automation-id"):
        if a.get(k):
            return "css=[%s='%s']" % (k, a[k])
    if a.get("id") and not _GENERATED_CLASS.match(a["id"]):
        return "id=%s" % a["id"]
    if a.get("name"):
        return "name=%s" % a["name"]
    if a.get("aria-label"):
        return "css=[aria-label='%s']" % a["aria-label"]
    if el["tag"] == "a" and (el.get("text") or "").strip():
        return "link=%s" % el["text"].strip()[:80]
    classes = [c for c in (a.get("class") or "").split()
               if c and not _GENERATED_CLASS.match(c)]
    if classes:
        return "css=%s.%s" % (el["tag"], ".".join(classes[:2]))
    if a.get("placeholder"):
        return "css=%s[placeholder='%s']" % (el["tag"], a["placeholder"])
    if a.get("role"):
        return "css=%s[role='%s']" % (el["tag"], a["role"])
    return None


def _slim(el, score, why):
    a = el["attrs"]
    keep = {k: v for k, v in a.items()
            if k in _HOOK_ATTRS or k in ("class", "type", "value", "href")}
    return {
        "tag":        el["tag"],
        "attrs":      keep,
        "text":       (el.get("text") or "")[:120],
        "score":      score,
        "why":        why,
        "suggestion": suggest_selector(el),
    }


# Above this, with nothing else close, the shortlist is confident enough to
# stand as a proposal on its own — no model involved. Below it, the candidates
# are shown to a human and the model stage has something worth doing.
AUTO_THRESHOLD = 0.9
RUNNER_UP_MAX  = 0.5
# Below this a candidate is not worth a row. When the element is simply absent
# — the page never finished loading, which is a large share of real failures —
# an empty table saying so is far more use than seven rows in the forties.
DISPLAY_FLOOR  = 0.55


def candidates(html, failed_locator, limit=8):
    """Rank the elements the failed locator most likely meant.

    Returns {locator, strategy, hint, candidates[], proposed|None}. `proposed`
    is filled only when exactly one candidate is convincing; everything else is
    left for a person or, later, for the model.
    """
    strategy, value = parse_locator(failed_locator)
    hint = locator_hint(strategy, value)

    elements = extract_elements(html)
    common   = common_tokens(elements)

    scored = []
    for el in elements:
        s, why = score_element(el, strategy, value, hint, common)
        # No derivable selector means there is nothing to offer, however well
        # it scored. This is mostly ancestors: text propagates up, so a <table>
        # wrapping the matching <td> scores identically and is never the answer.
        if s >= DISPLAY_FLOOR and suggest_selector(el):
            scored.append((s, why, el))
    # Deepest first on a tie, for the same reason.
    scored.sort(key=lambda t: (t[0], t[2]["depth"]), reverse=True)

    top = [_slim(el, s, why) for s, why, el in scored[:limit]]

    proposed = None
    if top and top[0]["score"] >= AUTO_THRESHOLD and top[0]["suggestion"]:
        runner_up = top[1]["score"] if len(top) > 1 else 0.0
        if runner_up <= RUNNER_UP_MAX:
            proposed = {
                "locator":    top[0]["suggestion"],
                "confidence": top[0]["score"],
                "rationale":  top[0]["why"] or "single strong match in the captured page",
                "source":     "deterministic",
            }

    return {
        "locator":    failed_locator,
        "strategy":   strategy,
        "hint":       hint,
        "candidates": top,
        "proposed":   proposed,
    }
