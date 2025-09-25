# backend/code_search_tool.py
import os, re, json, sys, pathlib
from typing import List, Dict, Any, Optional
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer
from interlinked_local import AI as InterAI

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "code_index")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
INTERLINKED_MODEL = os.getenv("INTERLINKED_MODEL", "gemini-2.5-pro")
INTERLINKED_BASE_URL = os.getenv("INTERLINKED_BASE_URL")
INTERLINKED_API_KEY = os.getenv("INTERLINKED_API_KEY")

_EMBEDDER: Optional[SentenceTransformer] = None
_AI_SINGLETON: Optional[InterAI] = None

def get_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)

def embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer(EMBED_MODEL)
    return _EMBEDDER

def _get_ai() -> InterAI:
    global _AI_SINGLETON
    if _AI_SINGLETON is None:
        try:
            _AI_SINGLETON = InterAI(model=INTERLINKED_MODEL, base_url=INTERLINKED_BASE_URL, api_key=INTERLINKED_API_KEY)
        except TypeError:
            _AI_SINGLETON = InterAI()
    return _AI_SINGLETON

def _as_text(resp) -> str:
    if resp is None:
        return ""
    if hasattr(resp, "response"):
        return str(getattr(resp, "response") or "")
    return str(resp)

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
    params: List[Dict[str, str]] = []
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
        p = re.sub(r"\s*=\s*[^,]+$", "", p)
        parts = p.strip().split()
        if parts:
            name = re.sub(r"[*&\[\]]", "", parts[-1])
            typ = " ".join(parts[:-1]) or parts[-1]
            params.append({"name": name, "type": typ, "meaning": ""})
    return params

def build_snippet_around_phrase(code: str, phrase: str, ctx_lines: int = 2) -> str:
    if not code:
        return ""
    lines = code.splitlines()
    pat = re.compile(re.escape(phrase), flags=re.I)
    for idx, line in enumerate(lines):
        if pat.search(line):
            start = max(0, idx - ctx_lines)
            end = min(len(lines), idx + ctx_lines + 1)
            return "\n".join(lines[start:end])
    return clip(code, 400)

def _rank_hits(hits: List[Dict[str, Any]], phrase: str) -> List[Dict[str, Any]]:
    p = phrase.lower()
    def score(pl):
        fn = (pl.get("function_name") or "").lower()
        fp = (pl.get("file") or "").lower()
        ct = (pl.get("content") or "").lower()
        s = 0
        if fn == p: s += 100
        if re.search(rf"\b{re.escape(p)}\b", fn): s += 40
        if re.search(rf"\b{re.escape(p)}\b", ct): s += 30
        if p in fp: s += 10
        return -s
    return sorted(hits, key=score)

def hybrid_hits(c: QdrantClient, phrase: str, page: int = 200, cap: int = 30, embed=None) -> List[Dict[str, Any]]:
    phrase = phrase.strip()
    hits, seen = [], set()
    flt = models.Filter(should=[
        models.FieldCondition(key="content", match=models.MatchText(text=phrase)),
        models.FieldCondition(key="function_name", match=models.MatchText(text=phrase)),
        models.FieldCondition(key="file", match=models.MatchText(text=phrase)),
    ])
    next_page = None
    while True and len(hits) < cap:
        points, next_page = c.scroll(collection_name=COLLECTION, limit=page, with_payload=True, with_vectors=False, offset=next_page, scroll_filter=flt)
        if not points:
            break
        for pt in points:
            pl = pt.payload or {}
            code = pl.get("content") or ""
            fn = pl.get("function_name") or ""
            fpath = pl.get("file") or ""
            ok = False
            if re.search(rf"\b{re.escape(phrase)}\b", code, flags=re.I): ok = True
            if re.search(rf"\b{re.escape(phrase)}\b", fn, flags=re.I): ok = True
            if re.search(re.escape(phrase), fpath, flags=re.I): ok = True
            key = (fpath, pl.get("start_line"), pl.get("end_line"))
            if ok and key not in seen:
                seen.add(key)
                hits.append(pl)
                if len(hits) >= cap:
                    break
        if next_page is None or len(hits) >= cap:
            break
    if len(hits) < cap and embed is not None:
        try:
            vec = embed.encode([phrase])[0].tolist()
            sr = c.search(collection_name=COLLECTION, query_vector=vec, limit=max(10, cap), with_payload=True)
            for r in sr:
                pl = r.payload or {}
                key = (pl.get("file"), pl.get("start_line"), pl.get("end_line"))
                if key in seen:
                    continue
                hits.append(pl)
                seen.add(key)
                if len(hits) >= cap:
                    break
        except Exception:
            pass
    return _rank_hits(hits[:cap], phrase)

def build_prompt(phrase: str, items: List[Dict[str, Any]], question: Optional[str] = None) -> str:
    entries: List[str] = []
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

def _to_json_safely(text: str):
    text = (text or "").strip()
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

def search_codebase(phrase: str, limit: int = 12, question: Optional[str] = None) -> List[Dict[str, Any]]:
    c = get_client()
    emb = embedder()
    hits = hybrid_hits(c, phrase, page=200, cap=limit, embed=emb)
    if not hits:
        return []
    items: List[Dict[str, Any]] = []
    for pl in hits:
        code = pl.get("content") or ""
        fn = pl.get("function_name") or ""
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
    ai = _get_ai()
    resp = ai.ask(prompt=prompt)
    resp_text = _as_text(resp)
    try:
        data = _to_json_safely(resp_text)
    except Exception:
        preview = (resp_text or "")[:2000]
        data = [{
            "function_name": it["function_name"],
            "location": f"{it['file']}:{it['start_line']}-{it['end_line']}",
            "role": preview,
            "parameters": it["params_guess"],
            "called_functions": it["calls_guess"],
            "logic_flow": [],
            "possible_causes": [],
            "diagnostics": [],
        } for it in items]
    return data