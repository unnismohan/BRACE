"""BRACE routes.ai — extracted application responsibility."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from db import decrypt_token, get_db
import authentication as mod_authentication
from fastapi import APIRouter

router = APIRouter()
MAX_CONSOLE_CHARS = 8000
MAX_SOURCE_CHARS = 12000
MAX_RESOURCE_FILES = 4


def _hush_insecure_warning(verify: bool) -> None:
    """Silence urllib3's per-request warning when verification is off by choice."""
    if verify:
        return
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass


def _get_ai_config() -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_config WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {
            "enabled": False,
            "api_base": "",
            "api_key": "",
            "model": "",
            "verify_ssl": True,
        }
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["verify_ssl"] = bool(d.get("verify_ssl", 1))
    d["api_key"] = decrypt_token(d.get("api_key") or "")
    return d


def _parse_output_xml(path: Path) -> list:
    """Extract failing tests + the keyword chain that failed from RF output.xml."""
    import xml.etree.ElementTree as ET

    if not path.exists():
        return []
    try:
        tree = ET.parse(str(path))
    except ET.ParseError:
        return []
    root = tree.getroot()
    failures = []

    def walk_kws(node, trail):
        """Depth-first through <kw>, recording the deepest FAIL trail."""
        found = []
        for kw in node.findall("kw"):
            st = kw.find("status")
            if st is None or st.get("status") != "FAIL":
                continue
            name = kw.get("name") or ""
            lib = kw.get("library") or kw.get("owner") or ""
            label = f"{lib}.{name}" if lib else name
            args = [a.text or "" for a in kw.findall("arguments/arg")]
            new_trail = trail + [{"keyword": label, "args": args}]
            msgs = [
                (m.text or "").strip()
                for m in kw.findall("msg")
                if m.get("level") in ("FAIL", "ERROR")
            ]
            deeper = walk_kws(kw, new_trail)
            if deeper:
                found.extend(deeper)
            else:
                found.append(
                    {
                        "trail": new_trail,
                        "messages": msgs,
                        "status_text": (st.text or "").strip(),
                    }
                )
        return found

    for test in root.iter("test"):
        st = test.find("status")
        if st is None or st.get("status") != "FAIL":
            continue
        chains = walk_kws(test, [])
        failures.append(
            {
                "test_name": test.get("name") or "",
                "message": (st.text or "").strip(),
                "chains": chains[:3],
            }
        )
    return failures


def _read_capped(path: Path, cap: int, tail: bool = False) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= cap:
        return text
    return "…(truncated)…\n" + text[-cap:] if tail else text[:cap] + "\n…(truncated)…"


def _collect_resources(robot_src: str, suites_dir: Path, suite_path: str) -> list:
    """Pull the Resource/Variables files a suite imports, so the model sees the keywords."""
    import re

    out = []
    base = (suites_dir / suite_path).parent if suite_path else suites_dir
    pattern = re.compile(
        "^(?:Resource|Variables)\\s+(.+?)\\s*$", re.MULTILINE | re.IGNORECASE
    )
    for m in pattern.finditer(robot_src):
        raw = m.group(1).strip()
        if "$" in raw:
            continue
        cand = (base / raw).resolve()
        try:
            cand.relative_to(suites_dir.resolve())
        except ValueError:
            continue
        if cand.exists() and cand.is_file():
            out.append(
                {
                    "path": str(cand.relative_to(suites_dir.resolve())).replace(
                        "\\", "/"
                    ),
                    "content": _read_capped(cand, MAX_SOURCE_CHARS // 2),
                }
            )
        if len(out) >= MAX_RESOURCE_FILES:
            break
    return out


def _build_debug_context(
    project_id: int, run_id: str, rf_run_id: Optional[str], suite_path: Optional[str]
) -> dict:
    import runtime as mod_runtime

    frozen = mod_runtime._project_results(project_id) / run_id / "sources"
    suites_dir = frozen if frozen.is_dir() else mod_runtime._project_suites(project_id)
    result_dir = mod_runtime._project_results(project_id) / run_id
    if rf_run_id:
        result_dir = result_dir / rf_run_id
    console = _read_capped(result_dir / "console.log", MAX_CONSOLE_CHARS, tail=True)
    failures = _parse_output_xml(result_dir / "output.xml")
    if not suite_path and rf_run_id:
        conn = get_db()
        row = conn.execute(
            "\n            SELECT COALESCE(tri.source_path, tc.suite_path) AS suite_path FROM test_run_items tri\n            LEFT JOIN test_cases tc ON tri.test_case_id = tc.id\n            WHERE tri.run_id=? AND tri.rf_run_id=?\n        ",
            (run_id, rf_run_id),
        ).fetchone()
        conn.close()
        if row:
            suite_path = row["suite_path"]
    robot_src, resources = ("", [])
    if suite_path:
        f = suites_dir / suite_path
        if f.exists() and f.is_file():
            robot_src = _read_capped(f, MAX_SOURCE_CHARS)
            resources = _collect_resources(robot_src, suites_dir, suite_path)
    return {
        "project_id": project_id,
        "run_id": run_id,
        "rf_run_id": rf_run_id,
        "suite_path": suite_path,
        "console": console,
        "failures": failures,
        "robot_src": robot_src,
        "resources": resources,
    }


def _render_debug_prompt(ctx: dict) -> str:
    """Self-contained prompt — usable verbatim in any external chat when air-gapped."""
    p = []
    p.append(
        "You are a Robot Framework test-automation debugging expert. A test has failed. Diagnose the root cause and give a concrete fix.\n"
    )
    p.append("## Environment")
    p.append(
        "- Robot Framework test running headless in a Linux container (Chrome + ChromeDriver via Xvfb)"
    )
    p.append(f"- Suite file: `{ctx.get('suite_path') or 'unknown'}`")
    p.append(f"- Run ID: `{ctx.get('run_id')}`\n")
    fails = ctx.get("failures") or []
    if fails:
        p.append("## Failures (parsed from output.xml)")
        for f in fails:
            p.append(f"\n### Test: {f['test_name']}")
            p.append(f"**Status message:** {f['message'] or '(none)'}")
            for i, ch in enumerate(f.get("chains") or [], 1):
                trail = ch.get("trail") or []
                if trail:
                    p.append(f"\n**Failing keyword chain {i}:**")
                    for depth, kw in enumerate(trail):
                        args = ", ".join(kw["args"]) if kw["args"] else ""
                        p.append(
                            f"{'  ' * depth}- `{kw['keyword']}`"
                            + (f"  args: `{args}`" if args else "")
                        )
                for msg in ch.get("messages") or []:
                    p.append(f"\n**Error message:**\n```\n{msg}\n```")
        p.append("")
    if ctx.get("console"):
        p.append("## Console output (tail)")
        p.append("```\n" + ctx["console"] + "\n```\n")
    if ctx.get("robot_src"):
        p.append(f"## Test suite source — `{ctx.get('suite_path')}`")
        p.append("```robotframework\n" + ctx["robot_src"] + "\n```\n")
    for r in ctx.get("resources") or []:
        p.append(f"## Imported resource — `{r['path']}`")
        p.append("```robotframework\n" + r["content"] + "\n```\n")
    p.append("## What I need from you")
    p.append(
        "1. **Root cause** — what actually failed and why (be specific about the keyword and data)."
    )
    p.append(
        "2. **Category** — one of: locator/selector issue, timing/synchronisation, test data, environment/config, application defect, or script logic bug."
    )
    p.append(
        "3. **Fix** — the exact change, as a code diff or replacement snippet against the source above."
    )
    p.append("4. **Verification** — how to confirm the fix worked.")
    p.append(
        "\nIf the evidence is insufficient for a confident diagnosis, say what additional log or screenshot would settle it, rather than guessing."
    )
    return "\n".join(p)


class AIDebugReq(BaseModel):
    run_id: str
    rf_run_id: Optional[str] = None
    suite_path: Optional[str] = None


@router.post("/api/projects/{project_id}/ai-debug/prompt")
def ai_debug_prompt(
    project_id: int, req: AIDebugReq, user=Depends(mod_authentication._proj_viewer)
):
    """Build the debug prompt. Always available — this is the air-gapped path."""
    ctx = _build_debug_context(project_id, req.run_id, req.rf_run_id, req.suite_path)
    cfg = _get_ai_config()
    return {
        "prompt": _render_debug_prompt(ctx),
        "ai_available": bool(cfg["enabled"] and cfg["api_key"]),
        "model": cfg["model"] if cfg["enabled"] else None,
        "has_failures": bool(ctx["failures"]),
        "suite_path": ctx["suite_path"],
    }


@router.post("/api/projects/{project_id}/ai-debug/analyze")
def ai_debug_analyze(
    project_id: int, req: AIDebugReq, user=Depends(mod_authentication._proj_viewer)
):
    """Stream a diagnosis from an OpenAI-compatible endpoint (OpenRouter, vLLM, LiteLLM…)."""
    cfg = _get_ai_config()
    if not cfg["enabled"] or not cfg["api_key"]:
        raise HTTPException(
            400, "AI assistant not configured. Use the prompt option instead."
        )
    ctx = _build_debug_context(project_id, req.run_id, req.rf_run_id, req.suite_path)
    prompt = _render_debug_prompt(ctx)

    def stream():
        import json
        import requests

        url = cfg["api_base"].rstrip("/") + "/chat/completions"
        _hush_insecure_warning(cfg["verify_ssl"])
        try:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                },
                stream=True,
                timeout=180,
                verify=cfg["verify_ssl"],
            )
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                yield f"\n[AI ERROR {resp.status_code}] {resp.text[:600]}\n"
                return
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    yield delta["content"]
        except Exception as exc:
            yield f"\n[AI ERROR] {type(exc).__name__}: {exc}\n"

    return StreamingResponse(stream(), media_type="text/plain")
