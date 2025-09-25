# utils/repo_ast_to_qdrant.py
import os
import sys
import argparse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.ast_extractor import extract_functions_from_repo
from utils.embedder import CodeEmbedder
from utils.qdrant_helper import CodeQdrantClient

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="/Users/jialongwangsmacbookpro16/Desktop/chatbot/data/scripts/appleapfactorytest")
    ap.add_argument("--collection", default="buds_index")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6333)
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    print(f"📂 扫描仓库: {repo}")

    funcs = extract_functions_from_repo(repo, exts=(".cpp", ".h"))
    print(f"🔎 提取到函数: {len(funcs)}")

    if not funcs:
        print("⚠️ 未提取到任何函数，请检查 repo 路径与扩展名")
        return

    embedder = CodeEmbedder("all-MiniLM-L6-v2")
    texts = [f["code"] for f in funcs]
    print("🧠 生成向量 ...")
    vectors = embedder.embed(texts)

    client = CodeQdrantClient(collection_name=args.collection, dim=vectors.shape[1],
                              host=args.host, port=args.port)
    metas = [{
        "type": "function",
        "function_name": f["name"],
        "file": f["file"],
        "start_line": f["start_line"],
        "end_line": f["end_line"],
        "content": f["code"],
    } for f in funcs]

    print("📤 上传到 Qdrant ...")
    client.upload_embeddings(vectors, metas)
    print(f"✅ 完成：已写入 {len(metas)} 条函数记录到集合 `{args.collection}`")

if __name__ == "__main__":
    main()