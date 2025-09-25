# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import json
import uuid
import hashlib
import argparse
from typing import Any, Dict, List, Tuple

from tqdm import tqdm
from tree_sitter_languages import get_parser
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sentence_transformers import SentenceTransformer

DEFAULT_REPO = "/ABS/PATH/TO/YOUR/REPO" 
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"
DEFAULT_COLLECTION = "repo_code"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_BATCH_SIZE = 512

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    "node_modules", "dist", "build", "out", ".venv", "venv", "env",
    ".idea", ".vscode", ".DS_Store"
}

PY_PARSER = get_parser("python")
BASH_PARSER = get_parser("bash")

def file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def sha1_bytes(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


def _node_text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode(errors="ignore")


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for c in n.children[::-1]:
            stack.append(c)


def extract_python(src: bytes, tree) -> List[Tuple[str, str, Any]]:
    out: List[Tuple[str, str, Any]] = []
    for n in _walk(tree.root_node):
        t = n.type
        if t == "function_definition":
            name = ""
            for c in n.children:
                if c.type == "identifier":
                    name = _node_text(src, c)
                    break
            out.append(("function", name, n))
        elif t == "class_definition":
            name = ""
            for c in n.children:
                if c.type == "identifier":
                    name = _node_text(src, c)
                    break
            out.append(("class", name, n))
        elif t in ("import_statement", "import_from_statement"):
            out.append(("import", _node_text(src, n).strip(), n))
    return out

def extract_bash(src: bytes, tree) -> List[Tuple[str, str, Any]]:
    out: List[Tuple[str, str, Any]] = []
    for n in _walk(tree.root_node):
        t = n.type
        if t == "function_definition":
            name = ""
            for c in n.children:
                if c.type in ("word", "identifier"):
                    name = _node_text(src, c)
                    break
            out.append(("function", name, n))
        elif t in ("command", "simple_command"):
            name = ""
            for c in n.children:
                if c.type in ("command_name", "word", "identifier"):
                    v = _node_text(src, c).strip()
                    if v:
                        name = v
                        break
            if name:
                out.append(("command", name, n))
    return out

def to_record(repo_root: str, rel_path: str, lang: str, kind: str, name: str, src: bytes, node) -> Dict[str, Any]:
    return {
        "id": sha1_bytes((rel_path + kind + (name or "") + str(node.start_byte) + str(node.end_byte)).encode()),
        "repo": repo_root,
        "path": rel_path,
        "language": lang,
        "kind": kind,
        "name": name,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "start_byte": node.start_byte,
        "end_byte": node.end_byte,
        "code": _node_text(src, node),
        "file_sha1": sha1_bytes(src),
        "ts": int(time.time()),
    }


def make_point_id(rec: Dict[str, Any]) -> str:
    key = f"{rec['path']}|{rec['kind']}|{rec.get('name','')}|{rec['start_byte']}|{rec['end_byte']}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

def ensure_collection(cli: QdrantClient, name: str, dim: int):

    exists = cli.collection_exists(name)
    if not exists:
        print(f"🆕 Creating collection: {name} (dim={dim}, named vectors: code/meta)")
        cli.create_collection(
            collection_name=name,
            vectors_config={
                "code": qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                "meta": qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            },
            on_disk_payload=True,
        )
    else:
        info = cli.get_collection(name)
        try:
            if hasattr(info, "config") and info.config and getattr(info.config, "params", None):
                params = info.config.params
                vecs = getattr(params, "vectors", None)
                if isinstance(vecs, dict):
                    print(f"📦 Using existing collection: {name} (vectors={list(vecs.keys())})")
                else:
                    print(f"📦 Using existing collection: {name}")
            else:
                print(f"📦 Using existing collection: {name}")
        except Exception:
            print(f"📦 Using existing collection: {name}")


def push_batch(cli: QdrantClient, model: SentenceTransformer, batch: List[Dict[str, Any]], collection: str):
    if not batch:
        return

    code_texts = [r["code"] for r in batch]
    meta_texts = [f'{r.get("name","")} | {r.get("kind","")} | {r.get("path","")}' for r in batch]

    code_vecs = model.encode(code_texts, normalize_embeddings=True, show_progress_bar=False)
    meta_vecs = model.encode(meta_texts, normalize_embeddings=True, show_progress_bar=False)

    points: List[qm.PointStruct] = []
    for r, cv, mv in zip(batch, code_vecs, meta_vecs):
        pid = make_point_id(r)
        payload = dict(r) 
        payload["stable_id"] = r["id"]

        points.append(
            qm.PointStruct(
                id=pid,
                vector={"code": cv.tolist(), "meta": mv.tolist()},
                payload=payload,
            )
        )

    cli.upsert(collection_name=collection, points=points)
    print(f"📤 Upsert {len(points)} points")

def main():
    ap = argparse.ArgumentParser(description="Index Python/Bash repo into Qdrant (named vectors: code/meta)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="Root of your scripts repo")
    ap.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        raise SystemExit(f"❌ repo not found: {repo}")

    cli = QdrantClient(url=args.qdrant_url)
    try:
        cols = cli.get_collections()
        print(f"✅ Qdrant OK. Existing: {[c.name for c in cols.collections]}")
    except Exception as e:
        raise SystemExit(f"❌ Cannot connect Qdrant @ {args.qdrant_url}: {e}")

    model = SentenceTransformer(args.model)
    dim = len(model.encode(["ping"], normalize_embeddings=True, show_progress_bar=False)[0])
    print(f"🧠 Embedder ready: dim={dim}")

    ensure_collection(cli, args.collection, dim)

    files: List[Tuple[str, str]] = []
    for root, dirs, fnames in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in fnames:
            p = os.path.join(root, f)
            if f.endswith((".py", ".pyw", ".pyx")):
                files.append(("python", p))
            elif f.endswith((".sh", ".bash")):
                files.append(("bash", p))

    if not files:
        print("⚠️ 未发现 Python/Bash 文件。请检查 --repo 路径。")
        return

    batch: List[Dict[str, Any]] = []
    parsed_count = 0
    for lang, path in tqdm(files, desc="Indexing repo"):
        try:
            src = file_bytes(path)
            if not src:
                continue
            if lang == "python":
                items = extract_python(src, PY_PARSER.parse(src))
            else:
                items = extract_bash(src, BASH_PARSER.parse(src))

            rel = os.path.relpath(path, repo)
            for kind, name, node in items:
                rec = to_record(repo, rel, lang, kind, name, src, node)
                batch.append(rec)
                parsed_count += 1
                if len(batch) >= args.batch_size:
                    push_batch(cli, model, batch, args.collection)
                    batch.clear()
        except Exception:
            continue

    if batch:
        push_batch(cli, model, batch, args.collection)

    try:
        cnt = cli.count(collection_name=args.collection, exact=False)
        points = getattr(cnt, "count", "unknown")
    except Exception:
        try:
            info = cli.get_collection(args.collection)
            points = None
            if hasattr(info, "points_count"):
                points = info.points_count
            if points is None:
                d = info.dict() if hasattr(info, "dict") else getattr(info, "__dict__", {}) or {}
                points = (d.get("result", {}) or {}).get("points_count") or d.get("points_count") or "unknown"
        except Exception:
            points = "unknown"

    print(f"✅ 完成：集合 `{args.collection}` 当前 points={points} （本次解析出 {parsed_count} 条片段）")


if __name__ == "__main__":
    main()