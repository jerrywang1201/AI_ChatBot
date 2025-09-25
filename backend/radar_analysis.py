# backend/radar_analysis.py
import os
from typing import Optional, List, Dict, Any
from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION  = os.getenv("RADAR_COLLECTION", "radar_index")
EMB_MODEL   = os.getenv("RADAR_EMBED_MODEL", "all-MiniLM-L6-v2")


_EMBEDDER_MODEL: Optional[SentenceTransformer] = None

def get_embedder() -> SentenceTransformer:
    global _EMBEDDER_MODEL
    if _EMBEDDER_MODEL is None:
        _EMBEDDER_MODEL = SentenceTransformer(EMB_MODEL)
    return _EMBEDDER_MODEL

def _build_filter(only_component: Optional[str], phrase: Optional[str]) -> Optional[models.Filter]:
    must: List[models.Condition] = [
        models.FieldCondition(key="type", match=models.MatchValue(value="radar")),
    ]
    if only_component:
        must.append(models.FieldCondition(key="component", match=models.MatchValue(value=str(only_component))))
    if phrase:
        must.append(models.FieldCondition(key="content", match=models.MatchText(text=phrase)))
    return models.Filter(must=must) if must else None

def _client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)

def find_similar_radar_issues(
    query: str,
    topk: int = 12,
    component: Optional[str] = None,
    phrase: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
  
    [
      { "radar_id": "...", "component": "...", "score": 0.987,
        "title": "...", "description": "..." }
    ]
    """
    cli = _client()
    vec = get_embedder().encode(query).tolist()  
    qfilter = _build_filter(component, phrase)

    res = cli.query_points(
        collection_name=COLLECTION,
        query=vec,
        limit=topk,
        search_params=models.SearchParams(hnsw_ef=256),
        query_filter=qfilter,
        with_payload=True,
    )

    out: List[Dict[str, Any]] = []
    for pt in res.points or []:
        pl = pt.payload or {}
        title = (pl.get("title") or "").strip()
        desc = (pl.get("description") or "").strip()
        # 兜底：从 content 中抽 Description 段
        if not desc:
            content = pl.get("content") or ""
            idx = content.find("Description:")
            if idx >= 0:
                desc = content[idx + len("Description:"):].strip()
        out.append({
            "radar_id": pl.get("radar_id"),
            "component": pl.get("component"),
            "score": pt.score,
            "title": title,
            "description": desc,
        })
    return out

__all__ = ["find_similar_radar_issues"]