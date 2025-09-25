# analyze_phrase_to_json.py
import os, re, json, argparse
from typing import List, Dict, Any
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer
from interlinked_local import AI

QDRANT_URL   = "http://127.0.0.1:6333"
COLLECTION   = "code_index"
EMBED_MODEL  = "all-MiniLM-L6-v2"

def get_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)

def embedder():
    return SentenceTransformer(EMBED_MODEL)

def clip(s: str, n=1200) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n/* ...clip... */"

def extract_called_functions(code: str, func_name: str) -> List[str]:
    if not code:
        return []
    body = code.split("{", 1)[-1] if "{" in code else code
    names = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))
    blacklist = {"if","for","while","switch","return","sizeof","catch","else","case"}
    return sorted([n for n in names if n not in blacklist and n != func_name])

def extract_params(code: str, func_name: str) -> List[Dict[str, str]]:
    params = []
    if not code:
        return params
    head = code.split("{", 1)[0]
    m = re.search(rf"{re.escape(func_name)}\s*\((.*?)\)", head, re.S)
    if not m:
        return params
    param_str = m.group(1).strip()
    if not param_str or param_str.lower() == "void":
        return params
    for p in re.split(r",\s*", param_str):
        p = re.sub(r"\s*=\s*[^,]+$", "", p)  # 去默认值
        parts = p.strip().split()
        if parts:
            name = re.sub(r"[*&\[\]]", "", parts[-1])
            typ  = " ".join(parts[:-1]) or parts[-1]
            params.append({"name": name, "type": typ, "meaning": ""})
    return params

def exact_phrase_hits(c: QdrantClient, phrase: str, page: int = 200, cap: int = 30) -> List[Dict[str, Any]]:
    flt = models.Filter(must=[models.FieldCondition(
        key="content",
        match=models.MatchText(text=phrase)
    )])

    hits, next_page = [], None
    while True and len(hits) < cap:
        points, next_page = c.scroll(
            collection_name=COLLECTION,
            limit=page,
            with_payload=True,
            with_vectors=False,
            offset=next_page,
            scroll_filter=flt,
        )
        if not points:
            break

        for pt in points:
            pl = pt.payload or {}
            code = pl.get("content") or ""
            if re.search(re.escape(phrase), code, flags=re.IGNORECASE):
                hits.append(pl)
                if len(hits) >= cap:
                    break

        if next_page is None:
            break

    return hits

def build_snippet_around_phrase(code: str, phrase: str, ctx_lines: int = 2) -> str:
    if not code:
        return ""
    lines = code.splitlines()
    for idx, line in enumerate(lines):
        if re.search(re.escape(phrase), line, flags=re.IGNORECASE):
            start = max(0, idx - ctx_lines)
            end   = min(len(lines), idx + ctx_lines + 1)
            return "\n".join(lines[start:end])
    return clip(code, 400)

def build_prompt(phrase: str, items: List[Dict[str, Any]], question: str = None) -> str:
    entries = []
    for it in items:
        entries.append(
f"""### ITEM
Function: {it.get('function_name')}
File: {it.get('file')}:{it.get('start_line')}-{it.get('end_line')}

ParamsGuess: {json.dumps(it.get('params_guess', []), ensure_ascii=False)}
CallsGuess: {json.dumps(it.get('calls_guess', []), ensure_ascii=False)}

Snippet:
{it.get('snippet')}
"""
        )
    joined = "\n\n".join(entries)
    qline = f"问题场景：{question}\n" if question else ""

    return f"""
你是资深嵌入式/C++代码分析助手。以下是代码库中**精确包含短语**“{phrase}”的函数或调用点片段（均来源于向量数据库服务端过滤+客户端二次校验）。{qline}
请基于每个条目的片段（以及给出的参数/被调函数猜测），分析该函数在代码库中的工作流程，并输出**严格 JSON**，仅返回 JSON、不要 Markdown。

上下文条目（若干）：
{joined}

必须返回的 JSON 结构（数组）：例如
[
  {{
    "function_name": "",
    "location": "{{file}}:{{start_line}}-{{end_line}}",
    "role": "该函数/调用在系统中的职责（1-2句）",
    "parameters": [{{"name":"","type":"","meaning":""}}],
    "called_functions": [],
    "logic_flow": ["按执行先后列步骤（尽量具体到条件/状态/调用）"],
    "possible_causes": ["与短语相关的“不吐数据/无输出”等可能原因（若适用）"],
    "diagnostics": ["可验证的排查建议（日志/寄存器/状态位/边界条件等）"]
  }}
]

严格要求：
- 仅输出 JSON，且是一个 JSON 数组，每个元素对应一个命中的条目
- 若信息不足，请尽量依据片段推断，但不要虚构不存在的 API/硬件寄存器名
- 参数/被调函数可用“猜测”补足（已提供 ParamsGuess/CallsGuess）
"""

def to_json_safely(text: str):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\[.*\]$", text, flags=re.S)
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
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)
        return json.loads(raw)

def _ai_text(prompt: str) -> str:
    """
    兼容 interlinked 返回的多种对象形态：
    - observation.response.raw
    - observation.response
    - 直接字符串
    """
    obs = AI.ask(prompt=prompt)
    # 1) observation.response.raw
    try:
        raw = obs.response.raw  # type: ignore[attr-defined]
        if isinstance(raw, str) and raw.strip():
            return raw
    except Exception:
        pass
    # 2) observation.response
    try:
        if isinstance(obs.response, str) and obs.response.strip():  # type: ignore[attr-defined]
            return obs.response  # type: ignore[return-value]
    except Exception:
        pass
    # 3) 直接字符串
    if isinstance(obs, str):
        return obs
    # 4) 其它兜底
    return str(getattr(obs, "response", obs))

# -------------------- 主流程 --------------------
def run(phrase: str, out_path: str, limit: int = 12, question: str = None):
    c = get_client()
    print(f"🔎 精确短语匹配：{phrase}")
    hits = exact_phrase_hits(c, phrase, page=200, cap=limit)
    if not hits:
        raise SystemExit("❌ 没有匹配到包含该短语的函数/调用片段。换个短语再试试。")

    items = []
    for pl in hits:
        code = pl.get("content") or ""
        fn   = pl.get("function_name") or ""
        items.append({
            "function_name": fn,
            "file": pl.get("file"),
            "start_line": pl.get("start_line"),
            "end_line": pl.get("end_line"),
            "snippet": build_snippet_around_phrase(code, phrase, ctx_lines=2),
            "params_guess": extract_params(code, fn) if fn else [],
            "calls_guess": extract_called_functions(code, fn) if fn else [],
        })

    prompt = build_prompt(phrase, items, question)
    print("🧠 调用 Interlinked 分析…")
    resp_text = _ai_text(prompt)
    try:
        data = to_json_safely(resp_text)
    except Exception:
        data = []
        for it in items:
            data.append({
                "function_name": it["function_name"],
                "location": f"{it['file']}:{it['start_line']}-{it['end_line']}",
                "role": resp_text[:2000],
                "parameters": it["params_guess"],
                "called_functions": it["calls_guess"],
                "logic_flow": [],
                "possible_causes": [],
                "diagnostics": [],
            })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 已生成：{out_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", "-p", required=True, help="要精确匹配的短语（大小写不敏感）")
    ap.add_argument("--out", "-o", default="output/phrase_analysis.json")
    ap.add_argument("--limit", "-k", type=int, default=12, help="最多分析的命中条目数")
    ap.add_argument("--q", help="可选：问题场景（如：为什么不吐数据？）")
    args = ap.parse_args()
    run(args.phrase, args.out, args.limit, args.q)
