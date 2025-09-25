# radar/search_imu_fail.py
import argparse
from typing import Optional, List

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer


def build_filter(only_component: Optional[str], phrase: Optional[str]) -> Optional[models.Filter]:
    must: List[models.Condition] = [
        models.FieldCondition(key="type", match=models.MatchValue(value="radar")),
    ]
    if only_component:
        must.append(models.FieldCondition(key="component", match=models.MatchValue(value=str(only_component))))
    if phrase:
        must.append(models.FieldCondition(key="content", match=models.MatchText(text=phrase)))
    return models.Filter(must=must) if must else None


def main():
    ap = argparse.ArgumentParser(description="在 Qdrant 中检索 Radar（title+description 已入库）")
    ap.add_argument("--collection", default="radar_index", help="Qdrant 集合名（默认 radar_index）")
    ap.add_argument("--qdrant-url", default="http://127.0.0.1:6333", help="Qdrant HTTP 地址")
    ap.add_argument("--query", "-q", default="This is an IMU problem. After running aopsensor dump, the data looks like the sensor was never initialized", help="语义查询内容")
    ap.add_argument("--topk", "-k", type=int, default=12, help="返回条数")
    ap.add_argument("--component", "-c", help="仅查看某个 component（字符串或数字）")
    ap.add_argument("--phrase", "-p", help="可选：服务端全文预过滤短语，如 'imu' 或 'IMU fail'")
    args = ap.parse_args()

    client = QdrantClient(url=args.qdrant_url)
    model = SentenceTransformer("all-MiniLM-L6-v2")

   
    qvec = model.encode(args.query).tolist()

   
    qfilter = build_filter(args.component, args.phrase)

    res = client.query_points(
        collection_name=args.collection,
        query=qvec,
        limit=args.topk,
        search_params=models.SearchParams(hnsw_ef=256),
        query_filter=qfilter,
        with_payload=True,
    )

    if not res.points:
        print("（没有命中，试试放宽 --phrase 或改 --query）")
        return

    for i, pt in enumerate(res.points, 1):
        pl = pt.payload or {}
        rid = pl.get("radar_id")
        comp = pl.get("component")
        title = (pl.get("title") or "").strip()
        desc = (pl.get("description") or "").strip()

        
        if not desc:
            content = pl.get("content") or ""
         
            idx = content.find("Description:")
            if idx >= 0:
                desc = content[idx + len("Description:"):].strip()

        print(f"[{i}] Radar {rid} (comp {comp})  score={pt.score:.3f}")
        print("Title:", title[:200] or "(空)")
        print("Description:", (desc[:800] + ("…" if len(desc) > 800 else "")) or "(空)")
        print("-" * 80)


if __name__ == "__main__":
    main()