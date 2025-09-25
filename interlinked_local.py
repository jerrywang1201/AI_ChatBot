import os
from typing import Optional, Any

__all__ = ["AI"]

try:
    from interlinked import AI as _SDKAI
except ImportError:
    _SDKAI = None

from interlinked.core.clients.googleaiclient import GoogleAIClient

_FIXED_API_KEY = "in-YTxvz7PxS1WCcTvtnfBcfA"
_FIXED_MODEL = "gemini-2.5-flash"


class _Resp:
    def __init__(self, text: str):
        self.response = text


def _as_resp(ret: Any) -> Any:
    if ret is None:
        return _Resp("")
    if hasattr(ret, "ask"):
        return ret
    if isinstance(ret, _Resp):
        return ret
    if isinstance(ret, str):
        return _Resp(ret)
    if isinstance(ret, dict):
        try:
            return _Resp(ret["choices"][0]["message"]["content"])
        except Exception:
            pass
        for key in ("response", "text", "content"):
            v = ret.get(key)
            if isinstance(v, str):
                return _Resp(v)
        return _Resp(str(ret))
    txt = getattr(ret, "response", None)
    if isinstance(txt, str):
        return _Resp(txt)
    return ret


class AI:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("INTERLINKED_API_KEY", _FIXED_API_KEY)
        self.model = _FIXED_MODEL
        self.sdk = None
        if _SDKAI:
            try:
                self.sdk = _SDKAI(self.api_key)
            except TypeError:
                try:
                    self.sdk = _SDKAI()
                except Exception:
                    self.sdk = None
        try:
            self.client = GoogleAIClient(model_name=_FIXED_MODEL, api_key=_FIXED_API_KEY)
        except Exception:
            self.client = None

    def _ask(self, prompt: str) -> Any:
        if self.sdk and hasattr(self.sdk, "ask"):
            try:
                return _as_resp(self.sdk.ask(prompt=prompt, client=self.client))
            except TypeError:
                try:
                    return _as_resp(self.sdk.ask(prompt, self.client))
                except Exception as e:
                    return _Resp(f"[AI SDK ask error] {e}")
            except Exception as e:
                return _Resp(f"[AI SDK ask error] {e}")
        if self.sdk and hasattr(self.sdk, "chat"):
            try:
                ret = self.sdk.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    client=self.client,
                )
                return _as_resp(ret)
            except Exception as e:
                return _Resp(f"[AI SDK chat error] {e}")
        if self.sdk and hasattr(self.sdk, "completions"):
            try:
                comps = getattr(self.sdk, "completions")
                if hasattr(comps, "create"):
                    ret = comps.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        client=self.client,
                    )
                    return _as_resp(ret)
                if callable(comps):
                    ret = comps(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        client=self.client,
                    )
                    return _as_resp(ret)
            except Exception as e:
                return _Resp(f"[AI SDK completions error] {e}")
        return _Resp(f"[fallback echo] {prompt}")

    @classmethod
    def ask(cls, prompt: str) -> Any:
        return cls()._ask(prompt)