# utils/qdrant_client.py
import os
from typing import List, Dict, Any, Iterable, Union, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
import numpy as np

class CodeQdrantClient:
    def __init__(
        self,
        collection_name: str = "code_index",
        dim: int = 384,
        host: str = "127.0.0.1",  
        port: int = 6333,    
        timeout: float = 60.0,
        recreate: bool = False,
    ):
        self.collection_name = collection_name

        for k in ["HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"]:
            os.environ.pop(k, None)

        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"

        url = f"http://{host}:{port}"
        print(f"🔌 QdrantClient connecting via HTTP URL: {url}")
        self.client = QdrantClient(url=url, timeout=timeout)

        try:
            cols = self.client.get_collections()
            print(f"✅ Connected. Existing collections: {[c.name for c in cols.collections]}")
        except Exception as e:
            raise RuntimeError(
                f"❌ Cannot connect to Qdrant at {url}. "
                f"请先确认: curl -s {url}/readyz 应返回 ok"
            ) from e

        self._ensure_collection(dim, recreate=recreate)

    def _ensure_collection(self, dim: int, recreate: bool = False):
        try:
            exists = any(c.name == self.collection_name
                         for c in self.client.get_collections().collections)
        except Exception as e:
            raise RuntimeError("❌ 无法列举集合：HTTP 连接异常。") from e

        if recreate and exists:
            print(f"♻️  Deleting existing collection: {self.collection_name}")
            self.client.delete_collection(self.collection_name)
            exists = False

        if not exists:
            print(f"🆕 Creating collection: {self.collection_name} (dim={dim})")
            self.client.recreate_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
        else:
            print(f"📦 Using existing collection: {self.collection_name}")

    def upload_embeddings(
        self,
        embeddings: Iterable[Union[list, "np.ndarray"]],
        metadatas: List[Dict[str, Any]],
        start_id: int = 0,
        batch_size: int = 2000,
    ):
        def to_list(v): return v.tolist() if hasattr(v, "tolist") else v
        total = len(metadatas)
        i = 0
        while i < total:
            j = min(i + batch_size, total)
            points = [
                PointStruct(
                    id=start_id + k,
                    vector=to_list(embeddings[k]),
                    payload=metadatas[k],
                )
                for k in range(i, j)
            ]
            self.client.upsert(collection_name=self.collection_name, points=points)
            print(f"📤 Uploaded {j} / {total}")
            i = j

    def search(self, query_vector, top_k: int = 5):
        if hasattr(query_vector, "tolist"):
            query_vector = query_vector.tolist()
        return self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
        )