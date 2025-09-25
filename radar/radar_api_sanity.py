# radar_api_sanity.py
import os
from typing import Any, Iterable

from radarclient import RadarClient
from radarclient.authenticationstrategy import AuthenticationStrategyAppleConnect

COMPONENT_IDS = [1345828, 1711116]   
PER_COMP_LIMIT = 5                   
SHOW_KV_FOR_FIRST = 3               

def flatten_text(x: Any) -> str:
 
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

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def make_client() -> RadarClient:
    user = os.getenv("APPLECONNECT_USER")
    pwd  = os.getenv("APPLECONNECT_PASS")
    if not user or not pwd:
        raise SystemExit("❌ 请先设置 APPLECONNECT_USER / APPLECONNECT_PASS 环境变量")
    auth = AuthenticationStrategyAppleConnect()
    auth.appleconnect_username = user
    auth.appleconnect_password = pwd
    return RadarClient(authentication_strategy=auth)

def try_fetch_component_radars(radar: RadarClient, component_id: int, limit: int):

    for call in (
        lambda: radar.find_radars({"component": {"id": component_id}}, limit=limit),
        lambda: radar.find_radars(component_id=component_id, limit=limit),
        lambda: radar.find_radars(components=[component_id], limit=limit),
    ):
        try:
            items = call()
            if items:
                return items
        except Exception:
            pass

    # B) query → add_groups_to_query / add_group_to_query → radars_for_query
    try:
        q = radar.create_query()
        try:
            radar.add_groups_to_query(q, [component_id])
        except Exception:
            radar.add_group_to_query(q, component_id)
        items = radar.radars_for_query(q, limit=limit)
        if items:
            return items
    except Exception:
        pass


    try:
        q = radar.create_query()
        try:
            radar.add_groups_to_query(q, [component_id])
        except Exception:
            radar.add_group_to_query(q, component_id)
        ids = radar.radar_ids_for_query(q, limit=limit)
        if ids:
            items = radar.radars_for_ids(ids[:limit])
            return items
    except Exception as e:
        print(f"⚠️ 组件 {component_id} 退化查询失败：{e}")

    return []


def get_problem_description_text(radar: RadarClient, problem_id: int) -> str:

    try:

        resp = radar.send_request(
            "GET",
            f"/problems/{problem_id}/description",
            headers={"Accept": "text/plain"}
        )
        if hasattr(resp, "text"):
            text = (resp.text or "").strip()
            if text:
                return text
    except Exception:
        pass

    # 回退到 KV
    try:
        kv = radar.key_values_for_radar_id(problem_id)
        desc = kv.get("problemDescription") or kv.get("description") or ""
        return flatten_text(desc).strip()
    except Exception:
        return ""

def main():
    radar = make_client()


    try:
        me = radar.current_user()
        print(f"✅ 登录成功，当前用户：{flatten_text(getattr(me, 'name', 'unknown'))}")
    except Exception as e:
        raise SystemExit(f"❌ 登录失败：{e}")


    for comp in COMPONENT_IDS:
        print(f"\n=== 组件 {comp}：拉取前 {PER_COMP_LIMIT} 条 ===")
        items = try_fetch_component_radars(radar, comp, PER_COMP_LIMIT)
        if not items:
            print("⚠️ 没拉到任何条目（可能无权限/组件为空/API变更）")
            continue

        for idx, r in enumerate(items, 1):
            rid   = _get(r, "id")

            title = flatten_text(_get(r, "title", "")) or ""
            if not title and rid:
                try:
                    kv = radar.key_values_for_radar_id(rid)
                    title = flatten_text(kv.get("problemTitle") or kv.get("title") or "")
                except Exception:
                    pass


            desc_txt = get_problem_description_text(radar, rid) if rid else ""

            print(f"[{idx}] Radar {rid}")
            print("   Title      :", (title or "(空)"))
            print("   Desc(sample):", (desc_txt[:120] + "…") if desc_txt else "(空)")


        print("\n— 使用 key_values_for_radar_id() 验证前几条 —")
        for r in items[:SHOW_KV_FOR_FIRST]:
            rid = _get(r, "id")
            if not rid:
                continue
            try:
                kv = radar.key_values_for_radar_id(rid)
                kv_title = flatten_text(kv.get("problemTitle") or kv.get("title") or "")
                kv_desc  = flatten_text(kv.get("problemDescription") or kv.get("description") or "")
                print(f"  Radar {rid}:")
                print("    KV-Title :", (kv_title[:100] + "…") if kv_title else "(空)")
                print("    KV-Desc  :", (kv_desc[:120] + "…") if kv_desc else "(空)")
            except Exception as e:
                print(f"  ⚠️ key_values_for_radar_id({rid}) 失败：{e}")

if __name__ == "__main__":
    main()