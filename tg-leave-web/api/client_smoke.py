# -*- coding: utf-8 -*-
"""客户端冒烟测试：连接已运行的 FastAPI 服务，演示模式跑通完整链路。"""
import json
import sys
import time
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787").rstrip("/")
PASS, FAIL = 0, 0


def req(method, path, data=None, raw=False):
    url = BASE + path
    body = json.dumps(data).encode("utf-8") if data is not None else None
    r = urllib.request.Request(url, data=body, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            if raw:
                return resp.status, resp.read()
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


print(f"=== 客户端冒烟测试（演示模式）@ {BASE} ===")

r = req("GET", "/api/version")
check("GET /api/version", r[0] == 200 and str(r[1].get("version", "")).startswith("v3"),
      f"-> {r[1].get('version')}, changelog={len(r[1].get('changelog', []))} 条")

r = req("POST", "/api/login/start", {"demo": True})
check("POST /api/login/start(demo)", r[0] == 200, f"-> {r[1]}")

ok_login = False
for _ in range(60):
    r = req("GET", "/api/login/state")
    if r[1].get("logged_in"):
        ok_login = True
        break
    time.sleep(0.1)
check("登录态轮询 -> logged_in", ok_login, f"-> {req('GET', '/api/login/state')[1]}")

r = req("POST", "/api/dialogs/refresh")
check("POST /api/dialogs/refresh", r[0] == 200)
time.sleep(0.5)
r = req("GET", "/api/dialogs")
items = r[1].get("items", [])
check("GET /api/dialogs 非空", len(items) > 0, f"-> {len(items)} 个会话")
check("会话含风险字段", all("risk" in d and "risk_cell" in d for d in items))
demo_risk = [d for d in items if d.get("risk")]
check("演示含标注风险的会话", len(demo_risk) >= 3, f"-> {len(demo_risk)} 个")
check("配额字段（500 上限 + 文案）",
      r[1].get("quota_limit") == 500 and "名额" in (r[1].get("quota_text") or ""),
      f"-> {r[1].get('quota_text')}")

# 重进：直接粘贴的坏目标要被拒（反向）
r = req("POST", "/api/actions/rejoin", {"kind": "username", "value": "", "raw": "https://example.com/x"})
check("重进：非 t.me 链接被拒", r[0] == 200 and r[1].get("state") == "fail", f"-> {r[1]}")
# 停止端点存在且可调用（demo 批量未跑时只是置标志）
r = req("POST", "/api/actions/cancel", {})
check("POST /api/actions/cancel", r[0] == 200 and r[1].get("ok") is True)

pks = [d["peer_key"] for d in items[:4]]
r = req("POST", "/api/stats", {"peer_keys": pks, "force": True})
check("POST /api/stats", r[0] == 200 and len(r[1].get("stats", {})) == 4)

r = req("GET", "/api/devices")
check("GET /api/devices", r[0] == 200 and len(r[1].get("devices", [])) == 2)

r = req("GET", "/api/folders")
check("GET /api/folders", r[0] == 200 and len(r[1].get("folders", [])) >= 1)

target = items[0]["peer_key"]
r = req("POST", "/api/whitelist", {"peer_keys": [target], "on": True})
check("POST /api/whitelist(on)", r[0] == 200)
r = req("GET", "/api/dialogs")
now = next((d for d in r[1]["items"] if d["peer_key"] == target), {})
check("白名单生效", now.get("whitelisted") is True)
req("POST", "/api/whitelist", {"peer_keys": [target], "on": False})

r = req("POST", "/api/actions/leave", {"peer_keys": [target]})
check("POST /api/actions/leave(demo)", r[1].get("results", [{}])[0].get("ok") is True, f"-> {r[1].get('results')}")

r = req("POST", "/api/actions/destroy", {"peer_keys": [target], "confirm_word": "删错"})
check("POST /api/actions/destroy(错词) 拒绝", r[1].get("results", [{}])[0].get("ok") is False)
r = req("POST", "/api/actions/destroy", {"peer_keys": [target], "confirm_word": "删除"})
check("POST /api/actions/destroy(删除) 通过", r[1].get("results", [{}])[0].get("ok") is True)

r = req("POST", "/api/actions/leave_many", {"peer_keys": pks[:2]})
check("POST /api/actions/leave_many(demo)",
      r[0] == 200 and all(x.get("ok") for x in r[1].get("results", []))
      and r[1].get("remaining") == [] and r[1].get("interrupted") is False, f"-> {r[1]}")
r = req("POST", "/api/actions/resume")
check("POST /api/actions/resume(空任务)",
      r[0] == 200 and r[1].get("results") == [] and bool(r[1].get("note")), f"-> {r[1]}")
r = req("GET", "/api/pending")
check("GET /api/pending 空", r[0] == 200 and r[1].get("keys") == [])
r = req("DELETE", "/api/pending")
check("DELETE /api/pending", r[0] == 200)

r = req("GET", "/api/history")
check("GET /api/history", r[0] == 200 and len(r[1].get("items", [])) >= 1)

r = req("POST", "/api/snapshot/save", {"slot": 0})
check("POST /api/snapshot/save", r[0] == 200)
r = req("POST", "/api/snapshot/save", {"slot": 1})
r = req("POST", "/api/snapshot/compare", {"slot_a": 0, "slot_b": 1})
check("POST /api/snapshot/compare", r[0] == 200 and "diff" in r[1])

st, content = req("POST", "/api/export", {"fmt": "csv", "peer_keys": pks}, raw=True)
ok_csv = st == 200 and content[:3] == b"\xef\xbb\xbf"
check("POST /api/export(csv)", ok_csv, f"-> {len(content)} bytes, BOM={content[:3]}")
st, content = req("POST", "/api/export", {"fmt": "xlsx", "peer_keys": pks}, raw=True)
check("POST /api/export(xlsx)", st == 200 and content[:2] == b"PK", f"-> {len(content)} bytes")

r = req("GET", "/api/logs")
check("GET /api/logs", r[0] == 200 and len(r[1].get("logs", [])) > 0)

print(f"\n=== 结果：通过 {PASS} / 失败 {FAIL} ===")
sys.exit(1 if FAIL else 0)
