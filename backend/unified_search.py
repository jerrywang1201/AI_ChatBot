
# backend/unified_search.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Unified search & report generator with intent classification, fuzzy matching, and log/scene processing.

Supports:
- radar: Search only Radar (bug reports).
- code: Search only code/function explanations (strict semantic → automatic fuzzy fallback if insufficient).
- mixed: Search both Radar + code and generate a unified report.
- scene: Given a “scenario description / terminal log”, automatically extract commands/keywords, run code JSON analysis + Radar comparison, and output a report.

CLI self-test:
    python3 -m backend.unified_search -q "Laguna readbatt output fail"
    python3 -m backend.unified_search -q "Any similar Radar records?"
    python3 -m backend.unified_search -q "What does aop_sensor_get_data_from_event do"
    python3 -m backend.unified_search --scene "On B788, after running 'kis enable', 'pmu reset' fails. Logs: ...<log>..."
"""

import argparse
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ========= Your existing search implementations =========
from backend.code_search_tool import search_codebase as _search_codebase
from backend.radar_analysis import find_similar_radar_issues as _find_radars

# ========= Unified LLM I/O (plain text in/out) =========
try:
    from ai.my_interlinked_core import ask_text
except Exception:
    def ask_text(prompt: str) -> str:
        # Local fallback: avoid crashing; echo the prompt for debugging
        return "[fallback echo]\n" + prompt


# =========================
# Utilities
# =========================
def _clip(s: str, n: int = 1200) -> str:
    s = s or ""
    return s if len(s) <= n else (s[:n] + "\n/*...clip...*/")

def _ensure_list(x) -> List:
    if not x:
        return []
    return x if isinstance(x, list) else [x]

def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


# =========================
# Intent Classification
# =========================
def classify_intent(query: str) -> str:
    """
    Return one of: 'radar' / 'code' / 'mixed' / 'scene' / 'other'
    """
    ql = query.lower()
    scene_like = any(k in ql for k in [
        "log", "trace", "stack", "cmd", "command", "repro", "reproduce", "steps to reproduce"
    ])
    if scene_like:
        return "scene"

    prompt = f"""
You are a query intent classifier. Given a user input, decide the query type:
- If the query is looking for similar issue reports (Radar, bug reports), return "radar"
- If the query is asking about code definitions, functions, or implementation details, return "code"
- If it requires both, return "mixed"
- If it contains logs/commands/reproduction steps, return "scene"
- Otherwise, return "other"

Only output one of: radar / code / mixed / scene / other.
User input: {query}
"""
    resp = (ask_text(prompt) or "").strip().lower()
    return resp if resp in ("radar", "code", "mixed", "scene") else ("scene" if scene_like else "other")


# =========================
# Fuzzy-match Strategy (progressive relaxation over _search_codebase)
# =========================
_SYNONYM_MAP = {
    "enable": ["turn on", "start", "set", "enable"],
    "disable": ["turn off", "stop", "clear", "disable"],
    "kis": ["KIS", "kis", "kernel integrity", "security"],
    "pmu": ["PMU", "power", "power manager", "power reset"],
    "batt": ["battery", "batt", "fuelgauge", "gpadc"],
    "aop": ["AOP", "aop", "apple aop"],
    "reset": ["reset", "reboot", "restart"],
    "health": ["health", "status", "capacity", "soh", "cycle"],
}

def _variants_for_token(tok: str) -> List[str]:
    t = tok.strip()
    if not t:
        return []
    v = {t, t.lower(), t.upper()}
    if "_" in t:
        v.add(t.replace("_", " "))
    if "-" in t:
        v.add(t.replace("-", " "))
    base = t.lower()
    if base in _SYNONYM_MAP:
        v.update(_SYNONYM_MAP[base])
    return list(v)

def _expand_query_variants(query: str) -> List[str]:
    # Tokenize (keep alnum / _ - . /)
    toks = re.findall(r"[A-Za-z0-9_\-./]+", query)
    if not toks:
        return [query]
    # Cartesian combinations of token variants with caps on breadth
    buckets: List[List[str]] = []
    for t in toks[:5]:  # limit first 5 tokens
        buckets.append(_variants_for_token(t)[:5] or [t])

    out = []
    prefix = [""]
    for _, choices in enumerate(buckets, 1):
        nxt = []
        for p in prefix:
            for c in choices[:4]:
                s = (p + " " + c).strip()
                nxt.append(s)
        prefix = list(dict.fromkeys(nxt))[:12]
        out.extend(prefix)
        if len(out) > 40:
            break
    # Add original query slices
    out.append(query)
    if len(toks) >= 2:
        out.append(" ".join(toks[:2]))
    if len(toks) >= 3:
        out.append(" ".join(toks[:3]))
    # Deduplicate & clean
    return list(dict.fromkeys([q for q in out if q.strip()]))

def _merge_code_hits(*hit_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by (file, start, end, function_name) while preserving order."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for hits in hit_lists:
        for h in hits or []:
            key = (
                h.get("file") or (h.get("location", "").split(":")[0] if h.get("location") else ""),
                h.get("start_line"), h.get("end_line"),
                h.get("function_name") or h.get("name") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
    return out

def _mark_fuzzy(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for h in hits:
        h.setdefault("notes", [])
        if "(fuzzy match)" not in h["notes"]:
            h["notes"].append("(fuzzy match)")
        if "role" in h and h["role"]:
            h["role"] = f"{h['role']}  [fuzzy match — please review manually]"
        else:
            h["role"] = "[fuzzy match — please review manually]"
    return hits

def _fuzzy_code_probe(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """
    Progressive relaxation:
      1) Combine synonyms / case / underscore variants
      2) Substring combinations
      3) If still sparse, widen the limit and take top results
    Finally, dedupe and mark as (fuzzy match).
    """
    variants = _expand_query_variants(query)
    all_hits: List[Dict[str, Any]] = []
    budget = max(limit * 3, 24)

    for qv in variants[:18]:
        try:
            hs = _search_codebase(qv, limit=limit, question=f"(fuzzy) {query}")
        except Exception:
            hs = []
        if hs:
            all_hits = _merge_code_hits(all_hits, hs)
        if len(all_hits) >= budget:
            break

    if len(all_hits) < limit // 2:
        for qv in variants[18:36]:
            try:
                hs = _search_codebase(qv, limit=limit, question=f"(fuzzy-2) {query}")
            except Exception:
                hs = []
            if hs:
                all_hits = _merge_code_hits(all_hits, hs)
            if len(all_hits) >= budget:
                break

    return _mark_fuzzy(all_hits[:max(limit, 8)])


# =========================
# Base Searches: Code / Radar / Dual
# =========================
def run_code_only(query: str, *, limit: int = 8) -> Dict[str, Any]:
    strict_hits = _search_codebase(query, limit=limit, question=query) or []
    if len(strict_hits) >= max(3, limit // 2):
        return {"query": query, "code": strict_hits, "radar": []}
    fuzzy_hits = _fuzzy_code_probe(query, limit=limit)
    code_hits = _merge_code_hits(strict_hits, fuzzy_hits)[: max(limit, 8)]
    return {"query": query, "code": code_hits, "radar": []}

def run_radar_only(query: str, *, topk: int = 8) -> Dict[str, Any]:
    radar_hits = _find_radars(query=query, topk=topk) or []
    return {"query": query, "code": [], "radar": radar_hits}

def run_dual_search(
    query: str,
    *,
    code_limit: int = 8,
    radar_topk: int = 8,
    code_phrase: Optional[str] = None,
    radar_component: Optional[str] = None,
    radar_phrase: Optional[str] = None,
) -> Dict[str, Any]:
    base_q = code_phrase or query
    strict_hits = _search_codebase(base_q, limit=code_limit, question=query) or []
    code_hits = strict_hits
    if len(strict_hits) < max(3, code_limit // 2):
        fuzz = _fuzzy_code_probe(base_q, limit=code_limit)
        code_hits = _merge_code_hits(strict_hits, fuzz)[: max(code_limit, 8)]

    radar_hits = _find_radars(
        query=query,
        topk=radar_topk,
        component=radar_component,
        phrase=radar_phrase
    ) or []
    return {"query": query, "code": code_hits, "radar": radar_hits}


# =========================
# Formatting code/Radar hits (for the report body)
# =========================
def _fmt_code_hits(code_hits: List[Dict[str, Any]]) -> str:
    if not code_hits:
        return "(no code hits)"
    lines: List[str] = []
    for i, it in enumerate(code_hits, 1):
        fn = it.get("function_name", "(unknown)")
        loc = it.get("location", "") or f"{it.get('file','')}:{it.get('start_line','')}-{it.get('end_line','')}"
        role = it.get("role", "")
        cf = _ensure_list(it.get("called_functions"))
        called = ", ".join([str(x) for x in cf[:6]])

        fuzzy_tag = ""
        notes = _ensure_list(it.get("notes"))
        if any("fuzzy" in (n or "").lower() for n in notes) or "[fuzzy match" in (role or ""):
            fuzzy_tag = "  [fuzzy match — please review manually]"

        lines.append(
            f"- [Code #{i}] {fn} @ {loc}{fuzzy_tag}\n"
            f"  role: {role}\n"
            f"  called: {called}"
        )
    return "\n".join(lines)

def _fmt_radar_hits(radar_hits: List[Dict[str, Any]]) -> str:
    if not radar_hits:
        return "(no radar hits)"
    lines: List[str] = []
    for i, it in enumerate(radar_hits, 1):
        rid = it.get("radar_id")
        comp = it.get("component")
        score = _safe_float(it.get("score"), 0.0)
        title = it.get("title", "")
        desc = (it.get("description") or "").strip()
        desc = _clip(desc, 500)
        lines.append(
            f"- [Radar #{i}] Radar {rid} (comp {comp}, score {score:.3f})\n"
            f"  Title: {title}\n"
            f"  Desc : {desc}"
        )
    return "\n".join(lines)


# =========================
# Code JSON Analysis (role/flow/diagnostics)
# =========================
def _build_code_json_prompt(phrase: str, items: List[Dict[str, Any]], question: str | None = None) -> str:
    entries: List[str] = []
    for it in items:
        entries.append(
            f"""### ITEM
Function: {it.get('function_name')}
File: {it.get('file')}:{it.get('start_line')}-{it.get('end_line')}

ParamsGuess: {json.dumps(it.get('params_guess', []), ensure_ascii=False)}
CallsGuess: {json.dumps(it.get('calls_guess', []), ensure_ascii=False)}

Snippet:
{_clip(it.get('snippet') or it.get('content') or '', 1200)}
"""
        )
    joined = "\n\n".join(entries)
    qline = f"Scenario: {question}\n" if question else ""

    return f"""
You are an experienced embedded/C++ code analysis assistant. The following are code snippets that **exactly or fuzzily match** the phrase "{phrase}" (found via semantic search, server-side filtering, and client validation). {qline}
For each item, analyze the function’s responsibility and flow, and output **strict JSON** only (no Markdown).

Make the explanations understandable for readers who are **not firmware engineers**:
- Use plain English.
- Briefly explain hardware terms, acronyms, or uncommon APIs when they appear.

Context items:
{joined}

Required JSON structure (array), for example:
[
  {{
    "function_name": "",
    "location": "{{file}}:{{start_line}}-{{end_line}}",
    "role": "1–2 sentence responsibility in plain English",
    "parameters": [{{"name":"","type":"","meaning":"plain-English explanation"}}],
    "called_functions": [],
    "logic_flow": ["Step-by-step execution (conditions/calls where possible)"],
    "possible_causes": ["Likely reasons for 'no output' or failures related to this phrase"],
    "diagnostics": ["Verifiable checks (logs, registers/status bits, conditions, tracing, etc.)"]
  }}
]

Strict rules:
- Output only valid JSON, as an array of objects (one per matched item).
- If details are insufficient, infer from the snippet when reasonable; do NOT invent APIs or hardware register names that do not exist.
- You may use ParamsGuess/CallsGuess to fill in missing parameters/callees.
""".strip()

def _json_safely(text: str) -> List[Dict[str, Any]]:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\[.*\]$", text, flags=re.S)  # Expect a JSON array
    if not m:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("No JSON found")
        raw = f"[{m.group(0)}]"
    else:
        raw = m.group(0)
    try:
        return json.loads(raw)
    except Exception:
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)  # Remove trailing commas
        return json.loads(raw)

def _naturalize_code_json(code_json: List[Dict[str, Any]]) -> str:
    """Convert the structured code JSON into readable paragraphs for the unified report."""
    if not code_json:
        return "(no structured code analysis)"
    out: List[str] = []
    for i, e in enumerate(code_json, 1):
        fn = e.get("function_name", "")
        loc = e.get("location", "")
        role = e.get("role", "")
        params = e.get("parameters") or []
        calls = e.get("called_functions") or []
        flow  = e.get("logic_flow") or []
        causes = e.get("possible_causes") or []
        diag = e.get("diagnostics") or []
        lines = [
            f"- [Code #{i}] {fn} @ {loc}",
            f"  Responsibility: {role}" if role else "  Responsibility: (missing)",
        ]
        if params:
            lines.append("  Parameters:")
            for p in params[:6]:
                nm = p.get("name",""); tp = p.get("type",""); me = p.get("meaning","")
                lines.append(f"    - {nm} ({tp}): {me}")
        if calls:
            lines.append("  Possible call chain: " + ", ".join([str(x) for x in calls[:8]]))
        if flow:
            lines.append("  Execution flow:")
            for step in flow[:8]:
                lines.append(f"    - {step}")
        if causes:
            lines.append("  Possible causes: " + "; ".join(causes[:5]))
        if diag:
            lines.append("  Diagnostics:")
            for d in diag[:6]:
                lines.append(f"    - {d}")
        out.append("\n".join(lines))
    return "\n".join(out)


# =========================
# Report Prompt (including "Code Structure" section)
# =========================
def build_report_prompt(bundle: Dict[str, Any],
                        *,
                        extra_section: str = "",
                        code_json_section: str = "",
                        code_structure_json: List[Dict[str, Any]] | None = None) -> str:
    """
    code_json_section: Natural-language expansion of the structured code JSON.
    code_structure_json: (optional) If provided, section 4 will list the "Code Structure (array)" using these items.
    """
    query = bundle.get("query", "")
    code_block = _fmt_code_hits(bundle.get("code", []))
    radar_block = _fmt_radar_hits(bundle.get("radar", []))

    # Section 4: Code Structure (array listing, not as a Markdown code block)
    code_struct_text = "(no structured items)"
    if code_structure_json:
        rows = []
        for i, e in enumerate(code_structure_json, 1):
            rows.append(
                f"- [{i}] {e.get('function_name','') or '(unknown)'} @ {e.get('location','')}\n"
                f"  role: {e.get('role','')}\n"
                f"  params: {e.get('parameters', [])}\n"
                f"  called_functions: {e.get('called_functions', [])}"
            )
        code_struct_text = "\n".join(rows) if rows else "(empty)"

    return f"""
You are a senior embedded systems troubleshooting assistant. User query:
{query}
{extra_section}

We searched in two sources:
[Code] Matches:
{code_block}

[Code] Structured analysis (natural language):
{code_json_section or "(no structured code analysis)"}

[Radar] Similar historical issues:
{radar_block}

Please generate a **unified technical report in English** that is understandable to people without deep firmware knowledge. 
Write in clear, plain English while preserving technical accuracy.

The report should include:
1) Summary conclusions (4–7 clear bullet points)
2) Observed symptoms and scope (confirmed facts only)
3) Possible root causes from the code side (reference [Code #] items; if marked "(fuzzy match)", mention that manual review is required)
4) Code structure (array):
{code_struct_text}
5) Historical Radar comparison (identify the 2–3 most similar, describe the fix/solution back then, and note similarities/differences with the current issue)
6) Reproduction scenario and observable signals (specific commands, key log values, registers/status flags, tracing options)
7) Suggested step-by-step investigation plan (in priority order)
8) Temporary workarounds (if any)
9) Next actions (modules to modify, risks, stakeholders to involve)

Requirements:
- When referencing specific functions or Radars, include “[Code #n] / [Radar #n]”.
- Explain technical terms briefly in plain English to aid non-firmware readers.
- If information is missing, include a “Questions to clarify” list.
""".strip()


def make_unified_report(bundle: Dict[str, Any], *,
                        extra_section: str = "",
                        code_json_section: str = "",
                        code_structure_json: List[Dict[str, Any]] | None = None) -> str:
    prompt = build_report_prompt(bundle,
                                 extra_section=extra_section,
                                 code_json_section=code_json_section,
                                 code_structure_json=code_structure_json)
    resp = ask_text(prompt)
    if isinstance(resp, str) and resp.startswith("[fallback echo]"):
        return "## Unified Report (Fallback)\n\n" + prompt
    return resp


# =========================
# Code function explanation entry
# =========================
def explain_function(func_query: str) -> str:
    hits = _search_codebase(func_query, limit=5, question=f"Explain function: {func_query}") or []
    if not hits:
        return "No related function or snippet found in the codebase."
    parts: List[str] = []
    for i, it in enumerate(hits, 1):
        fn = it.get("function_name", "")
        loc = it.get("location", "") or f"{it.get('file','')}:{it.get('start_line','')}-{it.get('end_line','')}"
        role = it.get("role", "")
        params = it.get("parameters") or []
        called = it.get("called_functions") or []
        parts.append(
            f"[Code #{i}] {fn} @ {loc}\n"
            f"role: {role}\n"
            f"params: {params}\n"
            f"called: {called}"
        )
    prompt = f"""
Below are several code snippets and notes related to `{func_query}`.
Please explain in concise English the function/interface responsibilities, key branches, and call flows,
and list typical failure scenarios and debugging suggestions:

{chr(10).join(parts)}
"""
    resp = ask_text(prompt)
    return resp


# =========================
# Extract commands & terms from a scene/log (heuristics + LLM fallback)
# =========================
_CMD_PAT = re.compile(r"(?:^|[\s`>])([a-zA-Z0-9_\-./]+(?:\s+-[-a-zA-Z0-9_]+|\s+--[-a-zA-Z0-9_]+|\s+[^\n\r]+)?)$")
def extract_commands_and_terms(scene_or_log: str) -> Tuple[List[str], List[str]]:
    if not scene_or_log:
        return [], []
    text = scene_or_log.strip()

    # Rough command extraction (supports "tool --flag", "sh script.sh arg", "python3 xxx.py ...")
    cmds: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("#", "$", ">", ">>", "=>")):
            line = line.lstrip("#$> =")
        m = _CMD_PAT.search(line)
        if m:
            cmd = m.group(1).strip()
            if " " in cmd or "/" in cmd or cmd.endswith((".sh", ".py")):
                cmds.append(cmd)

    # Keywords: pick error words & common hardware module terms
    terms = set()
    for w in re.findall(r"[A-Za-z0-9_./+\-]{3,}", text):
        wl = w.lower()
        if any(k in wl for k in ["error", "fail", "failed", "timeout", "crash", "panic"]):
            terms.add(w)
        if any(k in wl for k in ["kis", "pmu", "gpadc", "laguna", "aop", "imu", "batt", "readbatt"]):
            terms.add(w)
    terms = {t for t in terms if len(t) >= 3}

    # Optional LLM refinement
    try:
        prompt = f"""From the following scene/log text, extract the most critical commands (1–3) and terms/modules (2–6) for troubleshooting.
Output JSON only: {{"commands":[], "terms":[]}}. Do not add explanations.
Text:
{text}"""
        resp = ask_text(prompt)
        js = json.loads(resp) if isinstance(resp, str) else {}
        cmds_llm = _ensure_list(js.get("commands"))
        terms_llm = _ensure_list(js.get("terms"))
        cmds = list(dict.fromkeys(cmds_llm + cmds))
        terms = list(dict.fromkeys(terms_llm + list(terms)))
    except Exception:
        pass

    return cmds[:4], list(terms)[:10]


# =========================
# Scene/log → one-click report
# =========================
def handle_log_or_scene(scene_text: str) -> str:
    """
    1) Extract commands & keywords
    2) Code search (build items → ask model to produce JSON)
    3) Radar similarity lookup
    4) Generate unified report (functions/definitions/call chains/diagnostics + Radar history + code structure)
    """
    cmds, terms = extract_commands_and_terms(scene_text)
    # Code search query: prefer commands, then keywords, then raw text
    code_query = " ".join(cmds + terms) or scene_text[:160]
    radar_query = code_query

    # Code matches (semantic → fuzzy fallback if needed)
    base = run_code_only(code_query, limit=8)
    code_hits = base["code"] or []

    # Prepare items (snippet/parameter/called guess—use what search_codebase provides if available)
    items: List[Dict[str, Any]] = []
    for pl in code_hits:
        code = pl.get("content") or pl.get("code") or ""
        if not code:
            continue
        fn   = pl.get("function_name") or ""
        snippet = _clip(code, 900)
        items.append({
            "function_name": fn,
            "file": pl.get("file") or (pl.get("location","").split(":")[0] if pl.get("location") else ""),
            "start_line": pl.get("start_line"),
            "end_line": pl.get("end_line"),
            "snippet": snippet,
            "content": code,
            "params_guess": pl.get("parameters") or [],
            "calls_guess": pl.get("called_functions") or [],
        })

    # Ask model to produce structured JSON (role/call chain/diagnostics)
    code_json: List[Dict[str, Any]] = []
    structured_hits = [
        h for h in code_hits
        if isinstance(h, dict) and any(k in h for k in ("role", "parameters", "called_functions", "logic_flow", "diagnostics"))
    ]
    if structured_hits:
        code_json = structured_hits
    elif items:
        prompt = _build_code_json_prompt(phrase=code_query, items=items, question=scene_text[:200])
        resp = ask_text(prompt)
        if isinstance(resp, str) and resp.startswith("[fallback echo]"):
            code_json = []
        else:
            try:
                code_json = _json_safely(resp)
            except Exception:
                code_json = []

    code_json_section = _naturalize_code_json(code_json)

    # Radar: fetch similar history
    radar_hits = _find_radars(query=radar_query, topk=6) or []

    bundle = {
        "query": f"[Scene/Log] {scene_text[:240]}",
        "code": code_hits,
        "radar": radar_hits,
    }

    extra = ""
    if cmds or terms:
        extra = "[Scene extraction]\n- Commands: " + (", ".join(cmds) or "(none)") + "\n" + "- Keywords: " + (", ".join(terms) or "(none)") + "\n"

    return make_unified_report(
        bundle,
        extra_section=extra,
        code_json_section=code_json_section,
        code_structure_json=code_json or None,
    )


# =========================
# One-stop natural language entry (for ChatRouter)
# =========================
def handle_natural_query(query: str) -> str:
    intent = classify_intent(query)

    if intent == "radar":
        bundle = run_radar_only(query, topk=8)
        return make_unified_report(bundle)

    if intent == "code":
        # Prioritize "explain function" phrasing
        if any(kw in query.lower() for kw in ["what does", "explain"]) or "(" in query or "::" in query or "_" in query:
            return explain_function(query)
        bundle = run_code_only(query, limit=8)
        return make_unified_report(bundle)

    if intent == "mixed":
        bundle = run_dual_search(query, code_limit=8, radar_topk=8)
        return make_unified_report(bundle)

    if intent == "scene":  # log/scene one-click
        return handle_log_or_scene(query)

    # Fallback: try dual search first
    bundle = run_dual_search(query, code_limit=6, radar_topk=6)
    return make_unified_report(bundle)


# =========================
# CLI
# =========================
def _cli():
    p = argparse.ArgumentParser()
    p.add_argument("-q", "--query", help="Natural language question or search phrase")
    p.add_argument("--scene", help="Scene/log text (takes precedence if provided)")
    p.add_argument("--intent", choices=["auto", "radar", "code", "mixed", "scene"], default="auto")
    args = p.parse_args()

    if args.scene:
        print(handle_log_or_scene(args.scene))
        return

    if not args.query:
        print("Either --query or --scene is required.")
        return

    q = args.query.strip()
    if args.intent == "radar":
        bundle = run_radar_only(q)
        print(make_unified_report(bundle))
    elif args.intent == "code":
        if any(kw in q.lower() for kw in ["what does", "explain"]) or "(" in q or "::" in q or "_" in q:
            print(explain_function(q))
        else:
            bundle = run_code_only(q)
            print(make_unified_report(bundle))
    elif args.intent == "mixed":
        bundle = run_dual_search(q)
        print(make_unified_report(bundle))
    elif args.intent == "scene":
        print(handle_log_or_scene(q))
    else:
        print(handle_natural_query(q))


if __name__ == "__main__":
    _cli()
