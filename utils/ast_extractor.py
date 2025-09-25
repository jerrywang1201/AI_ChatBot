import os
from typing import List, Dict
from tree_sitter import Language, Parser

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_PATH = os.path.join(PROJECT_ROOT, "build", "my-languages.so")

CPP = Language(LIB_PATH, "cpp")
_parser = Parser()
_parser.set_language(CPP)

def _node_text(src: str, node) -> str:
    return src[node.start_byte: node.end_byte]

def extract_cpp_functions(file_path: str) -> List[Dict]:

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    tree = _parser.parse(code.encode("utf-8"))
    root = tree.root_node

    out = []
    def walk(n):
        if n.type == "function_definition":
            name = None
        
            for ch in n.children:
                if ch.type == "function_declarator":
                    for g in ch.children:
                        if g.type == "identifier":
                            name = _node_text(code, g)
                            break
            if name:
                out.append({
                    "name": name,
                    "code": _node_text(code, n),
                    "file": file_path,
                    "start_line": n.start_point[0] + 1,
                    "end_line": n.end_point[0] + 1,
                })
        for c in n.children:
            walk(c)
    walk(root)
    return out

def extract_functions_from_repo(repo_dir: str, exts=(".cpp", ".h")) -> List[Dict]:
    """遍历仓库，聚合所有函数"""
    results = []
    for r, _, files in os.walk(repo_dir):
        for f in files:
            if f.endswith(exts):
                full = os.path.join(r, f)
                try:
                    results.extend(extract_cpp_functions(full))
                except Exception as e:
                    print(f"⚠️ 跳过 {full}: {e}")
    return results