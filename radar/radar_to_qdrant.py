# radar/radar_to_qdrant_summary.py

import argparse
from typing import Any, List, Dict, Optional
from sentence_transformers import SentenceTransformer
from radarclient import RadarClient
from radarclient.authenticationstrategy import AuthenticationStrategyAppleConnect
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  
from utils.qdrant_helper import CodeQdrantClient

EMBED_MODEL = "all-MiniLM-L6-v2"    
DEFAULT_COLLECTION = "radar_index"  

def flatten_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="ignore")
        except Exception:
            return x.decode(errors="ignore")
    if isinstance(x, (list, tuple, set)):
        return " ".join(flatten_text(i) for i in x)
    if isinstance(x, dict):
        for k in ("summary", "text", "value", "string", "description", "title", "name"):
            if k in x and x[k] is not None:
                return flatten_text(x[k])
        try:
            return " ".join(flatten_text(v) for v in x.values())
        except Exception:
            return str(x)
    for attr in ("summary", "text", "value", "string", "description", "title", "name"):
        if hasattr(x, attr):
            try:
                return flatten_text(getattr(x, attr))
            except Exception:
                pass
    try:
        return " ".join(flatten_text(i) for i in list(x))
    except Exception:
        pass
    return str(x)

def make_radar_client() -> RadarClient:
    #user = os.getenv("APPLECONNECT_USER")
    #pwd  = os.getenv("APPLECONNECT_PASS")
    user = "jerrywang" 
    pwd = "wjl13623617468WJL@"
    if not user or not pwd:
        raise SystemExit("❌ 请先设置 APPLECONNECT_USER / APPLECONNECT_PASS 环境变量")
    print(f"🔑 AppleConnect 用户: {user}")
    auth = AuthenticationStrategyAppleConnect()
    auth.appleconnect_username = user
    auth.appleconnect_password = pwd
    return RadarClient(authentication_strategy=auth)

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def extract_summary_from_item(item: Any) -> str:
    desc = _get(item, "description")
    if desc is None:
        return ""
    summ = getattr(desc, "summary", None)
    if summ is not None:
        try:
            if callable(summ):
                summ = summ()
        except Exception:
            pass
        text = flatten_text(summ).strip()
        if text:
            return text
    try:
        parts = []
        for entry in list(desc):
            parts.append(flatten_text(_get(entry, "text", "")))
        text = "\n\n".join(p for p in parts if p).strip()
        if text:
            return text
    except Exception:
        pass
    return flatten_text(desc).strip()

def fetch_component_radars_with_summary(
    radar: RadarClient,
    component_id: int,
    limit: int = 1000
) -> List[Dict]:
    comp = str(component_id)
    print(f"\n📡 正在拉取 component {comp} 的 radar 数据（limit={limit}）...")

    items: Optional[List[Any]] = None
    for call in (
        lambda: radar.find_radars({"component": {"id": component_id}}, limit=limit),
        lambda: radar.find_radars(component_id=component_id, limit=limit),
        lambda: radar.find_radars(components=[component_id], limit=limit),
    ):
        try:
            items = call()
            if items:
                break
        except Exception as e:
            print(f"⚠️ find_radars 调用失败: {e}")
            items = None

    if not items:
        try:
            q = radar.create_query()
            try:
                radar.add_groups_to_query(q, [component_id])
            except Exception:
                radar.add_group_to_query(q, component_id)
            items = radar.radars_for_query(q, limit=limit)
        except Exception as e:
            print(f"⚠️ radars_for_query 调用失败: {e}")
            items = None

    if not items:
        try:
            q = radar.create_query()
            try:
                radar.add_groups_to_query(q, [component_id])
            except Exception:
                radar.add_group_to_query(q, component_id)
            ids = radar.radar_ids_for_query(q, limit=limit) or []
            print(f"🔍 radar_ids_for_query 返回 {len(ids)} 个 ID")
            items = radar.radars_for_ids(ids[:limit]) if ids else []
        except Exception as e:
            print(f"⚠️ radar_ids_for_query 调用失败: {e}")
            items = []

    docs: List[Dict] = []
    for i, it in enumerate(items or []):
        rid   = _get(it, "id")
        title = flatten_text(_get(it, "title", "")).strip()
        desc  = extract_summary_from_item(it)
        print(f"  [{i+1}] Radar {rid} | Title: {title} | Summary 长度: {len(desc)}")
        text = f"Radar {rid}\nComponent: {comp}\nTitle: {title}\n\nSummary:\n{desc}"
        docs.append({
            "type": "radar",
            "component": comp,
            "radar_id": rid,
            "problem_id": rid,
            "title": title,
            "description": desc,
            "content": text,
        })

    print(f"📎 component {comp}: 拉到 {len(docs)} 条 radar（含 summary）")
    return docs

def main():
    ap = argparse.ArgumentParser(description="把 Radar 的 title + description.summary 写入 Qdrant")
    ap.add_argument("--components", "-c", nargs="+", type=int, required=True)
    ap.add_argument("--limit-per-component", type=int, default=4000)
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    ap.add_argument("--batch-size", type=int, default=400)
    ap.add_argument("--recreate", action="store_true")
    args = ap.parse_args()

    rc = make_radar_client()
    all_docs: List[Dict] = []
    for cid in args.components:
        all_docs.extend(fetch_component_radars_with_summary(rc, cid, args.limit_per_component))

    if not all_docs:
        raise SystemExit("⚠️ 没有可写入的数据（可能鉴权失败或组件为空）")

    print(f"\n🧠 开始向量化 {len(all_docs)} 条数据，模型：{EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)
    vectors = model.encode([d["content"] for d in all_docs], show_progress_bar=True)

    host = args.qdrant_url.split("//")[-1].split(":")[0]
    port = int(args.qdrant_url.split(":")[-1])
    print(f"🔌 连接 Qdrant: {args.qdrant_url} → 集合：{args.collection}（dim={vectors.shape[1]}）")

    client = CodeQdrantClient(
        collection_name=args.collection,
        dim=vectors.shape[1],
        host=host,
        port=port,
        recreate=args.recreate,
    )
    print(f"⬆️ 开始批量写入，每批 {args.batch_size} 条...")
    client.upload_embeddings(
        embeddings=vectors,
        metadatas=all_docs,
        start_id=0,
        batch_size=args.batch_size,
    )
    print(f"✅ 完成：共写入 {len(all_docs)} 条 Radar(summary) 到 `{args.collection}`")

if __name__ == "__main__":
    main()