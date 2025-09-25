# vectorstore/qdrant_scripts_index.py

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
import os
import uuid

model = SentenceTransformer('all-MiniLM-L6-v2')
qdrant = QdrantClient(host="localhost", port=6333)

COLLECTION = "script_chunks"
DIM = 384

def init_collection():
    if COLLECTION not in [col.name for col in qdrant.get_collections().collections]:
        qdrant.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=DIM, distance=Distance.COSINE)
        )

def load_and_add_chunks():
    init_collection()
    points = []

    for root, _, files in os.walk("data/scripts"):
        for f in files:
            if f.endswith((".py", ".sh", ".md")):
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8', errors='ignore') as file:
                    lines = file.read().splitlines()
                    for i in range(0, len(lines), 20):
                        chunk = "\n".join(lines[i:i+20])
                        embedding = model.encode(chunk).tolist()
                        points.append(PointStruct(
                            id=str(uuid.uuid4()),
                            vector=embedding,
                            payload={
                                "text": chunk,
                                "file": path,
                                "lines": f"{i}-{i+20}"
                            }
                        ))
    if points:
        qdrant.upsert(collection_name=COLLECTION, points=points)

def search_scripts(query: str, top_k: int = 3):
    init_collection()
    embedding = model.encode(query).tolist()
    hits = qdrant.search(collection_name=COLLECTION, query_vector=embedding, limit=top_k)
    return [f"[File: {hit.payload['file']} lines {hit.payload['lines']}]\n{hit.payload['text']}" for hit in hits]