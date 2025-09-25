# quick_search.py
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

def main():
    q = QdrantClient(url="http://127.0.0.1:6333")
    model = SentenceTransformer("all-MiniLM-L6-v2")

    query = "imu stream 不吐数据"
    vec = model.encode(query).tolist()
    hits = q.search(collection_name="code_index", query_vector=vec, limit=5)

    for i, h in enumerate(hits, 1):
        p = h.payload
        print(f"[{i}] {p.get('function_name')}  <{p.get('file')}:{p.get('start_line')}-{p.get('end_line')}>  score={h.score:.3f}")
        print((p.get('content') or '')[:400], "...\n")

if __name__ == "__main__":
    main()