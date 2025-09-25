# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any, Dict, List

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sentence_transformers import SentenceTransformer


try:
    from ai.my_interlinked_core import ask_text
except Exception:
    ask_text = None  

QDRANT_URL = "http://127.0.0.1:6333"
COLLECTION = "repo_code"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

DEBUG = False


def log(msg: str):
    if DEBUG:
        print(f"[LOG] {msg}")


def clip(s: str, n: int = 1200) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "\n/*...*/"


def guess_usage(txt: str, rel: str) -> str:
    if not txt:
        return ""

    m = re.search(r'(?i)^\s*usage:\s*(.+)$', txt, re.M)
    if m:
        return m.group(1).strip()

    if rel.endswith((".py", ".pyw")) and "argparse" in txt:
        flags = re.findall(
            r'add_argument\(\s*[\'"](-{1,2}[\w\-]+)[\'"](?:\s*,\s*[\'"](-{1,2}[\w\-]+)[\'"])?',
            txt,
        )
        if flags:
            xs: List[str] = []
            for a, b in flags:
                xs.append(b if b else a)
            return " ".join(dict.fromkeys(xs))

    if rel.endswith((".sh", ".bash")):
        m = re.findall(r'getopts\s+[\'"]([^\'"]+)[\'"]', txt)
        if m:
            opt = m[0]
            flags = []
            for c in opt:
                if c != ":":
                    flags.append(f"-{c}")
            return " ".join(flags)
        if re.search(r'\$[1-9]', txt):
            nums = [int(x) for x in re.findall(r'\$([1-9])', txt)]
            if nums:
                n = max(nums)
                return " ".join([f"<arg{i}>" for i in range(1, n + 1)])

    return ""


def base_cmd(rel: str) -> str:
    if rel.endswith((".sh", ".bash")):
        return f"sh {rel}"
    if rel.endswith((".py", ".pyw")):
        return f"python3 {rel}"
    return rel or ""


def build_cmd(rel: str, code: str) -> str:
    b = base_cmd(rel)
    u = guess_usage(code, rel)
    if not u:
        return b
    uu = u.strip()
    if uu.lower().startswith("usage:"):
        uu = uu.split(":", 1)[1].strip()
    parts = uu.split()
    if parts and parts[0].endswith((".py", ".sh", ".bash")):
        uu = " ".join(parts[1:])
    return f"{b} {uu}".strip()


def load_model(device: str | None):
    t1 = time.time()
    try:
        m = SentenceTransformer(EMBED_MODEL, device=device) if device else SentenceTransformer(EMBED_MODEL)
        t2 = time.time()
        log(f"load_model device={device or 'cpu'} time={t2 - t1:.2f}s")
        return m
    except Exception as e:
        log(f"load_model fail device={device}: {e}")
        if device:
            return load_model(None)
        raise


def encode(model, q: str) -> List[float]:
    t1 = time.time()
    v = model.encode([q], normalize_embeddings=True, show_progress_bar=False)[0].tolist()
    t2 = time.time()
    log(f"encode time={t2 - t1:.2f}s dim={len(v)}")
    return v


def detect_named_vectors(cli: QdrantClient, collection: str) -> List[str]:

    try:
        info = cli.get_collection(collection_name=collection)
        vecs = None
        if hasattr(info, "config") and info.config and getattr(info.config, "params", None):
            vecs = getattr(info.config.params, "vectors", None)
        if isinstance(vecs, dict):
            names = list(vecs.keys())
            log(f"named vectors = {names}")
            return names
        return []
    except Exception as e:
        log(f"detect_named_vectors error: {e}")
        return []


def build_filter(must_text: str | None) -> qm.Filter | None:
    if not must_text:
        return None
    return qm.Filter(
        must=[
            qm.FieldCondition(
                key="code",
                match=qm.MatchText(text=must_text)
            )
        ]
    )


def query_points(cli: QdrantClient, collection: str, qvec: List[float], topk: int, ef: int, must_text: str | None):
    search_params = qm.SearchParams(hnsw_ef=ef, exact=False)
    qfilter = build_filter(must_text)

    names = detect_named_vectors(cli, collection)
    order = [n for n in ["meta", "code"] if n in names]
    for name in order:
        try:
            res = cli.query_points(
                collection_name=collection,
                query=qm.NamedVector(name=name, vector=qvec),
                limit=topk,
                with_payload=True,
                with_vectors=False,
                search_params=search_params, 
                query_filter=qfilter,
            )
            return res.points or []
        except Exception as e:
            log(f"{name} vector query failed: {e}")

    # 普通（非命名）向量
    try:
        res = cli.query_points(
            collection_name=collection,
            query=qvec,
            limit=topk,
            with_payload=True,
            with_vectors=False,
            search_params=search_params, 
            query_filter=qfilter,
        )
        return res.points or []
    except Exception as e:
        log(f"default vector query failed: {e}")
        return []


def vec_search(q: str, url: str, collection: str, topk: int, ef: int, device: str, must_text: str | None) -> List[Any]:
    t0 = time.time()
    cli = QdrantClient(url=url)
    t1 = time.time()
    model = load_model({"auto": "mps", "mps": "mps", "cuda": "cuda", "cpu": None}.get(device, None))
    qvec = encode(model, q)
    hits = query_points(cli, collection, qvec, topk, ef, must_text)
    t2 = time.time()
    log(f"timing init_client={t1 - t0:.2f}s total_query={t2 - t1:.2f}s hits={len(hits)}")
    return hits

def _payload_code(pl: Dict[str, Any]) -> str:
    for k in ("code", "content", "source", "snippet", "text"):
        v = pl.get(k)
        if v:
            return v
    return ""


def build_prompt(question: str, items: List[Dict[str, Any]]) -> str:
    rows = []
    for it in items:
        rows.append(
            f"""### ITEM
path: {it["path"]}
lang: {it["lang"]}
kind: {it["kind"]}
name: {it["name"]}
usage_guess: {it["usage"]}
code:
{clip(it["code"])}
"""
        )
    joined = "\n\n".join(rows)
    return f"""你是资深工具脚本顾问。用户需求：{question}
下面是代码库中最相关的脚本条目（来自向量检索），包含路径、语言、启发式 usage 和代码片段。请基于片段判断每个脚本是做什么的，并给出推荐的命令行。
仅输出 JSON 数组，每个元素结构如下：
{{
  "script_path": "",
  "purpose": "一句话概括用途",
  "recommended_command": "",
  "why": "推荐此命令的依据（从片段和 usage 推断）",
  "confidence": 0.0
}}
输出按相关性从高到低，元素数量不超过 {len(items)}。
上下文：
{joined}
"""


def parse_json(s: str) -> List[Dict[str, Any]]:
    s = (s or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s, flags=re.S)
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else [v]
    except Exception:
        m = re.search(r"\[.*\]$", s, flags=re.S)
        if m:
            return json.loads(m.group(0))
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            return [json.loads(m.group(0))]
        raise

def answer(
    question: str,
    url: str,
    collection: str,
    topk: int,
    ef: int,
    device: str,
    repo_root: str | None,
    use_llm_summary: bool,
    must_text: str | None,
) -> Dict[str, Any]:
    hits = vec_search(question, url, collection, topk, ef, device, must_text)
    if not hits:
        log("no hits from qdrant")
        return {"primary": None, "alternatives": []}

    items: List[Dict[str, Any]] = []
    for h in hits:
        pl = h.payload or {}
        path = pl.get("path") or pl.get("file") or ""
        lang = pl.get("language") or ""
        kind = pl.get("kind") or ""
        name = pl.get("name") or pl.get("function_name") or ""
        code = _payload_code(pl)
        usage = guess_usage(code, path)

        disp_path = path if not repo_root else f"{repo_root.rstrip('/')}/{path.lstrip('/')}"
        items.append({
            "path": disp_path,
            "lang": lang,
            "kind": kind,
            "name": name,
            "code": code,
            "usage": usage,
        })
        
    js: List[Dict[str, Any]] = []
    if use_llm_summary and ask_text:
        prompt = build_prompt(question, items)
        log(f"prompt:\n{'-'*40}\n{prompt}\n{'-'*40}")
        try:
            resp = ask_text(prompt)
            log(f"ai raw:\n{resp}")
            js = parse_json(resp)
        except Exception as e:
            log(f"LLM summary failed: {e}")
            js = []

    if not js:
        for it in items:
            js.append({
                "script_path": it["path"],
                "purpose": "Utility script (heuristic)",
                "recommended_command": build_cmd(it["path"], it["code"]),
                "why": "Heuristic based on file type and argparse/getopts patterns",
                "confidence": 0.6,
            })

    for e in js:
        sp = e.get("script_path") or items[0]["path"]
        code = ""
        for it in items:
            if it["path"] == sp:
                code = it["code"]
                break
        if not e.get("recommended_command"):
            e["recommended_command"] = build_cmd(sp, code)
        if not e.get("purpose"):
            e["purpose"] = "Utility script"
        if "confidence" not in e:
            e["confidence"] = 0.7

    primary = js[0] if js else None
    alts = js[1:3] if len(js) > 1 else []
    return {"primary": primary, "alternatives": alts, "all": js}


# ---------- CLI ----------
def main():
    global DEBUG

    ap = argparse.ArgumentParser(description="Semantic search for repo scripts and suggest commands.")
    ap.add_argument("q", help="用户自然语言需求，如：enable kis / pmu reset / collect logs")
    ap.add_argument("--qdrant-url", default=QDRANT_URL)
    ap.add_argument("--collection", default=COLLECTION)
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--ef", type=int, default=128)
    ap.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    ap.add_argument("--repo-root", default=None, help="用于输出友好的绝对路径（用于终端直接复制执行）")
    ap.add_argument("--must-text", default=None, help="payload.code 必须包含的文本（全文过滤）")
    ap.add_argument("--no-llm", action="store_true", help="禁用 LLM 总结，仅用启发式输出")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出全部候选")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    DEBUG = args.debug

    res = answer(
        question=args.q,
        url=args.qdrant_url,
        collection=args.collection,
        topk=args.topk,
        ef=args.ef,
        device=args.device,
        repo_root=args.repo_root,
        use_llm_summary=(not args.no_llm),
        must_text=args.must_text,
    )

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    p = res["primary"]
    if not p:
        print("No match")
        return

    print(p["recommended_command"])
    print(f"\nScript : {p['script_path']}")
    print(f"Purpose: {p['purpose']}")
    print(f"Reason : {p['why']}")
    if res.get("alternatives"):
        print("\nAlternatives:")
        for a in res["alternatives"]:
            print(f"- {a['recommended_command']}\n  Script : {a['script_path']}\n  Purpose: {a['purpose']}")


if __name__ == "__main__":
    main()