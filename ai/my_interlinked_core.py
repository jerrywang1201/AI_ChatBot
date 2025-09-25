# ai/my_interlinked_core.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import inspect
import traceback
from typing import Optional

try:
    from interlinked_local import AI as _SDKAI
except Exception:
    _SDKAI = None

import requests

_MODEL   = os.getenv("INTERLINKED_MODEL", "gemini-2.5-flash")
_BASEURL = os.getenv("INTERLINKED_BASE_URL")
_APIKEY  = os.getenv("INTERLINKED_API_KEY")

_SDK_SINGLETON: Optional[object] = None


def _get_sdk() -> Optional[object]:
    global _SDK_SINGLETON
    if _SDK_SINGLETON is not None:
        return _SDK_SINGLETON
    if not _SDKAI:
        return None

    try:
        params = set(inspect.signature(_SDKAI.__init__).parameters.keys())
        params.discard("self")
        kwargs = {}
        if "model" in params:
            kwargs["model"] = _MODEL
        if "api_key" in params and _APIKEY:
            kwargs["api_key"] = _APIKEY
        if "base_url" in params and _BASEURL:
            kwargs["base_url"] = _BASEURL
        if "token" in params and _APIKEY and "api_key" not in kwargs:
            kwargs["token"] = _APIKEY

        _SDK_SINGLETON = _SDKAI(**kwargs) if kwargs else _SDKAI()
        return _SDK_SINGLETON
    except Exception:
        print("[my_interlinked_core] SDK init failed:\n", traceback.format_exc())
        _SDK_SINGLETON = None
        return None


def _http_openai_compatible(prompt: str) -> Optional[str]:

    if not _BASEURL:
        return None
    url = _BASEURL.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if _APIKEY:
        headers["Authorization"] = f"Bearer {_APIKEY}"

    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                pass
            if "response" in data:
                return data["response"]
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        print("[my_interlinked_core] HTTP call failed:\n", traceback.format_exc())
        return None


def ask_text(prompt: str) -> str:

    sdk = _get_sdk()
    if sdk and hasattr(sdk, "ask"):
        try:
            resp = sdk.ask(prompt=prompt)
            return getattr(resp, "response", str(resp))
        except Exception:
            print("[my_interlinked_core] SDK ask failed:\n", traceback.format_exc())

    resp = _http_openai_compatible(prompt)
    if isinstance(resp, str):
        return resp

    return "[fallback echo]\n" + prompt