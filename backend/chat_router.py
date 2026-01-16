# backend/chat_router.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


from backend.unified_search import handle_natural_query, handle_log_or_scene


try:
    from ai.my_interlinked_core import ask_text
except Exception:
    def ask_text(prompt: str) -> str:
        return "[fallback echo]\n" + prompt


@dataclass
class _SessionState:
    pending_questions: List[str] = field(default_factory=list)
    user_context: Dict[str, str] = field(default_factory=dict)
    last_query: Optional[str] = None


_sessions: Dict[str, _SessionState] = {}


def _get_state(session_id: Optional[str]) -> _SessionState:
    sid = (session_id or "default").strip() or "default"
    state = _sessions.get(sid)
    if state is None:
        state = _SessionState()
        _sessions[sid] = state
    return state


def _gen_followups(initial_query: str) -> List[str]:
   
    prompt = f"""
你是调试助手。用户最初的问题是：
\"{initial_query}\"

请给出 3 个短而具体的追问，以帮助缩小排查范围。
仅返回 Python 列表（如：["设备型号？","系统版本？","是否重启后复现？"]），不要解释。
"""
    raw = ask_text(prompt)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            lst = json.loads(raw.strip())
            return lst if isinstance(lst, list) else []
        except Exception:
            return []
    return []


def _looks_like_log(text: str) -> bool:
   
    return (
        ("\n" in text and len(text.splitlines()) >= 3) and
        (
            re.search(r"\b\d{4}-\d{2}-\d{2}[T _]\d{2}:\d{2}", text)
            or re.search(r"\b(ERROR|FAIL|FAILED|Exception|timeout)\b", text, re.I)
        )
    )


def _looks_like_scenario(text: str) -> bool:
    
    return re.search(r"(执行|运行|输入|下发|run|invoke|call).+(报错|失败|error|fail|timeout)", text, re.I) is not None


def _too_vague(text: str) -> bool:
   
    t = (text or "").strip()
    if len(t) < 8:
        return True
    vague_terms = ["不行", "有问题", "失败了", "怎么解决", "有报错", "不工作", "异常"]
    return any(v in t for v in vague_terms)


def need_followups(text: str) -> bool:
   
    if _looks_like_log(text) or _looks_like_scenario(text):
        return False
    return _too_vague(text)


def reset_state(session_id: Optional[str] = None):
    """外部可调用，清理本地会话态"""
    state = _get_state(session_id)
    state.pending_questions.clear()
    state.user_context.clear()
    state.last_query = None


def route_user_input(query: str, *, force_followups: bool = False, session_id: Optional[str] = None) -> str:
  
    state = _get_state(session_id)

    query = (query or "").strip()
    if not query:
        return "请描述你的问题或贴出相关日志。"

   
    if state.pending_questions and state.last_query and query not in ("1", "2"):
        current_q = state.pending_questions.pop(0)
        state.user_context[current_q] = query
        if state.pending_questions:
           
            return state.pending_questions[0]
       
        query = state.last_query + "\n\n" + "\n".join(f"{k}: {v}" for k, v in state.user_context.items())

   
    if state.last_query is None:
        state.last_query = query
        state.user_context = {}
        if force_followups or need_followups(query):
            state.pending_questions = _gen_followups(query)
            if state.pending_questions:
                return state.pending_questions[0]

    
    try:
        if _looks_like_log(query) or _looks_like_scenario(query):
            answer = handle_log_or_scene(query)
        else:
            answer = handle_natural_query(query)

    
        reset_state(session_id)
        return answer

    except Exception as e:
        reset_state(session_id)
        return f"❌ Unified search failed: {e}"
