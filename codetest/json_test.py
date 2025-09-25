# exact_match_search.py
from qdrant_client import QdrantClient, models
import re

PHRASE = "aop_sensor_stream"

def main():
    c = QdrantClient(url="http://127.0.0.1:6333")

    flt = models.Filter(
        must=[
            models.FieldCondition(
                key="content",
                match=models.MatchText(text=PHRASE)
            )
        ]
    )

    next_page = None
    found = 0
    while True:
        points, next_page = c.scroll(
            collection_name="code_index",
            limit=200,               
            with_payload=True,
            with_vectors=False,
            offset=next_page,
            scroll_filter=flt,       
        )
        if not points:
            break

        for pt in points:
            pl = pt.payload
            code = pl.get("content") or ""
            if re.search(re.escape(PHRASE), code, flags=re.IGNORECASE):
                found += 1
                print(f"[{found}] {pl.get('function_name')}  <{pl.get('file')}:{pl.get('start_line')}-{pl.get('end_line')}>")
                lines = code.splitlines()
                for idx, line in enumerate(lines):
                    if re.search(re.escape(PHRASE), line, flags=re.IGNORECASE):
                        start = max(0, idx-2)
                        end   = min(len(lines), idx+3)
                        snippet = "\n".join(lines[start:end])
                        print(snippet)
                        print("-" * 80)
        if next_page is None:
            break

    if found == 0:
        print("没有匹配到包含短语的函数。可以尝试这些变体：")
        print("- 'imu_stream'  (下划线形式)")
        print("- 'IMU stream'  (大小写变体)")
        print("- 'imu streaming' / 'imu data stream'")

if __name__ == "__main__":
    main()