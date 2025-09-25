from qdrant_client import QdrantClient

c = QdrantClient(url="http://127.0.0.1:6333")

points, _ = c.scroll(
    collection_name="radar_index",
    limit=5,
    with_vectors=False,
    with_payload=True
)

for p in points:
    print(f"ID={p.id}")
    print(p.payload)
    print("-" * 80)