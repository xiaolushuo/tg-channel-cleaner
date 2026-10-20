# -*- coding: utf-8 -*-
"""FastAPI 应用：REST 接口 + WebSocket 日志流。前端（React）的唯一后端。"""
import asyncio
import io
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from .core import constants as C
from .core import export as X
from .core import engine as E
from .core import legacy as LG
from .core import store as S
from .core import loghub as L

app = FastAPI(title=C.APP_NAME, version=C.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

STATE = {
    "engine": None,
    "accounts": S.AccountStore(),
    "busy": False,
    "fetch_error": "",   # 最近一次会话拉取失败的原因（前端轮询时展示，不再静默空白）
}

# 前端静态资源目录（生产模式下直接托管 React 构建产物）
UI_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui", "dist")


def engine():
    e = STATE["engine"]
    if not e or not e.logged_in:
        return None
    return e


def require_engine():
    e = engine()
    if not e:
        raise HTTPError(409, "未登录或无引擎")
    return e


class HTTPError(Exception):
    def __init__(self, status, detail):
        self.status = status
        self.detail = detail


@app.exception_handler(HTTPError)
async def http_err(_req, exc: HTTPError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status, content={"error": exc.detail})


# ---------------- 版本 / 更新日志 ----------------
@app.get("/api/version")
def version():
    return {
        "name": C.APP_NAME, "version": C.APP_VERSION,
        "changelog": [{"version": v, "date": d, "notes": n} for v, d, n in C.CHANGELOG],
    }


# ---------------- 账号 ----------------
class AccountIn(BaseModel):
    api_id: str = ""
    api_hash: str = ""
    phone: str = ""
    proxy: str = ""
    nick: str = ""
    key: str = ""


class ProxyIn(BaseModel):
    proxy: str = ""


@app.post("/api/proxy/test")
async def proxy_test(body: ProxyIn):
    return await E.test_proxy(body.proxy)


@app.get("/api/accounts")
def accounts():
    accs = STATE["accounts"]
    return {
        "current": accs.current,
        "accounts": [{
            "key": S.account_key_from_phone(a.get("phone", "")),
            "phone": a.get("phone", ""),
            "nick": a.get("nick", ""),
            "proxy": a.get("proxy", ""),
            "api_id": a.get("api_id", ""),
        } for a in accs.accounts],
        "logged_in": bool(STATE["engine"] and STATE["engine"].logged_in),
    }


@app.post("/api/accounts")
def account_upsert(body: AccountIn):
    key = STATE["accounts"].upsert(body.api_id, body.api_hash, body.phone,
                                   body.proxy, body.nick)
    return {"key": key, "ok": True}


@app.post("/api/accounts/switch")
def account_switch(body: AccountIn):
    ok = STATE["accounts"].switch_to(body.key)
    STATE["engine"] = None
    return {"ok": ok}


@app.post("/api/accounts/remove")
def account_remove(body: AccountIn):
    ok = STATE["accounts"].remove(body.key, delete_files=body.proxy == "DELETE_FILES")
    return {"ok": ok}


# ---------------- 旧版登录态导入 ----------------
class LegacyBody(BaseModel):
    dir: str = ""


@app.get("/api/legacy/discover")
def legacy_discover(dir: str = ""):
    """在候选目录（或指定目录）里找旧版 Python 工具的登录态。"""
    dirs = [dir] if dir else LG.default_search_dirs()
    items, scanned = [], []
    for d in dirs:
        scanned.append(d)
        for item in LG.discover(d):
            item["base_dir"] = d
            items.append(item)
    return {"items": items, "scanned": scanned}


@app.post("/api/legacy/import")
def legacy_import(body: LegacyBody):
    dirs = [body.dir] if body.dir else LG.default_search_dirs()
    imported, failed = [], []
    for d in dirs:
        for item in LG.discover(d):
            r = LG.import_one(STATE["accounts"], item)
            r.update({"phone": item["phone"], "api_id": item["api_id"],
                      "api_hash": item["api_hash"], "proxy": item["proxy"],
                      "nick": item["nick"]})
            (imported if r["ok"] else failed).append(r)
    for r in imported:
        L.HUB.log(f"旧版登录态导入成功：{r['note']}")
    for r in failed:
        L.HUB.log(f"旧版登录态导入失败：{r['note']}", level=L.LOG_WARN)
    return {"imported": imported, "failed": failed}


# ---------------- 登录 ----------------
class LoginStart(BaseModel):
    key: str = ""
    demo: bool = False
    api_id: str = ""
    api_hash: str = ""
    phone: str = ""
    proxy: str = ""


@app.post("/api/login/start")
async def login_start(body: LoginStart):
    accs = STATE["accounts"]
    acc = accs.by_key(body.key) if body.key else None
    if not acc and not body.demo:
        # 允许直接传凭据现场建账号
        if body.phone and body.api_id:
            accs.upsert(body.api_id, body.api_hash, body.phone, body.proxy)
            acc = accs.by_key(S.account_key_from_phone(body.phone))
        else:
            raise HTTPError(400, "未指定账号或凭据")
    # 取消上一个引擎的僵尸连接任务（否则线路不通时会并排好几个 connect
    # 各自挂 30 秒，日志反复刷「正在连接」且互相干扰）
    old = STATE["engine"]
    if old is not None:
        old._stopped = True
        oc = getattr(old, "client", None)
        if oc is not None:
            try:
                await oc.disconnect()
            except Exception:  # noqa: BLE001
                pass
    e = E.TelegramEngine(acc or {}, demo=body.demo)
    STATE["engine"] = e

    async def run():
        await e.connect()
    asyncio.create_task(run())
    return {"state": e._login_state}


@app.get("/api/login/state")
def login_state():
    e = STATE["engine"]
    if not e:
        return {"state": "idle", "logged_in": False, "note": ""}
    return {
        "state": e._login_state, "logged_in": e.logged_in,
        "user": e.user, "demo": bool(getattr(e, "demo", False)),
        "note": getattr(e, "conn_note", "") or "",   # 分阶段连接说明（v3.20.1）
    }


# ---------------- 启动自动恢复（v3.20.3）----------------
@app.on_event("startup")
async def _boot_restore():
    """后端进程重启后自动恢复登录态（用户反馈：登录后刷新页面/重启后端就掉回未登录）。

    用现有账号的 .session 静默探测：有效直接回到已登录；无效则安静退出
    （probe=True 不发验证码，不会骚扰手机）。没有账号则跳过。"""
    accs = STATE["accounts"]
    if not accs.accounts or STATE["engine"] is not None:
        return
    acc = accs.current_account()
    if not acc or not (acc.get("api_id") and acc.get("api_hash")):
        return
    e = E.TelegramEngine(acc, demo=False)
    STATE["engine"] = e
    L.HUB.log("检测到本地登录态，正在自动恢复连接…（会话有效则免验证码直接登录）", L.LOG_INFO)
    asyncio.create_task(e.connect(probe=True))


@app.post("/api/login/code")
def login_code(body: AccountIn):
    e = STATE["engine"]
    if not e:
        raise HTTPError(400, "没有进行中的登录")
    e.set_code(body.key)
    return {"ok": True}


@app.post("/api/login/password")
def login_password(body: AccountIn):
    e = STATE["engine"]
    if not e:
        raise HTTPError(400, "没有进行中的登录")
    e.set_password(body.key)
    return {"ok": True}


# ---------------- 会话列表 ----------------
@app.get("/api/dialogs")
def dialogs():
    e = require_engine()
    items = e.dto_list()
    # 重新套用白名单状态
    for d in items:
        d["whitelisted"] = e.whitelist.has(d["peer_key"])
        d["is_protected"] = d["whitelisted"] or d["role"] in ("creator", "admin")
    used = e.quota_used()
    limit = e.quota_limit()
    left = max(0, limit - used)
    quota_text = (f"🎯 名额 {used}/{limit}（剩 {left}，"
                  f"{'Premium' if e.premium() else '免费账号'}估算）") if items else ""
    return {"items": items, "busy": STATE["busy"],
            "quota_used": used, "quota_limit": limit, "premium": e.premium(),
            "quota_text": quota_text, "fetch_error": STATE.get("fetch_error", "")}


@app.post("/api/dialogs/refresh")
async def dialogs_refresh():
    e = require_engine()
    STATE["busy"] = True
    STATE["fetch_error"] = ""

    async def run():
        try:
            await e.fetch_dialogs()
            L.HUB.log(f"会话列表已刷新（{len(e.dialogs)} 个）", L.LOG_INFO, "app")
        except Exception as ex:  # noqa: BLE001
            # 拉取失败必须让前端看得见 —— 之前错误只进日志，面板空着毫无提示
            STATE["fetch_error"] = humanize(ex)
            L.HUB.log(f"❌ 拉取会话失败：{STATE['fetch_error']}", L.LOG_ERROR, "app")
        finally:
            STATE["busy"] = False
    asyncio.create_task(run())
    return {"ok": True}


def humanize(ex):
    """统一走 errors.humanize_error，认不出就给 str(ex)。"""
    try:
        from .core.errors import humanize_error
        return humanize_error(ex)
    except Exception:  # noqa: BLE001
        return str(ex) or ex.__class__.__name__


# ---------------- 批量操作 ----------------
class KeysBody(BaseModel):
    peer_keys: list = []
    confirm_word: str = ""
    interval: float = 0.0   # 每条之间的退出间隔（秒）
    jitter: float = 0.0     # 随机抖动（秒）


@app.post("/api/actions/leave")
async def action_leave(body: KeysBody):
    e = require_engine()
    results = []
    for pk in body.peer_keys:
        r = await e.leave_one(pk)
        if r.get("ok"):
            e.history.add({"action": "leave", "peer_key": pk,
                           "title": e.dialogs.get(pk, {}).get("dto", {}).get("title", pk),
                           "note": r.get("note", ""), "t": _now()})
        results.append({"peer_key": pk, **r})
    return {"results": results}


@app.post("/api/actions/leave_many")
async def action_leave_many(body: KeysBody):
    """批量退出（带断点续跑 + 节奏控制）：逐条退出，进度落盘，中断可续。"""
    e = require_engine()
    return await e.leave_many(body.peer_keys, interval=body.interval, jitter=body.jitter)


@app.post("/api/actions/resume")
async def action_resume(body: KeysBody = None):
    """续跑：继续处理断点文件里的剩余会话（节奏参数与批量退出一致）。"""
    e = require_engine()
    pend = e.load_pending()
    if not pend:
        return {"results": [], "remaining": [], "interrupted": False,
                "note": "没有待续跑的任务"}
    interval = body.interval if body else 0.0
    jitter = body.jitter if body else 0.0
    return await e.leave_many(pend, interval=interval, jitter=jitter)


@app.get("/api/pending")
def api_pending():
    e = require_engine()
    return {"keys": e.load_pending(), "items": e.pending_items()}


@app.delete("/api/pending")
def api_pending_clear():
    e = require_engine()
    e.save_pending([])
    return {"ok": True}


@app.post("/api/actions/cancel")
def action_cancel():
    e = require_engine()
    e.cancel_flag = True
    return {"ok": True}


@app.post("/api/actions/destroy")
async def action_destroy(body: KeysBody):
    e = require_engine()
    results = []
    for pk in body.peer_keys:
        r = await e.destroy_one(pk, body.confirm_word)
        results.append({"peer_key": pk, **r})
    return {"results": results}


class RejoinBody(BaseModel):
    kind: str = "username"
    value: str = ""
    raw: str = ""       # 直接粘贴：@name / t.me/name / t.me/+HASH / joinchat/HASH


@app.post("/api/actions/rejoin")
async def action_rejoin(body: RejoinBody):
    e = require_engine()
    kind, value = body.kind, body.value
    if body.raw.strip():
        kind, value = E.parse_join_target(body.raw)
        if not kind:
            return {"state": "fail", "note": "解析不出目标 —— 支持 @用户名 / t.me/用户名 / t.me/+HASH / joinchat/HASH"}
    r = await e.rejoin(kind, value)
    if r.get("state") == "ok":
        e.history.add({"action": "rejoin", "value": value,
                       "note": r.get("note", ""), "t": _now()})
    return r


# ---------------- 统计 ----------------
class StatsBody(BaseModel):
    peer_keys: list = []
    force: bool = False
    conc: int = 1            # 并发数（1~32，越大越快也越容易限流）
    ttl_hours: float = 24.0  # 缓存有效期（小时）
    use_cache: bool = True


@app.post("/api/stats")
async def stats(body: StatsBody):
    e = require_engine()
    res = await e.fetch_stats(body.peer_keys, force=body.force, conc=body.conc,
                              ttl_hours=body.ttl_hours, use_cache=body.use_cache)
    # 回填到 dto
    for pk, v in res.items():
        d = e.dialogs.get(pk, {}).get("dto")
        if d:
            d["members"] = v.get("members")
            d["posts"] = v.get("posts")
            d["role"] = v.get("role", "")
            if v.get("last_ts"):
                d["last_ts"] = v["last_ts"]
                days = E.active_days_of(v["last_ts"])
                d["active_days"] = days
                d["active_text"] = E.active_text(days)
            elif v.get("active_text"):
                d["active_text"] = v["active_text"]
    return {"stats": res}


@app.delete("/api/stats/cache")
def stats_cache_clear():
    e = require_engine()
    n = e.clear_stats_cache()
    L.HUB.log(f"已清空统计缓存（{n} 条）", L.LOG_INFO, "app")
    return {"cleared": n}


@app.get("/api/stats/progress")
def stats_progress():
    """成员统计进度（用户数/作品数/活跃，前端轮询，状态栏显示「统计 N/M」）。未登录返回零值。"""
    e = STATE.get("engine")
    if not e:
        return {"done": 0, "total": 0, "running": False}
    p = getattr(e, "stats_prog", None) or {}
    return {"done": int(p.get("done", 0)), "total": int(p.get("total", 0)),
            "running": bool(p.get("running"))}


@app.get("/api/stats/cache")
def stats_cache_info():
    e = require_engine()
    return {"count": len(getattr(e.stats_cache, "data", {}) or {})}


# ---------------- 媒体统计（v3.17.0） ----------------
class MediaBody(BaseModel):
    peer_keys: list = []
    limit: int = 500        # 扫描每个会话最近多少条（mode=scan 时有效）
    force: bool = False
    conc: int = 2
    ttl_hours: float = 24.0
    use_cache: bool = True
    mode: str = "scan"      # scan=最近 N 条；totals=全部历史真实总数（v3.20.0）


@app.post("/api/media")
async def media_stats(body: MediaBody):
    e = require_engine()
    res = await e.fetch_media_stats(body.peer_keys, limit=max(1, min(int(body.limit), 5000)),
                                    force=body.force, conc=body.conc,
                                    ttl_hours=body.ttl_hours, use_cache=body.use_cache,
                                    mode=body.mode if body.mode in ("scan", "totals") else "scan")
    return {"media": res}


@app.get("/api/media")
def media_all():
    """全部已缓存的媒体统计（key -> summary）。total 现算（v3.20.7 旧缓存口径兜底）。"""
    e = require_engine()
    return {"media": {k: E.with_total(v) for k, (_ts, v) in (getattr(e.media_cache, "data", {}) or {}).items()}}


@app.get("/api/media/progress")
def media_progress():
    """媒体统计进度（前端轮询，状态栏显示「媒体统计 N/M」）。未登录返回零值。"""
    e = STATE.get("engine")
    if not e:
        return {"done": 0, "total": 0, "running": False}
    p = getattr(e, "media_prog", None) or {}
    return {"done": int(p.get("done", 0)), "total": int(p.get("total", 0)),
            "running": bool(p.get("running"))}


# ---------------- 设备 ----------------
@app.get("/api/devices")
async def devices():
    e = require_engine()
    return {"devices": await e.get_devices()}


class KillBody(BaseModel):
    hashes: list = []


@app.post("/api/devices/kill")
async def devices_kill(body: KillBody):
    e = require_engine()
    for h in body.hashes:
        await e.kill_device(h)
    return {"ok": True}


# ---------------- 分组 ----------------
@app.get("/api/folders")
async def folders():
    e = require_engine()
    return {"folders": await e.get_folders()}


class FolderBody(BaseModel):
    folder_id: int = 0
    peer_keys: list = []
    add: bool = True


@app.post("/api/folders/apply")
async def folders_apply(body: FolderBody):
    e = require_engine()
    return await e.folder_add_remove(body.folder_id, body.peer_keys, body.add)


# ---------------- 邀请链接（无用户名会话跳转客户端） ----------------
class PeerBody(BaseModel):
    peer_key: str = ""


@app.post("/api/invite")
async def invite(body: PeerBody):
    e = require_engine()
    res = await e.export_invite(body.peer_key)
    if not res:
        raise HTTPException(404, "会话不存在或无法导出邀请链接")
    return res


# ---------------- 静音 / 归档 ----------------
class FlagBody(BaseModel):
    peer_keys: list = []
    on: bool = True


async def _flag_loop(e, peer_keys, on, fn):
    """逐条执行并如实回报：不谎报成功（部分失败列出每条的失败原因）。"""
    ok, failed = 0, []
    for pk in peer_keys:
        try:
            if await fn(pk, on):
                ok += 1
            else:
                failed.append({"peer_key": pk, "note": "会话不存在"})
        except Exception as ex:  # noqa: BLE001
            failed.append({"peer_key": pk, "note": humanize(ex)})
    return {"ok": ok, "failed": failed, "total": len(peer_keys)}


@app.get("/api/avatar")
async def avatar(pk: str = ""):
    """会话头像（懒加载 + 磁盘缓存）。无头像 / 未登录 / 未拉到该会话 → 404。"""
    e = require_engine()
    path = await e.avatar_file(pk)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no avatar")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.post("/api/mute")
async def mute(body: FlagBody):
    e = require_engine()
    return await _flag_loop(e, body.peer_keys, body.on, e.set_mute)


@app.post("/api/archive")
async def archive(body: FlagBody):
    e = require_engine()
    return await _flag_loop(e, body.peer_keys, body.on, e.set_archive)


# ---------------- 快照 ----------------
@app.get("/api/snapshot")
def snapshot_current():
    e = require_engine()
    return {"snapshot": e.build_snapshot()}


@app.get("/api/snapshot/slots")
def snapshot_slots():
    """两个槽位的元信息（taken/count），供前端展示是否已有快照。"""
    e = require_engine()
    slots = []
    for i in (0, 1):
        s = e.snap.load_slot(i)
        slots.append({"taken": (s or {}).get("taken", ""), "count": (s or {}).get("count", 0)} if s else None)
    return {"slots": slots}


class SnapSaveBody(BaseModel):
    slot: int = 0


@app.post("/api/snapshot/save")
def snapshot_save(body: SnapSaveBody):
    e = require_engine()
    snap = e.build_snapshot()
    e.snap.save_into(body.slot, snap)
    return {"ok": True, "taken": snap["taken"], "count": snap["count"]}


class SnapCmpBody(BaseModel):
    slot_a: int = 0
    slot_b: int = 1


@app.post("/api/snapshot/compare")
def snapshot_compare(body: SnapCmpBody):
    e = require_engine()
    a = e.snap.load_slot(body.slot_a)
    b = e.snap.load_slot(body.slot_b)
    return {"diff": e.diff_snapshots(a, b)}


# ---------------- 历史 ----------------
@app.get("/api/history")
def history():
    e = require_engine()
    return {"items": list(reversed(e.history.items))}


@app.delete("/api/history")
def history_clear():
    e = require_engine()
    n = len(e.history.items)
    e.history.items = []
    e.history.save()
    L.HUB.log(f"已清空退出历史（{n} 条）", L.LOG_INFO, "app")
    return {"cleared": n}


# ---------------- 白名单 ----------------
@app.post("/api/whitelist")
def whitelist(body: FlagBody):
    e = require_engine()
    e.set_whitelist(body.peer_keys, body.on)
    return {"ok": True}


# ---------------- 导出 ----------------
class ExportBody(BaseModel):
    fmt: str = "xlsx"
    peer_keys: list = []
    scope_label: str = ""
    quota_text: str = ""


@app.post("/api/export")
def export(body: ExportBody):
    e = require_engine()
    items = [e.dialogs[pk]["dto"] for pk in body.peer_keys if pk in e.dialogs]

    def row_of(d):
        pk = d.get("peer_key", "")
        m = e.media_cache.get(pk, ttl_hours=24 * 365) if pk else None   # 导出读缓存：不设时效上限（扫过就算）
        return [
            d.get("title", ""), d.get("username") or "", d.get("kind", ""),
            d.get("members") if d.get("members") is not None else "",
            d.get("posts") if d.get("posts") is not None else "",
            d.get("active_text") or "", d.get("folders") or "", d.get("id", ""),
            d.get("whitelisted") and "🔒 白名单" or (d.get("role") == "creator" and "👑 创建者"
                or (d.get("role") == "admin" and "🛡 管理员") or ""),
            d.get("risk_cell") or "",
            E.media_cell_text(m),
        ]

    main_rows = [[("h", c[0]) for c in C.EXCEL_COLUMNS]] + [row_of(d) for d in items]
    detail_rows = [[("h", c[0]) for c in C.EXCEL_RISK_COLUMNS]]
    for d in items:
        for r in d.get("risk", []):
            detail_rows.append([d.get("title", ""), d.get("kind", ""), d.get("id", ""),
                                r.get("short", ""), r.get("why", ""), r.get("raw", "")])
    # 概览 sheet（对齐 v1.7.7：汇总统计一目了然）
    n_total = len(items)

    def _cnt(pred):
        return sum(1 for d in items if pred)

    kinds = {}
    for d in items:
        kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1
    overview_rows = [[("h", "项目"), ("h", "数值")],
                     ["导出范围", body.scope_label or "选中"],
                     ["会话总数", n_total],
                     ["频道/群组（成员）", _cnt(lambda d: d.get("is_member"))],
                     ["异常特征", _cnt(lambda d: d.get("risk"))],
                     ["白名单", _cnt(lambda d: d.get("whitelisted"))],
                     ["媒体已统计", _cnt(lambda d: e.media_cache.get(d.get("peer_key", ""), ttl_hours=24 * 365))],
                     ["订阅名额", body.quota_text or ""]]
    for k, n in kinds.items():
        overview_rows.append([f"类型：{k}", n])
    sheets = [
        {"name": "概览", "cols": [24, 24], "rows": overview_rows,
         "freeze": False, "filter": False},
        {"name": "会话清单", "cols": [c[1] for c in C.EXCEL_COLUMNS],
         "rows": main_rows, "freeze": True, "filter": True},
    ]
    if len(detail_rows) > 1:
        sheets.append({"name": "异常明细", "cols": [c[1] for c in C.EXCEL_RISK_COLUMNS],
                       "rows": detail_rows, "freeze": True, "filter": True})

    if body.fmt == "csv":
        data = X.build_csv(main_rows)
        media = "text/csv"
        fname = "tg_dialogs.csv"
    else:
        data = X.build_xlsx(sheets)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = "tg_dialogs.xlsx"
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ---------------- 日志 / WebSocket ----------------
@app.get("/api/logs")
def logs(limit: int = 500):
    return {"logs": L.HUB.recent(limit)}


@app.websocket("/api/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q = L.HUB.subscribe()
    try:
        while True:
            try:
                entry = await asyncio.wait_for(q.get(), timeout=30)
                await websocket.send_json(entry)
            except asyncio.TimeoutError:
                await websocket.send_json({"t": "", "ts": 0, "level": "info",
                                          "source": "ping", "message": ""})
    except WebSocketDisconnect:
        pass
    finally:
        L.HUB.unsubscribe(q)


# ---------------- 生产模式：托管前端 ----------------
@app.get("/api/debug/restrictions")
async def debug_restrictions(peer: str = ""):
    """临时调试：返回某会话实体的原始 restriction_reason 条目。"""
    e = require_engine()
    item = e.dialogs.get(peer) or {}
    ent = item.get("ent")
    if ent is None:
        return {"error": "peer not found", "peer": peer}
    rows = []
    for r in (getattr(ent, "restriction_reason", None) or []):
        rows.append({"platform": str(getattr(r, "platform", "")),
                     "reason": str(getattr(r, "reason", "")),
                     "text": str(getattr(r, "text", ""))})
    return {"peer": peer, "title": str(getattr(ent, "title", "") or getattr(ent, "first_name", "")),
            "restricted": bool(getattr(ent, "restricted", False)),
            "is_user": str(type(ent).__name__),
            "entries": rows}


@app.get("/{full_path:path}")
def serve_ui(full_path: str):
    index = os.path.join(UI_DIST, "index.html")
    cand = os.path.join(UI_DIST, full_path)
    if full_path and os.path.isfile(cand):
        # 带 hash 的静态资源（assets/*）：内容不变文件名变，可放心长期缓存
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
            if full_path.startswith("assets/") else \
            {"Cache-Control": "no-cache"}   # 其他散文件（如图标）每次协商
        return FileResponse(cand, headers=headers)
    if os.path.isfile(index):
        # HTML 入口必须 no-cache：只靠 ETag 协商，浏览器可能启发式缓存旧页面，
        # 导致发版后用户 F5 仍跑旧 bundle（v3.20.0 用户实际踩到）
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return {"error": "前端未构建：请先 `npm run build` 生成 ui/dist"}


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)

