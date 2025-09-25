import os
from radarclient import RadarClient
from radarclient.authenticationstrategy import AuthenticationStrategyAppleConnect

def flatten_text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="ignore")
        except Exception:
            return x.decode(errors="ignore")
    if isinstance(x, (list, tuple, set)):
        return " ".join(flatten_text(i) for i in x)
    if isinstance(x, dict):
        for k in ("text", "value", "string", "description", "title", "name"):
            if k in x and x[k] is not None:
                return flatten_text(x[k])
        try:
            return " ".join(flatten_text(v) for v in x.values())
        except Exception:
            return str(x)
    for attr in ("text", "value", "string", "description", "title", "name"):
        if hasattr(x, attr):
            try:
                return flatten_text(getattr(x, attr))
            except Exception:
                pass
    try:
        return " ".join(flatten_text(i) for i in list(x))
    except Exception:
        pass
    return str(x)

user = os.environ.get("APPLECONNECT_USER")
pwd  = os.environ.get("APPLECONNECT_PASS")
if not user or not pwd:
    raise RuntimeError("请先 export APPLECONNECT_USER=xxx 以及 APPLECONNECT_PASS=xxx")

auth = AuthenticationStrategyAppleConnect()
auth.appleconnect_username = user
auth.appleconnect_password = pwd
radar = RadarClient(authentication_strategy=auth)

test_ids = [108595573, 108541908, 108383816]

items = radar.radars_for_ids(test_ids)
for r in items:
    rid   = getattr(r, "id", None)
    title = flatten_text(getattr(r, "title", ""))
    desc  = flatten_text(getattr(r, "description", ""))
    print("=" * 80)
    print(f"Radar {rid}")
    print("Title       :", title or "(无标题)")
    print("Description :", desc  or "(无描述)")