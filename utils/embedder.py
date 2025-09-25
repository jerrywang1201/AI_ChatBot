# utils/embedder.py
from sentence_transformers import SentenceTransformer
# utils/qdrant_client.py
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
class CodeEmbedder:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)

    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        return self.model.encode(texts, show_progress_bar=False, convert_to_numpy=True)



class CodeQdrantClient:
    def __init__(self, collection_name="codebase", dim=384, host="localhost", port=6333):
        self.collection_name = collection_name
        self.client = QdrantClient(host=host, port=port)
        self._init_collection(dim)

    def _init_collection(self, dim):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE)
            )

    def upload_embeddings(self, embeddings, metadatas):
        points = [
            PointStruct(id=i, vector=vec.tolist(), payload=meta)
            for i, (vec, meta) in enumerate(zip(embeddings, metadatas))
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def search(self, query_vector, top_k=5):
        return self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k
        )