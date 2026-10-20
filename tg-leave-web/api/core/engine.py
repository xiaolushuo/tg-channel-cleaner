# -*- coding: utf-8 -*-
"""Telegram 引擎（Telethon 封装）+ 演示模式。移植自 Python 版 tg_channel_leave.py。

所有面向前端的操作都在这里：登录、拉取会话、退出/删除、销毁、重进、统计、
设备、分组、静音归档、快照。演示模式可在无凭据情况下跑通完整流程。
"""
import asyncio
import json
import os
import random
import time

from telethon import TelegramClient, errors as tg_errors
from telethon.tl import functions as tg_fn
from telethon.tl import types as tl_types
from telethon.tl.functions import account as tl_account
from telethon.tl.functions import channels as tl_channels
from telethon.tl.functions import contacts as tl_contacts
from telethon.tl.functions import folders as tl_folders
from telethon.tl.functions import messages as tl_messages
from telethon.utils import get_input_peer

try:
    from python_socks import ProxyType
    from python_socks.async_.asyncio import Proxy
    _HAS_PSOCKS = True
except Exception:  # pragma: no cover
    _HAS_PSOCKS = False

from . import constants as C
from . import risk as R
from . import store as S
from .errors import humanize_error
from .loghub import HUB, LOG_INFO, LOG_WARN, LOG_ERROR


def parse_proxy(proxy_str):
    """socks5://host:port 或 http://host:port -> Telethon proxy dict，失败返回 None。"""
    if not proxy_str:
        return None
    try:
        s = proxy_str.strip()
        if "://" not in s:
            return None
        scheme, rest = s.split("://", 1)
        scheme = scheme.lower()
        auth, hostport = ("", rest) if "@" not in rest else rest.split("@", 1)
        host, _, port = hostport.rpartition(":")
        if not host or not port.isdigit():
            return None
        ptype = {"socks5": "socks5", "socks4": "socks4", "http": "http"}.get(scheme)
        if not ptype:
            return None
        d = {"proxy_type": ptype, "addr": host, "port": int(port)}
        if auth:
            user, _, pw = auth.partition(":")
            d["username"] = user
            d["password"] = pw
        return d
    except Exception:
        return None


async def test_proxy(proxy_str, timeout=15):
    """两段式代理测试（v3.20.1）：TCP 连通 + TLS 握手到 TG DC。

    只测 TCP 会误报「代理连通」——节点可能收下 TCP 却掐掉到 Telegram 的 TLS 数据
    （实测踩过：TCP ok 但 TLS 15s 超时，换节点即愈）。返回 {'ok', 'tcp', 'tls', 'note'}。"""
    if not _HAS_PSOCKS:
        return {"ok": False, "tcp": False, "tls": False, "note": "缺少 python-socks，无法测试代理"}
    parsed = parse_proxy(proxy_str)
    if not parsed:
        return {"ok": False, "tcp": False, "tls": False, "note": "代理格式不正确（示例：socks5://127.0.0.1:1080）"}
    ptype = {"socks5": ProxyType.SOCKS5, "socks4": ProxyType.SOCKS4,
             "http": ProxyType.HTTP}.get(parsed["proxy_type"])
    proxy_desc = f"{parsed['proxy_type']} {parsed['addr']}:{parsed['port']}"
    try:
        proxy = Proxy(ptype, host=parsed["addr"], port=parsed["port"],
                      username=parsed.get("username"), password=parsed.get("password"))
        sock = await asyncio.wait_for(
            proxy.connect(dest_host="149.154.167.50", dest_port=443), timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "tcp": False, "tls": False,
                "note": f"TCP 连接超时——代理 {proxy_desc} 本身不可用（检查代理软件是否在运行）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tcp": False, "tls": False,
                "note": f"代理连接失败：{humanize_error(e)}"}
    # TCP 已通 → TLS 握手验证到 TG 的真实线路（IP 直连不校验证书，只测可达性）。
    # proxy.connect 返回阻塞 socket，握手放线程跑，外层 wait_for 控总时长。
    import ssl as _ssl
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE

    def _tls_probe(s):
        s.settimeout(8)
        with ctx.wrap_socket(s, server_hostname="149.154.167.50"):
            return True   # with 退出即关闭；握手成功即为目的

    try:
        await asyncio.wait_for(asyncio.to_thread(_tls_probe, sock), timeout=10)
        return {"ok": True, "tcp": True, "tls": True,
                "note": f"代理连通，Telegram 线路可达（{proxy_desc}，TLS 握手成功）"}
    except asyncio.TimeoutError:
        return {"ok": False, "tcp": True, "tls": False,
                "note": f"代理 {proxy_desc} 本身可用，但该节点到 Telegram 的线路不通"
                        "（TLS 握手超时）——请在代理软件更换节点"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tcp": True, "tls": False,
                "note": f"代理可用，但到 Telegram 的 TLS 握手失败（{humanize_error(e)}）"
                        "——多为该节点到 TG 的线路被阻断，建议更换节点"}


# 媒体类型桶（固定顺序，导出/展示共用）
MEDIA_BUCKETS = ("text", "photo", "video", "voice", "audio",
                 "sticker", "gif", "file", "link", "other")

# 媒体桶 -> 导出用中文短标签（与前端 MEDIA_LABEL 措辞一致）
MEDIA_LABELS = {"text": "文本", "photo": "图片", "video": "视频", "voice": "语音",
                "audio": "音频", "sticker": "贴纸", "gif": "GIF", "file": "文件",
                "link": "链接", "other": "其他"}

# 全部历史真实总数（v3.20.0）：每类一个服务端搜索过滤器，口径对齐官方客户端媒体面板。
# messages.search 取 total_count 即该类媒体在全部历史中的真实条数，1 请求/类，不逐条扫消息。
# 注意存「实例」不存类：传类本身序列化时会 _bytes() 缺 self（v3.20.0 踩坑）。
MEDIA_TOTAL_FILTERS = (
    ("photo", tl_types.InputMessagesFilterPhotos()),
    ("video", tl_types.InputMessagesFilterVideo()),
    ("gif", tl_types.InputMessagesFilterGif()),
    ("voice", tl_types.InputMessagesFilterVoice()),
    ("audio", tl_types.InputMessagesFilterMusic()),
    ("file", tl_types.InputMessagesFilterDocument()),
    ("link", tl_types.InputMessagesFilterUrl()),
)


def media_total(counts):
    """媒体总数 = 六类媒体桶之和（不含文本与链接；排序 / 导出 / 前端共用同一口径；纯函数，供自测）。

    链接不计入总数（v3.20.7）：服务端「链接」搜索过滤器会把配文带网址的媒体消息
    重复计入（同一张图既是图片又是链接，图片桶和链接桶各算一次），相加会虚高、
    甚至超过会话消息总数（实锤：fsmcss 作品数 1392，图片1389+链接304 相加=1695）。
    官方客户端同样把媒体与链接分成两个页签分别显示，从不相加。链接条数单列于
    counts["link"]，界面堆叠条与详情弹窗照常展示。"""
    if not isinstance(counts, dict):
        return 0
    return sum(v for k, v in counts.items()
               if k not in ("text", "link") and isinstance(v, (int, float)))


def with_total(val):
    """读路径兜底（v3.20.7）：旧缓存里存的 total 是含链接的老口径，按 counts 重算，
    600+ 个已统计会话免重扫直接校正。原地写并返回。"""
    if isinstance(val, dict) and isinstance(val.get("counts"), dict):
        val["total"] = media_total(val["counts"])
    return val


def media_cell_text(val):
    """媒体统计 -> 导出单元格文本（CSV 与 Excel 共用；空值统一「未统计」）。

    扫描模式：共 15 · 图片 12 · 视频 3 · 文本 450（扫 465/500）；
    全历史模式：共 15 · 图片 12 · 视频 3（全历史真实计数）；零计数桶不写，没统计过留空不写 0。"""
    if not val or not isinstance(val, dict) or not val.get("counts"):
        return "未统计"
    parts = [f"{MEDIA_LABELS[k]} {v}" for k, v in val["counts"].items() if v]
    if not parts:
        parts = ["文本 0"]
    head = f"共 {media_total(val['counts'])} · " if media_total(val["counts"]) else ""
    if val.get("mode") == "totals":
        return head + " · ".join(parts) + "（全历史真实计数）"
    tail = f"（扫 {val.get('scanned', 0)}/{val.get('limit', 0)}"
    if val.get("exhausted"):
        tail += "，到底"
    tail += "）"
    return head + " · ".join(parts) + tail


def classify_message(msg):
    """消息媒体归类（纯函数，供自测）。返回 MEDIA_BUCKETS 之一。

    归类优先级：贴纸 > GIF > 视频（含圆视频）> 语音/音频 > 图片 > 文件；
    无媒体=文本，网页预览=链接，投票/位置/联系人等=其他。"""
    media = getattr(msg, "media", None)
    if media is None:
        return "text"
    if isinstance(media, tl_types.MessageMediaPhoto):
        return "photo"
    if isinstance(media, tl_types.MessageMediaDocument):
        doc = getattr(media, "document", None)
        attrs = list(getattr(doc, "attributes", []) or []) if doc else []
        if any(isinstance(a, tl_types.DocumentAttributeSticker) for a in attrs):
            return "sticker"
        if any(isinstance(a, tl_types.DocumentAttributeAnimated) for a in attrs):
            return "gif"
        if any(isinstance(a, tl_types.DocumentAttributeVideo) for a in attrs):
            return "video"
        for a in attrs:
            if isinstance(a, tl_types.DocumentAttributeAudio):
                return "voice" if getattr(a, "voice", False) else "audio"
        if any(isinstance(a, tl_types.DocumentAttributeImageSize) for a in attrs):
            return "photo"
        return "file"
    if isinstance(media, tl_types.MessageMediaWebPage):
        return "link"
    return "other"


def demo_media(peer_key: str, limit: int) -> dict:
    """演示会话的媒体统计（固定字符串种子，跨进程稳定；预生成与手动统计同一数据源）。

    同一 pk 以相同顺序抽取计数——小深度是大深度的前缀，与真实扫描的语义一致；
    消息总数不足深度时标 exhausted（「已到底」），与真实引擎对齐。
    """
    rnd = random.Random(f"media:{peer_key}")
    total = rnd.randint(120, 900)                     # 该会话消息总数（模拟）
    scanned = min(total, max(1, int(limit)))
    counts = dict.fromkeys(MEDIA_BUCKETS, 0)
    for _ in range(scanned):
        counts[MEDIA_BUCKETS[rnd.randrange(len(MEDIA_BUCKETS))]] += 1
    out = {"scanned": scanned, "limit": int(limit),
           "counts": counts, "total": media_total(counts), "ts": int(time.time())}
    if total <= int(limit):
        out["exhausted"] = True
    return out


def avatar_cache_path(peer_key, photo_id):
    """头像磁盘缓存路径：peer_key 冒号/斜杠换下划线，按 photo_id 区分换头像。"""
    safe = str(peer_key).replace(":", "_").replace("/", "_").replace("\\", "_")
    return os.path.join(S.BASE_DIR, "avatars", f"{safe}_{photo_id}.jpg")


def peer_key_of(ent):
    if isinstance(ent, tl_types.User):
        return f"u:{ent.id}"
    if isinstance(ent, tl_types.Chat):
        return f"g:{ent.id}"
    if isinstance(ent, (tl_types.Channel, tl_types.ChannelForbidden, tl_types.ChatForbidden)):
        return f"c:{ent.id}"
    # InputPeer 构造体（DialogFilter.include_peers / pinned_peers 等场景）：
    # 属性是 user_id / chat_id / channel_id，没有 .id —— 不补这条会全部落成 x:0，
    # 分组打标与批量划分的去重全被打坏（v3.6.1 真因）。
    if isinstance(ent, (tl_types.InputPeerUser, tl_types.InputUser)):
        return f"u:{getattr(ent, 'user_id', 0)}"
    if isinstance(ent, tl_types.InputPeerChat):
        return f"g:{getattr(ent, 'chat_id', 0)}"
    if isinstance(ent, (tl_types.InputPeerChannel, tl_types.InputChannel)):
        return f"c:{getattr(ent, 'channel_id', 0)}"
    return f"x:{getattr(ent, 'id', 0)}"


def invite_tg_link(link: str) -> str:
    """把导出的邀请链接转成 tg://join 唤起链接。

    只认两种私有邀请形态：https://t.me/+<hash>（现行）与
    t.me/joinchat/<hash>（遗留）；公开用户名链接返回空串。
    （无用户名的频道/群组没有按 ID resolve 的协议，邀请链接是官方
    文档 core.telegram.org/api/links 给出的唯一唤起通路。）
    """
    for marker in ("t.me/+", "joinchat/"):
        if marker in link:
            h = link.split(marker, 1)[1].split("?", 1)[0].strip("/")
            if h:
                return f"tg://join?invite={h}"
    return ""


def session_kind(ent):
    if isinstance(ent, tl_types.Channel):
        return C.KIND_CHANNEL if getattr(ent, "broadcast", False) else C.KIND_GROUP
    if isinstance(ent, tl_types.Chat):
        return C.KIND_GROUP
    if isinstance(ent, tl_types.User):
        return C.KIND_BOT if getattr(ent, "bot", False) else C.KIND_USER
    if isinstance(ent, (tl_types.ChatForbidden, tl_types.ChannelForbidden)):
        return C.KIND_GONE
    return C.KIND_OTHER


def active_days_of(ts):
    if not ts:
        return None
    return max(0, int((time.time() - ts) / 86400.0))


def active_text(days):
    if days is None:
        return "未知"
    if days >= 1_000_000_000:
        return "无消息"
    if days <= 0:
        return "今天"
    return f"{days}天前"


def parse_join_target(raw):
    """把用户粘贴的东西解析成 (类型, 值)：
      @name / name -> ("username", "name")；t.me/name -> ("username", "name")；
      t.me/+HASH / joinchat/HASH / +HASH -> ("invite", "HASH")。解析不出返回 (None, "")。"""
    s = str(raw or "").strip()
    if not s:
        return None, ""
    s = s.split("?", 1)[0].split("#", 1)[0] if s.startswith("http") else s
    low = s.lower()
    for host in ("https://t.me/", "http://t.me/", "t.me/", "https://telegram.me/",
                 "telegram.me/", "https://www.t.me/"):
        if low.startswith(host):
            s = s[len(host):]
            break
    s = s.strip().strip("/")
    low = s.lower()
    if low.startswith("joinchat/"):
        h = s[len("joinchat/"):].strip()
        return ("invite", h) if h else (None, "")
    if s.startswith("+"):
        h = s[1:].strip()
        return ("invite", h) if h else (None, "")
    s = s.lstrip("@").strip()
    if not s or any(ch.isspace() for ch in s):
        return None, ""
    # 用户名不含「/」，带斜杠说明是没认出的别的链接（如 example.com/x）—— 拒绝，不瞎猜
    if "://" in s or "/" in s:
        return None, ""
    return "username", s


def is_ghost_user(ent):
    """幽灵账号：已注销，或服务端只回了 id、一个名字字段都没有。

    这一类是服务端唯一会「删掉记录、却把会话条目留着」的，
    区分出来是为了只对它们动用更强的写法（revoke / 临时封禁），不误伤正常联系人。"""
    if not isinstance(ent, tl_types.User):
        return False
    if getattr(ent, "is_self", False):
        return False
    if getattr(ent, "deleted", False):
        return True
    return not (getattr(ent, "first_name", None)
                or getattr(ent, "last_name", None)
                or getattr(ent, "username", None))


class TelegramEngine:
    def __init__(self, account: dict, demo: bool = False):
        self.account = account
        self.demo = demo
        self.client = None
        self.user = None
        self.logged_in = False
        self.cancel_flag = False
        self.media_prog = {"done": 0, "total": 0, "running": False}   # 媒体统计进度（v3.19.0）
        self.stats_prog = {"done": 0, "total": 0, "running": False}   # 成员统计进度（用户数/作品数/活跃）
        self.dialogs = {}          # peer_key -> {"ent": entity, "dto": dict}
        self._code_fut = None
        self._pwd_fut = None
        self._login_state = "idle"
        self._stopped = False      # 新登录取代旧引擎时置位：僵尸 connect 任务静默退出
        self.conn_note = ""        # 连接分阶段说明（connecting/failed 时前端直接展示）
        self._auto_media_task = None   # 后台全量媒体总数任务（v3.20.2，拉完列表自动启动）
        self.snapshot = None
        self.stats_cache = S.CacheStore("stats", self.key())
        self.role_cache = S.CacheStore("role", self.key())
        self.history = S.HistoryStore(self.key())
        self.whitelist = S.WhitelistStore(self.key())
        self.snap = S.SnapshotStore(self.key())
        self.devices_cache = []
        self._avatar_sem = asyncio.Semaphore(6)  # 头像懒下载并发上限
        self.media_cache = S.CacheStore("media", self.key())
        if self.demo:
            # 演示模式预生成全部会话的媒体统计（v3.19.3）：进入 demo 媒体列即有数，
            # 不再要求用户先手动勾选统计；固定种子与手动统计同一数据源，一次写盘
            ts = int(time.time())
            for d in self._demo_dialogs():
                self.media_cache.data[d["peer_key"]] = (ts, demo_media(d["peer_key"], 500))
            self.media_cache.save()

    def key(self):
        return S.account_key_from_phone(self.account.get("phone", ""))

    def session_path(self):
        return S.session_path_for(self.key())

    def log(self, msg, level=None, source="app"):
        return HUB.log(msg, level, source)

    # ---------------- 登录 ----------------
    async def connect(self, probe=False):
        """完整登录流程（后台运行）：连接 → 验证码 → 两步验证，直到登录成功或失败。

        conn_note 记录分阶段进度（v3.20.1）：前端 /api/login/state 轮询拿到后直接显示，
        不再只有一句「正在连接」——卡在哪一步、为什么卡，一眼可见。
        probe=True（v3.20.3 后端启动自动恢复）：只验证 .session 是否仍有效——
        有效直接进登录态；无效则安静退出，绝不 send_code_request（后端每次重启
        都会走这条路径，发验证码会骚扰用户手机）。"""
        if self.demo:
            self.log("演示模式：模拟登录成功（无需任何凭据）", LOG_INFO, "tg")
            await asyncio.sleep(0.3)
            self.logged_in = True
            self.user = {"id": 0, "first_name": "演示", "last_name": "用户"}
            self._login_state = "logged_in"
            return
        if getattr(self, "_stopped", False):
            return   # 已被新登录取代，静默退出（防止僵尸任务继续刷日志）
        self._login_state = "connecting"
        proxy = parse_proxy(self.account.get("proxy", ""))
        via = f"经代理 {proxy['addr']}:{proxy['port']}（{proxy['proxy_type']}）" if proxy else "直连"
        self.log(f"正在连接 Telegram…（{via}）", LOG_INFO, "tg")
        self.conn_note = f"正在建立连接（{via}），最长等待 30 秒…"
        api_id = int(self.account.get("api_id") or 0)
        api_hash = self.account.get("api_hash", "")
        phone = self.account.get("phone", "")
        self.client = TelegramClient(self.session_path(), api_id, api_hash, proxy=proxy)
        try:
            # Telethon connect() 在线路半通（TCP 过、TLS 被掐）时会无限挂住，必须加超时
            await asyncio.wait_for(self.client.connect(), timeout=30)
        except asyncio.TimeoutError:
            if getattr(self, "_stopped", False):
                return
            self.conn_note = "连接超时：网络到 Telegram 不通（常见：代理节点到 TG 线路被阻断）"
            self._login_state = "failed"
            self.log("连接超时（30 秒）：TCP 可能可达但 Telegram 握手无响应。"
                     "请在代理软件更换节点，或用「测试代理」复查（已含 TLS 层探测）", LOG_ERROR, "tg")
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return
        except Exception as e:  # noqa: BLE001
            if getattr(self, "_stopped", False):
                return
            self.conn_note = f"连接失败：{humanize_error(e)}"
            self._login_state = "failed"
            self.log(f"连接失败：{humanize_error(e)}", LOG_ERROR, "tg")
            return
        if getattr(self, "_stopped", False):
            return
        self.conn_note = "已建立连接，正在验证登录态…"
        self.log("连接已建立，正在验证登录态…", LOG_INFO, "tg")
        if await self.client.is_user_authorized():
            self.user = await self.client.get_me()
            self.logged_in = True
            self._login_state = "logged_in"
            self.conn_note = ""
            self.log(f"已登录：{self.user.first_name}", LOG_INFO, "tg")
            return
        if probe:
            # 启动自动恢复：.session 失效（换号/注销/文件缺失）→ 安静退出，不发验证码
            self._login_state = "idle"
            self.conn_note = ""
            self.log("本地登录态已失效，请在右侧「账号」页重新登录（本次未发送验证码）", LOG_INFO, "tg")
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            return
        self.conn_note = "登录态无效，正在请求验证码…"
        await self.client.send_code_request(phone)
        self.log("请输入手机收到的验证码", LOG_INFO, "tg")
        me = await self._login_loop(phone)
        if me is not None:
            self.user = me
            self.logged_in = True
            self._login_state = "logged_in"
            self.log(f"已登录：{getattr(me, 'first_name', me)}", LOG_INFO, "tg")
        else:
            self._login_state = self._login_state or "failed"

    async def _login_loop(self, phone):
        me = None
        while me is None:
            try:
                self._code_fut = asyncio.get_event_loop().create_future()
                self._login_state = "need_code"
                self.conn_note = "等待输入验证码（手机收到后填入）"
                code = await self._code_fut
                self.conn_note = "验证码提交中…"
                me = await self.client.sign_in(phone, code)
            except tg_errors.SessionPasswordNeededError:
                self._login_state = "need_password"
                self.conn_note = "等待输入两步验证密码"
                self.log("请输入两步验证密码", LOG_INFO, "tg")
                self._pwd_fut = asyncio.get_event_loop().create_future()
                pwd = await self._pwd_fut
                self.conn_note = "密码提交中…"
                me = await self.client.sign_in(password=pwd)
            except tg_errors.PhoneCodeInvalidError:
                self.log("验证码错误，请重新输入", LOG_WARN, "tg")
                continue
            except Exception as e:
                self.log(f"登录失败：{humanize_error(e)}", LOG_ERROR, "tg")
                self._login_state = "failed"
                return None
        return me

    def set_code(self, code):
        if self._code_fut and not self._code_fut.done():
            self._code_fut.set_result(code.strip())

    def set_password(self, pwd):
        if self._pwd_fut and not self._pwd_fut.done():
            self._pwd_fut.set_result(pwd)

    # ---------------- 拉取会话 ----------------
    async def fetch_dialogs(self, limit=2000):
        if self.demo:
            items = self._demo_dialogs()
            self.dialogs = {d["peer_key"]: {"ent": None, "dto": d} for d in items}
            await self._load_folder_tags()
            self.log(f"演示模式：已加载 {len(items)} 个会话", LOG_INFO, "tg")
            return items
        if not self.client:
            return []
        items = []
        total = 0
        async for dialog in self.client.iter_dialogs(limit=limit):
            ent = dialog.entity
            dto = self._build_dto(ent, dialog)
            self.dialogs[dto["peer_key"]] = {"ent": ent, "dto": dto}
            items.append(dto)
            total += 1
            if total % 200 == 0:
                self.log(f"已加载 {total} 个会话", LOG_INFO, "tg")
        self.log(f"共加载 {len(items)} 个会话", LOG_INFO, "tg")
        await self._load_folder_tags()
        self.log_quota()
        self._kick_auto_totals()   # 后台自动拉全部历史的真实媒体总数（用户无需手动操作）
        return items

    def _build_dto(self, ent, dialog=None):
        kind = session_kind(ent)
        title = self._title_of(ent)
        username = getattr(ent, "username", None) or None
        rows = R.risk_rows(ent)
        last_ts = None
        if dialog and getattr(dialog, "message", None):
            try:
                last_ts = int(getattr(dialog.message, "date", 0).timestamp())
            except Exception:
                last_ts = None
        days = active_days_of(last_ts) if last_ts else None
        # A 组零成本字段：原始 Dialog TL 对象自带（Dialog 包装类是空壳，属性全在
        # dialog.dialog 上——同 top_message 的教训）；verified/signatures/date 在实体上
        raw_dlg = getattr(dialog, "dialog", None) if dialog else None
        ns = getattr(raw_dlg, "notify_settings", None)
        mute_until = getattr(ns, "mute_until", None)
        muted = False
        if mute_until:
            try:
                ts = mute_until.timestamp() if hasattr(mute_until, "timestamp") else int(mute_until)
                muted = ts > time.time()
            except Exception:
                muted = False
        created = getattr(ent, "date", None)
        # 零成本角色识别：creator / admin_rights 就在实体上（不必等统计跑完才受保护）
        role = ""
        if isinstance(ent, (tl_types.Channel, tl_types.Chat)):
            if getattr(ent, "creator", False):
                role = "creator"
            elif getattr(ent, "admin_rights", None):
                role = "admin"
        pk = peer_key_of(ent)
        wl = self.whitelist.has(pk)
        return {
            "id": getattr(ent, "id", 0),
            "title": title,
            "username": username,
            "kind": kind,
            "kind_key": kind,
            "peer_key": peer_key_of(ent),
            "risk": [{"code": c, "short": s, "why": w, "raw": r} for c, s, w, r in rows],
            "restriction_platforms": R.restriction_platforms(ent),
            "risk_cell": C.RISK_SEP.join(s for _c, s, _w, _r in rows),
            "members": None,
            "posts": None,
            # ⭐ Telethon 的 Dialog 包装类不透出 top_message（在原始 TL 对象
            # dialog.dialog 上），直接 getattr(dialog,...) 恒得 0
            "top_message": int(getattr(raw_dlg, "top_message", 0) or 0) if raw_dlg else 0,
            "unread": int(getattr(raw_dlg, "unread_count", 0) or 0) if raw_dlg else 0,
            "pinned": bool(getattr(raw_dlg, "pinned", False)),
            "archived": int(getattr(raw_dlg, "folder_id", 0) or 0) == 1,
            "muted": muted,
            "verified": bool(getattr(ent, "verified", False)),
            "signature": bool(getattr(ent, "signatures", False)),
            "gigagroup": bool(getattr(ent, "gigagroup", False)),
            "has_photo": bool(getattr(ent, "photo", None)),
            "created_ts": int(created.timestamp()) if created else None,
            "last_ts": last_ts,
            "active_days": days,
            "active_text": active_text(days),
            "role": role,
            "folders": "",
            "status": "待处理",
            "whitelisted": wl,
            "is_protected": bool(role) or wl,
            "is_member": kind in (C.KIND_CHANNEL, C.KIND_GROUP),
            "can_destroy": kind in (C.KIND_CHANNEL, C.KIND_GROUP),
        }

    def _title_of(self, ent):
        if isinstance(ent, tl_types.User):
            t = " ".join(filter(None, [getattr(ent, "first_name", ""),
                                        getattr(ent, "last_name", "")])).strip()
            return t or (getattr(ent, "username", "") or str(getattr(ent, "id", "")))
        return getattr(ent, "title", "") or str(getattr(ent, "id", ""))

    def dto_list(self):
        return [v["dto"] for v in self.dialogs.values()]

    # ---------------- 退出 / 删除（按类型分派 + 只读复核 + 5 步重试） ----------------

    def _remove_requests(self, ent):
        """「让这个会话从我这边消失」的请求列表 —— 按类型分派的单一真源。

        私聊/机器人 → DeleteHistoryRequest(just_clear=False, revoke=False)；
        普通群 → DeleteChatUserRequest(self, revoke_history=False)；
        频道/超级群 → LeaveChannelRequest。对频道发 deleteHistory 不报错但会话纹丝不动，
        那是「提示成功却没删」的真因。"""
        kind = session_kind(ent)
        if isinstance(ent, tl_types.User):
            return [tl_messages.DeleteHistoryRequest(
                peer=ent, max_id=0, just_clear=False, revoke=False)]
        if isinstance(ent, tl_types.Chat):
            return [tl_messages.DeleteChatUserRequest(
                ent.id, tl_types.InputUserSelf(), revoke_history=False)]
        if isinstance(ent, tl_types.Channel):
            return [tl_channels.LeaveChannelRequest(ent)]
        raise ValueError(f"不支持的会话类型，无法删除：{kind}")

    async def leave_one(self, peer_key):
        item = self.dialogs.get(peer_key)
        if not item and not self.demo:
            return {"ok": False, "note": "找不到该会话"}
        if self.demo:
            return {"ok": True, "gone": True, "note": "演示模式：已模拟退出/删除"}
        ent = item["ent"]
        # 收藏夹：绝不发删除请求 —— 请求里「删记录」的那一半会真的清空收藏内容
        if getattr(ent, "is_self", False):
            return {"ok": False, "blocked": True,
                    "note": "这是你自己的「收藏夹 / Saved Messages」：Telegram 不允许删除这个会话，"
                            "本工具不对它发任何删除请求（否则会真的清空你收藏的内容）"}
        # 创建者退不出去是 Telegram 的设计：直接说清出路，别让用户对着裸 RPC 猜
        if session_kind(ent) in (C.KIND_CHANNEL, C.KIND_GROUP) and getattr(ent, "creator", False):
            return {"ok": False, "blocked": True,
                    "note": "你是它的创建者 —— Telegram 不允许创建者直接退出自己的频道/群组"
                            "（会让它失去所有者）。要彻底去掉请用「销毁」，"
                            "或先在 Telegram 客户端里把所有权转给别人"}
        try:
            note = ""
            try:
                for req in self._remove_requests(ent):
                    await self.client(req)
            except (tg_errors.ChannelPrivateError, tg_errors.UserNotParticipantError):
                return {"ok": True, "gone": True, "note": "会话已不存在或已不可访问"}
            except tg_errors.PeerIdInvalidError:
                # 实体缓存过期：退化到高层接口，它会先解析实体再处理
                await self.client.delete_dialog(ent)
                note = "（实体缓存过期，已走兼容路径）"
            except tg_errors.ChatAdminRequiredError:
                return {"ok": False, "blocked": True, "note": "没有权限删除该会话"}
            except tg_errors.RPCError as e:
                return {"ok": False, "note": humanize_error(e)}
            # 只读复核：没抛异常 ≠ 成功，服务端可能收下不执行
            still = await self._still_present(ent)
            if still is None:
                return {"ok": True, "gone": None,
                        "note": (note + " " if note else "") + "无法复核（服务端未确认），请刷新列表核对"}
            if peer_key not in still:
                return {"ok": True, "gone": True,
                        "note": (note or "已退出/删除") + "（服务端复核确认）"}
            # 复核仍在 → 按序重试，每试一步都重新复核，不靠猜
            retried = await self._retry_removal(peer_key, ent, item)
            if retried is True:
                return {"ok": True, "gone": True, "note": "首次请求未生效，已用更强的写法重试成功（复核确认）"}
            if isinstance(retried, str):
                return {"ok": False, "blocked": True,
                        "note": f"服务端确认会话仍然存在。{retried}"}
            return {"ok": False, "still_there": True,
                    "note": "服务端确认会话仍然存在 —— 已试完全部备用写法。"
                            "刷新列表看它是否真的还在；若还在，多半只能在客户端里手动处理"}
        except Exception as e:
            return {"ok": False, "note": humanize_error(e)}

    async def _block_unblock_delete(self, ent, revoke=False):
        """封禁 → 解封 → 再删（解封一定执行）。对「对方账号已注销」的顽固会话，
        先改一遍封禁状态逼服务端重建 peer 的会话状态，随后的删除才会真的生效。
        只对已勾选删除的会话使用；封禁不会通知对方。"""
        await self.client(tl_contacts.BlockRequest(ent))
        try:
            await self.client(tl_contacts.UnblockRequest(ent))
        except Exception as e:
            raise RuntimeError(
                f"解封失败，该会话可能仍处于封禁状态（请到 Telegram 客户端里手动解封）：{e}")
        await self.client(tl_messages.DeleteHistoryRequest(
            peer=ent, max_id=0, just_clear=False, revoke=bool(revoke)))

    async def _retry_removal(self, peer_key, ent, item):
        """「删了、但服务端说还在」时的备用写法，按序尝试，每步与上一步真的不同。

        返回：True=某步重试后复核消失；str=全部试完仍在（附解释）；False=中途异常中断。
        """
        top = int(item.get("dto", {}).get("top_message", 0) or 0)
        steps = (
            ("explicit_peer", "改用显式 InputPeer 重发（排除实体解析这一层的问题）"),
            ("max_id", "带上服务端返回的 top_message 当 max_id 重发（排除 max_id=0 被当成「无事可做」）"),
            ("delete_dialog", "改走 Telethon 的高层 delete_dialog"),
            ("revoke", "改带 revoke=True 重发（只对已注销账号：那边已删无可删）"),
            ("block_cycle", "临时封禁 → 解封 → 再删（重置服务端对这条会话的状态，只对幽灵账号）"),
        )
        for step, label in steps:
            try:
                skip = None
                if step == "explicit_peer":
                    try:
                        peer = get_input_peer(ent)
                    except Exception:
                        continue
                    await self.client(tl_messages.DeleteHistoryRequest(
                        peer=peer, max_id=0, just_clear=False, revoke=False))
                elif step == "max_id":
                    if top <= 0:
                        continue
                    await self.client(tl_messages.DeleteHistoryRequest(
                        peer=ent, max_id=top, just_clear=False, revoke=False))
                elif step == "delete_dialog":
                    await self.client.delete_dialog(ent)
                elif step == "revoke":
                    if getattr(ent, "is_self", False):
                        continue
                    if not getattr(ent, "deleted", False):
                        skip = ("revoke=True 会连对方那份聊天记录一起删掉，"
                                "对方是正常在用的账号，这一步不替你做这个决定")
                    else:
                        await self.client(tl_messages.DeleteHistoryRequest(
                            peer=ent, max_id=0, just_clear=False, revoke=True))
                elif step == "block_cycle":
                    if getattr(ent, "is_self", False):
                        continue
                    if not is_ghost_user(ent):
                        skip = ("临时封禁只对已注销的幽灵账号使用，"
                                "不替你封禁一个正常在用的陌生/联系人")
                    else:
                        await self._block_unblock_delete(ent)
                still = await self._still_present(ent)
                if still is not None and peer_key not in still:
                    self.log(f"重试写法生效（{label}），复核确认已消失", LOG_INFO, "tg")
                    return True
                if skip:
                    self.log(f"{label}：跳过 —— {skip}", LOG_WARN, "tg")
                else:
                    self.log(f"重试写法未生效：{label}", LOG_WARN, "tg")
            except Exception as e:
                self.log(f"重试写法（{label}）失败：{humanize_error(e)}", LOG_WARN, "tg")
        return False

    async def _still_present(self, ents):
        """只读复核：返回仍在的 peer_key 集合，或 None（无法复核）。"""
        peers = [get_input_peer(e) for e in (ents if isinstance(ents, list) else [ents]) if e]
        if not peers:
            return {}
        try:
            res = await self.client(tl_messages.GetPeerDialogsRequest(peers=peers))
        except Exception as e:
            self.log(f"只读复核失败：{humanize_error(e)}", LOG_WARN, "tg")
            return None
        dialogs = getattr(res, "dialogs", None)
        if dialogs is None:
            return None
        still = set()
        for d in dialogs:
            try:
                still.add(peer_key_of(d.peer))
            except Exception:
                continue
        return still

    async def destroy_one(self, peer_key, confirm_word):
        if confirm_word != C.DESTROY_CONFIRM_WORD:
            return {"ok": False, "note": f"销毁需在确认框手打「{C.DESTROY_CONFIRM_WORD}」"}
        item = self.dialogs.get(peer_key)
        if not item and not self.demo:
            return {"ok": False, "note": "找不到该会话"}
        if self.demo:
            return {"ok": True, "gone": True, "note": "演示模式：已模拟销毁"}
        ent = item["ent"]
        try:
            if isinstance(ent, tl_types.Chat):
                await self.client(tl_messages.DeleteChatRequest(ent.id))
            else:
                await self.client(tl_channels.DeleteChannelRequest(ent))
            return {"ok": True, "gone": True, "note": "已销毁（不可恢复）"}
        except tg_errors.ChatAdminRequiredError:
            return {"ok": False, "note": "你不是该频道/群组的创建者，无法删除"}
        except Exception as e:
            return {"ok": False, "note": humanize_error(e)}

    # ---------------- 重进 ----------------
    async def rejoin(self, kind, value):
        if self.demo:
            return {"state": "ok", "note": "演示模式：已模拟加入"}
        if not value:
            return {"state": "fail", "note": "空的目标"}
        try:
            if kind == C.REJOIN_INVITE:
                try:
                    await self.client(tl_messages.CheckChatInviteRequest(value))
                except Exception as e:
                    return {"state": "fail", "note": f"邀请链接不可用（可能已过期/被撤销）：{humanize_error(e)}"}
                await self.client(tl_messages.ImportChatInviteRequest(value))
                return {"state": "ok", "note": "已通过邀请链接加入"}
            entity = await self.client.get_entity(value)
            await self.client(tl_channels.JoinChannelRequest(entity))
            return {"state": "ok", "note": "已加入公开频道/群组"}
        except tg_errors.UserAlreadyParticipantError:
            return {"state": "already", "note": "已经是成员"}
        except (tg_errors.InviteHashExpiredError, tg_errors.InviteHashInvalidError) as e:
            return {"state": "fail", "note": f"邀请链接失效：{humanize_error(e)}"}
        except tg_errors.FloodWaitError as e:
            return {"state": "fail", "note": f"被限流，请等 {getattr(e, 'seconds', '?')} 秒后重试"}
        except tg_errors.UsernameNotOccupiedError:
            return {"state": "fail", "note": "这个用户名已经不存在（频道可能已注销或改名）"}
        except tg_errors.ChannelPrivateError:
            return {"state": "fail", "note": "该频道已不可访问（被封/私密/你被拉黑）"}
        except Exception as e:
            return {"state": "fail", "note": humanize_error(e)}

    # ---------------- 统计（成员/作品/活跃/角色）+ 缓存 ----------------
    async def fetch_stats(self, peer_keys, force=False, conc=1, ttl_hours=None, use_cache=True):
        """获取用户数 / 作品数 / 活跃（对齐 v1.7.7：并发可调、缓存 TTL 可调、可用缓存开关）。

        conc>1 时用信号量并发请求（越快越容易限流）；ttl_hours 覆盖缓存默认有效期；
        use_cache=False 等价 force（不发请求读缓存的通道直接关掉）。"""
        if self.demo:
            out = {}
            for pk in peer_keys:
                rnd = random.Random(hash(pk)).randint
                out[pk] = {
                    "members": rnd(3, 50000), "posts": rnd(0, 3000),
                    "last_ts": int(time.time()) - rnd(0, 90) * 86400,
                    "role": "", "active_text": f"{rnd(0, 90)}天前"}
            return out
        results = {}
        hits = 0
        # 进度跟踪（v3.19.1）：前端经 /api/stats/progress 轮询，状态栏显示「统计 N/M」
        self.stats_prog = {"done": 0, "total": len(peer_keys), "running": True}
        self.cancel_flag = False   # 复位全局停止标志（上次批量/任务的残留不得吞掉本次任务）

        async def one(pk, ent):
            try:
                val = await self._stat_one(ent)
            finally:
                self.stats_prog["done"] += 1
            self.stats_cache.put(pk, val)
            results[pk] = val

        todo = []
        for pk in peer_keys:
            item = self.dialogs.get(pk)
            if not item:
                continue
            cached = self.stats_cache.get(pk, ttl_hours=ttl_hours) if (use_cache and not force) else None
            if cached:
                results[pk] = cached
                hits += 1
            else:
                todo.append((pk, item["ent"]))
        self.stats_prog["done"] = hits
        if hits:
            self.log(f"统计：{hits} 个会话命中缓存"
                     + (f"，{len(todo)} 个需要请求" if todo else "，无需请求"))
        if todo:
            conc = max(1, min(int(conc or 1), 32))
            if conc == 1:
                for pk, ent in todo:
                    if self.cancel_flag:
                        break
                    await one(pk, ent)
            else:
                sem = asyncio.Semaphore(conc)

                async def guarded(pk, ent):
                    if self.cancel_flag:
                        return
                    async with sem:
                        await one(pk, ent)

                await asyncio.gather(*(guarded(pk, ent) for pk, ent in todo))
        self.stats_prog["running"] = False
        self.stats_prog["done"] = self.stats_prog["total"]
        return results

    def clear_stats_cache(self):
        """清空统计缓存（对齐 v1.7.7「清空统计缓存」按钮）。返回清掉的条数。"""
        n = len(getattr(self.stats_cache, "data", {}) or {})
        self.stats_cache.data = {}
        self.stats_cache.save()
        return n

    async def fetch_media_stats(self, peer_keys, limit=500, force=False,
                                ttl_hours=None, use_cache=True, conc=2, mode="scan"):
        """媒体统计（v3.17.0 新增；v3.20.0 加全历史真实总数模式）。

        mode="scan"：扫每个会话最近 limit 条消息按类型计数。缓存按「已扫条数 ≥ 本次 limit」
        判定可复用；上次已扫到底（会话历史不足）的会话对任意更大深度都直接命中；
        「停止」中断的部分结果不落缓存；cancel_flag 置位即停。
        mode="totals"：全部历史真实总数（口径对齐官方客户端媒体面板）——每类媒体一个
        messages.search 过滤器取 total_count，每会话 7 个请求，与扫描深度无关；已做过且
        未失败的会话（full=True）直接命中缓存。"""
        if self.demo:
            out = {}
            for pk in peer_keys:
                v = demo_media(pk, limit)
                if mode == "totals":
                    v = {**v, "mode": "totals", "full": True, "limit": 0}
                out[pk] = v
            if use_cache:
                # 演示模式同样落缓存（导出媒体列 / GET /api/media 才有数据）；一次性写盘
                ts = int(time.time())
                for pk, v in out.items():
                    self.media_cache.data[pk] = (ts, v)
                self.media_cache.save()
            return out
        results = {}
        hits = 0
        # 进度跟踪（v3.19.0）：前端经 /api/media/progress 轮询，状态栏显示「媒体统计 N/M」
        self.media_prog = {"done": 0, "total": len(peer_keys), "running": True}
        self.cancel_flag = False   # 复位全局停止标志（上次批量/任务的残留不得吞掉本次任务）

        async def one(pk, ent):
            try:
                if mode == "totals":
                    val = await self._totals_one(ent)
                else:
                    val = await self._media_one(ent, limit)
            finally:
                self.media_prog["done"] += 1
            if val is not None:
                self.media_cache.put(pk, val)
                results[pk] = val
                title = self.dialogs.get(pk, {}).get("dto", {}).get("title", pk)
                self.log(f"媒体统计：{title} —— "
                         + (f"全历史真实计数 共 {val['total']}" if mode == "totals"
                            else f"扫描 {val['scanned']} 条")
                         + (f"，失败：{val['error']}" if val.get("error") else ""))

        todo = []
        for pk in peer_keys:
            item = self.dialogs.get(pk)
            if not item:
                continue
            cached = self.media_cache.get(pk, ttl_hours=ttl_hours) if (use_cache and not force) else None
            if cached:
                c_mode = cached.get("mode", "scan")
                if c_mode == mode:
                    c_limit = int(cached.get("limit", 0) or 0)
                    if mode == "totals" and cached.get("full"):
                        # 全历史计数是完整事实，永远直接命中（未出错的才算 full）
                        results[pk] = with_total(cached)
                        hits += 1
                        continue
                    if c_limit >= int(limit):
                        results[pk] = with_total(cached)
                        hits += 1
                        continue
                    if cached.get("exhausted"):
                        # 上次已扫到底（会话历史不足该深度）：对任意更大深度都无需重扫
                        results[pk] = with_total({**cached, "limit": max(c_limit, int(limit))})
                        hits += 1
                        continue
            todo.append((pk, item["ent"]))
        self.media_prog["done"] = hits
        if hits:
            self.log(f"媒体统计：{hits} 个会话直接命中缓存"
                     + (f"，{len(todo)} 个需要扫描" if todo else "，无需扫描"))
        if todo:
            conc = max(1, min(int(conc or 1), 8))
            if conc == 1:
                for pk, ent in todo:
                    if self.cancel_flag:
                        break
                    await one(pk, ent)
            else:
                sem = asyncio.Semaphore(conc)

                async def guarded(pk, ent):
                    if self.cancel_flag:
                        return
                    async with sem:
                        await one(pk, ent)

                await asyncio.gather(*(guarded(pk, ent) for pk, ent in todo))
        self.media_prog["running"] = False
        self.media_prog["done"] = self.media_prog["total"]
        return results

    async def _media_one(self, ent, limit):
        counts = dict.fromkeys(MEDIA_BUCKETS, 0)
        scanned = 0
        cancelled = False
        try:
            async for m in self.client.iter_messages(ent, limit=limit):
                if self.cancel_flag:
                    cancelled = True
                    break
                counts[classify_message(m)] += 1
                scanned += 1
        except Exception as e:  # noqa: BLE001 —— 历史隐藏 / 权限等，记错误但仍返回已扫部分
            return {"scanned": scanned, "limit": limit, "counts": counts,
                    "ts": int(time.time()), "error": humanize_error(e)}
        if cancelled:
            # 「停止」中断的部分结果不落缓存（防止下次被当成完整结果），仅本次响应可见
            return None
        return {"scanned": scanned, "limit": limit, "counts": counts,
                "total": media_total(counts), "ts": int(time.time()),
                "exhausted": scanned < int(limit)}

    def _kick_auto_totals(self):
        """后台自动统计全部历史媒体总数（v3.20.2）：拉完列表即启动，用户零操作。

        断点续跑：full=True 的永久缓存自动跳过，重启 / 重连后只补缺的和失败的。"""
        if self.demo or self._auto_media_task and not self._auto_media_task.done():
            return   # 演示模式无需（预填充）；已在跑则不重复起
        self._auto_media_task = asyncio.get_event_loop().create_task(self._auto_totals())

    async def _auto_totals(self):
        """逐个向服务端查询全部会话的全历史媒体总数（每类一次 search，7 请求/会话）。"""
        todo = []
        for pk, item in self.dialogs.items():
            if item.get("ent") is None:
                continue
            cached = self.media_cache.get(pk, ttl_hours=24 * 365)
            if cached and cached.get("mode") == "totals" and cached.get("full"):
                continue   # 已有完整结果，永不重查
            todo.append(pk)
        if not todo:
            return
        self.log(f"后台媒体总数统计启动：{len(todo)} 个会话待查"
                 f"（共 {len(self.dialogs)} 个，其余已有缓存）", LOG_INFO, "tg")
        self.media_prog = {"done": 0, "total": len(todo), "running": True}
        self.cancel_flag = False
        sem = asyncio.Semaphore(2)   # 低并发防限流：每会话 7 请求，610 会话约 15~25 分钟
        done_count = 0
        fails = 0

        async def one(pk):
            nonlocal done_count, fails
            item = self.dialogs.get(pk)
            ent = item.get("ent") if item else None
            if ent is None:
                self.media_prog["done"] += 1
                return
            async with sem:
                if self.cancel_flag:
                    return
                try:
                    val = await self._totals_one(ent)
                except Exception as e:  # noqa: BLE001 —— 单会话异常不拖垮整体
                    val = {"mode": "totals", "full": False, "limit": 0, "scanned": 0,
                           "counts": {}, "total": 0, "ts": int(time.time()),
                           "error": humanize_error(e)}
                if val is not None:
                    self.media_cache.put(pk, val)
                    if val.get("error"):
                        fails += 1
            self.media_prog["done"] += 1
            done_count += 1
            if done_count % 50 == 0:
                self.log(f"后台媒体总数：{done_count}/{len(todo)}…", LOG_INFO, "tg")

        await asyncio.gather(*[one(pk) for pk in todo])
        self.media_prog["running"] = False
        if self.cancel_flag:
            self.log("后台媒体总数统计已停止（重新拉取列表即继续，已完成的不会重查）",
                     LOG_WARN, "tg")
        else:
            msg = f"后台媒体总数统计完成：{len(todo) - fails} 个成功"
            if fails:
                msg += f"，{fails} 个失败（下次启动自动重试）"
            self.log(msg, LOG_INFO, "tg")

    async def _totals_one(self, ent):
        """全部历史真实媒体总数（v3.20.0）：对齐官方客户端媒体面板的口径。

        每类媒体发一次 messages.search（limit=1，只取 total_count），共 7 个请求；
        不逐条扫消息，几百 GB 的历史也秒回。中途出错返回已拿到的部分并带 error
        （不标 full，下次可重试）；「停止」中断不落缓存。"""
        counts = dict.fromkeys([b for b, _ in MEDIA_TOTAL_FILTERS], 0)
        error = None
        for bucket, flt in MEDIA_TOTAL_FILTERS:
            if self.cancel_flag:
                return None   # 中断：不落缓存，与扫描模式语义一致
            try:
                # MEDIA_TOTAL_FILTERS 存的是 TL 实例（存类会 _bytes() 缺 self，
                # v3.20.0 存量 bug：真实账号全部会话统计失败）
                r = await self.client(tl_messages.SearchRequest(
                    peer=ent, q="", filter=flt, min_date=None, max_date=None,
                    offset_id=0, add_offset=0, limit=1, max_id=0, min_id=0, hash=0))
                counts[bucket] = int(getattr(r, "count", 0) or 0)
            except Exception as e:  # noqa: BLE001 —— 部分失败：留已拿到的部分，下次重试
                error = humanize_error(e)
                break
        out = {"mode": "totals", "full": error is None, "limit": 0,
               "scanned": sum(counts.values()), "counts": counts,
               "total": media_total(counts), "ts": int(time.time())}
        if error:
            out["error"] = error
        return out

    async def _stat_one(self, ent):
        members = None
        if isinstance(ent, tl_types.Channel):
            members = getattr(ent, "participants_count", None)
            if not members and (getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False)):
                try:
                    full = await self.client(tl_channels.GetFullChannelRequest(ent))
                    members = getattr(getattr(full, "full_chat", None), "participants_count", None)
                except Exception:
                    members = None
        elif isinstance(ent, tl_types.Chat):
            members = getattr(ent, "participants_count", None)
        posts = None
        last_ts = None
        try:
            res = await self.client(tl_messages.GetHistoryRequest(
                peer=ent, offset_id=0, offset_date=None, add_offset=0,
                limit=1, max_id=0, min_id=0, hash=0))
            posts = int(getattr(res, "count", 0) or 0)
            msg = (getattr(res, "messages", []) or [None])[0]
            if msg and getattr(msg, "date", None):
                last_ts = int(msg.date.timestamp())
        except Exception:
            pass
        role = ""
        if isinstance(ent, tl_types.Channel):
            if getattr(ent, "creator", False):
                role = "creator"
            elif getattr(ent, "admin_rights", None):
                role = "admin"
        elif isinstance(ent, tl_types.Chat):
            if getattr(ent, "creator", False):
                role = "creator"
            elif getattr(ent, "admin_rights", None):
                role = "admin"
        days = active_days_of(last_ts) if last_ts else None
        return {"members": members, "posts": posts, "last_ts": last_ts,
                "role": role, "active_text": active_text(days)}

    # ---------------- 设备 ----------------
    async def get_devices(self):
        if self.demo:
            return [
                {"hash": 1, "current": True, "device": "演示-本机", "system": "Windows 11",
                 "ip": "1.2.3.4", "country": "中国", "created": "2026-01-01 08:00", "active": "2026-10-17 08:00"},
                {"hash": 2, "current": False, "device": "演示-手机", "system": "Android 14",
                 "ip": "5.6.7.8", "country": "中国", "created": "2025-12-01 10:00", "active": "2026-10-16 22:00"},
            ]
        res = await self.client(tl_account.GetAuthorizationsRequest())
        out = []
        for a in getattr(res, "authorizations", []) or []:
            out.append({
                "hash": a.hash,
                "current": bool(getattr(a, "current", False)),
                "device": (getattr(a, "device_model", "") or "").strip() or "未知设备",
                "system": " ".join(filter(None, [getattr(a, "system_version", ""),
                                                 getattr(a, "platform", "")])),
                "ip": getattr(a, "ip", "") or "—",
                "country": getattr(a, "country", "") or "—",
                "created": self._fmt_dt(getattr(a, "date_created", None)),
                "active": self._fmt_dt(getattr(a, "date_active", None)),
            })
        return out

    async def kill_device(self, h):
        if self.demo:
            return True
        return await self.client(tl_account.ResetAuthorizationRequest(h))

    @staticmethod
    def _fmt_dt(dt):
        if not dt:
            return "—"
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "—"

    # ---------------- 跳转客户端 ----------------
    async def export_invite(self, peer_key: str):
        """导出会话邀请链接（供无用户名的频道/群组唤起客户端）。

        返回 {"link": 原始链接, "tg": "tg://join?invite=<hash>"}；
        自己是成员时点 join 链接客户端直接进会话。"""
        if self.demo:
            return {"link": "https://t.me/+demo123456", "tg": "tg://join?invite=demo123456"}
        info = self.dialogs.get(peer_key)
        if not info or not self.client or info["ent"] is None:
            return None
        try:
            res = await self.client(tl_messages.ExportChatInviteRequest(info["ent"]))
        except Exception as e:
            self.log(f"导出邀请链接失败：{humanize_error(e)}", LOG_WARN, "tg")
            raise RuntimeError(f"导出邀请链接失败：{humanize_error(e)}") from e
        link = getattr(res, "link", "") or ""
        if not link:
            return None
        return {"link": link, "tg": invite_tg_link(link)}

    # ---------------- 分组 ----------------
    async def _load_folder_tags(self):
        """把每个会话所属的 TG 分组标题写进 dto.folders（一次 GetDialogFilters，本地打标）。

        这是「会话列表分组信息为空」的真因修复：dto 里 folders 原来写死 ""，
        前端的分组筛选与分组列永远匹配不上。"""
        if self.demo:
            for it in self.dialogs.values():
                k = it["dto"]["kind"]
                it["dto"]["folders"] = "演示分组" if k in (C.KIND_CHANNEL, C.KIND_GROUP) else ""
            return
        if not self.client:
            return
        try:
            res = await self.client(tl_messages.GetDialogFiltersRequest())
        except Exception as e:
            self.log(f"读取分组失败（不影响列表）：{humanize_error(e)}", LOG_WARN, "tg")
            return
        tags = {}
        for f in res.filters if res else []:
            fid = getattr(f, "id", None)
            if fid is None or type(f).__name__ == "DialogFilterDefault":
                continue
            title = getattr(getattr(f, "title", None), "text", None) or f"分组{fid}"
            for p in list(getattr(f, "include_peers", []) or []) + list(getattr(f, "pinned_peers", []) or []):
                tags.setdefault(peer_key_of(p), []).append(title)
        for it in self.dialogs.values():
            titles = tags.get(peer_key_of(it["ent"]))
            it["dto"]["folders"] = " · ".join(dict.fromkeys(titles)) if titles else ""

    async def get_folders(self):
        if self.demo:
            return [{"id": 100, "title": "演示分组", "editable": True,
                     "include": ["c:1001"], "exclude": [], "pinned": [],
                     "rules": "无自动规则（纯手工）"}]
        res = await self.client(tl_messages.GetDialogFiltersRequest())
        out = []
        for f in res.filters if res else []:
            if getattr(f, "id", None) is None or type(f).__name__ == "DialogFilterDefault":
                continue
            editable = type(f).__name__ != "DialogFilterChatlist"
            title = getattr(getattr(f, "title", None), "text", None) or f"分组{f.id}"
            rules = []
            for flag, label in (("contacts", "联系人"), ("non_contacts", "非联系人"),
                                ("groups", "群组"), ("broadcasters", "频道"),
                                ("bots", "机器人")):
                if getattr(f, flag, None):
                    rules.append(label)
            out.append({
                "id": f.id, "title": title, "editable": editable,
                "include": [peer_key_of(p) for p in (getattr(f, "include_peers", []) or [])],
                "exclude": [peer_key_of(p) for p in (getattr(f, "exclude_peers", []) or [])],
                "pinned": [peer_key_of(p) for p in (getattr(f, "pinned_peers", []) or [])],
                "rules": " + ".join(rules) if rules else "无自动规则（纯手工）",
            })
        return out

    async def folder_add_remove(self, folder_id, peer_keys, add):
        if self.demo:
            return {"ok": len(peer_keys), "skipped": 0, "note": f"演示：已{'加入' if add else '移出'}分组"}
        res = await self.client(tl_messages.GetDialogFiltersRequest())
        target = None
        for f in res.filters if res else []:
            if getattr(f, "id", None) == folder_id:
                target = f
                break
        if target is None:
            return {"ok": 0, "skipped": len(peer_keys), "note": "分组不存在"}
        if type(target).__name__ == "DialogFilterChatlist":
            return {"ok": 0, "skipped": len(peer_keys), "note": "这是共享分组，成员由服务端维护，本工具不改它"}
        peers = []
        for pk in peer_keys:
            item = self.dialogs.get(pk)
            if item:
                try:
                    peers.append(get_input_peer(item["ent"]))
                except Exception:
                    pass
        if not peers:
            return {"ok": 0, "skipped": len(peer_keys), "note": "没有可操作的会话"}
        inc = list(getattr(target, "include_peers", []) or [])
        exc = list(getattr(target, "exclude_peers", []) or [])
        pinned = list(getattr(target, "pinned_peers", []) or [])
        have = {peer_key_of(p) for p in inc + pinned}
        if add:
            for p in peers:
                k = peer_key_of(p)
                if k not in have:
                    inc.append(p)
                    have.add(k)
            target.include_peers = inc
            target.exclude_peers = [p for p in exc if peer_key_of(p) not in {peer_key_of(x) for x in peers}]
        else:
            target.include_peers = [p for p in inc if peer_key_of(p) not in {peer_key_of(x) for x in peers}]
            target.pinned_peers = [p for p in pinned if peer_key_of(p) not in {peer_key_of(x) for x in peers}]
            for p in peers:
                k = peer_key_of(p)
                if k not in exc:
                    exc.append(p)
            target.exclude_peers = exc
        if not target.include_peers and not target.pinned_peers:
            return {"ok": 0, "skipped": len(peer_keys), "note": "这样会把分组清空，已跳过"}
        await self.client(tl_messages.UpdateDialogFilterRequest(target.id, target))
        await self._load_folder_tags()
        return {"ok": len(peers), "skipped": len(peer_keys) - len(peers),
                "note": f"已把 {len(peers)} 个会话{'加入' if add else '移出'}分组"}

    # ---------------- 静音 / 归档 ----------------
    async def avatar_file(self, peer_key):
        """返回该会话小尺寸头像的本地缓存路径；无头像 / 演示模式 / 下载失败返回 None。
        懒下载：首次命中发一次小图请求（download_big=False），之后全走磁盘缓存零请求。"""
        if self.demo:
            return None
        item = self.dialogs.get(peer_key)
        if not item:
            return None
        ent = item["ent"]
        photo = getattr(ent, "photo", None)
        if not photo:
            return None
        photo_id = getattr(photo, "photo_id", 0) or 0
        path = avatar_cache_path(peer_key, photo_id)
        if os.path.exists(path):
            return path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        async with self._avatar_sem:
            if os.path.exists(path):  # 等锁期间可能已被并发请求下好
                return path
            # 失败重试 2 次（FloodWait / 网络抖动一次失败很常见；重试间隔递增退避）
            for attempt in range(3):
                try:
                    out = await self.client.download_profile_photo(ent, file=path, download_big=False)
                    return path if out else None
                except Exception:  # noqa: BLE001 —— 头像失败绝不影响主流程
                    try:  # 半截文件清掉，避免下次误判缓存命中
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        pass
                    if attempt < 2:
                        await asyncio.sleep(0.8 * (attempt + 1))
            return None

    async def set_mute(self, peer_key, mute):
        if self.demo:
            return True
        item = self.dialogs.get(peer_key)
        if not item:
            return False
        until = 2_147_483_647 if mute else 0
        await self.client(tl_account.UpdateNotifySettingsRequest(
            peer=tl_types.InputNotifyPeer(peer=await self._input(item["ent"])),
            settings=tl_types.InputPeerNotifySettings(mute_until=until)))
        item["dto"]["muted"] = bool(mute)   # 回写 dto：前端 sync 后状态如实反映
        return True

    async def set_archive(self, peer_key, archive):
        if self.demo:
            return True
        item = self.dialogs.get(peer_key)
        if not item:
            return False
        folder_id = 1 if archive else 0
        await self.client(tl_folders.EditPeerFoldersRequest([
            tl_types.InputFolderPeer(peer=await self._input(item["ent"]), folder_id=folder_id)]))
        item["dto"]["archived"] = bool(archive)   # 回写 dto：前端 sync 后状态如实反映
        return True

    async def _input(self, ent):
        try:
            return await self.client.get_input_entity(ent)
        except Exception:
            return get_input_peer(ent)

    # ---------------- 快照 ----------------
    def build_snapshot(self):
        items = self.dto_list()
        return {
            "taken": time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(items),
            "rows": {d["peer_key"]: {
                "title": d["title"], "kind": d["kind"], "members": d["members"],
                "risk_cell": d["risk_cell"]} for d in items},
        }

    def diff_snapshots(self, old, new):
        if not old:
            return {"added": [], "removed": [], "renamed": [], "member_changed": [], "risk_new": []}
        o, n = old.get("rows", {}), new.get("rows", {})
        added = [k for k in n if k not in o]
        removed = [k for k in o if k not in n]

        def name_of(d):
            return d.get("title", "")
        renamed = []
        for k in n:
            if k in o and name_of(n[k]) != name_of(o[k]) and name_of(o[k]):
                renamed.append((k, name_of(o[k]), name_of(n[k])))
        member_changed = []
        for k in n:
            if k in o and isinstance(n[k].get("members"), int) and isinstance(o[k].get("members"), int) \
                    and n[k]["members"] != o[k]["members"]:
                member_changed.append((k, o[k]["members"], n[k]["members"]))
        risk_new = []
        for k in n:
            if k in o and n[k].get("risk_cell") and not o[k].get("risk_cell"):
                risk_new.append((k, n[k].get("risk_cell")))
        return {"added": added, "removed": removed, "renamed": renamed,
                "member_changed": member_changed, "risk_new": risk_new}

    # ---------------- 演示数据 ----------------
    def _demo_row(self, pk, title, uname, kind, risks, rng, **extra):
        """构造一行与真实 _build_dto 字段对齐的演示 dto（缺键会让前端列读到 undefined）。"""
        member_like = kind in (C.KIND_CHANNEL, C.KIND_GROUP)
        rcell = C.RISK_SEP.join(s for _c, s, _w, _r in risks)
        wl = self.whitelist.has(pk)
        role = str(extra.get("role", ""))
        last_days = rng.randint(0, 400)
        d = {
            "id": int(pk.split(":")[1]), "title": title, "username": uname,
            "kind": kind, "kind_key": kind, "peer_key": pk,
            "risk": [{"code": c, "short": s, "why": w, "raw": r} for c, s, w, r in risks],
            "risk_cell": rcell,
            "restriction_platforms": [],
            "members": rng.randint(3, 50000) if member_like else None,
            "posts": rng.randint(0, 3000) if member_like else None,
            "top_message": rng.randint(1, 90000) if member_like else 0,
            "unread": rng.choice([0, 0, 0, 0, 0, 0, 1, 2, 3, 5, 8, 13, 21, 99, 999]),
            "pinned": bool(extra.pop("pinned", rng.random() < 0.03)),
            "archived": bool(extra.pop("archived", rng.random() < 0.04)),
            "muted": bool(extra.pop("muted", rng.random() < 0.25)),
            "verified": bool(extra.pop("verified", False)),
            "signature": bool(extra.pop("signature", False)),
            "gigagroup": bool(extra.pop("gigagroup", False)),
            "has_photo": False,   # demo 无真图：前端按类型着色圆标回退，不发头像请求
            "created_ts": int(time.time()) - rng.randint(30, 2200) * 86400,
            "last_ts": int(time.time()) - last_days * 86400,
            "active_days": last_days,
            "active_text": active_text(last_days),
            "role": role,
            "folders": "",
            "status": "待处理",
            "whitelisted": wl,
            "is_protected": bool(role) or wl,
            "is_member": member_like,
            "can_destroy": member_like,
        }
        d.update(extra)
        return d

    def _demo_dialogs(self):
        # 演示数据：12 行手写示例（异常类型与实际常见异常对齐，按用户裁定不放
        # 「诈骗/假冒」示例；封禁类 kind 与真实判定一致 = KIND_GONE）
        # + 随机批量凑 500 条（v3.18.3），固定种子保证每次重启演示数据一致。
        base = [
            ("c:1001", "科技日报（演示频道）", "demo_channel", C.KIND_CHANNEL, [],
             {"verified": True, "pinned": True}),
            ("c:1002", "羊毛群（演示超级群）", "demo_group", C.KIND_GROUP, [], {}),
            ("c:1003", "受限群（演示）", None, C.KIND_GROUP,
             [(C.RISK_RESTRICTED, "违规受限", "违规受限", "Violates copyright policy")], {}),
            ("u:2001", "通知机器人", "demo_bot", C.KIND_BOT, [], {}),
            ("u:2002", "张三 Demo", None, C.KIND_USER, [], {"muted": True}),
            ("g:3001", "家人群（演示普通群）", None, C.KIND_GROUP, [], {}),
            ("u:2003", "已注销私聊（演示）", None, C.KIND_USER,
             [(C.RISK_GHOST, "已注销", "对方账号已注销", "")], {}),
            ("c:1005", "禁言群（演示）", None, C.KIND_GROUP,
             [(C.RISK_BANNED, "禁言", "被禁言", "")], {}),
            ("c:1006", "已封禁频道（演示）", None, C.KIND_GONE,
             [(C.RISK_FORBIDDEN, "已封禁", "已封禁 / 不可访问", "")], {}),
            ("g:3002", "已停用群（演示）", None, C.KIND_GONE,
             [(C.RISK_DEAD, "已停用", "已停用 / 已不在", "")], {}),
            ("c:1008", "需审核频道（演示）", "demo_nojoin", C.KIND_CHANNEL,
             [(C.RISK_NOJOIN, "需审核", "加入需批准", "")], {}),
            ("c:1009", "入群先审批群（演示）", None, C.KIND_GROUP,
             [(C.RISK_NEEDJOIN, "须先加入", "须先加入才能发言", "")], {}),
        ]
        rng = random.Random(20261018)
        out = [self._demo_row(pk, t, u, k, r, rng, **extra)
               for pk, t, u, k, r, extra in base]

        # ---- 随机批量（词库拼标题，id 从 50000 起保证与手写行不冲突） ----
        ch_topics = ["科技", "新闻", "影视", "资源", "读书", "摄影", "财经", "设计", "游戏",
                     "音乐", "编程", "数码", "汽车", "美食", "旅行", "教育", "健身", "宠物", "动漫", "软件"]
        ch_suffix = ["日报", "精选", "分享", "基地", "社区", "频道", "合集", "研究所", "观察", "小站"]
        gr_suffix = ["交流群", "讨论组", "资源群", "互助群", "闲聊群", "粉丝群"]
        surnames = ["张", "李", "王", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "马", "朱", "胡", "郭", "何", "林"]
        givens = ["伟", "芳", "娜", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "霞", "平", "刚", "静", "敏"]
        bot_words = ["通知", "下载", "签到", "翻译", "天气", "日历", "备忘", "投票", "短链", "音乐"]
        gone_words = ["失效频道", "已解散群", "退出的群", "被封频道"]
        risk_pool = [
            (C.RISK_RESTRICTED, "违规受限", "违规受限", "Violates terms of service"),
            (C.RISK_BANNED, "禁言", "被禁言", ""),
            (C.RISK_NOJOIN, "需审核", "加入需批准", ""),
            (C.RISK_NEEDJOIN, "须先加入", "须先加入才能发言", ""),
        ]
        kind_pre = {C.KIND_CHANNEL: "c", C.KIND_GROUP: "g", C.KIND_USER: "u",
                    C.KIND_BOT: "u", C.KIND_GONE: "c"}
        ids = {"c": 50000, "g": 50000, "u": 50000}
        seq = 0
        while len(out) < 500:
            seq += 1
            roll = rng.random()
            if roll < 0.35:
                kind = C.KIND_CHANNEL
            elif roll < 0.60:
                kind = C.KIND_GROUP
            elif roll < 0.85:
                kind = C.KIND_USER
            elif roll < 0.93:
                kind = C.KIND_BOT
            else:
                kind = C.KIND_GONE
            member_like = kind in (C.KIND_CHANNEL, C.KIND_GROUP)
            risks = []
            if kind == C.KIND_GONE:
                code = C.RISK_FORBIDDEN if rng.random() < 0.6 else C.RISK_DEAD
                risks = [(code, C.RISK_SHORT[code], C.RISK_LABEL[code], "")]
                title = f"{rng.choice(gone_words)}（演示 {seq}）"
            elif member_like and rng.random() < 0.05:
                risks = [risk_pool[rng.randrange(len(risk_pool))]]
                if kind == C.KIND_CHANNEL:
                    title = f"{rng.choice(ch_topics)}{rng.choice(ch_suffix)}（演示 {seq}）"
                else:
                    title = f"{rng.choice(ch_topics)}{rng.choice(gr_suffix)}（演示 {seq}）"
            elif kind == C.KIND_CHANNEL:
                title = f"{rng.choice(ch_topics)}{rng.choice(ch_suffix)}（演示 {seq}）"
            elif kind == C.KIND_GROUP:
                title = f"{rng.choice(ch_topics)}{rng.choice(gr_suffix)}（演示 {seq}）"
            elif kind == C.KIND_USER:
                title = f"{rng.choice(surnames)}{rng.choice(givens)}（演示 {seq}）"
            else:
                title = f"{rng.choice(bot_words)}助手（演示 {seq}）"
            uname = None if rng.random() < 0.45 else f"demo_{seq}"
            pre = kind_pre[kind]
            ids[pre] += 1
            pk = f"{pre}:{ids[pre]}"
            role = ""
            if member_like:
                rr = rng.random()
                role = "creator" if rr < 0.02 else ("admin" if rr < 0.05 else "")
            out.append(self._demo_row(pk, title, uname, kind, risks, rng, role=role))
        return out

    # ---------------- 白名单 ----------------
    def toggle_whitelist(self, peer_key):
        return self.whitelist.toggle(peer_key)

    def set_whitelist(self, peer_keys, on):
        for pk in peer_keys:
            if on:
                self.whitelist.add(pk)
            else:
                self.whitelist.remove(pk)

    # ---------------- 批量退出 + 断点续跑 ----------------
    def pending_path(self):
        return S.cache_file("pending", self.key())

    def load_pending(self):
        try:
            with open(self.pending_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_pending(self, keys):
        try:
            S.ensure_dir()
            p = self.pending_path()
            if keys:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(list(keys), f, ensure_ascii=False, indent=1)
            elif os.path.exists(p):
                os.remove(p)
        except Exception as e:
            self.log(f"断点文件写入失败：{humanize_error(e)}", LOG_WARN, "app")

    def pending_items(self):
        """未完成的断点队列（带标题，供前端展示）。"""
        out = []
        for pk in self.load_pending():
            item = self.dialogs.get(pk, {})
            out.append({"peer_key": pk,
                        "title": item.get("dto", {}).get("title", pk)})
        return out

    async def leave_many(self, peer_keys, interval=0.0, jitter=0.0):
        """批量退出：执行前写断点文件，逐条处理后从断点移除并落盘。

        处理中的网络异常会中断批次（剩余项留在断点，等待续跑）；
        单条业务失败（ok=False）不中断，继续下一条。
        interval/jitter（秒）：每条之间等 interval + 0~jitter 的随机时长，降低限流概率（对齐 v1.7.7）。
        """
        queue = [k for k in peer_keys if k]
        before_quota = self.quota_used()
        self.save_pending(queue)
        results = []
        self.cancel_flag = False
        for i, pk in enumerate(queue):
            if self.cancel_flag:
                self.log("收到停止指令：批量任务已中止，剩余项保留在断点，可随时续跑", LOG_WARN, "tg")
                return {"results": results, "remaining": self.pending_items(),
                        "interrupted": True, "stopped": True}
            if i > 0 and interval > 0:
                wait = interval + (random.random() * jitter if jitter > 0 else 0.0)
                if wait > 0:
                    await asyncio.sleep(min(wait, 600.0))
            try:
                r = await self.leave_one(pk)
            except Exception as e:
                self.log(f"批量退出中断于 {pk}：{humanize_error(e)}", LOG_ERROR, "tg")
                results.append({"peer_key": pk, "ok": False, "note": f"中断：{humanize_error(e)}"})
                remaining = [k for k in self.load_pending() if k != pk]
                self.save_pending(remaining)
                return {"results": results, "remaining": self.pending_items(),
                        "interrupted": True}
            if r.get("ok"):
                self.history.add({"action": "leave", "peer_key": pk,
                                  "title": self.dialogs.get(pk, {}).get("dto", {}).get("title", pk),
                                  "note": r.get("note", ""), "t": time.strftime("%Y-%m-%d %H:%M:%S")})
            results.append({"peer_key": pk, **r})
            remaining = [k for k in self.load_pending() if k != pk]
            self.save_pending(remaining)
        if not any(not r.get("ok") for r in results):
            self.log_quota(before=before_quota)
        return {"results": results, "remaining": [], "interrupted": False}

    def quota_used(self):
        """已占用名额 = 频道 + 群组成员数（「已不在」的不算）。"""
        return sum(1 for v in self.dialogs.values() if v.get("dto", {}).get("is_member"))

    def premium(self):
        return bool(getattr(self.user, "premium", False)) if self.user else False

    def quota_limit(self):
        return C.QUOTA_PREMIUM if self.premium() else C.QUOTA_FREE

    def log_quota(self, before=None):
        """名额写进日志；before 给了就顺便报「本次腾出几个」。"""
        if not self.dialogs:
            return
        used, limit = self.quota_used(), self.quota_limit()
        line = f"🎯 订阅名额：{used}/{limit}（剩 {max(0, limit - used)}）"
        line += (" —— Premium 上限 1000（按客户端估算）" if self.premium()
                 else " —— 免费账号上限 500（按客户端估算）")
        self.log(line, LOG_INFO, "tg")
        if before is not None and before > used:
            self.log(f"   · 本次腾出 {before - used} 个名额（{before} → {used}）。", LOG_INFO, "tg")

    def dispose(self):
        if self.client and not self.demo:
            try:
                asyncio.get_event_loop().create_task(self.client.disconnect())
            except Exception:
                pass
