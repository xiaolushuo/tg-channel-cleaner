#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TG 频道批量退出工具 (GUI 版)
================================
使用 Telethon 操作你【自己的】Telegram 账号，列出已订阅的频道/群组，
勾选后批量退出。纯本地工具，不依赖任何第三方服务器。

使用前准备:
  1. 打开 https://my.telegram.org ，登录后「API development tools」创建一个应用，
     拿到 api_id 与 api_hash。
  2. pip install telethon
  3. 运行本脚本，填入 api_id / api_hash / 手机号，点击「连接/登录」。
     首次登录会要求输入手机收到的验证码（若开启了两步验证还需输入密码）。
  4. 等会话列表加载完，勾选要处理的，点「退出 / 删除选中」。
     频道 / 群组会被**退出**；私聊 / 机器人没有「退出」这个概念，会被**删除会话**。

注意事项:
  - 本工具仅用于管理你自己的账号，请遵守 Telegram 服务条款。
  - 退出间隔默认 2 秒；若提示限流(FloodWait)，工具会自动等待后重试。
  - 登录态保存在本目录 tg_session.session，请勿泄露该文件。
"""

import asyncio
import csv
import datetime
import json
import os
import random
import re
import sys
import threading
import time

# GUI 依赖 tkinter（Python 标准库），但部分“精简/嵌入式”Python 或 Linux 默认未安装。
# 若缺失，给出明确指引而非原始报错。
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
except ModuleNotFoundError:
    base = os.path.dirname(os.path.abspath(__file__))
    exe_path = os.path.join(base, "dist", "TG频道批量退出工具.exe")
    has_exe = os.path.isfile(exe_path)

    guide = (
        "[错误] 当前 Python 未包含 tkinter（GUI 模块），无法启动界面。\n"
        "解决办法（任选其一）：\n"
        "  1) 直接用已打包好的单文件程序：dist/TG频道批量退出工具.exe（无需安装 Python）。\n"
        "  2) 换用带 tkinter 的完整版 Python：\n"
        "     - Windows：从 https://www.python.org 安装官方 CPython（默认即含 tkinter）。\n"
        "     - Linux(Debian/Ubuntu)：sudo apt install python3-tk\n"
        "     - macOS：brew install python-tk，或重装 python.org 版。\n"
        "  3) 装好 tkinter 后，再 pip install -r requirements.txt 运行本脚本。\n"
    )

    # 若已存在打包好的 exe，直接询问是否打开它（最省事，不依赖 tkinter）
    launched = False
    if has_exe:
        try:
            import ctypes

            res = ctypes.windll.user32.MessageBoxW(
                0,
                "当前 Python 缺少 tkinter，无法直接运行本脚本。\n"
                "已检测到打包好的单文件程序，是否现在直接打开它？",
                "TG 频道批量退出工具",
                0x24,  # MB_YESNO | MB_ICONQUESTION
            )
            if res == 6:  # IDYES
                os.startfile(exe_path)
                launched = True
        except Exception:
            pass

    if not launched:
        # sys.stderr 可能为 None（如 pythonw 启动），优先用系统弹窗提示
        shown = False
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, guide, "TG 频道批量退出工具", 0x10)
            shown = True
        except Exception:
            pass
        if not shown:
            try:
                target = sys.__stderr__ if sys.stderr is None else sys.stderr
                target.write(guide + "\n")
            except Exception:
                try:
                    with open(os.path.join(base, "tkinter_missing.log"), "w", encoding="utf-8") as f:
                        f.write(guide)
                except Exception:
                    pass
    sys.exit(0 if launched else 1)

from telethon import TelegramClient
from telethon import errors as telethon_errors
from telethon.errors import FloodWaitError
from telethon.tl import types as tl_types
from telethon.tl.functions import channels as tl_channels
from telethon.tl.functions import contacts as tl_contacts
from telethon.tl.functions import messages as tl_messages
from telethon.tl.types import Channel
from telethon.utils import get_peer_id
from telethon.utils import get_input_peer

# 代理类型：用字符串形式，Telethon 的 _parse_proxy 在“python_socks 优先”和“PySocks 兜底”
# 两种分支里都识别 socks5 / socks4 / http，因此不必直接依赖 socks 模块。
# 真正建立异步代理连接需要 python-socks 包（已加入 requirements 与打包 hidden-imports）。
PROXY_TYPES = {"SOCKS5": "socks5", "SOCKS4": "socks4", "HTTP": "http"}

# 让 PyInstaller 能在构建时静态分析到 Telethon 的 TL 模块（否则打包后运行会报缺少子模块）
try:
    import telethon.tl.types as _tl_types  # noqa: F401
    import telethon.tl.functions as _tl_functions  # noqa: F401
    import telethon.tl.functions.channels as _tl_fn_channels  # noqa: F401
    import telethon.tl.functions.messages as _tl_fn_messages  # noqa: F401
    import telethon.tl.custom as _tl_custom  # noqa: F401
except Exception:
    pass

# 同步代理接口：仅用于“测试代理”功能里真实地经代理出网验证，
# 显式 import 以确保 PyInstaller 打包时也能收集到该子模块。
try:
    import python_socks.sync as _pysocks_sync  # noqa: F401
except Exception:
    _pysocks_sync = None

if getattr(sys, "frozen", False):
    # PyInstaller onefile：运行时自身被解压到临时目录（_MEI*），__file__ 指向那里，
    # 不可靠。用 exe 所在目录作为数据目录，保证 session / config 真正持久化（关闭不丢）。
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "tg_cleaner_config.json")

# ---------------- 多账号：数据文件命名 ----------------
# v1.1.x 是单账号，session / 缓存直接躺在 BASE_DIR 根下。v1.2.0 起每个账号一套：
#   tg_session_<key>.session     登录态（= 授权，删掉就要重新收验证码）
#   tg_stats_cache_<key>.json    统计缓存
#   tg_role_cache_<key>.json     角色缓存
#   config 里的 per_account[key] 白名单 + 抓取配置
# 下面这组 LEGACY_* 只用于「把旧数据升级成第一个账号」，新代码不要再直接引用。
LEGACY_SESSION_FILE = os.path.join(BASE_DIR, "tg_session")
LEGACY_STATS_CACHE_FILE = os.path.join(BASE_DIR, "tg_stats_cache.json")
LEGACY_ROLE_CACHE_FILE = os.path.join(BASE_DIR, "tg_role_cache.json")
# 兼容旧名字（--selftest 会整体重定向这几个路径）
SESSION_FILE = LEGACY_SESSION_FILE
STATS_CACHE_FILE = LEGACY_STATS_CACHE_FILE
ROLE_CACHE_FILE = LEGACY_ROLE_CACHE_FILE

DEFAULT_ACCOUNT_KEY = "default"
ACCOUNT_SESSION_PREFIX = "tg_session_"


def account_key_from_phone(phone):
    """手机号 -> 稳定的账号 key。

    只保留字母数字：一是文件名安全，二是**同一号码永远算出同一个 key**，
    所以重复添加同号码会命中同一个 session，直接复用登录态、不用再收验证码。
    """
    digits = re.sub(r"[^0-9A-Za-z]", "", str(phone or ""))
    return digits or DEFAULT_ACCOUNT_KEY


def account_session_base(acc):
    """账号的 session 基名（不含 .session）。acc 可以是记录 dict 或 key 字符串。"""
    if isinstance(acc, dict):
        name = str(acc.get("session") or "").strip()
        if name:
            return os.path.join(BASE_DIR, name)
        return os.path.join(BASE_DIR, ACCOUNT_SESSION_PREFIX + str(acc.get("key") or DEFAULT_ACCOUNT_KEY))
    return os.path.join(BASE_DIR, ACCOUNT_SESSION_PREFIX + str(acc or DEFAULT_ACCOUNT_KEY))


def account_cache_file(kind, key):
    """账号私有的数据文件。kind: 'stats_cache' / 'role_cache'。"""
    return os.path.join(BASE_DIR, "tg_%s_%s.json" % (kind, str(key or DEFAULT_ACCOUNT_KEY)))


def account_label(acc):
    """账号在界面上的显示名：优先昵称 / @用户名，兜底手机号 / key。

    头部已经用了手机号或 key 时不再重复补一段尾巴 ——
    否则会出现「+8613800138000 · 8613800138000」这种看着像 bug 的重复。
    """
    if not isinstance(acc, dict):
        return str(acc)
    name = str(acc.get("label") or "").strip()
    uname = str(acc.get("username") or "").strip()
    phone = str(acc.get("phone") or "").strip()
    key = str(acc.get("key") or "")
    if name and uname:
        head = "%s (@%s)" % (name, uname)
    elif name:
        head = name
    elif uname:
        head = "@" + uname
    elif phone:
        head = phone
    else:
        head = key
    tail = ""
    if head not in (phone, key):
        tail = phone or key
    return "%s · %s" % (head, tail) if tail else head


def write_config_file(data, path=None):
    """把 config 落盘（模块级：__init__ 早期就要能写，那时 UI 变量还没建）。"""
    try:
        with open(path or CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def migrate_legacy_account(config):
    """把 v1.1.x 的单账号数据升级成「第一个账号」。

    旧布局：tg_session.session + tg_stats_cache.json + config 顶层 whitelist/phone。
    新布局：accounts[0]（session 仍指向旧文件）+ tg_stats_cache_<key>.json + per_account[key]。

    ⚠️ 刻意**不重命名 session 文件**：登录态丢了要重新收验证码，为了文件名好看去赌
    一次 rename 不值得（rename 失败 = 用户被登出）。账户记录里写明 session 基名即可。
    幂等：config 里已有 accounts 时原样返回。
    """
    cfg = config if isinstance(config, dict) else {}
    existing = cfg.get("accounts")
    if existing:
        return list(existing), False

    # 路径一律从 BASE_DIR 现算（而不是读 LEGACY_* 常量）——
    # 常量是在 import 时定死的，测试重定向 BASE_DIR 后它们不会跟着变。
    legacy_session = os.path.join(BASE_DIR, "tg_session")
    legacy_stats = os.path.join(BASE_DIR, "tg_stats_cache.json")
    legacy_role = os.path.join(BASE_DIR, "tg_role_cache.json")

    phone = str(cfg.get("phone") or "").strip()
    has_old_session = os.path.exists(legacy_session + ".session")
    if not has_old_session and not phone and not cfg.get("whitelist"):
        return [], False          # 全新用户，没有历史数据要迁

    key = account_key_from_phone(phone)
    accs = [{
        "key": key,
        "phone": phone,
        "label": "",
        "username": "",
        "user_id": None,
        "session": os.path.basename(legacy_session),
    }]

    # 缓存文件按账号改名（rename 而不是复制：统计缓存可能上千条，没必要存两份）
    for kind, src in (("stats_cache", legacy_stats),
                      ("role_cache", legacy_role)):
        if os.path.exists(src):
            dst = account_cache_file(kind, key)
            if not os.path.exists(dst):
                try:
                    os.replace(src, dst)
                except Exception:
                    pass

    # 顶层 whitelist -> 该账号的 per_account
    pa = dict(cfg.get("per_account") or {})
    st = dict(pa.get(key) or {})
    if cfg.get("whitelist") and not st.get("whitelist"):
        st["whitelist"] = list(cfg["whitelist"])
    pa[key] = st
    cfg["per_account"] = pa
    cfg.pop("whitelist", None)    # 已迁走，免得下次又被当成全局白名单读回来

    return accs, True


# 身份标识 → 界面文案。创建者/管理员退不出去（Telegram 不允许），必须提前识别并跳过。
ROLE_LABEL = {"creator": "创建者", "admin": "管理员"}

# ---------------- 版本号与更新日志 ----------------
# 版本号只有一个真源：窗口标题、状态栏标签、更新日志窗口、--selftest 全部读这里。
# 发版时改 APP_VERSION，并在 CHANGELOG 最前面插一条（仓库里的 CHANGELOG.md 同步更新）。
APP_NAME = "TG 频道批量退出工具"
APP_VERSION = "v1.4.0"
APP_REPO = "https://github.com/xiaolushuo/tg-channel-cleaner"

# [(版本, 日期, [变更条目, ...]), ...] —— 新的放最前面
CHANGELOG = [
    ("v1.4.0", "2026-10-15", [
        "新增：**🚫 异常筛选**（列表上方按钮，或右键「只看这种异常」）。一次筛出**违规 / "
        "已封禁 / 不可关注 / 被禁言**的频道与群组 —— 表格新增「异常」列，每行标出最重的那一类"
        "（如「🚫 违规受限 +2」），鼠标右键选「为什么异常」会把每一条原因逐条写进日志",
        "特征全部来自 **Telegram 已给的实体字段，零请求**：ChannelForbidden / ChatForbidden"
        "（已封禁·不可访问）、restriction_reason（违规受限：版权 / 服务条款 / 色情 …）、"
        "scam / fake（官方标记的诈骗 / 假冒）、deactivated（群已停用）、left（已不在，条目残留）、"
        "join_request / join_to_send（不能直接关注 / 须先加入才能发言）、"
        "banned_rights / default_banned_rights（你被禁言 / 全员禁言）、User.deleted（对方已注销）",
        "**不按名字猜关键词**：靠 title 里出现「福利」「资源」这类词来判违规，会把大批正常频道"
        "误判成违规。这里只认服务端标记，判不了就是判不了，理由与依据都摆在日志里",
        "受限原因**代码优先、文案兜底**：restriction_reason.reason 是稳定代码（copyright / "
        "terms / porn …），先认代码；代码不认识时才去匹配文案。社区里不少脚本只硬编码英文原文"
        "（'This channel is unavailable due to copyright infringement.'），客户端换个语言或 "
        "Telegram 改文案就静默失效",
        "加载完列表自动打印分类统计（零请求）：共 N 个会话、命中 M 个、逐类明细；"
        "「🚫 异常 ▾」菜单里各项都带**实时**计数（postcommand 重算，不会显示旧数字）",
        "菜单还提供：**勾选全部异常行**（含被筛选藏起来的行；受保护项照样跳过但会把跳过数写清楚）、"
        "**把异常统计写进日志**、**记住这些异常（写入白名单）** —— 想留下其中的一部分时先圈出来",
        "导出 CSV 新增「异常特征」列，带完整原因（不只写个标记），方便先看清单再动手",
    ]),
    ("v1.3.2", "2026-10-14", [
        "修复：**已注销账号的私聊会话「删不掉」的真相搞清了**。您发回来的日志显示："
        "60 个私聊全部「服务端确认仍在」。实测发现它们的聊天记录**已经被删掉**"
        "（服务端返回的 top_message 是 0），被留下的是**会话条目本身** —— "
        "对方账号已注销（客户端里是灰头像 / 纯数字名）时，Telegram 只删记录、不摘条目。"
        "所以以前报「已删除」是错的（记录确实删了，会话也确实还在）",
        "修复：复核没通过时**换 4 种更强写法重试**（新增「带 revoke=True 重发」与"
        "「临时封禁 → 解封 → 再删」—— 后者是重置服务端对这条会话状态的办法）。"
        "revoke=True 只对**已注销**的账号使用：对方账号已不存在，不存在误删别人记录的问题；"
        "对正常在用的联系人会显式跳过并说明原因",
        "修复：**「收藏夹 / Saved Messages」不再发任何删除请求**。Telegram 不会删除这个会话，"
        "但请求里「删记录」那一半**真的会清空您收藏的内容** —— 那是不可逆的数据损失。"
        "现在直接拦住并提示「要清空请到客户端用『清空聊天记录』」",
        "修复：**复核没通过时一定会做只读实测并给出数字**（抽查 getHistory 看记录还剩几条）。"
        "上一版这段诊断写在「命中硬限制提示就不诊断」的条件里，结果最需要它的场景（命中「收藏夹 / 已注销」"
        "提示的那一批）反而一次都没执行，日志里只有推测、没有实测",
        "新增：「未能删除」的每一行都补上原因标签（对方账号已注销 / 收藏夹 / 幽灵账号），"
        "不用再靠翻上面的提示去对号入座",
    ]),
    ("v1.3.1", "2026-10-13", [
        "修复：**「删除会话」不再说成功却什么都没发生**。以前只要请求没抛异常就写「已删除」，"
        "而 Telegram 会安静地收下请求却不执行 —— 这就是「提示成功、客户端里会话还在」。"
        "现在删完会**回头问服务端**「这个会话还在吗」：确认没了才写「已删除」，"
        "还在就如实报「未能删除」并把该行**留在列表里**，绝不把「请求发出去了」当成「删掉了」",
        "修复：复核发现仍在时会**换 3 种写法重试**（显式 InputPeer 重发 / 带上服务端返回的"
        " top_message 当 max_id 重发 / 改走 Telethon 高层 delete_dialog），每换一步都重新问一次"
        "服务端；哪一步生效会写进日志，方便定位服务端究竟认哪种写法",
        "修复：**「删除会话」以前对频道 / 群组完全无效**。Telegram 里这类会话只有退出才会从"
        "列表消失，此前对它们发的删除请求服务端收下、不报错、会话却纹丝不动。现在按类型分派："
        "私聊 / 机器人删会话，频道 / 群组**退出**（确认窗逐类写明后果）",
        "修复：创建者点「删除会话」不再只甩一句看不懂的服务端报错，而是直接说明"
        "「创建者退不出去，要删请用『🗑 删除我创建的群组 / 频道』」",
        "新增：确认无法删除的会话会**直说原因**，不再让您反复点 —— 自己的「收藏夹 / "
        "Saved Messages」、对方已注销的账号（Deleted Account）、Telegram 官方支持账号，"
        "这三类是服务端的硬限制，重试也没有用",
        "新增：日志落盘到程序目录的 `tg_leave_log.txt`（界面关掉后依然能查）；复核没通过时"
        "附上只读诊断 —— 消息是否已被清空，用来区分「请求完全没生效」和「记录删了、"
        "会话被 Telegram 保留」。遇到问题把这个文件发我即可",
        "「退出」路径同样接入复核：私聊在「退出」里也是删会话，两条路要么都说真话，要么都在骗人",
    ]),
    ("v1.3.0", "2026-10-13", [
        "**批量划分分组**：新增「📁 分组 ▾ → 批量划分到分组…」对话框，"
        "一次把一批频道 / 群组加进或移出某个分组；快捷键 **Ctrl+G**",
        "划分范围可选三档：**已勾选的行** / **当前筛选与搜索结果** / **全部会话**"
        "——搜完直接整批划进分组，不必先挨个勾选",
        "分组操作不再跳过受保护项（白名单 / 创建者 / 管理员）。"
        "「受保护」只对退出 / 删除有意义，此前用「全选」划分组会静默少划一部分",
        "修复：把会话加入分组时，若它在**排除名单（exclude_peers）**里，"
        "此前只写 include_peers 会被排除名单压住 —— 日志说已加入、Telegram 里却没有",
        "修复：从**带自动规则**的分组移出会话（如「所有群组」这类）此前只从手工名单里删除，"
        "规则下次仍会把它收回来 —— 日志报「已移出 N 个」，客户端里看还在。"
        "现在会写入排除名单，并且日志会说明有多少个属于这种情况",
        "修复：「只看已勾选」开着时清空勾选，列表不会跟着变空（那段刷新代码写在 return 之后，"
        "以前根本执行不到）",
        "分组菜单改为**弹出时实时刷新**，直接显示「已勾选 N 个」「筛选结果 N 个」",
    ]),
    ("v1.2.0", "2026-10-13", [
        "**多账号**：可添加多个账号随时切换，**切换时不会退出登录**"
        "——每个账号的登录态各自保留，切回来直接可用，不需要再收验证码",
        "每个账号有**独立的数据**：白名单、统计缓存、角色缓存、筛选条件与抓取配置互不干扰",
        "列表上方新增账号下拉框，选中即切换，并自动重新加载该账号的会话列表",
        "「账号」页升级为账号管理：添加账号 / 切换 / 删除账号，可单独删除某个账号的登录态",
        "旧版单账号数据自动升级为第一个账号，登录态与缓存原样保留，不用重新登录",
    ]),
    ("v1.1.2", "2026-10-13", [
        "「退出 / 删除选中」现在能处理**全部会话类型**：频道 / 群组真的退出，"
        "私聊 / 机器人没有「退出」这个概念，自动改为删除会话"
        "（此前勾到私聊会整批报「不支持的会话类型」，现在不会再出现）",
        "确认窗直接说清这批「退出多少个、删除多少个」，不再让你自己猜",
        "界面显示版本号（状态栏右下角），新增「📋 更新日志」窗口",
        "统计日志更准确：全部命中本地缓存时直接说明「未发任何请求」，"
        "不再出现「成功 0 个」这种看着像失败的说法",
        "仓库补充 CHANGELOG.md",
    ]),
    ("v1.1.1", "2026-10-13", [
        "修复：分组会把全部群组 / 频道都算进去 —— 判定改为「按会话类别各查各的开关」，"
        "对齐 Telegram 官方客户端（此前勾了「非联系人」的分组会收下所有群组和频道）",
        "新增：列表上方的类型统计条（共 N 个 │ 频道 N │ 群组 N │ 私聊 N │ 机器人 N │ 已不在 N），"
        "筛选生效时显示「显示 N / 共 M」",
        "新增：加载分组时逐个打印它的自动规则，一眼看清「这个分组为什么收了这么多」",
        "修复：Telegram 的「共享分组」不再被误改（只读展示，加入 / 移出会被拦下并说明原因）",
    ]),
    ("v1.1.0", "2026-10-13", [
        "列表扩展为「全部会话」：频道 / 超级群 / 普通群 / 私聊 / 机器人 / 已不在，"
        "新增「类型」列与类型筛选",
        "新增「活跃(天前)」列：最后一条消息距今多少天，并按活跃天数筛选（预设到 365 天）",
        "新增「分组」列与分组筛选，可批量把会话加入 / 移出指定分组（只改归属，不退出也不删除）",
        "新增「删除会话」：清掉会话及其聊天记录（本地隐藏，并不会退出）",
        "新增「删除我创建的频道 / 群组」：真正销毁，仅创建者可用、不可恢复，需手打「删除」确认",
        "新增「作品数」（消息总数），与用户数共用一次请求",
    ]),
    ("v1.0.0", "2026-10-13", [
        "首个公开版本：批量退出频道 / 群组",
        "图形界面，分页加载，几百上千个订阅也不卡；点表头排序",
        "并发获取「用户数」+ 本地缓存（默认 24 小时）",
        "白名单保护、退出间隔与随机抖动、遇 FloodWait 自动退避重试",
        "导出 CSV（当前显示 / 全部 / 仅已勾选）",
        "深色 / 浅色主题、代理设置、--selftest 打包自检",
    ]),
]


def changelog_text():
    """把 CHANGELOG 渲染成纯文本（更新日志窗口、自检、命令行 --changelog 共用）。"""
    out = []
    for ver, date, items in CHANGELOG:
        out.append(f"{ver}    {date}")
        for it in items:
            out.append(f"   · {it}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def console_write(text):
    """写标准输出。打包成「窗口版」exe 后 sys.stdout 是 None，直接 print 会抛
    AttributeError，所以这里统一兜底（命令行参数在 GUI 下静默无效即可）。"""
    out = getattr(sys, "stdout", None)
    if out is None:
        return
    try:
        out.write(text)
        out.flush()
    except Exception:
        pass


def var_bool(obj, attr, default=False):
    """安全读取某个 BooleanVar：属性不存在或不是变量时返回默认值。
    放在模块级是为了测试桩（只有属性、没有方法）也能用。"""
    var = getattr(obj, attr, None)
    try:
        return bool(var.get())
    except Exception:
        return default


def whitelist_of(obj):
    """取白名单集合（元素是字符串形式的频道 id），必要时懒初始化。
    同样放在模块级，供测试桩复用。"""
    s = getattr(obj, "_whitelist", None)
    if not isinstance(s, set):
        s = set(s or ())
        try:
            obj._whitelist = s
        except Exception:
            pass
    return s


# ---------------- 退出频道/群组的底层实现 ----------------
# 注意：Telethon 的 TelegramClient **没有** leave_channel() 方法。
# 「client.leave_channel(entity)」是旧版文档/示例里的写法，在 1.45 上会直接抛
# AttributeError。正确做法是发 TL 请求，按实体类型分派：
#   频道 / 超级群 (Channel) → channels.LeaveChannelRequest
#   普通群       (Chat)     → messages.DeleteChatUserRequest(chat_id, InputUserSelf())
# 刻意不用 client.delete_dialog()：它对非 bot 会再补一次 DeleteHistoryRequest，
# 批量退出时请求数翻倍、更容易撞 FloodWait。只在实体解析失败时兜底用它。
LEAVE_OK = "ok"            # 成功退出
LEAVE_GONE = "gone"        # 本来就不在里面（等价于已完成）
LEAVE_BLOCKED = "blocked"  # 创建者/管理员，Telegram 不允许退出


def build_leave_request(ent):
    """按实体类型构造「退出」请求。普通群必须用 InputUserSelf()，
    传字符串 "me" 会 TypeError（TL 的 user_id 需要 InputUser 类型）。

    对未知类型**显式报错**而不是默默兜底成 LeaveChannelRequest ——
    否则传入 User 之类会构造出一个发出去必然报错的请求，真正的错因被掩盖。"""
    if isinstance(ent, tl_types.Chat):
        return tl_messages.DeleteChatUserRequest(
            ent.id, tl_types.InputUserSelf(), revoke_history=True)
    if isinstance(ent, tl_types.Channel):
        return tl_channels.LeaveChannelRequest(ent)
    raise ValueError(
        "不支持的会话类型，无法退出：%s（只支持频道 / 超级群 / 普通群）"
        % type(ent).__name__)


async def leave_entity(client, ent):
    """退出一个频道/群组，返回 (状态, 说明)，状态取值见 LEAVE_* 常量。

    把 Telegram 的几种「其实不用退出」/「压根退不出去」的情况翻译成人话，
    避免用户看到一堆原始异常。"""
    try:
        await client(build_leave_request(ent))
        return LEAVE_OK, ""
    except telethon_errors.ChatAdminRequiredError:
        return LEAVE_BLOCKED, "你是该频道/群组的创建者或管理员，Telegram 不允许退出"
    except (telethon_errors.ChannelPrivateError,
            telethon_errors.UserNotParticipantError):
        return LEAVE_GONE, "已经不在该频道/群组中"
    except telethon_errors.PeerIdInvalidError:
        # 实体缓存过期：退化到高层接口，它会先解析实体再决定退频道还是退群
        await client.delete_dialog(ent)
        return LEAVE_OK, ""


# ---------------- 会话类型（一处判定，界面列 / 筛选 / 请求分派共用） ----------------
# 为什么要单独抽出来：同一个「类型」至少要喂给 5 个地方 ——
#   类型列显示、类型筛选、统计请求分派（GetFullChannel 只对 Channel 有效）、
#   角色检测（对 User 调 get_permissions 必失败）、退出/删除能力判断。
# 分散判定必然漂移，例如把超级群误当频道去请求，或对 450 个私聊各做 3 次无效重试。
KIND_CHANNEL = "频道"      # Channel, broadcast=True
KIND_GROUP = "群组"        # Channel(broadcast=False，即超级群) 或 Chat(普通群)
KIND_USER = "私聊"         # User, bot=False
KIND_BOT = "机器人"        # User, bot=True
KIND_GONE = "已不在"       # ChatForbidden / ChannelForbidden：已被移出或频道已失效
KIND_OTHER = "其它"
# 下拉框取值顺序
TYPE_CHOICES = ("全部", KIND_CHANNEL, KIND_GROUP, KIND_USER, KIND_BOT, KIND_GONE)
# 类型 -> 图标。顺序与 TYPE_CHOICES 一致，方便和「类型」下拉对照。
KIND_ICON = ((KIND_CHANNEL, "📢"), (KIND_GROUP, "👥"), (KIND_USER, "💬"),
             (KIND_BOT, "🤖"), (KIND_GONE, "🚫"), (KIND_OTHER, "❔"))
# 私聊 / 机器人 / 已不在：没有「成员数」概念，也没有创建者/管理员，更不能「退出」
NOT_APPLICABLE = "-"

# 分组（Telegram 里叫 dialog filter，UI 上是会话列表顶部的分页标签）
FOLDER_ALL = "全部分组"
FOLDER_NONE = "未分组"
# ⚠️ 与「归档」区分：归档是 peer folder 的 folder_id=1，不在 dialog filter 里。
# 一个会话可以同时属于多个自定义分组，因此「分组」列可能显示多个名字。

# ---------------- 活跃（最后一条消息距今多少天） ----------------
# 「活跃」列里放**纯数字**（表头写明单位是「天前」），这样点表头能按数值正确排序；
# 无消息 / 不适用 / 未知各给固定文案，_sort_key 会把「无消息」当最大天数处理。
ACTIVE_NONE = "无消息"       # 取过了，这个会话一条消息都没有
ACTIVE_UNKNOWN = "…"        # 还没取过（统计未跑）
ACTIVE_NEVER = 10 ** 9       # 筛选用：把「无消息」当成「久到无法更久」

# 缓存里 last_ts 的三种取值：数字=时间戳；None=确认无消息；"-"=不适用（已不在）
# 注意缓存必须存**时间戳**而不是天数 —— 存天数的话隔天就永久偏小了。


def active_text(last_ts, now_ts=None):
    """把缓存的「最后消息时间戳」渲染成「活跃」列文本。"""
    if last_ts == NOT_APPLICABLE:
        return NOT_APPLICABLE          # 已不在的会话，消息根本取不到
    if last_ts is None:
        return ACTIVE_NONE
    try:
        ts = float(last_ts)
    except (TypeError, ValueError):
        return ACTIVE_UNKNOWN          # 还没取过
    if ts <= 0:
        return ACTIVE_NONE
    now = time.time() if now_ts is None else float(now_ts)
    # 服务器时间可能略超前于本地时钟，算出负数一律归零
    return str(max(0, int((now - ts) // 86400)))


UNSET = object()      # 哨兵：区分「本次不更新该列」与「明确置空」


# ---------------- 分组（Telegram 里叫 dialog filter） ----------------
# 三个必须分清的事实（都是查官方文档/实测确认的，别想当然）：
# 1) ⚠️ folders.editPeerFolders **只能**设 folder_id 0（主列表）/ 1（归档）。
#    官方文档原文："no other folder_id is allowed at the moment" ——
#    它**不能**把会话放进自定义分组！自定义分组必须走
#    messages.getDialogFilters 读 + messages.updateDialogFilter 整表写回。
# 2) updateDialogFilter 是**整表替换**：提交的 include_peers 就是该分组之后的完整手写列表，
#    所以必须先读最新状态再改；并且要**在服务器返回的对象上原地改字段**（而不是自己 new 一个），
#    否则 Telethon 不认识的字段（如 color / title_noanimate）会被悄悄丢掉。
# 3) 分组还能靠**自动规则**匹配（groups / broadcasts / bots / contacts /
#    exclude_read / exclude_muted / exclude_archived）。只按 include_peers 反查，
#    会把靠规则进来的会话漏掉 —— 必须在本地复现规则，才能和客户端看到的一致。
FOLDER_RULE_FLAGS = ("contacts", "non_contacts", "groups", "broadcasts", "bots",
                     "exclude_muted", "exclude_read", "exclude_archived")
FOLDER_TYPE_FLAGS = ("contacts", "non_contacts", "groups", "broadcasts", "bots")
# Telegram 对「一个分组手工容纳多少会话」有限制，超过就提示（不阻止，由服务端最终裁决）
FOLDER_SOFT_LIMIT = 100
# exclude_peers 同样有上限；移出「靠规则进来的」会话会消耗它
FOLDER_EXCLUDE_LIMIT = 100

# 批量划分分组时「对哪些行生效」的三档范围。
# 「当前筛选 / 搜索结果」是这里最要紧的一档：搜索完直接划分，不必先挨个勾选。
FOLDER_SCOPE_CHECKED = "checked"    # 已勾选的行（含被筛选隐藏的勾选）
FOLDER_SCOPE_VISIBLE = "visible"    # 当前筛选 / 搜索结果（列表里显示出来的行）
FOLDER_SCOPE_ALL = "all"            # 全部会话（含被筛选隐藏的）
FOLDER_SCOPES = (
    (FOLDER_SCOPE_CHECKED, "已勾选的行"),
    (FOLDER_SCOPE_VISIBLE, "当前筛选 / 搜索结果"),
    (FOLDER_SCOPE_ALL, "全部会话"),
)
FOLDER_SCOPE_LABEL = dict(FOLDER_SCOPES)


def peer_key_of(ent):
    """会话的分组归属键。**必须带类型**：channel_id / chat_id / user_id 的数值空间会重叠，
    只按数字匹配会把不同会话混成一个。"""
    try:
        eid = int(getattr(ent, "id", None))
    except (TypeError, ValueError):
        return None
    if isinstance(ent, tl_types.Channel):
        return ("channel", eid)
    if isinstance(ent, tl_types.Chat):
        return ("chat", eid)
    if isinstance(ent, tl_types.User):
        return ("user", eid)
    return None


def input_peer_key(p):
    """inputPeerChannel / inputPeerChat / inputPeerUser -> 与 peer_key_of 一致的键。"""
    for attr, tag in (("channel_id", "channel"), ("chat_id", "chat"), ("user_id", "user")):
        v = getattr(p, attr, None)
        if v:
            try:
                return (tag, int(v))
            except (TypeError, ValueError):
                return None
    return None


def safe_peer_id(ent):
    """get_peer_id 的「绝不抛异常」版。

    复核阶段拿不到 id 只意味着**这一个**会话无法判定，不该把整批复核带崩
    （实体是 min、或 access_hash 缺失时 get_peer_id 会抛）。"""
    try:
        return get_peer_id(ent)
    except Exception:
        return None


def folder_title(f):
    t = getattr(f, "title", None)
    if isinstance(t, tl_types.TextWithEntities):
        t = t.text
    return str(t or "").strip()


def is_real_folder(f):
    """是否是用户自建的分组。DialogFilterDefault 是「全部聊天」默认视图（id=None），不能改。"""
    return getattr(f, "id", None) is not None and type(f).__name__ != "DialogFilterDefault"


def is_editable_folder(f):
    """这个分组能不能用 updateDialogFilter 改成员名单。

    DialogFilterChatlist（Telegram 的「共享分组 / 文件夹」）没有类型开关、也没有
    exclude_peers，成员是服务端维护的，**只能用 chatlists.* 那套接口管**。
    拿它的 id 去 updateDialogFilter，等于用一个新的 DialogFilter 去覆盖它，
    会把这个共享分组改坏 —— 所以「加入 / 移出分组」要把它排除掉。
    读取（分组列、分组筛选）不受影响。
    """
    return is_real_folder(f) and not isinstance(f, tl_types.DialogFilterChatlist)


def manual_peer_keys(f):
    """分组里「手工加入」的会话键集合（pinned 也算 —— pinned 只是置顶显示）。"""
    keys = set()
    for attr in ("pinned_peers", "include_peers"):
        for p in (getattr(f, attr, None) or []):
            keys.add(input_peer_key(p))
    keys.discard(None)
    return keys


def folder_rules_on(f):
    return [n for n in FOLDER_RULE_FLAGS if getattr(f, n, None)]


FOLDER_FLAG_LABEL = {
    "contacts": "联系人", "non_contacts": "非联系人",
    "groups": "群组", "broadcasts": "频道", "bots": "机器人",
    "exclude_muted": "排除已静音", "exclude_read": "仅保留未读",
    "exclude_archived": "排除已归档",
}


def folder_rules_text(f):
    """把分组的开关翻译成人话。日志里逐个打印，直接回答
    「这个分组到底是什么规则、为什么它收了这么多会话」。"""
    on = [FOLDER_FLAG_LABEL[n] for n in FOLDER_RULE_FLAGS if getattr(f, n, None)]
    return " + ".join(on) if on else "无自动规则（纯手工）"


def folder_flag_of(state):
    """该会话在分组规则里对应的**那一个**开关名。

    ⚠️ 这里必须「按会话类别各查各的开关」。Telegram 官方客户端
    （Desktop 的 ChatFilter::contains / Android 的 DialogFilter）就是这么判的：

        用户(非 bot)：联系人 -> contacts，非联系人 -> non_contacts
        bot：      -> bots
        普通群 / 超级群 -> groups
        广播频道   -> broadcasts

    不能写成「命中任意一个类型开关就算属于」。曾经的写法是
        `non_contacts and not state["contact"]`
    而 Channel / Chat 实体**根本没有 contact 属性** -> 恒为 False ->
    **所有群组、所有频道都会被算成「非联系人」而误入该分组**，
    表现出来就是「这个分组显示出了所有」，但 Telegram 客户端里并没有。
    """
    kind = state.get("kind")
    if kind == KIND_CHANNEL:
        return "broadcasts"
    if kind == KIND_GROUP:
        return "groups"
    if kind == KIND_BOT:
        return "bots"
    if kind == KIND_USER:
        return "contacts" if state.get("contact") else "non_contacts"
    if kind == KIND_GONE:
        # 已不在：ChannelForbidden 带 broadcast 标记，其余按群组算
        return "broadcasts" if state.get("is_broadcast") else "groups"
    return None


def folder_keeps_by_rule(f, state):
    """**光靠自动规则**，这个分组会不会把该会话算进来。

    只看「该会话类别对应的那一个类型开关」+ 三个 exclude_* 开关，
    **完全不看 include_peers / pinned_peers / exclude_peers** —— 这一点是关键，
    因为它同时要回答两个不同的问题：

    1) `filter_matches` 的规则分支：规则本身是否命中；
    2) **移出分组时**：光把会话从 include_peers 里删掉够不够？
       如果这里返回 True，说明规则仍会把它捞回来 —— 必须额外写进 exclude_peers
       才能真正移出去（否则日志报「已移出 N 个」，用户在 Telegram 里一看还在）。

    所以这个函数是「分组归属」这件事的单一真源，不要在两处各写一份开关判断。
    """
    flag = folder_flag_of(state)
    if flag is None or not getattr(f, flag, None):
        return False
    if getattr(f, "exclude_muted", None) and state.get("muted"):
        return False
    if getattr(f, "exclude_read", None) and not (state.get("unread") or 0):
        return False
    if getattr(f, "exclude_archived", None) and state.get("archived"):
        return False
    return True


def filter_matches(f, key, state):
    """本地复现「这个会话属不属于该分组」。规则语义（对齐 Telegram 官方客户端）：
    - exclude_peers 命中 -> 不属于（**最高优先级**，比手工加入还高）
    - 手工加入（include/pinned）-> 属于，不再看规则
    - 否则看规则：该会话类别对应的那个开关必须打开
      （一个类型开关都没勾的分组因此等于「纯手工」，不会自动收人）
    """
    try:
        for p in (getattr(f, "exclude_peers", None) or []):
            if input_peer_key(p) == key:
                return False
        if key in manual_peer_keys(f):
            return True
        return folder_keeps_by_rule(f, state)
    except Exception:
        return False


def dialog_state(d, kind, ent):
    """取本地复现分组规则所需的状态（Telethon 的 Dialog 没有 muted 属性，要钻到 notify_settings）。"""
    st = {"kind": kind, "contact": bool(getattr(ent, "contact", False)),
          "is_broadcast": bool(getattr(ent, "broadcast", False)),
          "muted": False, "unread": 0, "archived": False}
    try:
        st["unread"] = int(getattr(d, "unread_count", 0) or 0)
    except Exception:
        st["unread"] = 0
    try:
        st["archived"] = bool(getattr(d, "folder_id", None))
    except Exception:
        st["archived"] = False
    try:
        ns = getattr(getattr(d, "dialog", None), "notify_settings", None)
        mu = getattr(ns, "mute_until", None)
        if mu is not None:
            try:
                st["muted"] = mu.timestamp() > time.time()
            except Exception:
                st["muted"] = bool(mu)
    except Exception:
        st["muted"] = False
    return st


def session_kind(ent):
    """判定会话实体的类型（所有地方都必须走这一个函数）。"""
    try:
        if isinstance(ent, tl_types.Channel):
            return KIND_CHANNEL if getattr(ent, "broadcast", False) else KIND_GROUP
        if isinstance(ent, tl_types.Chat):
            return KIND_GROUP
        if isinstance(ent, tl_types.User):
            return KIND_BOT if getattr(ent, "bot", False) else KIND_USER
        if isinstance(ent, (tl_types.ChatForbidden, tl_types.ChannelForbidden)):
            return KIND_GONE
    except Exception:
        pass
    return KIND_OTHER


def is_member_kind(kind):
    """是否「有成员、可退出」的类型 —— 只有频道/群组有成员数、
   有创建者/管理员概念、需要也才能够做角色检测。"""
    return kind in (KIND_CHANNEL, KIND_GROUP)


# ---------------- 异常 / 违规 频道·群组的特征提取 ----------------
# 设计原则：**只认服务端给的字段，零请求，不猜名字**。
# Telegram 自己在实体上带标记（scam / fake / restricted / restriction_reason /
# ChannelForbidden …），会话列表里就已经把这些字段给全了，再发请求去查纯属浪费，
# 而且按名字里的关键词猜（"福利""资源"…）会把正常频道大批误判成违规。
RISK_FORBIDDEN = "forbidden"    # 已封禁 / 不可访问（ChannelForbidden / ChatForbidden）
RISK_RESTRICTED = "restricted"  # 违规受限（服务端给了原因：版权 / 服务条款 / 色情 …）
RISK_SCAM = "scam"              # Telegram 标记：诈骗
RISK_FAKE = "fake"              # Telegram 标记：假冒
RISK_DEAD = "dead"              # 群已停用 / 你已不在（条目残留）
RISK_BANNED = "banned"          # 你被禁言 / 全员默认禁言
RISK_NOJOIN = "nojoin"          # 不能直接关注（加入需管理员批准）
RISK_NEEDJOIN = "needjoin"      # 必须先加入才能发言
RISK_GHOST = "ghost"            # 对方账号已注销（私聊）

# 类别码 -> 短标签（表格「异常」列用）与人话说明（日志 / 导出 / 提示用）
RISK_META = (
    (RISK_FORBIDDEN, "🚫 已封禁", "已封禁 / 不可访问"),
    (RISK_RESTRICTED, "🚫 违规受限", "违规受限（版权 / 服务条款 / 色情等）"),
    (RISK_SCAM, "🚫 诈骗", "Telegram 标记：诈骗"),
    (RISK_FAKE, "🚫 假冒", "Telegram 标记：假冒"),
    (RISK_DEAD, "🚫 已停用", "已停用 / 已不在（被踢或已退出，条目残留）"),
    (RISK_BANNED, "🚫 禁言", "被禁言 / 全员禁言"),
    (RISK_NOJOIN, "🚫 需审核", "不能直接关注（加入需管理员批准）"),
    (RISK_NEEDJOIN, "🚫 须先加入", "必须先加入才能发言"),
    (RISK_GHOST, "🚫 已注销", "对方账号已注销"),
)
RISK_CODES = tuple(c for c, _s, _l in RISK_META)
RISK_SHORT = {c: s for c, s, _l in RISK_META}
RISK_LABEL = {c: l for c, _s, l in RISK_META}
# 严重度顺序（一个会话可能同时命中多个特征，表格列只显示最重的那一个 + 「＋N」）
RISK_ORDER = (RISK_FORBIDDEN, RISK_RESTRICTED, RISK_SCAM, RISK_FAKE, RISK_DEAD,
              RISK_BANNED, RISK_NOJOIN, RISK_NEEDJOIN, RISK_GHOST)

# 菜单里的一键筛选项（把 9 类收成 8 项，避免菜单铺满一屏）
RISK_GROUPS = (
    ("违规 / 被封（全部）", (RISK_FORBIDDEN, RISK_RESTRICTED, RISK_SCAM, RISK_FAKE)),
    ("已封禁 / 不可访问", (RISK_FORBIDDEN,)),
    ("违规受限（版权 / 服务条款）", (RISK_RESTRICTED,)),
    ("诈骗 / 假冒标记", (RISK_SCAM, RISK_FAKE)),
    ("已停用 / 已不在", (RISK_DEAD,)),
    ("被禁言 / 全员禁言", (RISK_BANNED,)),
    ("不能关注 / 须先加入", (RISK_NOJOIN, RISK_NEEDJOIN)),
    ("对方账号已注销", (RISK_GHOST,)),
    ("任意异常", RISK_CODES),
)

# restriction_reason.reason 是**稳定代码**；text 是可读文案（随客户端语言/版本变）。
# 只认英文原文（不少开源脚本就是这么写的）会在换语言或 Telegram 改文案时静默失效，
# 所以这里代码优先、文案只做兜底。
RESTRICTION_REASON_LABEL = {
    "copyright": "版权投诉", "terms": "违反服务条款", "porn": "色情内容",
    "spam": "垃圾信息", "violence": "暴力内容", "drugs": "违禁品",
    "fake": "虚假信息", "geo": "地区限制", "impersonation": "冒充他人",
    "scam": "诈骗", "illegal": "违法内容",
}
RESTRICTION_TEXT_HINTS = ("copyright", "terms of service", "violated", "unavailable",
                          "infringement", "restricted")


def restriction_reasons(ent):
    """restriction_reason → [(短标签, 服务端原文), ...]。代码优先、文案兜底。"""
    out = []
    for r in (getattr(ent, "restriction_reason", None) or []):
        reason = str(getattr(r, "reason", "") or "").strip()
        text = str(getattr(r, "text", "") or "").strip()
        label = RESTRICTION_REASON_LABEL.get(reason.lower())
        if not label:
            low = text.lower()
            if any(h in low for h in RESTRICTION_TEXT_HINTS):
                label = "服务端标注受限"
            else:
                label = reason or "受限"
        out.append((label, text))
    return out


def restriction_text(ent):
    """一句话说完所有受限原因（表格列 / 导出用）。"""
    parts = []
    for label, text in restriction_reasons(ent):
        parts.append(label if (not text or text == label) else f"{label}：{text}")
    return "；".join(parts)


def _send_banned(rights):
    """这份 ChatBannedRights 是否禁止发言 —— 禁言是「受限群」最直观的特征。"""
    return bool(rights) and bool(getattr(rights, "send_messages", False))


def risk_of(ent):
    """提取一个会话的异常特征，返回 [(类别码, 人话原因), ...]；空列表 = 没发现异常。

    **全部来自服务端已经给我们的字段，零请求**：
      ChannelForbidden / ChatForbidden          -> 已封禁 / 不可访问
      Channel.scam / Channel.fake               -> Telegram 官方标记
      Channel.restricted + restriction_reason   -> 违规受限（版权 / 服务条款 / 色情 …）
      Chat.deactivated                          -> 群已停用（通常是封禁）
      Channel.left / Chat.left                  -> 已不在（会话条目残留）
      Channel.join_request                      -> 加入需管理员批准（不能直接关注）
      Channel.join_to_send                      -> 必须先加入才能发言
      banned_rights / default_banned_rights     -> 你被禁言 / 全员禁言
      User.deleted                              -> 对方账号已注销
    """
    out = []
    if ent is None:
        return out
    if isinstance(ent, tl_types.ChannelForbidden):
        return [(RISK_FORBIDDEN, "服务端返回 ChannelForbidden：频道已不可访问"
                                 "（被封 / 被踢 / 频道已删）")]
    if isinstance(ent, tl_types.ChatForbidden):
        return [(RISK_FORBIDDEN, "服务端返回 ChatForbidden：群已不可访问"
                                 "（被封 / 被踢 / 群已删）")]
    if isinstance(ent, tl_types.Channel):
        if getattr(ent, "scam", False):
            out.append((RISK_SCAM, "Telegram 标记该频道为诈骗"))
        if getattr(ent, "fake", False):
            out.append((RISK_FAKE, "Telegram 标记该频道为假冒"))
        detail = restriction_text(ent)
        if detail:
            out.append((RISK_RESTRICTED, "服务端标注受限 —— " + detail))
        elif getattr(ent, "restricted", False):
            out.append((RISK_RESTRICTED, "被服务端标记为受限（未给出具体原因）"))
        if getattr(ent, "left", False):
            out.append((RISK_DEAD, "你已经不在这个频道 / 群组里（会话条目残留）"))
        if getattr(ent, "join_request", False):
            out.append((RISK_NOJOIN, "加入需要管理员批准，不能直接关注"))
        elif getattr(ent, "join_to_send", False):
            out.append((RISK_NEEDJOIN, "必须先加入才能发言"))
        if _send_banned(getattr(ent, "banned_rights", None)):
            out.append((RISK_BANNED, "你在这个频道 / 群里被禁止发言"))
        elif _send_banned(getattr(ent, "default_banned_rights", None)):
            out.append((RISK_BANNED, "全员默认被禁止发言"))
    elif isinstance(ent, tl_types.Chat):
        if getattr(ent, "deactivated", False):
            out.append((RISK_DEAD, "该群已被停用（通常是封禁）"))
        if getattr(ent, "left", False):
            out.append((RISK_DEAD, "你已经不在这个群里"))
        if _send_banned(getattr(ent, "default_banned_rights", None)):
            out.append((RISK_BANNED, "全员默认被禁止发言"))
    elif isinstance(ent, tl_types.User):
        if getattr(ent, "deleted", False):
            out.append((RISK_GHOST, "对方账号已注销（客户端里是灰头像 / 纯数字名）"))
        if getattr(ent, "scam", False):
            out.append((RISK_SCAM, "Telegram 标记该用户为诈骗"))
        if getattr(ent, "fake", False):
            out.append((RISK_FAKE, "Telegram 标记该用户为假冒"))
        if getattr(ent, "restricted", False):
            out.append((RISK_RESTRICTED, "该账号被服务端标记为受限"))
    return out


def risk_codes_of(ent):
    """只要类别码（去重、按严重度排序）—— 筛选与计数都走它。"""
    codes = set(c for c, _why in risk_of(ent))
    return tuple(c for c in RISK_ORDER if c in codes)


def risk_cell_text(ent):
    """表格「异常」列的文字：最重的一类 + 「＋N」。"""
    codes = risk_codes_of(ent)
    if not codes:
        return ""
    txt = RISK_SHORT.get(codes[0], codes[0])
    return txt if len(codes) == 1 else f"{txt} +{len(codes) - 1}"


def risk_detail_text(ent):
    """把该会话的全部异常原因连成一串（日志 / 导出 / 提示用）。"""
    return "；".join(why for _c, why in risk_of(ent))


# ---------------- 删除会话 / 销毁自己创建的群组·频道 ----------------
# ⚠️ 三个概念必须分清楚，混用会做出「以为删了、其实只是退出了」或者相反的功能：
#
#   退出       channels.LeaveChannelRequest / messages.DeleteChatUserRequest(revoke_history=True)
#              -> 不再是成员。对频道/群组有效；创建者/管理员**退不出去**。
#
#   删除会话   私聊 / 机器人 → messages.DeleteHistoryRequest(just_clear=False, revoke=False)
#                          -> 从**我这边**删掉会话与聊天记录，对方完全不知情。
#             频道 / 群组   → **只能靠退出**（同上面的「退出」）。
#
#              ⚠️ 这里曾经写错、也把功能带错了：以为 deleteHistory 对所有类型都有效，
#                 于是对频道/群组也发它。真相是——Telegram 里**群组/频道的会话
#                 只有退出才会消失**，deleteHistory 对它们发出去服务端照收不报错、
#                 会话却纹丝不动（v1.3.1 之前「显示删除成功、客户端会话还在」就是这个）。
#                 佐证：Telethon 自己的 client.delete_dialog() 对频道发
#                 channels.LeaveChannelRequest、对普通群发 messages.DeleteChatUserRequest，
#                 **只有私聊才用 DeleteHistoryRequest**。
#                 语义上 just_clear=False 才是「连会话一起删」，just_clear=True 只清记录、
#                 会话保留（= 「清空聊天记录」那个功能）。
#
#   销毁       channels.DeleteChannelRequest（频道 / 超级群，对所有成员生效、不可恢复）
#              messages.DeleteChatRequest（普通群，同上）
#              -> 只有**创建者**能调用，调用后频道/群对所有人消失，且无法撤销。
#
# 注意 Telethon 1.45 里发的是 channels.DeleteChannelRequest（单个），
# **没有** DeleteChannelsRequest —— 后者是旧版别的层里的名字，import 会直接 ImportError。
DEL_OK = "ok"              # 成功
DEL_GONE = "gone"          # 已经不存在 / 不可访问（等价于已完成）
DEL_BLOCKED = "blocked"    # 没权限（例如非创建者去销毁）

# 销毁属于「不可恢复」操作，除确认清单外还要求在输入框里手打这个词
DESTROY_CONFIRM_WORD = "删除"


def build_delete_history_request(ent):
    """构造「删除私聊会话（含聊天记录）」请求 —— **只对私聊 / 机器人有意义**。

    revoke=False 是不可省的：它明确表示「不动对方那份记录」，
    这正是「删除会话」的默认语义，也是这几条破坏性操作里最温和的一条。
    max_id=0 表示删除该会话的全部消息（不只是当前已加载的那批）。
    just_clear 必须为 False：True 是「只清空聊天记录、会话留在列表里」，
    那是另一个功能，不是「删除会话」。"""
    return tl_messages.DeleteHistoryRequest(
        peer=ent, max_id=0, just_clear=False, revoke=False)


def build_remove_dialog_requests(ent, revoke=False):
    """构造「让这个会话从我这边彻底消失」所需的请求列表（一条或两条）。

    这是「删除会话」按类型分派的**单一真源**。分派依据不是偏好，是 Telegram 的硬约束：

      私聊 / 机器人 (User)   → messages.DeleteHistoryRequest(just_clear=False)
                              —— 唯一能删掉会话的方法；just_clear=True 只清记录。
      普通群        (Chat)   → messages.DeleteChatUserRequest(self, revoke_history=False)
                              —— 普通群没有「删会话」这回事，把自己踢出去才会消失。
     频道 / 超级群 (Channel) → channels.LeaveChannelRequest
                              —— 同上，只有退出才会消失。

    以前这里对所有类型一律发 deleteHistory：服务端不报错、会话却不动，
    日志却已经写下「已删除」—— 那就是「显示成功、客户端还在」的真因。
    未知类型显式报错，不替调用方猜意图。"""
    if isinstance(ent, tl_types.User):
        return [build_delete_history_request(ent)]
    if isinstance(ent, tl_types.Chat):
        # revoke_history=False：只删我这边那份记录，不动其他成员的聊天记录。
        return [tl_messages.DeleteChatUserRequest(
            ent.id, tl_types.InputUserSelf(), revoke_history=bool(revoke))]
    if isinstance(ent, tl_types.Channel):
        return [tl_channels.LeaveChannelRequest(ent)]
    raise ValueError(
        "不支持的会话类型，无法删除：%s（私聊 / 机器人 / 群组 / 频道才可删）"
        % type(ent).__name__)


def build_destroy_request(ent):
    """构造「销毁自己创建的群组 / 频道」请求。普通群与超级群走的是不同接口，
    按实体类型分派；未知类型**显式报错**，避免构造出必然失败的请求把错因盖住。"""
    if isinstance(ent, tl_types.Chat):
        return tl_messages.DeleteChatRequest(ent.id)
    if isinstance(ent, tl_types.Channel):
        return tl_channels.DeleteChannelRequest(ent)
    raise ValueError(
        "不支持的会话类型，无法删除：%s（只有你创建的频道 / 群组可以删除）"
        % type(ent).__name__)


def can_destroy_kind(kind):
    """只有频道 / 群组才谈得上「被创建者销毁」。私聊、机器人、已不在都不行。"""
    return kind in (KIND_CHANNEL, KIND_GROUP)


async def delete_history_entity(client, ent):
    """删除会话（= 让这个会话从我这边彻底消失），返回 (状态, 说明)。

    按类型分派见 build_remove_dialog_requests —— 频道 / 群组走的是「退出」，
    这是 Telegram 的硬约束，不是可选项。

    ⚠️ 本函数**只负责把请求发出去**。是否真的生效必须由调用方用
    dialogs_still_present() 回头复核 —— 不能拿「没抛异常」当成功：
    Telegram 会安静地收下请求却什么都不做，v1.3.0 之前就是拿它当成功，
    才出现「显示删除成功、客户端会话还在」。"""
    try:
        for req in build_remove_dialog_requests(ent):
            await client(req)
        return DEL_OK, ""
    except (telethon_errors.ChannelPrivateError,
            telethon_errors.UserNotParticipantError):
        return DEL_GONE, "会话已不存在或已不可访问"
    except telethon_errors.PeerIdInvalidError:
        # 实体缓存过期：退化到高层接口，它会先解析实体再处理
        await client.delete_dialog(ent)
        return DEL_OK, "（实体缓存过期，已走兼容路径）"
    except telethon_errors.ChatAdminRequiredError:
        return DEL_BLOCKED, "没有权限删除该会话的聊天记录"
    except telethon_errors.RPCError as e:
        # 创建者退不出去是 Telegram 的设计，不是故障：直接说清该怎么走，
        # 别让用户对着一句裸 RPC 报错猜。
        if is_member_kind(session_kind(ent)) and getattr(ent, "creator", False):
            return DEL_BLOCKED, (
                "你是它的创建者 —— Telegram 不允许创建者直接退出自己的频道 / 群组"
                "（会让它失去所有者）。要彻底去掉请用「🗑 删除我创建的群组 / 频道」，"
                "或先在 Telegram 客户端里把所有权转给别人"
                f"（服务端原话：{e}）")
        raise


async def dialogs_still_present(client, ents):
    """只读复核：这些会话在「我这边」**是否仍然存在**。

    用 messages.getPeerDialogs 精确问这几个 peer（比重拉整个会话列表便宜得多，
    也不改变任何状态）。删除之后必须靠它判定成败。

    返回 (仍在的 {peer_id: Dialog}, 无法复核的原因)：
      - 能复核   -> ({...}, "")
      - 复核失败 -> (None, 原因)，调用方**不得**当作成功，也不得当作失败。

    ⚠️ 响应里没有 dialogs 字段时必须报「无法复核」，**绝不能**当成「都不在了」——
    那等于换个姿势继续「没核对就报成功」，正是这个功能要根治的病。"""
    peers = [e for e in (ents or []) if e is not None]
    if not peers:
        return {}, ""
    try:
        res = await client(tl_messages.GetPeerDialogsRequest(peers=peers))
    except Exception as e:
        return None, str(e)
    dialogs = getattr(res, "dialogs", None)
    if dialogs is None:
        return None, "服务端响应里没有 dialogs 字段，无法判定"
    still = {}
    for d in dialogs:
        try:
            still[get_peer_id(getattr(d, "peer", None))] = d
        except Exception:
            continue
    return still, ""


def is_ghost_user(ent):
    """对方是不是「幽灵账号」—— 已注销，或服务端只回了 id、一个名字字段都没有。

    这一类在客户端里显示成灰头像 / 纯数字名（本工具的取名单链是
    `d.name or title or str(ent.id)`，名字空就退化成 id，所以列表里那些
    「7877144361」就是它们），也正是服务端唯一会「删掉记录、却把会话条目留着」的一类。
    区分出来是为了只对它们动用更强的写法（revoke / 临时封禁），不误伤正常在用的联系人。
    """
    if not isinstance(ent, tl_types.User):
        return False
    if getattr(ent, "is_self", False):
        return False
    if getattr(ent, "deleted", False):
        return True
    return not (getattr(ent, "first_name", None)
                or getattr(ent, "last_name", None)
                or getattr(ent, "username", None))


async def block_unblock_delete(client, ent, revoke=False):
    """封禁 → 解封 → 再删。**解封一定执行**（放在 try/except 里显式兜底）。

    为什么会有这么怪的一招：对「对方账号已注销」的会话，服务端会收下
    messages.deleteHistory、把聊天记录删掉，却**不摘掉会话条目** —— 复核时它的
    top_message 是 0（记录没了），条目却一直在列表里。社区里对这类「顽固幽灵会话」
    唯一有效的办法就是先把这条会话的封禁状态改一遍，逼服务端重建这个 peer 的会话状态，
    随后的删除才会真的生效。

    安全性：这里只处理用户**已经勾选要删除**的会话；对已注销 / 陌生账号来说封禁没有副作用，
    且封禁**不会通知对方**。解封放在 finally 语义的位置 —— 宁可这一步整体失败，
    也绝不把用户留在「误封禁了别人」的状态里。"""
    await client(tl_contacts.BlockRequest(ent))
    try:
        await client(tl_contacts.UnblockRequest(ent))
    except Exception as e:      # noqa: BLE001
        raise RuntimeError("解封失败，该会话可能仍处于封禁状态"
                           "（请到 Telegram 客户端里手动解封）：%s" % e)
    await client(tl_messages.DeleteHistoryRequest(
        peer=ent, max_id=0, just_clear=False, revoke=bool(revoke)))


# 「删了、但服务端说还在」时的备选写法。按顺序试，每一步都和上一步**真的不同**，
# 每试一步都重新复核一次 —— 不靠猜，只看服务端最后怎么说。
DIALOG_REMOVE_RETRY_STEPS = (
    ("explicit_peer", "改用显式 InputPeer 重发（排除实体解析这一层的问题）"),
    ("max_id", "带上服务端返回的 top_message 当 max_id 重发（排除 max_id=0 被当成「无事可做」）"),
    ("delete_dialog", "改走 Telethon 的高层 delete_dialog"),
    ("revoke", "改带 revoke=True 重发（针对「对方账号已注销」这类只被删记录、不摘条目的会话）"),
    ("block_cycle", "临时封禁 → 解封 → 再删（重置服务端对这条会话的状态）"),
)


async def retry_remove_dialog(client, ent, step, dialog=None):
    """备选写法。返回一句说明（跳过时）或 None。"""
    if step == "explicit_peer":
        try:
            peer = get_input_peer(ent)
        except Exception as e:
            return "跳过（实体信息不全，无法构造 InputPeer：%s）" % e
        await client(tl_messages.DeleteHistoryRequest(
            peer=peer, max_id=0, just_clear=False, revoke=False))
        return None
    if step == "max_id":
        top = int(getattr(dialog, "top_message", 0) or 0)
        if top <= 0:
            return "跳过（服务端没给出 top_message）"
        await client(tl_messages.DeleteHistoryRequest(
            peer=ent, max_id=top, just_clear=False, revoke=False))
        return None
    if step == "delete_dialog":
        await client.delete_dialog(ent)
        return None
    if step == "revoke":
        # revoke=True 是「连对方那份记录一起删」。只有对方账号**已经注销**时才可以这么干：
        # 那边已经不存在了，删无可删；对着正常在用的联系人这么发就是替用户做了
        # 一个他没点头的决定，所以显式跳过并说清原因。
        if getattr(ent, "is_self", False):
            return "跳过（你自己的收藏夹：Telegram 不允许删除，revoke 也没有用）"
        if not getattr(ent, "deleted", False):
            return ("跳过（对方是正常在用的账号：revoke=True 会连对方那份聊天记录一起删掉，"
                    "这一步不替你做这个决定）")
        await client(tl_messages.DeleteHistoryRequest(
            peer=ent, max_id=0, just_clear=False, revoke=True))
        return None
    if step == "block_cycle":
        if getattr(ent, "is_self", False):
            return "跳过（你自己的收藏夹：Telegram 不允许删除）"
        if not is_ghost_user(ent):
            return ("跳过（对方是正常在用的账号：不替你临时封禁一个陌生 / 联系人，"
                    "这一步只对已注销的幽灵账号使用）")
        await block_unblock_delete(client, ent)
        return None
    raise ValueError("未知的重试写法：%s" % step)


def dialog_remove_hint(ent):
    """「服务端确认这个会话仍然存在」时的解释。

    这些是服务端行为，不是本工具请求写错了 —— 必须把**代价**和**出路**说清楚，
    不然用户只会一直点、一直被骗「已删除」。v1.3.2 之前这里写的是「重试也没用」，
    那是错的：已注销账号的会话确实还能用更强的写法再试（见 DIALOG_REMOVE_RETRY_STEPS）。"""
    try:
        if getattr(ent, "is_self", False):
            return ("这是你自己的「收藏夹 / Saved Messages」：Telegram **不允许删除**这个会话"
                    "（服务端接受请求但不执行）。本工具已经不再对它发任何删除请求 —— "
                    "因为请求里「删记录」的那一半会**真的清空你收藏的内容**。"
                    "要清空请到客户端里用「清空聊天记录」，会话条目会一直留在列表里。")
        if getattr(ent, "deleted", False):
            return ("对方的账号**已注销**（客户端里显示为灰头像 / 纯数字名的 Deleted Account）。"
                    "服务端对这类会话只删聊天记录、**不摘会话条目** —— 所以「删了但会话还在」"
                    "不是没删掉，是条目被 Telegram 留着了。本工具会改用 revoke=True / "
                    "临时封禁→解封→再删 这两种更强的写法重试，成不成以复核为准。")
        if isinstance(ent, tl_types.User) and is_ghost_user(ent):
            return ("这个用户实体一个名字字段都没有（服务端只回了 id），"
                    "客户端里通常就是已注销 / 已被封的幽灵账号 —— "
                    "服务端对这类会话往往只删记录、不摘条目，本工具会走更强的写法重试。")
        if getattr(ent, "support", False):
            return "这是 Telegram 官方支持账号（Telegram），它的会话不能被删除。"
        if getattr(ent, "min", False):
            return ("这个用户实体是精简版（min），access_hash 不全，服务端定位不到会话；"
                    "先点「🔄 刷新列表」重取完整实体再试。")
    except Exception:
        pass
    return ""


def dialog_remove_tag(ent):
    """给「未能删除」那一行补一个一眼能看懂的原因标签（没把握就返回空串）。"""
    try:
        if getattr(ent, "is_self", False):
            return "（收藏夹）"
        if getattr(ent, "deleted", False):
            return "（对方账号已注销）"
        if is_ghost_user(ent):
            return "（幽灵账号：服务端没给任何名字）"
    except Exception:
        pass
    return ""


async def dialog_history_cleared(client, ent):
    """只读诊断：这个会话的消息到底少了没有。

    用来区分两种「看起来一样」的失败：
      * 历史仍在 -> 请求**根本没生效**；
      * 历史已空 -> 记录确实删掉了，只是**会话本身**被 Telegram 保留（收藏夹 / 已注销账号）。
    返回 (剩余消息总数 或 None, 说明)。"""
    try:
        res = await client(tl_messages.GetHistoryRequest(
            peer=ent, offset_id=0, offset_date=None, add_offset=0,
            limit=1, max_id=0, min_id=0, hash=0))
        return int(getattr(res, "total", 0) or 0), ""
    except Exception as e:
        return None, str(e)


async def destroy_entity(client, ent):
    """销毁自己创建的群组 / 频道（对所有成员生效、不可恢复），返回 (状态, 说明)。"""
    try:
        await client(build_destroy_request(ent))
        return DEL_OK, ""
    except telethon_errors.ChatAdminRequiredError:
        return DEL_BLOCKED, "你不是该频道 / 群组的创建者，无法删除"
    except (telethon_errors.ChannelPrivateError,
            telethon_errors.UserNotParticipantError):
        return DEL_GONE, "已经不在该频道 / 群组中，无需删除"
    except telethon_errors.PeerIdInvalidError:
        await client.delete_dialog(ent)
        return DEL_OK, ""


# ---------------- 「退出」的统一入口 ----------------
# Telegram 里只有**频道 / 群组**谈得上「退出」；**私聊和机器人压根没有这个动作** ——
# 唯一能做的是删掉会话（只影响自己这边）。所以「退出选中」必须自己按类型分派，
# 而不是把 User 当成错误拒掉：用户勾了一批私聊 / 机器人，期望的是「这些别再出现了」，
# 报一个「不支持的会话类型」整批失败，是最没用的行为。
EXIT_LEAVE = "leave"     # 频道 / 超级群 / 普通群 -> 真的退出（不再是成员）
EXIT_DELETE = "delete"   # 私聊 / 机器人        -> 退不了，只能删除会话
EXIT_SKIP = "skip"       # 已不在 / 未知类型    -> 没什么可做的


def exit_action_of(kind):
    """按会话类型决定「退出」该走哪条路。"""
    if is_member_kind(kind):
        return EXIT_LEAVE
    if kind in (KIND_USER, KIND_BOT):
        return EXIT_DELETE
    return EXIT_SKIP


async def leave_or_delete_entity(client, ent, kind=None):
    """「退出」的真正入口：能退的退，退不了的删会话。

    这是 build_leave_request 之外的**外层分派**：build_leave_request 仍然只认
    频道 / 群组（对未知类型显式报错是对的，它不该替调用方猜意图），
    而这里负责先把类型翻译成动作，再交给对应的原语。
    返回 (状态, 说明)，状态沿用 LEAVE_* 常量，调用方不必区分两条路。"""
    action = exit_action_of(kind or session_kind(ent))
    if action == EXIT_LEAVE:
        return await leave_entity(client, ent)
    if action == EXIT_DELETE:
        status, note = await delete_history_entity(client, ent)
        return {DEL_OK: LEAVE_OK, DEL_GONE: LEAVE_GONE,
                DEL_BLOCKED: LEAVE_BLOCKED}.get(status, LEAVE_OK), note
    return LEAVE_GONE, "该会话已不在列表中"


# 列表分页：每拉满一页就在界面追加显示，避免大账号长时间“没反应”
DIALOG_PAGE = 200

# 快捷筛选预设：(显示名, 用户数上限(<), 作品数上限(<), 活跃天数下限(>))；None = 该条件不启用
# 「活跃」用的是「最后一条消息距今多少天」，所以下限越大 = 越久没消息 = 越值得清理。
# 「无消息」的会话按 ACTIVE_NEVER（极大值）参与比较，会被「活跃 > N 天」一并捞出来。
FILTER_PRESETS = [
    ("不筛选（显示全部）", None, None, None),
    ("用户数 < 10", 10, None, None),
    ("用户数 < 50", 50, None, None),
    ("用户数 < 100", 100, None, None),
    ("作品数 < 10", None, 10, None),
    ("作品数 < 50", None, 50, None),
    ("作品数 < 100", None, 100, None),
    # 休眠会话：按「多久没消息」挑 —— 这是「退掉也不可惜」最直接的判据
    ("活跃 > 30 天没消息", None, None, 30),
    ("活跃 > 90 天没消息", None, None, 90),
    ("活跃 > 180 天没消息", None, None, 180),
    ("活跃 > 365 天没消息", None, None, 365),
    ("用户数<10 且 作品数<10", 10, 10, None),
    ("用户数<100 且 作品数<10", 100, 10, None),
    ("用户数<10 且 作品数<100", 10, 100, None),
    # 组合：又小又死的频道
    ("用户数<50 且 活跃>90 天", 50, None, 90),
    ("作品数<10 且 活跃>180 天", None, 10, 180),
]
FILTER_CUSTOM = "自定义…"


def migrate_legacy_data():
    """旧版 onefile 把 session/config 写进了 PyInstaller 临时解压目录(_MEI*)，
    程序一关就被清理，导致登录态/配置丢失。启动时把它们迁移到数据目录。
    仅在目标文件缺失时才迁移；若临时目录已被清理则静默跳过。

    ⚠️ 目标目录从 **CONFIG_FILE 所在目录**推导，而不是直接读 BASE_DIR 常量 ——
    这样测试只要重定向 CONFIG_FILE 就不会往真实目录写东西。
    """
    import glob
    import shutil
    import tempfile
    base = os.path.dirname(CONFIG_FILE) or BASE_DIR
    temp = tempfile.gettempdir()
    mappings = {
        "tg_cleaner_config.json": CONFIG_FILE,
        "tg_session.session": os.path.join(base, "tg_session.session"),
        "tg_session.session-journal": os.path.join(base, "tg_session.session-journal"),
    }
    for src_name, dst in mappings.items():
        if os.path.exists(dst):
            continue
        for d in glob.glob(os.path.join(temp, "_MEI*")):
            src = os.path.join(d, src_name)
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dst)
                    print(f"[migrate] 已从临时目录恢复 {src_name} -> {dst}")
                except Exception as e:
                    print(f"[migrate] 恢复失败 {src_name}: {e}")
                break


class OperationCancelled(Exception):
    """用户取消登录输入时抛出。"""
    pass


# ---------------- 日志落盘 ----------------
# 界面日志一关就没了：用户报「显示成功但实际没生效」时，我这边只能靠猜。
# 所以每条日志同时追加到程序目录，出问题直接读文件就能定位真因。
LOG_FILE_NAME = "tg_leave_log.txt"
LOG_FILE_MAX = 3 * 1024 * 1024      # 超过 3MB 轮转一代，不无限长大


def log_file_path():
    """日志文件路径（跟着 BASE_DIR 走，测试重定向 BASE_DIR 时自动隔离）。"""
    return os.path.join(BASE_DIR, LOG_FILE_NAME)


def append_log_file(line):
    """把一行日志追加到 log 文件。**绝不抛异常** —— 记日志失败不能影响主流程。"""
    try:
        path = log_file_path()
        try:
            if os.path.getsize(path) > LOG_FILE_MAX:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1200x780")

        self.client = None          # TelethonClient 实例
        self.loop = None            # 后台 asyncio 事件循环（单独线程拥有）
        self.code_future = None     # 等待用户输入验证码/两步密码的 future
        self.channels = {}          # tree item id -> entity
        self._tree_epoch = 0        # 列表代次：刷新/清空时递增，用于废弃过期后台回填任务
        self._sort_col = None       # 当前排序列
        self._sort_rev = False      # 是否倒序
        self._stats_cache = {}      # {频道id: [成员数, 作品数, 时间戳]} —— 载入见 _init_accounts
        self._role_cache = {}       # {频道id: [角色, 时间戳]} —— 载入见 _init_accounts
        self._whitelist = set()     # 白名单：频道 id（字符串），永不退出，全选自动跳过（按账号）
        self._stats_cancel = threading.Event()   # 持久化取消事件：「停止」按钮始终作用于当前任务
        self._list_cancel = threading.Event()    # 列表分页加载的取消事件（「停止全部」可中断）
        self._row_order = []        # 行 id 的插入顺序（筛选隐藏行后用于按原顺序恢复显示）
        self._visible = set()       # 当前可见（未被筛选隐藏）的行 id
        self._filter_active = False # 当前是否有生效的筛选条件
        self._sel_hidden = set()    # 已勾选、但当前被筛选隐藏的行 id（退出时不会处理，需提示用户）
        self._filter_members_lt = None   # 当前生效的阈值（供 _set_stat 实时显隐判断）
        self._filter_posts_lt = None
        self._only_checked = False  # 是否「只看已勾选」
        self._search_after = None   # 搜索防抖的 after id
        self._risk = {}             # 行 id -> (异常类别码, ...)：违规 / 已封禁 / 不可关注…
        self._risk_filter = None    # 当前生效的异常筛选（None=不筛；否则是类别码元组）
        self._task_start = None     # 当前阶段任务开始时间（用于显示耗时）
        self._progress_running = False
        self._left_visible = True
        self._log_visible = True
        self._last_left_width = 340
        self._dark = False
        self._suppress_save = False  # 恢复配置期间屏蔽变量 trace 触发的自动保存（见 _auto_save）
        self._default_theme = None  # 系统默认 ttk 主题（浅色时恢复回去）
        self._role = {}             # iid -> "creator"/"admin"/""（本会话检测结果，随列表刷新重建）
        migrate_legacy_data()       # 把旧版临时目录里的 session/config 救回 exe 旁
        self.config = self.load_config()
        # 多账号：切走的账号的 client 留在这个池里，**不 disconnect** ——
        # 这就是「切换账号不退出、登录态一直保持」的实现方式。
        self._clients = {}          # key -> TelegramClient
        self._accounts = []         # 账号注册表（config["accounts"] 的规范化副本）
        self._current_key = None    # 当前账号 key
        self._init_accounts()       # 迁移旧单账号数据 + 定当前账号 + 载入该账号的缓存/白名单
        # 分组（dialog filter）：每次刷新列表时重新拉取一次（成本 1 个请求）。
        # _dlg_state 存本地复现分组规则所需的状态（免打扰 / 未读 / 归档 / 是否联系人）。
        self._filters = []          # 服务器返回的 DialogFilter 列表（原样保留，回写时要用）
        self._dlg_state = {}        # peer_key -> 规则判定所需状态
        self._folder_names = []     # 用户自建分组名（进下拉框与菜单）

        self.build_ui()
        self.apply_config_to_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 启动后自动尝试用已保存的登录态连接（无需每次手动连、手动输验证码）
        self.root.after(300, self.schedule_auto_connect)

    # ---------------- 多账号 ----------------
    # 「属于某个账号」的设置项：(存盘 key, 界面变量名, 类型, 默认值)
    # 白名单也在其中，但它存在 _whitelist 而非变量里，由 _account_state 单独处理。
    ACCOUNT_STATE_FIELDS = (
        ("filter_members_lt", "filter_members_var", "str", ""),
        ("filter_posts_lt", "filter_posts_var", "str", ""),
        ("filter_active_gt", "filter_active_var", "str", ""),
        ("filter_hide_unknown", "filter_unknown_var", "bool", True),
        ("stats_concurrency", "conc_var", "int", 6),
        ("stats_use_cache", "cache_var", "bool", True),
        ("stats_ttl_hours", "ttl_var", "int", 24),
        ("leave_interval", "interval_var", "int", 2),
        ("leave_jitter", "jitter_var", "int", 0),
        ("detect_roles", "detect_role_var", "bool", True),
        ("skip_protected", "skip_protected_var", "bool", True),
        ("hide_protected", "hide_protected_var", "bool", False),
        ("preview_before_leave", "preview_leave_var", "bool", True),
    )

    def _init_accounts(self):
        """初始化账号注册表：迁移旧单账号数据 -> 定当前账号 -> 载入该账号的缓存与白名单。

        只在 __init__ 里调用一次。此时 UI 变量还没建，所以只能走「内存 + 落盘」，
        绝不能碰 save_config（它要读界面变量）。
        """
        accs, changed = migrate_legacy_account(self.config)
        norm = []
        for a in accs:
            if not isinstance(a, dict) or not a.get("key"):
                continue
            a.setdefault("phone", "")
            a.setdefault("label", "")
            a.setdefault("username", "")
            a.setdefault("user_id", None)
            norm.append(a)
        self._accounts = norm
        if changed:
            self.config["accounts"] = norm
            write_config_file(self.config)   # 迁移结果越早落盘越好
        keys = [a["key"] for a in norm]
        want = str(self.config.get("current_account") or "").strip()
        self._current_key = want if want in keys else (keys[0] if keys else None)
        self._consume_pending_state(self._current_key)
        self._whitelist = self._whitelist_of(self._current_key)
        self._stats_cache = self.load_stats_cache()
        self._role_cache = self.load_role_cache()

    def _consume_pending_state(self, key):
        """把「还没有账号时先调好的设置」交给第一个出现的账号。

        否则用户在添加账号之前改的设置（并发数 / 间隔 / 白名单…）会在下次启动时静默丢失。
        """
        st = (self.config or {}).pop("pending_account_state", None)
        if not key or not isinstance(st, dict) or not st:
            return
        pa = dict((self.config or {}).get("per_account") or {})
        if not pa.get(key):
            pa[key] = st
            self.config["per_account"] = pa

    def _per_account(self, key=None):
        """取某个账号在 config 里的私有设置（dict 副本）。"""
        pa = (self.config or {}).get("per_account")
        if not isinstance(pa, dict):
            return {}
        st = pa.get(self._current_key if key is None else key)
        return dict(st) if isinstance(st, dict) else {}

    def _whitelist_of(self, key=None):
        """白名单是**按账号**存的：不同账号关心的频道完全不同，混在一起会误保护。"""
        return {str(x) for x in (self._per_account(key).get("whitelist") or []) if str(x).strip()}

    def _current_account(self):
        for a in self._accounts:
            if a.get("key") == self._current_key:
                return a
        return None

    def _account_by_key(self, key):
        for a in self._accounts:
            if a.get("key") == key:
                return a
        return None

    def _account_state(self):
        """收集「属于当前账号」的可持久化状态（白名单 + 筛选条件 + 抓取配置）。"""
        st = {"whitelist": sorted(self._whitelist)}
        for key, var, kind, _default in self.ACCOUNT_STATE_FIELDS:
            obj = getattr(self, var, None)
            if obj is None:
                continue
            try:
                if kind == "bool":
                    st[key] = bool(obj.get())
                elif kind == "int":
                    st[key] = self._safe_int(obj, 0, 0, 10 ** 6)
                else:
                    st[key] = str(obj.get()).strip()
            except Exception:
                continue
        return st

    def _apply_account_state(self, st, into_vars=True):
        """把账号状态写回内存与界面变量。

        ⚠️ 缺失的字段**回落到默认值**（而不是「保持上一个账号的值」）——切号时
        上一个账号的阈值/间隔绝对不能留在界面上，否则会用错条件去筛错数据。
        into_vars=False 表示只更新内存（__init__ 早期 UI 变量还没建时用）。
        """
        st = st if isinstance(st, dict) else {}
        self._whitelist = {str(x) for x in (st.get("whitelist") or []) if str(x).strip()}
        if not into_vars:
            return
        for key, var, kind, default in self.ACCOUNT_STATE_FIELDS:
            obj = getattr(self, var, None)
            if obj is None:
                continue
            val = st.get(key, default)
            if val in (None, ""):
                val = default
            try:
                if kind == "bool":
                    obj.set(bool(val))
                elif kind == "int":
                    obj.set(int(val))
                else:
                    obj.set(str(val))
            except Exception:
                continue

    # ---------------- 配置 ----------------
    def load_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self):
        data = dict(self.config or {})
        data.update({
            "api_id": self.api_id_var.get().strip(),
            "api_hash": self.api_hash_var.get().strip(),
            "proxy_enabled": self.proxy_enabled_var.get(),
            "proxy_type": self.proxy_type_var.get(),
            "proxy_host": self.proxy_host_var.get().strip(),
            "proxy_port": self.proxy_port_var.get().strip(),
            "proxy_user": self.proxy_user_var.get().strip(),
            "proxy_pass": self.proxy_pass_var.get().strip(),
            "dark_mode": bool(getattr(self, "dark_var", None) and self.dark_var.get()),
            "remember_layout": bool(getattr(self, "remember_layout_var", None) and self.remember_layout_var.get()),
        })
        # 账号注册表 + 当前账号（登录态、显示名、session 基名都在账号记录里）
        data["accounts"] = list(self._accounts)
        data["current_account"] = self._current_key
        # 按账号保存的私有设置（白名单 / 筛选 / 抓取配置）—— 切号时各自独立
        pa = dict(data.get("per_account") or {})
        if self._current_key:
            pa[self._current_key] = self._account_state()
        else:
            # 还没有任何账号（用户上来先调设置）——先存到过渡槽，
            # 等添加第一个账号时带过去，否则这些设置会在下次启动时静默丢失。
            data["pending_account_state"] = self._account_state()
        alive = {a.get("key") for a in self._accounts}
        data["per_account"] = {k: v for k, v in pa.items() if k in alive}
        # 这些在 v1.2.0 起改为「按账号」存，别再以全局字段留在配置里，否则切号后会串数据
        for k in ("phone", "whitelist", "stats_concurrency", "stats_use_cache", "stats_ttl_hours",
                  "filter_members_lt", "filter_posts_lt", "filter_active_gt", "filter_hide_unknown",
                  "leave_interval", "leave_jitter", "skip_protected", "hide_protected",
                  "detect_roles", "preview_before_leave"):
            data.pop(k, None)
        # 布局类字段：先清掉再按当前状态写回（“记住布局”关掉时就不该残留旧值）
        for k in ("win_geometry", "col_widths", "left_width", "left_visible", "log_visible"):
            data.pop(k, None)
        data.update(self._ui_state())
        self.config = data
        write_config_file(data)

    @staticmethod
    def _safe_int(var, default, lo, hi):
        try:
            v = int(var.get())
        except Exception:
            return default
        return max(lo, min(hi, v))

    def _bool_of(self, attr, default=False):
        """兼容入口：等价于模块级 var_bool(self, attr, default)。"""
        return var_bool(self, attr, default)

    # ---- 缓存文件都按账号分开：切号时各自载入，绝不让 A 账号的数据串到 B ----
    def _stats_cache_path(self):
        if self._current_key:
            return account_cache_file("stats_cache", self._current_key)
        return STATS_CACHE_FILE

    def _role_cache_path(self):
        if self._current_key:
            return account_cache_file("role_cache", self._current_key)
        return ROLE_CACHE_FILE

    def load_role_cache(self):
        try:
            with open(self._role_cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_role_cache(self):
        try:
            with open(self._role_cache_path(), "w", encoding="utf-8") as f:
                json.dump(self._role_cache, f, ensure_ascii=False)
        except Exception:
            pass

    def load_stats_cache(self):
        try:
            with open(self._stats_cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_stats_cache(self):
        try:
            with open(self._stats_cache_path(), "w", encoding="utf-8") as f:
                json.dump(self._stats_cache, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------------- 界面 ----------------
    # ================================================================
    # 界面结构：顶部工具栏 + 登录验证条 +（左设置栏 | 右主区）+ 状态栏 + 日志抽屉
    #   设计原则：设置项收进左栏（不常点），主区整块留给表格（常看常点），
    #             状态与进度固定在底部，日志按需收起，尽量把纵向空间还给列表。
    # ================================================================
    def build_ui(self):
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.minsize(1080, 680)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)      # 第 2 行（主区）吃掉纵向空间
        try:
            self._default_theme = ttk.Style(self.root).theme_use()   # 浅色时恢复系统原生主题
        except Exception:
            self._default_theme = "vista"

        self._build_header()        # row 0 工具栏 / row 1 登录验证条
        self._build_body()          # row 2 左设置栏 | 右主区
        self._build_statusbar()     # row 3 进度条 + 任务状态
        self._build_log_panel()     # row 4 日志抽屉

        self.on_proxy_toggle()
        self._bind_shortcuts()

        # 输入项改动即自动保存（API/手机号/代理/统计/筛选/退出节奏），避免每次重填；关窗时再兜底保存
        for var in (self.api_id_var, self.api_hash_var, self.phone_var,
                    self.proxy_enabled_var, self.proxy_type_var,
                    self.proxy_host_var, self.proxy_port_var,
                    self.proxy_user_var, self.proxy_pass_var,
                    self.conc_var, self.ttl_var, self.cache_var,
                    self.filter_members_var, self.filter_posts_var,
                    self.filter_unknown_var,
                    self.interval_var, self.jitter_var):
            try:
                var.trace_add("write", lambda *_a: self._auto_save())
            except Exception:
                pass
        self.refresh_cache_info()

    def _auto_save(self):
        """界面变量变化触发的自动保存。

        ⚠️ 「恢复配置」期间必须跳过：apply_config_to_ui 会逐个 set 这些变量，
        而 save_config 是「以当前 UI 值为准」重建 per_account 的 —— 不挡住的话，
        第一个 set 就会把还没恢复完的账号设置覆盖成默认值（实测就是这么坏的）。
        """
        if getattr(self, "_suppress_save", False):
            return
        self.save_config()

    # ---------------- 顶部工具栏 ----------------
    def _build_header(self):
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 2))
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(4, weight=1)         # 搜索框所在列吃掉多余宽度

        self.btn_left_toggle = ttk.Button(bar, text="◧ 设置", width=9, command=self.toggle_left_panel)
        self.btn_left_toggle.grid(row=0, column=0, padx=(0, 6))

        # 连接状态：一眼看出是否已连上（登录态来自 session，有效时无需再输验证码）
        self.status_var = tk.StringVar(value="⚪ 尚未连接")
        self.lbl_status = ttk.Label(bar, textvariable=self.status_var, foreground="#7f8c8d")
        self.lbl_status.grid(row=0, column=1, sticky="w", padx=(0, 12))

        self.btn_connect = ttk.Button(bar, text="连接 / 登录", command=self.on_connect)
        self.btn_connect.grid(row=0, column=2, padx=3)
        self.btn_refresh = ttk.Button(bar, text="刷新列表", command=self.on_refresh, state="disabled")
        self.btn_refresh.grid(row=0, column=3, padx=3)

        # 搜索：输入即筛（250ms 防抖），回车立即生效，Esc/✕ 清空
        sfrm = ttk.Frame(bar)
        sfrm.grid(row=0, column=4, sticky="ew", padx=10)
        ttk.Label(sfrm, text="🔍").pack(side="left")
        self.search_var = tk.StringVar()
        self.ent_search = ttk.Entry(sfrm, textvariable=self.search_var)
        self.ent_search.pack(side="left", fill="x", expand=True, padx=4)
        self.search_var.trace_add("write", lambda *_a: self.on_search_changed())
        self.ent_search.bind("<Return>", lambda e: self._apply_search(verbose=True))
        self.btn_search_clear = ttk.Button(sfrm, text="✕", width=3, command=self.on_search_clear)
        self.btn_search_clear.pack(side="left")
        self.lbl_search_hit = ttk.Label(sfrm, text="", foreground="#6b7280")
        self.lbl_search_hit.pack(side="left", padx=(6, 0))

        ttk.Label(bar, text="类型:").grid(row=0, column=5, padx=(4, 2))
        self.type_filter_var = tk.StringVar(value="全部")
        self.cmb_type = ttk.Combobox(bar, textvariable=self.type_filter_var, state="readonly",
                                     width=7, values=TYPE_CHOICES)
        self.cmb_type.grid(row=0, column=6)
        self.cmb_type.bind("<<ComboboxSelected>>", lambda e: self.on_quick_filter_changed())

        # 分组筛选：取值在连上账号、读到 dialog filters 后填充（见 refresh_folders）
        ttk.Label(bar, text="分组:").grid(row=0, column=7, padx=(10, 2))
        self.folder_filter_var = tk.StringVar(value=FOLDER_ALL)
        self.cmb_folder = ttk.Combobox(bar, textvariable=self.folder_filter_var, state="readonly",
                                       width=12, values=(FOLDER_ALL, FOLDER_NONE))
        self.cmb_folder.grid(row=0, column=8)
        self.cmb_folder.bind("<<ComboboxSelected>>", lambda e: self.on_quick_filter_changed())

        self.only_checked_var = tk.BooleanVar(value=False)
        self.chk_only_checked = ttk.Checkbutton(bar, text="只看已勾选", variable=self.only_checked_var,
                                                command=self.on_quick_filter_changed)
        self.chk_only_checked.grid(row=0, column=9, padx=8)

        # 统计与任务控制
        self.btn_stats = ttk.Button(bar, text="获取 / 更新统计", command=self.on_fetch_stats, state="disabled")
        self.btn_stats.grid(row=0, column=10, padx=3)
        self.btn_stats_stop = ttk.Button(bar, text="停止统计", command=self.on_stop_stats, state="disabled")
        self.btn_stats_stop.grid(row=0, column=11, padx=3)
        self.btn_stop_all = ttk.Button(bar, text="⏹ 停止全部", command=self.on_stop_all)
        self.btn_stop_all.grid(row=0, column=12, padx=3)
        self.btn_log_toggle = ttk.Button(bar, text="🗒 日志", width=8, command=self.toggle_log_panel)
        self.btn_log_toggle.grid(row=0, column=13, padx=(8, 0))

        # 登录验证条：固定在界面上（不用弹窗，避免窗口被遮住时看不到输入框）
        strip = ttk.Frame(self.root, padding=(10, 2, 10, 6))
        strip.grid(row=1, column=0, sticky="ew")
        self.code_prompt_var = tk.StringVar(value="尚未连接。")
        ttk.Label(strip, textvariable=self.code_prompt_var, foreground="#c0392b").pack(side="left")
        ttk.Label(strip, text="   验证码 / 两步密码:").pack(side="left")
        self.code_var = tk.StringVar()
        self.code_entry = ttk.Entry(strip, textvariable=self.code_var, width=26)
        self.code_entry.pack(side="left", padx=4)
        self.btn_code_submit = ttk.Button(strip, text="提交", command=self.on_submit_code, state="disabled")
        self.btn_code_submit.pack(side="left", padx=2)
        self.code_entry.bind("<Return>", lambda e: self.on_submit_code())

    # ---------------- 主体：左设置栏 + 右主区 ----------------
    def _build_body(self):
        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))

        self.panel_left = ttk.Frame(self.paned)
        self.panel_main = ttk.Frame(self.paned)
        self.paned.add(self.panel_left, weight=0)
        self.paned.add(self.panel_main, weight=1)

        nb = ttk.Notebook(self.panel_left)
        nb.pack(fill="both", expand=True)
        self.nb_settings = nb
        self._tab_account(nb)
        self._tab_network(nb)
        self._tab_stats(nb)
        self._tab_leave(nb)
        self._tab_appearance(nb)

        self._build_main_area(self.panel_main)

    def _tab_account(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="账号")
        self.tab_account = f
        f.grid_columnconfigure(0, weight=1)

        ttk.Label(f, text="账号列表（可添加多个，随时切换；切换时不会退出登录）",
                  foreground="#374151", wraplength=290, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        box = ttk.Frame(f)
        box.grid(row=1, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        self.acc_tree = ttk.Treeview(
            box, columns=("account", "phone", "state"), show="headings",
            height=6, selectmode="browse")
        for c, t, w, st in (("account", "账号", 140, True), ("phone", "手机号", 96, False),
                            ("state", "状态", 96, False)):
            self.acc_tree.heading(c, text=t)
            self.acc_tree.column(c, width=w, anchor="w", stretch=st)
        self.acc_tree.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.acc_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.acc_tree.configure(yscrollcommand=sb.set)
        self.acc_tree.bind("<Double-1>", lambda e: self.on_switch_selected_account())

        btns = ttk.Frame(f)
        btns.grid(row=2, column=0, sticky="ew", pady=(5, 4))
        ttk.Button(btns, text="切换到选中", command=self.on_switch_selected_account).pack(side="left", padx=2)
        ttk.Button(btns, text="移除账号", command=lambda: self.on_remove_selected_account(False)).pack(
            side="left", padx=2)
        ttk.Button(btns, text="移除并删登录态", command=lambda: self.on_remove_selected_account(True)).pack(
            side="left", padx=2)

        ttk.Separator(f, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=6)

        row = ttk.Frame(f)
        row.grid(row=4, column=0, sticky="ew")
        row.grid_columnconfigure(1, weight=1)
        ttk.Label(row, text="手机号").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.phone_var = tk.StringVar()
        self.phone_entry = ttk.Entry(row, textvariable=self.phone_var)
        self.phone_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(row, text="＋ 添加账号并登录", command=self.on_add_account).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(6, 2))

        ttk.Separator(f, orient="horizontal").grid(row=5, column=0, sticky="ew", pady=6)

        api = ttk.Frame(f)
        api.grid(row=6, column=0, sticky="ew")
        api.grid_columnconfigure(1, weight=1)
        ttk.Label(api, text="API ID").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.api_id_var = tk.StringVar()
        ttk.Entry(api, textvariable=self.api_id_var).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(api, text="API HASH").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.api_hash_var = tk.StringVar()
        ttk.Entry(api, textvariable=self.api_hash_var).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(api, text="连接 / 登录当前账号", command=self.on_connect).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 4))
        ttk.Label(api, text="在 https://my.telegram.org 创建应用后获取 API ID / HASH。\n"
                           "API 与代理是全局设置，所有账号共用；登录态按账号分别保存，"
                           "所以切换账号不需要重新输验证码。",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

        self.lbl_session_info = ttk.Label(f, text="", foreground="#6b7280", wraplength=290, justify="left")
        self.lbl_session_info.grid(row=7, column=0, sticky="w", padx=4, pady=(6, 0))
        self.btn_clear_session = ttk.Button(f, text="删除当前账号的登录态", command=self.on_clear_session)
        self.btn_clear_session.grid(row=8, column=0, sticky="ew", padx=4, pady=(8, 2))

    # ---------------- 账号切换（界面入口）----------------
    def _build_account_bar(self, parent):
        bar = ttk.LabelFrame(parent, text="账号（切换不会退出登录，各账号数据独立）", padding=6)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(bar, text="当前账号:").pack(side="left", padx=(2, 4))
        self.account_var = tk.StringVar(value="（未添加账号）")
        self.account_combo = ttk.Combobox(bar, textvariable=self.account_var, state="readonly",
                                          width=32, values=["（未添加账号）"])
        self.account_combo.pack(side="left", padx=2)
        self.account_combo.bind("<<ComboboxSelected>>", self.on_account_combo_selected)
        ttk.Button(bar, text="＋ 添加账号", command=self.on_add_account).pack(side="left", padx=6)
        self.lbl_account_info = ttk.Label(bar, text="", foreground="#6b7280")
        self.lbl_account_info.pack(side="left", padx=(10, 2))

    def _selected_account_key(self):
        """从「账号」下拉框当前文本反查账号 key（文本就是 account_label）。"""
        try:
            text = str(self.account_var.get() or "")
        except Exception:
            return None
        for a in self._accounts:
            if account_label(a) == text:
                return a.get("key")
        return None

    def on_account_combo_selected(self, event=None):
        key = self._selected_account_key()
        if key is None or key == self._current_key:
            return
        if not self.switch_account(key):
            self._refresh_account_ui()      # 切换被拒（任务在跑）时把下拉框拉回当前账号

    def on_switch_selected_account(self):
        sel = ()
        try:
            sel = self.acc_tree.selection()
        except Exception:
            pass
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中一个账号。")
            return
        self.switch_account(str(sel[0]))

    def on_remove_selected_account(self, delete_files=False):
        sel = ()
        try:
            sel = self.acc_tree.selection()
        except Exception:
            pass
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中一个账号。")
            return
        self.remove_account(str(sel[0]), delete_files=delete_files)

    def _tab_network(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="网络")
        f.grid_columnconfigure(1, weight=1)
        f.grid_columnconfigure(3, weight=1)
        self.proxy_enabled_var = tk.BooleanVar(value=False)
        self.chk_proxy = ttk.Checkbutton(f, text="启用代理", variable=self.proxy_enabled_var,
                                         command=self.on_proxy_toggle)
        self.chk_proxy.grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(f, text="类型").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.proxy_type_var = tk.StringVar(value="SOCKS5")
        self.proxy_type_om = ttk.OptionMenu(f, self.proxy_type_var, "SOCKS5", "SOCKS5", "SOCKS4", "HTTP")
        self.proxy_type_om.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(f, text="主机").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.proxy_host_var = tk.StringVar()
        self.proxy_host_entry = ttk.Entry(f, textvariable=self.proxy_host_var)
        self.proxy_host_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(f, text="端口").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.proxy_port_var = tk.StringVar()
        self.proxy_port_entry = ttk.Entry(f, textvariable=self.proxy_port_var)
        self.proxy_port_entry.grid(row=3, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(f, text="用户名").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        self.proxy_user_var = tk.StringVar()
        self.proxy_user_entry = ttk.Entry(f, textvariable=self.proxy_user_var)
        self.proxy_user_entry.grid(row=4, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(f, text="密码").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        self.proxy_pass_var = tk.StringVar()
        self.proxy_pass_entry = ttk.Entry(f, textvariable=self.proxy_pass_var, show="*")
        self.proxy_pass_entry.grid(row=5, column=1, sticky="ew", padx=4, pady=4)

        self.btn_proxy_test = ttk.Button(f, text="测试代理（TCP + 出网到 Telegram）", command=self.on_proxy_test)
        self.btn_proxy_test.grid(row=6, column=0, columnspan=2, sticky="ew", padx=4, pady=(10, 4))
        ttk.Label(f, text="国内网络通常需要代理（如 127.0.0.1:7890）。\n"
                          "测试会先连本地端口，再经代理访问 api.telegram.org:443。",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

    def _tab_stats(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="统计")
        f.grid_columnconfigure(1, weight=1)
        ttk.Label(f, text="并发数").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.conc_var = tk.IntVar(value=6)
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.conc_var, width=6).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="缓存有效期(小时)").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.ttl_var = tk.IntVar(value=24)
        ttk.Spinbox(f, from_=1, to=720, textvariable=self.ttl_var, width=6).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)
        self.cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="使用本地统计缓存", variable=self.cache_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self.lbl_cache_info = ttk.Label(f, text="缓存 0 条", foreground="#6b7280", wraplength=290, justify="left")
        self.lbl_cache_info.grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 2))
        self.btn_clear_cache = ttk.Button(f, text="清空统计缓存", command=self.on_clear_stats_cache)
        self.btn_clear_cache.grid(row=4, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 2))
        ttk.Label(f, text="「用户数 / 作品数」会对每个频道各发 1~2 次请求，\n"
                          "并发越大越快、也越容易触发限流；命中缓存则完全不发请求。",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 2))

    def _tab_leave(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="退出")
        f.grid_columnconfigure(1, weight=1)
        ttk.Label(f, text="退出间隔(秒)").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.interval_var = tk.IntVar(value=2)
        ttk.Spinbox(f, from_=1, to=120, textvariable=self.interval_var, width=6).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="随机抖动(秒)").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.jitter_var = tk.IntVar(value=0)
        ttk.Spinbox(f, from_=0, to=60, textvariable=self.jitter_var, width=6).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="间隔 + 0~抖动 的随机等待，可降低被限流概率。",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 8))

        ttk.Separator(f, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(f, text="按关键词批量退\n（名称/用户名包含任一关键词即匹配，逗号或空格分隔）",
                  wraplength=290, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", padx=4)
        self.kw_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.kw_var).grid(row=5, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 4))
        self.btn_kw_leave = ttk.Button(f, text="按关键词退出", command=self.on_kw_leave, state="disabled")
        self.btn_kw_leave.grid(row=6, column=0, columnspan=2, sticky="ew", padx=4)

        # ---- 安全防护 ----
        ttk.Separator(f, orient="horizontal").grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Label(f, text="安全防护", font=("", 9, "bold")).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=4)
        self.skip_protected_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="退出时自动跳过 白名单 / 创建者 / 管理员",
                        variable=self.skip_protected_var).grid(
            row=9, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 0))
        self.detect_role_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="获取统计时顺带检测创建者 / 管理员\n（每个多 1 次请求，结果会被缓存）",
                        variable=self.detect_role_var).grid(
            row=10, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 0))
        self.preview_leave_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="退出前显示待退出清单，确认后才执行",
                        variable=self.preview_leave_var).grid(
            row=11, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 6))
        self.lbl_whitelist_info = ttk.Label(f, text="白名单 0 个频道（永不退出）",
                                            foreground="#6b7280", wraplength=290, justify="left")
        self.lbl_whitelist_info.grid(row=12, column=0, columnspan=2, sticky="w", padx=4)
        self.btn_clear_whitelist = ttk.Button(f, text="清空白名单", command=self.clear_whitelist)
        self.btn_clear_whitelist.grid(row=13, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 2))
        ttk.Label(f, text="创建者 / 管理员在 Telegram 里无法直接退出（创建者需先转让或删除频道），\n"
                          "不跳过会白跑一遍并报错。白名单用于标记「我就是要保留」的频道。",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=14, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

    def _tab_appearance(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="外观")
        f.grid_columnconfigure(0, weight=1)
        self.dark_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="深色模式", variable=self.dark_var, command=self.on_toggle_theme).grid(
            row=0, column=0, sticky="w", padx=4, pady=4)
        self.remember_layout_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="记住窗口大小 / 列宽 / 面板开合", variable=self.remember_layout_var,
                        command=self.save_config).grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.btn_reset_layout = ttk.Button(f, text="恢复默认布局", command=self.on_reset_layout)
        self.btn_reset_layout.grid(row=2, column=0, sticky="ew", padx=4, pady=(10, 4))
        ttk.Label(f, text="快捷键：Ctrl+F 搜索 / Ctrl+A 全选 / 空格 勾选 / F5 刷新 / Esc 清搜索",
                  foreground="#6b7280", wraplength=290, justify="left").grid(
            row=3, column=0, sticky="w", padx=4, pady=(10, 2))

    # ---------------- 右主区：筛选 + 表格 + 选择 + 退出 ----------------
    def _build_main_area(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # 0) 账号条：多账号在这里切换（切换不退出登录，各账号数据独立）
        self._build_account_bar(parent)

        # 1) 快捷筛选（分两行，避免窄窗口时被挤没）
        frm_filter = ttk.LabelFrame(parent, text="快捷筛选（各条件之间是「且」的关系）", padding=7)
        frm_filter.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.frm_filter = frm_filter
        row1 = ttk.Frame(frm_filter)
        row1.pack(fill="x")
        row2 = ttk.Frame(frm_filter)
        row2.pack(fill="x", pady=(5, 0))

        ttk.Label(row1, text="预设:").pack(side="left", padx=(2, 2))
        self.filter_preset_var = tk.StringVar(value=FILTER_PRESETS[0][0])
        self.filter_preset_cb = ttk.Combobox(
            row1, textvariable=self.filter_preset_var, state="readonly", width=22,
            values=[p[0] for p in FILTER_PRESETS] + [FILTER_CUSTOM],
        )
        self.filter_preset_cb.pack(side="left", padx=2)
        self.filter_preset_cb.bind("<<ComboboxSelected>>", lambda e: self.on_filter_preset())

        ttk.Label(row1, text="用户数 <").pack(side="left", padx=(12, 2))
        self.filter_members_var = tk.StringVar()
        ent_m = ttk.Entry(row1, textvariable=self.filter_members_var, width=7)
        ent_m.pack(side="left", padx=2)
        ent_m.bind("<Return>", lambda e: self.apply_filter())

        ttk.Label(row1, text="作品数 <").pack(side="left", padx=(12, 2))
        self.filter_posts_var = tk.StringVar()
        ent_p = ttk.Entry(row1, textvariable=self.filter_posts_var, width=7)
        ent_p.pack(side="left", padx=2)
        ent_p.bind("<Return>", lambda e: self.apply_filter())

        # 活跃：只给「大于」方向 —— 清理时真正想知道的是「多久没动静了」
        ttk.Label(row1, text="活跃(天) >").pack(side="left", padx=(12, 2))
        self.filter_active_var = tk.StringVar()
        ent_a = ttk.Entry(row1, textvariable=self.filter_active_var, width=7)
        ent_a.pack(side="left", padx=2)
        ent_a.bind("<Return>", lambda e: self.apply_filter())

        self.filter_unknown_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="隐藏统计未获取的行", variable=self.filter_unknown_var,
                        command=self.apply_filter).pack(side="left", padx=12)

        ttk.Button(row2, text="应用", command=self.apply_filter).pack(side="left", padx=2)
        ttk.Button(row2, text="应用并全选", command=self.apply_filter_and_select_all).pack(side="left", padx=2)
        ttk.Button(row2, text="清除", command=self.clear_filter).pack(side="left", padx=2)
        ttk.Label(row2, text="提示：筛选生效时，「全选 / 反选」只作用于当前显示出来的行；"
                            "被隐藏的勾选会用「清空全部」一并清掉。",
                  foreground="#6b7280").pack(side="left", padx=(10, 2))

        # 2) 列表（表格占满剩余空间，横向纵向都能滚）
        frm_list = ttk.LabelFrame(parent, text="全部会话（频道 / 群组 / 私聊 / 机器人）—— 点「选择」列或按空格勾选", padding=7)
        frm_list.grid(row=2, column=0, sticky="nsew")
        frm_list.grid_rowconfigure(1, weight=1)   # 第 0 行让给列表上方的类型统计条
        frm_list.grid_columnconfigure(0, weight=1)

        # 类型统计条：频道 / 群组 / 私聊 / 机器人 / 已不在 各多少个。
        # 不设前景色，跟随 ttk 主题，深色模式下同样可读。
        # ⚠️ 名字不能叫 lbl_stats —— 状态栏里已有一个同名标签（统计进度用），会被覆盖。
        self.stats_var = tk.StringVar(value="")
        self.lbl_kind_stats = ttk.Label(frm_list, textvariable=self.stats_var, anchor="w")
        self.lbl_kind_stats.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        # 「id」「safe」保持在末尾：按位置构造行值的既有代码/测试不受影响，
        # 新增列一律插在 posts 之后、id 之前。
        cols = ("sel", "name", "username", "type", "members", "posts", "active",
                "folder", "risk", "id", "safe")
        self.tree = ttk.Treeview(frm_list, columns=cols, show="headings", height=16, selectmode="extended")
        self._col_titles = {"sel": "选择", "name": "名称", "username": "用户名",
                            "type": "类型", "members": "用户数", "posts": "作品数",
                            "active": "活跃", "folder": "分组", "risk": "异常",
                            "id": "ID", "safe": "安全"}
        # 受保护行的整行着色：白名单=蓝，创建者/管理员=红（一眼看出哪些不会 / 不能退出）
        self.tree.tag_configure("wl", foreground="#185FA5")
        self.tree.tag_configure("unleaveable", foreground="#a32d2d")
        self._col_index = {c: i for i, c in enumerate(cols)}
        for c, w, txt in (("sel", 50, "选择"), ("name", 220, "名称"),
                          ("username", 160, "用户名"), ("type", 60, "类型"),
                          ("members", 80, "用户数"), ("posts", 80, "作品数"),
                          ("active", 72, "活跃"),
                          ("folder", 120, "分组"),
                          ("risk", 104, "异常"),
                          ("id", 110, "ID"), ("safe", 92, "安全")):
            # 除「选择」列外，点列头即可排序（注意：ttk 不接受 command=None，需分支设置）
            if c == "sel":
                self.tree.heading(c, text=txt)
            else:
                self.tree.heading(c, text=txt, command=lambda cc=c: self.sort_by(cc))
            self.tree.column(c, width=w,
                             anchor="center" if c in ("sel", "type", "members", "posts",
                                                       "active", "risk", "safe") else "w")
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)

        vsb = ttk.Scrollbar(frm_list, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frm_list, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")

        # 3) 选择条
        # 范围说明：筛选生效时「全选 / 反选」只作用于**当前显示出来的筛选结果**，
        # 被隐藏的行既不会被勾上、也不会被反选；要连隐藏行一起清掉用「清空全部」。
        frm_sel = ttk.Frame(parent)
        frm_sel.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        self.btn_sel_all = ttk.Button(frm_sel, text="全选", command=self.select_all_visible)
        self.btn_sel_all.pack(side="left", padx=3)
        self.btn_sel_invert = ttk.Button(frm_sel, text="反选", command=self.invert)
        self.btn_sel_invert.pack(side="left", padx=3)
        ttk.Button(frm_sel, text="清空全部", command=lambda: self.set_all(False, scope="all")).pack(side="left", padx=3)
        self.btn_whitelist = ttk.Button(frm_sel, text="🔒 白名单", command=self.on_toggle_whitelist_checked)
        self.btn_whitelist.pack(side="left", padx=3)
        # 异常筛选：违规 / 已封禁 / 不可关注 / 禁言 …… 特征全部来自服务端字段（零请求）
        self.btn_risk = ttk.Menubutton(frm_sel, text="🚫 异常 ▾")
        self._risk_menu = tk.Menu(self.btn_risk, tearoff=0,
                                  postcommand=self._refresh_risk_menu)
        self.btn_risk["menu"] = self._risk_menu
        self.btn_risk.pack(side="left", padx=3)
        self._rebuild_risk_menu()
        # 分组操作：对一批行批量加入 / 移出分组（只改归属，不退出也不删除）
        self.btn_folder = ttk.Menubutton(frm_sel, text="📁 分组 ▾")
        # postcommand：菜单弹出前重算（勾选数 / 筛选结果数会一直变，静态菜单会显示旧数字）
        self._folder_menu = tk.Menu(self.btn_folder, tearoff=0,
                                    postcommand=self._refresh_folder_menu)
        self.btn_folder["menu"] = self._folder_menu
        self.btn_folder.pack(side="left", padx=3)
        self._rebuild_folder_menu()
        self.hide_protected_var = tk.BooleanVar(value=False)
        self.chk_hide_protected = ttk.Checkbutton(frm_sel, text="隐藏受保护项",
                                                  variable=self.hide_protected_var,
                                                  command=self.on_quick_filter_changed)
        self.chk_hide_protected.pack(side="left", padx=(6, 0))
        self.btn_export = ttk.Menubutton(frm_sel, text="导出 CSV ▾", state="disabled")
        self._export_menu = tk.Menu(self.btn_export, tearoff=0)
        self._export_menu.add_command(label="导出当前显示的行", command=lambda: self.on_export("visible"))
        self._export_menu.add_command(label="导出全部（含被筛选隐藏）", command=lambda: self.on_export("all"))
        self._export_menu.add_command(label="仅导出已勾选", command=lambda: self.on_export("checked"))
        self.btn_export["menu"] = self._export_menu
        self.btn_export.pack(side="left", padx=3)
        self.lbl_count = ttk.Label(frm_sel, text="共 0 个，已选 0 个")
        self.lbl_count.pack(side="right", padx=4)

        # 4) 退出条（主操作，固定在列表下方）
        frm_act = ttk.Frame(parent)
        frm_act.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        self.btn_leave = ttk.Button(frm_act, text="🚪 退出 / 删除选中", command=self.on_leave, state="disabled")
        self.btn_leave.pack(side="left", padx=3)
        # 删除类操作收进下拉菜单：破坏性高、使用频率低，
        # 平铺在主按钮旁边反而容易连同「退出」一起误点。
        self.btn_destroy = ttk.Menubutton(frm_act, text="🗑 删除 ▾", state="disabled")
        self._destroy_menu = tk.Menu(self.btn_destroy, tearoff=0)
        self._destroy_menu.add_command(label="删除会话（含聊天记录）…",
                                      command=self.on_delete_dialogs)
        self._destroy_menu.add_separator()
        self._destroy_menu.add_command(
            label="删除我创建的频道（不可恢复）…",
            command=lambda: self.on_destroy_created(KIND_CHANNEL))
        self._destroy_menu.add_command(
            label="删除我创建的群组（不可恢复）…",
            command=lambda: self.on_destroy_created(KIND_GROUP))
        self.btn_destroy["menu"] = self._destroy_menu
        self.btn_destroy.pack(side="left", padx=3)
        self.lbl_pending = ttk.Label(frm_act, text="待退出 0 个")
        self.lbl_pending.pack(side="left", padx=8)
        ttk.Label(frm_act, text="退出间隔(秒):").pack(side="left", padx=(10, 2))
        ttk.Spinbox(frm_act, from_=1, to=120, textvariable=self.interval_var, width=5).pack(side="left", padx=2)
        ttk.Label(frm_act, text="随机抖动(秒):").pack(side="left", padx=(10, 2))
        ttk.Spinbox(frm_act, from_=0, to=60, textvariable=self.jitter_var, width=5).pack(side="left", padx=2)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(10, 2, 10, 2))
        bar.grid(row=3, column=0, sticky="ew")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100, length=240)
        self.progress.pack(side="left")
        self.lbl_task = ttk.Label(bar, text="空闲")
        self.lbl_task.pack(side="left", padx=8)
        # 右侧（pack 顺序 = 从右往左）：版本号 | 更新日志 | 统计进度
        # 版本号必须在界面上看得见 —— 排查问题时第一个要问的就是「你用的是哪一版」。
        self.lbl_version = ttk.Label(bar, text=APP_VERSION, foreground="#6b7280")
        self.lbl_version.pack(side="right", padx=(10, 0))
        self.btn_changelog = ttk.Button(bar, text="📋 更新日志", width=12,
                                        command=self.on_show_changelog)
        self.btn_changelog.pack(side="right", padx=(10, 0))
        self.lbl_stats = ttk.Label(bar, text="")
        self.lbl_stats.pack(side="right", padx=4)

    def _build_log_panel(self):
        self.frm_log = ttk.Frame(self.root, padding=(10, 2, 10, 8))
        self.frm_log.grid(row=4, column=0, sticky="ew")
        head = ttk.Frame(self.frm_log)
        head.pack(fill="x")
        ttk.Label(head, text="运行日志").pack(side="left")
        ttk.Button(head, text="清空", width=6, command=self.on_clear_log).pack(side="left", padx=6)
        ttk.Button(head, text="导出", width=6, command=self.on_export_log).pack(side="left")
        self.log_autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(head, text="自动滚动", variable=self.log_autoscroll_var).pack(side="left", padx=10)
        ttk.Label(head, text="提示：退出间隔太短容易触发限流；日志区可用顶部「🗒 日志」收起。",
                  foreground="#6b7280").pack(side="right")
        self.log = scrolledtext.ScrolledText(self.frm_log, height=7, state="normal")
        self.log.pack(fill="both", expand=True, pady=(3, 0))

    # ---------------- 更新日志 ----------------
    def on_show_changelog(self):
        """弹出更新日志窗口：当前版本置顶高亮，下面倒序列出历史版本。

        内容来自模块级 CHANGELOG（与仓库里的 CHANGELOG.md 同一份事实），
        所以打包成单文件 exe 后也照样能看，不依赖任何外部文件。"""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"更新日志 — {APP_NAME}")
        dlg.transient(self.root)
        try:
            dlg.geometry("760x580")
        except Exception:
            pass

        outer = ttk.Frame(dlg, padding=12)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text=f"{APP_NAME} {APP_VERSION}",
                  font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(head, text=APP_REPO, foreground="#2563eb").pack(anchor="w", pady=(2, 0))

        txt = scrolledtext.ScrolledText(outer, wrap="word", height=20)
        txt.pack(fill="both", expand=True, pady=(10, 8))
        # 标签样式：当前版本用强调色 + 加粗，历史版本标题弱化，正文常规
        txt.tag_configure("ver_cur", font=("", 11, "bold"), foreground="#2563eb",
                          spacing1=2, spacing3=2)
        txt.tag_configure("ver_old", font=("", 10, "bold"),
                          spacing1=10, spacing3=2)
        txt.tag_configure("date", foreground="#6b7280")
        txt.tag_configure("item", lmargin1=18, lmargin2=32, spacing1=1)
        for idx, (ver, date, items) in enumerate(CHANGELOG):
            head_tag = "ver_cur" if idx == 0 else "ver_old"
            txt.insert("end", ver, head_tag)
            txt.insert("end", f"    {date}", "date")
            if idx == 0:
                txt.insert("end", "    （当前版本）", "date")
            txt.insert("end", "\n")
            for it in items:
                txt.insert("end", f"· {it}\n", "item")
        txt.configure(state="disabled")

        foot = ttk.Frame(outer)
        foot.pack(fill="x")
        ttk.Label(foot, text="最新版本与下载：" + APP_REPO + "/releases",
                  foreground="#6b7280").pack(side="left")
        ttk.Button(foot, text="关闭", width=8, command=dlg.destroy).pack(side="right")
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---------------- 面板开合 ----------------
    def toggle_left_panel(self):
        self._set_left_panel(not getattr(self, "_left_visible", True))

    def _left_in_paned(self):
        """左栏是否已挂在分割窗里。
        注意：不能用 winfo_ismapped() —— 窗口尚未完成映射时它恒为 0，
        会导致「展开→收起」被跳过，面板看起来点不动。"""
        try:
            return str(self.panel_left) in set(self.paned.panes())
        except Exception:
            return bool(getattr(self, "_left_visible", True))

    def _set_left_panel(self, visible):
        try:
            mounted = self._left_in_paned()
            if visible and not mounted:
                self.paned.insert(0, self.panel_left, weight=0)
            elif not visible and mounted:
                try:
                    w = int(self.paned.sashpos(0))
                    # 窗口尚未完成布局时 sashpos 可能返回 0/越界值，只在合理区间内才记录，
                    # 否则会把用户拖出来的宽度覆盖成一个废值。
                    if 200 < w < 1200:
                        self._last_left_width = w
                except Exception:
                    pass
                self.paned.forget(self.panel_left)
        except Exception as e:
            self.log_msg(f"❌ 切换设置栏失败: {e}")
        self._left_visible = bool(visible)
        try:
            self.btn_left_toggle.configure(text="◧ 设置" if visible else "▸ 设置")
        except Exception:
            pass
        if visible:
            try:
                w = int(self._last_left_width or 340)
            except Exception:
                w = 340
            self.root.after(80, lambda: self._set_sash(max(240, min(620, w))))

    def _set_sash(self, width):
        try:
            self.paned.sashpos(0, int(width))
        except Exception:
            pass

    def toggle_log_panel(self):
        self._set_log_panel(not getattr(self, "_log_visible", True))

    def _set_log_panel(self, visible):
        self._log_visible = bool(visible)
        try:
            if visible:
                self.frm_log.grid()
            else:
                self.frm_log.grid_remove()
            self.btn_log_toggle.configure(text="🗒 日志" if visible else "🗒 日志 ▸")
        except Exception:
            pass

    # ---------------- 快捷键 ----------------
    def _bind_shortcuts(self):
        self.root.bind("<Control-a>", self._on_select_all_key)
        self.root.bind("<Control-A>", self._on_select_all_key)
        self.root.bind("<Control-f>", self._on_focus_search)
        self.root.bind("<Control-F>", self._on_focus_search)
        # Ctrl+G：搜索完直接开「批量划分到分组」，不必先点菜单
        self.root.bind("<Control-g>", self._on_folder_bulk_key)
        self.root.bind("<Control-G>", self._on_folder_bulk_key)
        self.root.bind("<F5>", self._on_refresh_key)
        self.root.bind("<Escape>", self._on_escape)
        self.tree.bind("<space>", self._on_space_toggle)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

    def _on_folder_bulk_key(self, event=None):
        """Ctrl+G：打开「批量划分到分组」。搜索 / 筛选完直接按它，把整批结果一次划进分组。"""
        self._folder_bulk_dialog(True)
        return "break"

    def _on_focus_search(self, event=None):
        try:
            self.ent_search.focus_set()
            self.ent_search.select_range(0, "end")
        except Exception:
            pass
        return "break"

    def _on_refresh_key(self, event=None):
        self.on_refresh()
        return "break"

    def _on_escape(self, event=None):
        if self.search_var.get():
            self.on_search_clear()
            return "break"
        return None

    def _on_space_toggle(self, event=None):
        """空格：勾选 / 取消勾选当前高亮的行（支持多选）。"""
        rows = list(self.tree.selection())
        if not rows and self.tree.focus():
            rows = [self.tree.focus()]
        for iid in rows:
            try:
                if self.tree.exists(iid):
                    self.tree.set(iid, "sel", "☐" if self._is_checked(iid) else "☑")
                    self._sel_hidden.discard(iid)
            except Exception:
                pass
        self.update_count()
        self._maybe_apply_only_checked()
        return "break"

    # ---------------- 搜索 / 类型 / 只看已勾选 ----------------
    def on_search_changed(self, *_a):
        """输入即筛：250ms 防抖，避免每敲一个字符就重排一次表格。"""
        try:
            if self._search_after is not None:
                self.root.after_cancel(self._search_after)
        except Exception:
            pass
        try:
            self._search_after = self.root.after(250, self._apply_search)
        except Exception:
            self._search_after = None

    def _apply_search(self, verbose=False):
        self._search_after = None
        shown = self.apply_filter(verbose=False)
        if verbose and self.search_var.get().strip():
            self.log_msg(f"🔍 搜索「{self.search_var.get().strip()}」：命中 {shown} 个")

    def on_search_clear(self):
        self.search_var.set("")
        self._apply_search()

    def on_quick_filter_changed(self):
        """类型下拉 / 只看已勾选：立即重新应用筛选。"""
        self._only_checked = bool(self.only_checked_var.get())
        self.apply_filter(verbose=True)

    def _maybe_apply_only_checked(self):
        """开启「只看已勾选」时，取消勾选的行应立即从视图里消失。"""
        if getattr(self, "_only_checked", False):
            self.apply_filter(verbose=False)

    # ---------------- 任务进度 ----------------
    def start_task(self, text):
        self._task_start = time.time()
        self.set_progress(0, 1, text, indeterminate=True)

    def finish_task(self, text="空闲"):
        el = ""
        if self._task_start:
            el = f"（耗时 {time.time() - self._task_start:.1f}s）"
            self._task_start = None
        self.set_progress(None, None, text + el)

    def set_progress(self, done=None, total=None, text=None, indeterminate=False):
        """更新底部进度条与任务文字。
        total=None 或 indeterminate=True → 不确定进度（滚动条），其余按百分比。"""
        try:
            if indeterminate or (done is not None and total in (None, 0)):
                if not self._progress_running:
                    self.progress.configure(mode="indeterminate")
                    self.progress.start(12)
                    self._progress_running = True
            else:
                if self._progress_running:
                    self.progress.stop()
                    self._progress_running = False
                if done is None:
                    self.progress.configure(mode="determinate", value=0)
                else:
                    pct = 0 if not total else max(0, min(100, done * 100.0 / total))
                    self.progress.configure(mode="determinate", value=pct)
            if text is not None:
                self.lbl_task.configure(text=text)
        except Exception:
            pass

    def on_stop_all(self):
        """一键停止：统计任务 + 列表分页加载。"""
        stopped = []
        try:
            if self._stats_cancel is not None and not self._stats_cancel.is_set():
                self._stats_cancel.set()
                stopped.append("统计")
        except Exception:
            pass
        try:
            if self._list_cancel is not None and not self._list_cancel.is_set():
                self._list_cancel.set()
                stopped.append("列表加载")
        except Exception:
            pass
        self.log_msg("⏹ 已请求停止：" + ("、".join(stopped) if stopped else "当前没有正在运行的任务。"))

    # ---------------- 日志工具 ----------------
    def on_clear_log(self):
        try:
            self.log.delete("1.0", "end")
        except Exception:
            pass

    def on_export_log(self):
        try:
            content = self.log.get("1.0", "end").strip()
        except Exception:
            content = ""
        if not content:
            messagebox.showinfo("提示", "日志为空，无需导出。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")],
            initialfile="tg_leave_log.txt",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            self.log_msg(f"✅ 日志已导出：{path}")
            messagebox.showinfo("完成", f"日志已导出到：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ---------------- 缓存 / 登录态 ----------------
    def refresh_cache_info(self):
        n = len(self._stats_cache or {})
        path = self._stats_cache_path()
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        try:
            self.lbl_cache_info.configure(
                text=f"当前账号缓存 {n} 条，文件 {size / 1024.0:.1f} KB（{os.path.basename(path)}）")
            self.btn_clear_cache.configure(state="normal" if n else "disabled")
        except Exception:
            pass
        self._refresh_session_info()

    def _refresh_session_info(self):
        try:
            acc = self._current_account()
            if acc is None:
                self.lbl_session_info.configure(
                    text="尚未添加账号：填好手机号后点「添加账号并登录」，"
                         "登录态会按账号分别保存。")
                self.btn_clear_session.configure(state="disabled")
                return
            p = account_session_base(acc) + ".session"
            if os.path.exists(p):
                size = os.path.getsize(p) / 1024.0
                self.lbl_session_info.configure(
                    text=f"当前账号：{account_label(acc)}\n"
                         f"登录态：{os.path.basename(p)}（{size:.0f} KB）")
                self.btn_clear_session.configure(state="normal")
            else:
                self.lbl_session_info.configure(
                    text=f"当前账号：{account_label(acc)}\n"
                         "尚未保存登录态，首次登录需输入手机验证码。")
                self.btn_clear_session.configure(state="disabled")
        except Exception:
            pass

    def on_clear_stats_cache(self):
        n = len(self._stats_cache or {})
        if not messagebox.askyesno("确认", f"确定清空当前账号的统计缓存（{n} 条）吗？\n清空后下次获取统计需要重新请求 Telegram。"):
            return
        self._stats_cache = {}
        path = self._stats_cache_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            self.log_msg(f"⚠️ 删除缓存文件失败（不影响使用）：{e}")
        self.refresh_cache_info()
        self.log_msg(f"🧹 已清空统计缓存（{n} 条）。")

    def on_clear_session(self):
        """删除**当前账号**的登录态（其它账号不受影响）。"""
        acc = self._current_account()
        if acc is None:
            messagebox.showinfo("提示", "当前没有账号。")
            return
        base = account_session_base(acc)
        p = base + ".session"
        if not os.path.exists(p):
            messagebox.showinfo("提示", "当前账号没有已保存的登录态。")
            return
        if not messagebox.askyesno(
                "确认", f"确定删除账号「{account_label(acc)}」的登录态吗？\n"
                        "删除后这个账号需要重新登录（会重新收到验证码），其它账号不受影响。"):
            return
        try:
            cli = self._clients.pop(acc["key"], None)
            if cli is not None:
                self.run_async(cli.disconnect())
            if acc["key"] == self._current_key:
                self.client = None
        except Exception:
            pass
        removed = 0
        for path in (p, base + ".session-journal"):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            except Exception as e:
                self.log_msg(f"⚠️ 删除 {os.path.basename(path)} 失败：{e}")
        self._refresh_account_ui()
        self.refresh_cache_info()
        self.set_status("⚪ 当前账号的登录态已删除，点「连接 / 登录」重新登录", "#7f8c8d")
        self.log_msg(f"🗑 已删除登录态文件（{removed} 个）。点「连接 / 登录」可重新登录该账号。")

    # ---------------- 外观 / 布局 ----------------
    def on_toggle_theme(self):
        try:
            self._apply_theme(bool(self.dark_var.get()))
        except Exception as e:
            self.log_msg(f"⚠️ 切换主题失败: {e}")
        self.save_config()

    def _apply_theme(self, dark=None):
        """浅色保持系统原生主题；深色切到 clam 并统一配色（ttk 只有 clam 系列支持改底色）。"""
        if dark is None:
            dark = bool(getattr(self, "_dark", False))
        self._dark = bool(dark)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam" if self._dark else self._default_theme)
        except Exception:
            pass
        if self._dark:
            bg, panel, fg, field, sel = "#1f2125", "#26282d", "#e8eaed", "#2c2f35", "#3a4450"
            head, sub = "#2f3238", "#9aa0a6"
        else:
            bg, panel, fg, field, sel = "#f5f6f7", "#ffffff", "#1f2328", "#ffffff", "#cfe3ff"
            head, sub = "#eaeef2", "#6b7280"
        try:
            style.configure(".", background=bg, foreground=fg, fieldbackground=field)
            style.configure("TFrame", background=bg)
            style.configure("TLabelframe", background=bg, bordercolor=head)
            style.configure("TLabelframe.Label", background=bg, foreground=fg)
            style.configure("TLabel", background=bg, foreground=fg)
            style.configure("TButton", padding=(6, 3))
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.configure("TNotebook", background=bg, borderwidth=0)
            style.configure("TNotebook.Tab", padding=(10, 4))
            style.configure("TEntry", fieldbackground=field, foreground=fg)
            style.configure("TCombobox", fieldbackground=field, foreground=fg)
            style.configure("TSpinbox", fieldbackground=field, foreground=fg)
            style.configure("Treeview", background=panel, fieldbackground=panel, foreground=fg, rowheight=22)
            style.map("Treeview", background=[("selected", sel)], foreground=[("selected", fg)])
            style.configure("Treeview.Heading", background=head, foreground=fg)
            style.configure("Horizontal.TProgressbar", background="#3f6fb5")
            self.root.configure(bg=bg)
        except Exception:
            pass
        try:
            self.log.configure(bg="#17191c" if self._dark else "#ffffff",
                               fg=fg if self._dark else "#1f2328",
                               insertbackground=fg)
        except Exception:
            pass

    def on_reset_layout(self):
        if not messagebox.askyesno("确认", "恢复默认布局（窗口大小 / 列宽 / 面板开合）？"):
            return
        cfg = self.config or {}
        for k in ("win_geometry", "col_widths", "left_width", "left_visible", "log_visible"):
            cfg.pop(k, None)
        self.root.geometry("1200x780")
        for c, w in (("sel", 50), ("name", 220), ("username", 160), ("type", 60),
                     ("members", 80), ("posts", 80), ("active", 72), ("folder", 120),
                     ("risk", 104), ("id", 110), ("safe", 92)):
            try:
                self.tree.column(c, width=w)
            except Exception:
                pass
        self._set_left_panel(True)
        self._set_log_panel(True)
        self.save_config()
        self.log_msg("↩️ 已恢复默认布局。")

    # ---------------- 表格右键菜单 ----------------
    def on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.channels:
            return
        try:
            self.tree.selection_set(iid)
        except Exception:
            pass
        self._ctx_iid = iid
        if not hasattr(self, "_ctx_menu") or self._ctx_menu is None:
            m = tk.Menu(self.root, tearoff=0)
            m.add_command(label="勾选 / 取消勾选", command=self._ctx_toggle_checked)
            m.add_command(label="加入 / 移出白名单（永不退出）", command=self._ctx_toggle_whitelist)
            m.add_separator()
            m.add_command(label="复制名称", command=lambda: self._ctx_copy("name"))
            m.add_command(label="复制用户名", command=lambda: self._ctx_copy("username"))
            m.add_command(label="复制 ID", command=lambda: self._ctx_copy("id"))
            m.add_command(label="复制 t.me 链接", command=self._ctx_copy_link)
            m.add_command(label="在浏览器打开", command=self._ctx_open_link)
            m.add_separator()
            m.add_command(label="只看该类型", command=self._ctx_filter_type)
            m.add_command(label="只看这种异常", command=self._ctx_filter_risk)
            m.add_command(label="为什么异常（写日志）", command=self._ctx_explain_risk)
            m.add_command(label="搜索该名称", command=self._ctx_search_name)
            m.add_separator()
            m.add_command(label="只退出这一个", command=self._ctx_leave_one)
            m.add_separator()
            m.add_command(label="只删除这一个会话（含聊天记录）", command=self._ctx_delete_one)
            m.add_command(label="只删除这一个（仅当我是创建者）", command=self._ctx_destroy_one)
            self._ctx_menu = m
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._ctx_menu.grab_release()
            except Exception:
                pass

    def _ctx_row(self):
        iid = getattr(self, "_ctx_iid", None)
        if not iid or not self.tree.exists(iid):
            return None, {}
        return iid, {c: str(self.tree.set(iid, c) or "") for c in ("name", "username", "type", "id")}

    def _ctx_toggle_checked(self):
        iid, _ = self._ctx_row()
        if not iid:
            return
        self.tree.set(iid, "sel", "☐" if self._is_checked(iid) else "☑")
        self._sel_hidden.discard(iid)
        self.update_count()
        self._maybe_apply_only_checked()

    def _ctx_copy(self, col):
        iid, vals = self._ctx_row()
        if not iid:
            return
        text = vals.get(col, "")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_msg(f"📋 已复制{self._col_titles.get(col, col)}: {text}")

    def _tme_link(self, iid):
        ent = self.channels.get(iid)
        uname = str(self.tree.set(iid, "username") or "").strip().lstrip("@")
        if uname:
            return f"https://t.me/{uname}"
        cid = getattr(ent, "id", None) or self.tree.set(iid, "id")
        return f"https://t.me/c/{cid}"

    def _ctx_copy_link(self):
        iid, _ = self._ctx_row()
        if not iid:
            return
        link = self._tme_link(iid)
        self.root.clipboard_clear()
        self.root.clipboard_append(link)
        self.log_msg(f"📋 已复制链接: {link}")

    def _ctx_open_link(self):
        iid, _ = self._ctx_row()
        if not iid:
            return
        import webbrowser
        link = self._tme_link(iid)
        try:
            webbrowser.open(link)
            self.log_msg(f"🌐 已用浏览器打开: {link}")
        except Exception as e:
            self.log_msg(f"❌ 打开链接失败: {e}")

    def _ctx_toggle_whitelist(self):
        iid, _ = self._ctx_row()
        if iid:
            self.toggle_whitelist([iid])

    def _ctx_filter_type(self):
        iid, vals = self._ctx_row()
        if not iid:
            return
        t = vals.get("type") or "全部"
        if t not in ("频道", "群组"):
            t = "全部"
        self.type_filter_var.set(t)
        self.on_quick_filter_changed()

    def _ctx_search_name(self):
        iid, vals = self._ctx_row()
        if not iid:
            return
        self.search_var.set(vals.get("name", ""))
        self._apply_search(verbose=True)

    def _ctx_filter_risk(self):
        """右键：只看这一行命中的异常类别。"""
        iid = getattr(self, "_ctx_iid", None)
        ent = self.channels.get(iid) if iid else None
        codes = self._risk.get(iid, ())
        if not codes:
            self.log_msg("🚫 这一行没有命中任何异常特征"
                         "（依据是服务端字段：封禁 / 违规受限 / 诈骗 / 假冒 / 停用 / 禁言 …）。")
            return
        self.set_risk_filter(codes)
        self.log_msg("   · 原因：" + risk_detail_text(ent))

    def _ctx_explain_risk(self):
        """右键：把这一个会话的异常特征逐条写进日志（回答「凭什么说它异常」）。"""
        iid = getattr(self, "_ctx_iid", None)
        if not iid:
            return
        try:
            name = self.tree.set(iid, "name")
        except Exception:
            name = str(iid)
        rows = risk_of(self.channels.get(iid))
        if not rows:
            self.log_msg(f"🚫 {name}：未命中任何异常特征。")
            return
        self.log_msg(f"🚫 {name} 的异常特征（{len(rows)} 条，全部来自服务端字段）：")
        for code, why in rows:
            self.log_msg(f"   · {RISK_LABEL.get(code, code)}：{why}")

    def _ctx_leave_one(self):
        iid, vals = self._ctx_row()
        if not iid:
            return
        self.leave_items([iid])

    def _ctx_delete_one(self):
        """右键单行删除会话：走与批量完全相同的确认与执行链路（不做任何快捷后门）。"""
        iid, vals = self._ctx_row()
        if not iid:
            return
        to_del, blocked = self.build_delete_plan([iid])
        if not to_del:
            reason = blocked[0]["reason"] if blocked else "未知原因"
            messagebox.showinfo("提示", f"该行受保护，未删除（{reason}）。")
            self.log_msg(f"⏭ 该行受保护，已跳过删除（{reason}）。")
            return
        if not self._confirm_delete_dialogs(to_del, blocked, ""):
            self.log_msg("已取消删除。")
            return
        self._set_run_buttons_disabled()
        self.run_async(self._delete_dialogs([iid], self._interval(), self._jitter()))

    def _ctx_destroy_one(self):
        """右键单行销毁：类型由该行自身决定，仍然是「类型 + 创建者 + 手打确认词」三道闸。"""
        iid, vals = self._ctx_row()
        if not iid:
            return
        ent = self.channels.get(iid)
        want_kind = session_kind(ent) if ent is not None else ""
        if not can_destroy_kind(want_kind):
            messagebox.showinfo("提示", "只有你创建的频道 / 群组才能被删除。")
            return
        self._destroy_entry([iid], want_kind)

    def apply_config_to_ui(self):
        # 注意：set 这些变量会触发自动保存，而自动保存会改写 self.config。
        # 因此先取一份本地快照，后续一律从快照读，避免"恢复过程中配置被自己覆盖"。
        # v1.2.0 起再加一道保险：整个恢复过程屏蔽自动保存 —— 因为 save_config 现在
        # 会用 UI 现值重建 per_account，第一个 set 就会把正要恢复的账号设置冲掉。
        cfg = dict(self.config or {})
        self._suppress_save = True
        try:
            # ---- 全局（所有账号共用）----
            self.api_id_var.set(cfg.get("api_id", ""))
            self.api_hash_var.set(cfg.get("api_hash", ""))
            self.proxy_enabled_var.set(cfg.get("proxy_enabled", False))
            self.proxy_type_var.set(cfg.get("proxy_type", "SOCKS5"))
            self.proxy_host_var.set(cfg.get("proxy_host", ""))
            self.proxy_port_var.set(cfg.get("proxy_port", ""))
            self.proxy_user_var.set(cfg.get("proxy_user", ""))
            self.proxy_pass_var.set(cfg.get("proxy_pass", ""))
            # 手机号框＝「要添加的账号」，预填当前账号的号码，方便照着加第二个
            acc = self._current_account()
            self.phone_var.set((acc or {}).get("phone") or cfg.get("phone", ""))
            # ---- 当前账号（白名单 / 筛选条件 / 抓取配置）----
            self._apply_account_state(self._per_account(self._current_key))
            self._sync_filter_preset_name()
            # 外观与布局（全局）
            self.remember_layout_var.set(bool(cfg.get("remember_layout", True)))
            self.dark_var.set(bool(cfg.get("dark_mode", False)))
            self._apply_theme(bool(self.dark_var.get()))
            self._apply_ui_layout(cfg)
            self._refresh_account_ui()
            self.refresh_cache_info()
            self.refresh_whitelist_info()
            self._refresh_all_safe_cells()
        finally:
            self._suppress_save = False

    # ---------------- 界面状态持久化 ----------------
    def _apply_ui_layout(self, cfg=None):
        """恢复上次的窗口大小 / 位置 / 列宽 / 面板开合。"""
        cfg = dict(cfg if cfg is not None else (self.config or {}))
        if not cfg.get("remember_layout", True):
            return
        geo = str(cfg.get("win_geometry", "") or "")
        if re.match(r"^\d+x\d+([+-]\d+[+-]\d+)?$", geo):
            try:
                self.root.geometry(geo)
            except Exception:
                pass
        widths = cfg.get("col_widths") or {}
        if isinstance(widths, dict):
            for c, w in widths.items():
                if c in self._col_index:
                    try:
                        self.tree.column(c, width=max(40, int(w)))
                    except Exception:
                        pass
        try:
            self._last_left_width = int(cfg.get("left_width", 340) or 340)
        except Exception:
            self._last_left_width = 340
        self._set_left_panel(bool(cfg.get("left_visible", True)))
        self._set_log_panel(bool(cfg.get("log_visible", True)))

    def _ui_state(self):
        """收集需要持久化的界面状态（记住布局关闭时返回空，避免写入脏数据）。"""
        st = {}
        if not bool(getattr(self, "remember_layout_var", None) and self.remember_layout_var.get()):
            return st
        try:
            if self.root.state() == "normal":
                st["win_geometry"] = self.root.geometry()
                if str(st["win_geometry"]).startswith("1x1"):
                    st.pop("win_geometry", None)   # 窗口尚未映射时 geometry() 是 1x1+0+0，忽略
        except Exception:
            pass
        try:
            st["col_widths"] = {c: int(self.tree.column(c, "width")) for c in self._col_index}
        except Exception:
            pass
        try:
            w = int(self.paned.sashpos(0)) if self._left_visible else int(self._last_left_width or 340)
            st["left_width"] = w if 200 < w < 900 else 340
        except Exception:
            st["left_width"] = 340
        st["left_visible"] = bool(self._left_visible)
        st["log_visible"] = bool(self._log_visible)
        return st

    # ---------------- 工具方法 ----------------
    def log_msg(self, msg):
        # 镜像到 stderr：调试版(--console)能实时看到日志，便于定位“没反应”问题
        if sys.stderr is not None:
            try:
                print(msg, file=sys.stderr, flush=True)
            except Exception:
                pass
        append_log_file(msg)      # 同时落盘，界面关掉后还能查
        self.root.after(0, lambda: self._log(msg))

    def _log(self, msg):
        try:
            self.log.insert("end", msg + "\n")
            if getattr(self, "log_autoscroll_var", None) is None or self.log_autoscroll_var.get():
                self.log.see("end")
            # 日志过长时裁掉最前面的旧内容，避免长时间运行后界面变卡
            try:
                if int(str(self.log.index("end-1c")).split(".")[0]) > 4000:
                    self.log.delete("1.0", "1500.0")
            except Exception:
                pass
        except Exception:
            pass

    # ---------------- 登录验证（界面内嵌输入框，避免弹窗不可见）----------------
    def enable_code_input(self, prompt_text, fut):
        """在主线程启用验证码/密码输入框，并绑定 future 等待用户提交。"""
        self.code_future = fut
        self.code_prompt_var.set(prompt_text)
        self.code_var.set("")
        self.code_entry.configure(state="normal")
        self.btn_code_submit.configure(state="normal")
        self.code_entry.focus_set()

    def disable_code_input(self, text="验证完成，等待下一步。"):
        self.code_entry.configure(state="disabled")
        self.btn_code_submit.configure(state="disabled")
        self.code_prompt_var.set(text)

    def set_status(self, text, color="#7f8c8d"):
        """更新顶部连接状态栏（灰=未连 / 橙=连接中 / 绿=已连 / 红=失败）。"""
        self.status_var.set(text)
        try:
            self.lbl_status.configure(foreground=color)
        except Exception:
            pass

    def on_submit_code(self):
        if self.code_future is None or self.code_future.done():
            return
        val = self.code_var.get().strip()
        if not val:
            messagebox.showwarning("提示", "请输入验证码或密码后再提交。")
            return
        self.code_var.set("")
        self.disable_code_input()
        fut = self.code_future
        self.code_future = None
        try:
            self.loop.call_soon_threadsafe(fut.set_result, val)
        except Exception as e:
            self.log_msg(f"❌ 提交验证码失败: {e}")

    def validate_inputs(self):
        try:
            api_id = int(self.api_id_var.get().strip())
        except Exception:
            messagebox.showerror("错误", "API ID 必须为数字，请在 my.telegram.org 获取。")
            return None
        api_hash = self.api_hash_var.get().strip()
        phone = self.phone_var.get().strip()
        if not api_hash or not phone:
            messagebox.showerror("错误", "请填写 API HASH 和手机号。")
            return None
        return api_id, api_hash, phone

    # ---------------- 事件循环线程 ----------------
    def ensure_loop(self):
        if self.loop is None:
            def runner():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()

            threading.Thread(target=runner, daemon=True).start()
            while self.loop is None:
                time.sleep(0.02)

    def run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ---------------- 连接 / 登录 ----------------
    def build_proxy(self):
        """在主线程构建代理元组；未启用返回 None，字段非法返回 "INVALID"。

        类型用字符串 socks5/socks4/http，Telethon 两种代理分支都识别。
        """
        if not self.proxy_enabled_var.get():
            return None
        host = self.proxy_host_var.get().strip()
        try:
            port = int(self.proxy_port_var.get().strip())
        except Exception:
            messagebox.showerror("错误", "代理端口必须为数字。")
            return "INVALID"
        if not host:
            messagebox.showerror("错误", "请填写代理主机地址。")
            return "INVALID"
        ptype = PROXY_TYPES.get(self.proxy_type_var.get(), "socks5")
        user = self.proxy_user_var.get().strip()
        pwd = self.proxy_pass_var.get().strip()
        if user and pwd:
            return (ptype, host, port, user, pwd)
        return (ptype, host, port)

    def on_proxy_toggle(self):
        state = "normal" if self.proxy_enabled_var.get() else "disabled"
        for w in (self.proxy_type_om, self.proxy_host_entry, self.proxy_port_entry,
                  self.proxy_user_entry, self.proxy_pass_entry, self.btn_proxy_test):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def on_proxy_test(self):
        if not self.proxy_enabled_var.get():
            messagebox.showinfo("提示", "请先勾选「启用代理」再测试。")
            return
        proxy = self.build_proxy()
        if proxy == "INVALID":
            return
        ptype, host, port = proxy[0], proxy[1], proxy[2]
        user = proxy[3] if len(proxy) > 3 else ""
        pwd = proxy[4] if len(proxy) > 4 else ""
        self.btn_proxy_test.configure(state="disabled")
        self.log_msg(f"正在测试代理 {host}:{port}（TCP 连通 + 出网到 Telegram）...")
        self.ensure_loop()
        self.run_async(self._test_proxy(ptype, host, port, user, pwd))

    async def _test_proxy(self, ptype, host, port, user, pwd):
        import socket

        # ---- 第 1 步：本地代理端口 TCP 连通 ----
        try:
            def tcp_blocking():
                s = socket.create_connection((host, port), timeout=8)
                s.close()
                return True

            tcp_ok = await self.loop.run_in_executor(None, tcp_blocking)
            tcp_err = ""
        except Exception as e:
            tcp_ok = False
            tcp_err = str(e)

        if not tcp_ok:
            self.log_msg(f"❌ 代理 {host}:{port} TCP 不可达：{tcp_err}")
            self.log_msg("   请确认代理软件已启动、端口填写正确（如 Clash 默认 7890）。")
            self.root.after(0, lambda: messagebox.showerror(
                "测试失败", f"代理端口 {host}:{port} 不可达：\n{tcp_err}\n\n请确认代理软件已启动、端口正确。"))
            self.root.after(0, lambda: self.btn_proxy_test.configure(state="normal"))
            return

        # ---- 第 2 步：真实地经代理出网到 Telegram ----
        try:
            def net_blocking():
                from python_socks.sync import Proxy
                auth = f"{user}:{pwd}@" if user and pwd else ""
                url = f"{ptype}://{auth}{host}:{port}"
                p = Proxy.from_url(url)
                sock = p.connect(dest_host="api.telegram.org", dest_port=443, timeout=12)
                import ssl
                ctx = ssl.create_default_context()
                ssock = ctx.wrap_socket(sock, server_hostname="api.telegram.org")
                ssock.sendall(
                    b"GET / HTTP/1.1\r\nHost: api.telegram.org\r\n"
                    b"User-Agent: tg-tool\r\nConnection: close\r\n\r\n"
                )
                data = b""
                while True:
                    chunk = ssock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > 1500:
                        break
                ssock.close()
                return data

            data = await self.loop.run_in_executor(None, net_blocking)
            net_ok = b"HTTP" in data[:20] or len(data) > 0
            net_err = ""
        except Exception as e:
            net_ok = False
            net_err = str(e)

        if net_ok:
            self.log_msg(f"✅ 代理 {host}:{port} TCP 可连通，且能经代理访问 Telegram 外网。可以连接。")
            self.root.after(0, lambda: messagebox.showinfo(
                "测试结果", f"代理 {host}:{port} 可正常访问 Telegram 外网。\n可以点击「连接/登录」了。"))
        else:
            self.log_msg(f"❌ 代理 {host}:{port} 端口虽通，但无法经其访问 Telegram 外网：{net_err}")
            self.log_msg("⚠ 请检查：①代理软件的节点/分流规则是否放行 Telegram；"
                         "②在「类型」里把 SOCKS5 与 HTTP 切换后再试；③确认该节点本身能上 Telegram。")
            self.root.after(0, lambda: messagebox.showerror(
                "测试失败",
                f"代理端口可连，但出网到 Telegram 失败：\n{net_err}\n\n"
                f"请检查代理节点/规则，或把「类型」在 SOCKS5 与 HTTP 之间切换后重试。"))
        self.root.after(0, lambda: self.btn_proxy_test.configure(state="normal"))

    def schedule_auto_connect(self):
        """启动时若「API 凭据完整」且「当前账号有登录态」，自动连接（无需验证码）。"""
        cfg = self.config or {}
        has_creds = bool(str(cfg.get("api_id", "")).strip()
                         and str(cfg.get("api_hash", "")).strip())
        if not has_creds:
            self.set_status("⚪ 尚未连接（请填写 API 信息后点「连接 / 登录」）", "#7f8c8d")
            return
        acc = self._current_account()
        if acc is None:
            self.set_status("⚪ 尚未添加账号（在左侧「账号」页添加）", "#7f8c8d")
            return
        if not os.path.exists(account_session_base(acc) + ".session"):
            self.set_status("⚪ 尚未连接（无登录态，点「连接 / 登录」后需输入验证码）", "#7f8c8d")
            return
        self.log_msg(f"检测到账号「{account_label(acc)}」的登录态，正在自动连接 ..."
                     "（session 有效则无需输入验证码）")
        self.set_status("⏳ 正在自动连接 ...", "#e67e22")
        self.root.after(200, self.on_connect)

    def validate_api_creds(self):
        """API ID / HASH 是**全局**设置：一个 Telegram 应用可以登录多个手机号。"""
        try:
            api_id = int(self.api_id_var.get().strip())
        except Exception:
            messagebox.showerror("错误", "API ID 必须为数字，请在 my.telegram.org 获取。")
            return None
        api_hash = self.api_hash_var.get().strip()
        if not api_hash:
            messagebox.showerror("错误", "请填写 API HASH（在 my.telegram.org 获取）。")
            return None
        return api_id, api_hash

    # ---------------- 账号：连接 / 添加 / 切换 ----------------
    def on_connect(self):
        """连接**当前账号**。session 有效时通常无需验证码。"""
        creds = self.validate_api_creds()
        if not creds:
            return
        api_id, api_hash = creds
        acc = self._current_account()
        if acc is None:
            self.on_add_account()          # 一个账号都没有 -> 走添加流程
            return
        key = acc["key"]
        pooled = self._clients.get(key)
        if pooled is not None and self.client is pooled:
            try:
                alive = pooled.is_connected()
            except Exception:
                alive = False
            if alive:
                self.set_status(f"🟢 已连接：{account_label(acc)}", "#27ae60")
                self.log_msg(f"账号「{account_label(acc)}」已处于连接状态，直接刷新列表。")
                self.on_refresh()
                return
        proxy = self.build_proxy()
        if proxy == "INVALID":
            return
        self.save_config()
        self._set_conn_busy(True)
        self.set_status(f"⏳ 正在连接「{account_label(acc)}」...", "#e67e22")
        if proxy:
            self.log_msg(f"正在连接 Telegram ...（账号 {account_label(acc)}，经代理 {proxy[1]}:{proxy[2]}）")
        else:
            self.log_msg("正在连接 Telegram ...（⚠ 未启用代理：直连可能被网络限制拦截）")
        self.ensure_loop()
        future = self.run_async(self._connect(api_id, api_hash, acc.get("phone", ""), proxy,
                                              session_base=account_session_base(acc),
                                              account_key=key))
        future.add_done_callback(self._on_connect_done)

    def on_add_account(self, phone=None):
        """添加并登录一个**新账号**。同一手机号会直接切过去，不会重复添加。"""
        creds = self.validate_api_creds()
        if not creds:
            return
        api_id, api_hash = creds
        if phone is None:
            phone = self.phone_var.get().strip()
        if not phone:
            messagebox.showwarning(
                "提示", "请先在左侧「账号」页填写要添加的手机号（含国家码，如 +8613800138000）。")
            try:
                self.nb_settings.select(self.tab_account)
                self.phone_entry.focus_set()
            except Exception:
                pass
            return
        phone = str(phone).strip()
        key = account_key_from_phone(phone)
        exist = self._account_by_key(key)
        if exist is not None:
            if messagebox.askyesno("已存在",
                                   f"账号「{account_label(exist)}」已在列表中。\n\n要切换到它吗？"):
                self.switch_account(key)
            return
        proxy = self.build_proxy()
        if proxy == "INVALID":
            return
        session_base = account_session_base({"key": key})
        if os.path.exists(session_base + ".session"):
            self.log_msg(f"发现 {os.path.basename(session_base)}.session 已存在 —— "
                         "该号码登录过，直接复用登录态，通常无需验证码。")
        self.save_config()
        self._set_conn_busy(True)
        self.set_status(f"⏳ 正在添加账号 {phone} ...", "#e67e22")
        self.log_msg(f"正在添加账号 {phone} ...")
        self.ensure_loop()
        future = self.run_async(self._connect(api_id, api_hash, phone, proxy,
                                              session_base=session_base, account_key=key))
        future.add_done_callback(self._on_connect_done)

    def _set_conn_busy(self, busy):
        try:
            self.btn_connect.configure(state="disabled" if busy else "normal")
        except Exception:
            pass

    def _on_connect_done(self, future):
        try:
            future.result()
        except OperationCancelled:
            self.log_msg("❌ 已取消登录。")
            self.root.after(0, lambda: self._set_conn_busy(False))
            self.root.after(0, lambda: self.set_status("🔴 未连接（已取消登录）", "#c0392b"))
        except Exception as e:
            self.log_msg(f"❌ 连接失败: {e}")
            self.root.after(0, lambda: self._set_conn_busy(False))
            self.root.after(0, lambda: self.set_status(
                f"🔴 未连接：{type(e).__name__}（点「连接 / 登录」可重试）", "#c0392b"))

    def _register_account(self, key, phone, session_name, me):
        """把登录成功的账号写入注册表（已存在则更新昵称 / 用户名 / user_id）。"""
        acc = self._account_by_key(key)
        if acc is None:
            acc = {"key": key, "phone": phone, "label": "", "username": "",
                   "user_id": None, "session": session_name}
            self._accounts.append(acc)
        if phone:
            acc["phone"] = phone
        if session_name:
            acc["session"] = session_name     # session 基名决定了读哪个登录态文件
        if me is not None:
            acc["user_id"] = getattr(me, "id", None)
            acc["username"] = getattr(me, "username", None) or ""
            full = " ".join(str(x) for x in (getattr(me, "first_name", None),
                                             getattr(me, "last_name", None)) if x).strip()
            if full:
                acc["label"] = full
            if not acc.get("phone") and getattr(me, "phone", None):
                acc["phone"] = "+" + str(me.phone)
        self._consume_pending_state(key)   # 首个账号接手「添加前调好的设置」
        return acc

    def _after_login(self, key, status_text, switched):
        """登录成功后的主线程收尾（Tk 变量只能在主线程碰）。"""
        self.set_status(status_text, "#27ae60")
        self.disable_code_input("✅ 已登录成功，无需再输入验证码。")
        self._set_conn_busy(False)
        if switched:
            self._reset_account_view()       # 换账号：上一个账号的列表 / 分组 / 角色全部作废
        self._refresh_account_ui()
        self.refresh_cache_info()
        self.refresh_whitelist_info()
        self.save_config()
        for b in (self.btn_refresh, self.btn_leave, self.btn_kw_leave,
                  self.btn_destroy, self.btn_export):
            try:
                b.configure(state="normal")
            except Exception:
                pass
        self.on_refresh()

    # ---------------- 账号：切换 / 移除 ----------------
    def _busy(self):
        """是否有任务在跑（加载列表 / 统计 / 退出）。切号必须等它停，否则两账号数据会串。"""
        return bool(getattr(self, "_progress_running", False))

    def _reset_account_view(self):
        """清掉「上一个账号」遗留的内存状态与列表。

        列表、分组、角色都是跟着**当前登录的那个账号**走的，换账号后必须整个作废，
        否则会出现「新账号的列表上挂着旧账号的分组列 / 角色标记」这种串号。
        """
        try:
            self._stats_cancel.set()
            self._list_cancel.set()
        except Exception:
            pass
        self._dlg_state = {}
        self._filters = []
        self._folder_names = []
        self._editable_folder_names = []
        self._role = {}
        try:
            self._begin_list()          # 清空表格 + 递增 epoch（作废仍在跑的后台回填）
        except Exception:
            pass
        try:
            self.folder_filter_var.set(FOLDER_ALL)
            self._rebuild_folder_menu()
        except Exception:
            pass
        # 旧账号的取消事件已经 set 过，给新账号一对全新的
        self._stats_cancel = threading.Event()
        self._list_cancel = threading.Event()

    def switch_account(self, key):
        """切换到另一个账号，**不退出**当前账号。

        当前账号的 client 留在 self._clients 池里不断开：登录态（session）与 TCP 连接
        都还在，切回来直接复用，连重新握手都不用，更不会要验证码。
        返回 True 表示确实执行了切换。
        """
        key = str(key or "")
        if not key or key == self._current_key:
            return False
        acc = self._account_by_key(key)
        if acc is None:
            messagebox.showerror("错误", "账号不存在，请重新添加。")
            return False
        if self._busy():
            messagebox.showinfo(
                "提示", "有任务正在进行（加载列表 / 获取统计 / 退出中）。\n"
                        "等它跑完或点「停止」后再切换账号，免得把两个账号的数据混在一起。")
            self._refresh_account_ui()      # 把下拉框拉回当前账号，别显示成切过去了
            return False
        old_key = self._current_key
        old_acc = self._account_by_key(old_key)
        tail = f"（「{account_label(old_acc)}」的登录态保留，不会退出）" if old_acc else ""
        self.log_msg(f"🔀 正在切换到账号「{account_label(acc)}」...{tail}")
        # 1) 先把当前账号的状态落盘：缓存文件 + config 里的 per_account[key]
        try:
            self.save_stats_cache()
            self.save_role_cache()
            self.save_config()
        except Exception as e:
            self.log_msg(f"⚠️ 保存当前账号状态失败（不影响切换）：{e}")
        if old_key and self.client is not None:
            self._clients[old_key] = self.client      # 保底：确认旧的 client 已入池
        # 2) 切到目标账号：client、缓存、白名单、配置一起换
        self._current_key = key
        self.client = self._clients.get(key)
        self._stats_cache = self.load_stats_cache()
        self._role_cache = self.load_role_cache()
        self._apply_account_state(self._per_account(key))
        self._sync_filter_preset_name()
        self.save_config()
        # 3) 界面：清掉上一个账号的列表，再连接 / 刷新
        self._reset_account_view()
        self._refresh_account_ui()
        self.refresh_cache_info()
        self.refresh_whitelist_info()
        self._refresh_all_safe_cells()
        conn, alive = self.client, False
        if conn is not None:
            try:
                alive = bool(conn.is_connected())
            except Exception:
                alive = False
        if alive:
            self.log_msg(f"✅ 已切换到「{account_label(acc)}」：直接复用池中保持的连接，无需重新登录。")
            self.set_status(f"🟢 已连接：{account_label(acc)}", "#27ae60")
            self.on_refresh()
        else:
            self.log_msg(f"已切换到「{account_label(acc)}」，正在用已保存的登录态连接 ...")
            self.on_connect()
        return True

    def remove_account(self, key, delete_files=False):
        """从列表移除账号。delete_files=True 时连登录态与缓存一起删。"""
        key = str(key or "")
        acc = self._account_by_key(key)
        if acc is None:
            return False
        is_current = (key == self._current_key)
        msg = f"确定要从列表移除账号「{account_label(acc)}」吗？"
        if delete_files:
            msg += ("\n\n⚠️ 同时删除它的登录态与缓存文件 —— "
                    "下次用这个手机号登录需要重新输入验证码。")
        else:
            msg += "\n\n登录态文件会保留：下次用同一手机号添加时可直接复用，不用再收验证码。"
        if not messagebox.askyesno("确认移除", msg):
            return False
        # 只有「用户明确要删账号」才 disconnect —— 切换账号是绝不断开的
        cli = self._clients.pop(key, None)
        if cli is not None:
            try:
                self.run_async(cli.disconnect())
            except Exception:
                pass
        if is_current:
            self.client = None
        if delete_files:
            base = account_session_base(acc)
            for p in (base + ".session", base + ".session-journal",
                      account_cache_file("stats_cache", key),
                      account_cache_file("role_cache", key)):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception as e:
                    self.log_msg(f"⚠️ 删除 {os.path.basename(p)} 失败：{e}")
        # 从注册表与 config 里摘掉
        self._accounts = [a for a in self._accounts if a.get("key") != key]
        pa = dict((self.config or {}).get("per_account") or {})
        pa.pop(key, None)
        self.config["per_account"] = pa
        self.config["accounts"] = list(self._accounts)
        if is_current:
            self._current_key = self._accounts[0]["key"] if self._accounts else None
            self.config["current_account"] = self._current_key
            self._stats_cache = self.load_stats_cache()
            self._role_cache = self.load_role_cache()
            self._apply_account_state(self._per_account(self._current_key))
            self._reset_account_view()
            self.set_status("⚪ 已移除当前账号（可在左侧「账号」页添加其它账号）", "#7f8c8d")
        self.save_config()
        self._refresh_account_ui()
        self.refresh_cache_info()
        self.refresh_whitelist_info()
        self.log_msg(f"🗑 已移除账号「{account_label(acc)}」"
                     + ("（含登录态与缓存）" if delete_files else "（登录态已保留）"))
        if is_current and self._current_key:
            self.on_connect()
        return True

    def _refresh_account_ui(self):
        """刷新工具栏下拉框与「账号」页的账号列表。"""
        texts = [account_label(a) for a in self._accounts]
        cur = self._current_account()
        try:
            self.account_combo.configure(values=texts or ["（未添加账号）"])
            self.account_var.set(account_label(cur) if cur else "（未添加账号）")
        except Exception:
            pass
        try:
            tree = self.acc_tree
            for iid in tree.get_children():
                tree.delete(iid)
            for a in self._accounts:
                k = a.get("key")
                conn = self._clients.get(k)
                alive = False
                try:
                    alive = bool(conn is not None and conn.is_connected())
                except Exception:
                    alive = False
                if k == self._current_key:
                    mark = "✅ 当前 · 已连接" if alive else "● 当前 · 未连接"
                else:
                    mark = "☑ 已保持登录" if alive else "— 未连接"
                tree.insert("", "end", iid=str(k), values=(account_label(a), a.get("phone", ""), mark))
        except Exception:
            pass
        try:
            self.lbl_account_info.configure(
                text=(f"已添加 {len(self._accounts)} 个账号 —— 切换时不会退出登录，"
                      "各账号的登录态与数据各自独立"
                      if self._accounts else
                      "尚未添加账号：填好下方手机号后点「添加账号并登录」"))
        except Exception:
            pass
        self._refresh_session_info()

    async def _connect(self, api_id, api_hash, phone, proxy,
                       session_base=None, account_key=None):
        """连接 / 登录。session_base 决定复用哪个账号的登录态文件。"""
        if not session_base:
            acc = self._current_account()
            session_base = account_session_base(acc) if acc else LEGACY_SESSION_FILE
        if proxy:
            self.log_msg(f"🔌 通过代理 {proxy[1]}:{proxy[2]} 连接 Telegram ...")
        else:
            self.log_msg("⚠ 未启用代理：直连可能因网络限制失败，建议先配置并测试代理。")
        client = TelegramClient(
            session_base, api_id, api_hash, proxy=proxy,
            connection_retries=3, timeout=15,
        )
        self.log_msg("正在连接 Telegram 数据中心 ...（若长时间无响应，多为代理无法访问 Telegram 或网络限制）")

        async def code_cb():
            fut = self.loop.create_future()
            self.root.after(0, lambda: self.enable_code_input("📩 请输入手机收到的验证码：", fut))
            return await fut

        async def pw_cb():
            fut = self.loop.create_future()
            self.root.after(0, lambda: self.enable_code_input("🔑 请输入两步验证密码：", fut))
            return await fut

        try:
            await client.start(phone=(phone or None), code_callback=code_cb, password=pw_cb)
        except OperationCancelled:
            raise
        except Exception as e:
            self.log_msg(f"❌ 连接/登录失败: {type(e).__name__} - {e}")
            emsg = str(e)
            if ("IncompleteReadError" in emsg or "Server closed the connection" in emsg
                    or "0 bytes read" in emsg or "ConnectionReset" in emsg):
                self.log_msg("⚠ 诊断：代理端口虽能连通，但无法经代理访问 Telegram 数据中心。"
                             "常见原因与对策：")
                self.log_msg("   1) 代理软件的节点/分流规则未放行 Telegram —— 先用浏览器或 TG 桌面端确认能上；")
                self.log_msg("   2) 在「代理设置」里把「类型」在 SOCKS5 与 HTTP 之间切换后再连；")
                self.log_msg("   3) 点「测试代理」确认能出网到 Telegram（新版本已支持真实出网测试）。")
            raise
        # 取账号信息：既用于状态栏，也喂给账号记录的昵称
        me = None
        try:
            me = await client.get_me()
        except Exception:
            pass
        key = account_key or self._current_key or account_key_from_phone(phone)
        acc = self._register_account(key, phone, os.path.basename(session_base), me)
        # ⚠️ 关键：client 只入池、**不 disconnect**。切走以后它还留在这里，
        # 切回来直接复用这个连接 —— 登录态与网络握手都不用重来。
        self._clients[acc["key"]] = client
        switched = (self._current_key != acc["key"])
        self._current_key = acc["key"]
        self.client = client
        self.log_msg("✅ 登录成功！")
        status_text = f"🟢 已连接：{account_label(acc)}"
        self.root.after(0, lambda: self._after_login(acc["key"], status_text, switched))

    # ---------------- 刷新频道 ----------------
    def on_refresh(self):
        if not self.client:
            return
        self.log_msg("正在获取频道列表 ...")
        self.run_async(self._refresh())

    async def _refresh(self):
        """分页拉取全部会话，每满一页就在界面追加显示（大账号也能渐进看到结果）。"""
        self._list_cancel.clear()      # 新任务开始：清掉上一轮遗留的停止信号
        self.root.after(0, lambda: self.start_task("正在加载频道列表…"))
        try:
            self.log_msg("正在获取频道 / 群组列表（分页加载）...")
            await self._load_folders()      # 先取分组，行插入时就能立刻填「分组」列
            self.root.after(0, self._begin_list)
            page = []
            scanned = 0
            found = 0
            kinds = {}          # 类型 -> 个数，加载完打一行分布，便于确认没漏类型
            gen = self.client.iter_dialogs(limit=None)
            try:
                async for d in gen:
                    if self._list_cancel.is_set():
                        self.log_msg(f"⏹ 已停止获取列表（已扫描 {scanned} 个会话）。")
                        break
                    ent = d.entity
                    scanned += 1
                    if ent is None:
                        continue
                    typ = session_kind(ent)
                    title = (getattr(ent, "title", None)
                             or getattr(ent, "first_name", None) or "")
                    uname = getattr(ent, "username", None) or ""
                    if typ in (KIND_USER, KIND_BOT) or not title:
                        # 私聊/机器人的实体名可能为空，用 Dialog 的显示名更准（含你给的联系人备注名）
                        title = (d.name or title or str(getattr(ent, "id", "")))
                    page.append((ent, title, uname, typ, getattr(ent, "id", "")))
                    kinds[typ] = kinds.get(typ, 0) + 1
                    # 记下规则判定所需状态（免打扰/未读/归档/是否联系人），供「分组」列使用
                    try:
                        pk = peer_key_of(ent)
                        if pk is not None:
                            self._dlg_state[pk] = dialog_state(d, typ, ent)
                    except Exception:
                        pass
                    if len(page) >= DIALOG_PAGE:
                        b, page = page, []
                        found += len(b)
                        self.root.after(0, lambda b=b, s=scanned: self._append_rows(b, s))
                        n, s = found, scanned
                        self.root.after(0, lambda n=n, s=s: self.set_progress(
                            n, None, f"正在加载列表：已加载 {n} 个（已扫描 {s} 个会话）"))
                else:
                    if page:
                        b, page = page, []
                        found += len(b)
                        self.root.after(0, lambda b=b, s=scanned: self._append_rows(b, s))
                    dist = "、".join(f"{k} {v}" for k, v in kinds.items())
                    self.log_msg(f"✅ 列表加载完成：扫描 {scanned} 个会话，全部收下 {found} 个"
                                 + (f"（{dist}）" if dist else "。"))
            finally:
                # 主动关闭分页生成器，避免“coroutine was never awaited”之类的告警
                aclose = getattr(gen, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass
        except OperationCancelled:
            self.log_msg("⏹ 已停止获取列表。")
        except Exception as e:
            self.log_msg(f"❌ 获取频道失败: {e}")
        finally:
            self._list_cancel.clear()
            self.root.after(0, self._finish_list)

    def _begin_list(self):
        self._tree_epoch += 1          # 废弃上一轮尚未完成的后台回填任务
        self._sort_col, self._sort_rev = None, False   # 新列表重置排序状态
        # 注意：被筛选隐藏的行不在 get_children() 里，必须按 channels 的键全删，避免残留幽灵项
        for iid in list(self.channels):
            try:
                self.tree.delete(iid)
            except Exception:
                pass
        for iid in self.tree.get_children():
            try:
                self.tree.delete(iid)
            except Exception:
                pass
        self.channels.clear()
        self._row_order = []
        self._visible = set()
        self._sel_hidden = set()
        self._role = {}             # 行 id 变了，角色映射随之作废（白名单按频道 id 存，不受影响）
        self._risk = {}             # 异常特征同样按行 id 记，行没了就必须一起清
        self.update_count()
        self._refresh_headings()

    def _tree_columns(self):
        """真实列名元组。真实界面永远走 tree.cget("columns")；
        测试桩（既没有 cget 也不暴露 columns）退回**历史 7 列** ——
        这样按位置构造行值、按位置取值的老桩仍然对齐，不会因为中间插入
        active / folder 两列而整体错位一格。"""
        try:
            cols = self.tree.cget("columns")
            return tuple(str(c) for c in cols) if cols else ("sel", "name", "username",
                                                             "type", "members", "posts", "id")
        except Exception:
            return ("sel", "name", "username", "type", "members", "posts", "id")

    def _row_values(self, title, uname, typ, cid, sel="☐"):
        # 私聊 / 机器人 没有「成员数」，「已不在」连消息都取不到 ——
        # 这类列直接给「-」，否则会被 _stat_num 当成「未获取」，白白发一轮必然失败的请求。
        members0 = "…" if is_member_kind(typ) else NOT_APPLICABLE
        posts0 = NOT_APPLICABLE if typ == KIND_GONE else "…"
        active0 = NOT_APPLICABLE if typ == KIND_GONE else "…"
        cell = {"sel": sel, "name": title, "username": uname, "type": typ,
                "members": members0, "posts": posts0, "active": active0,
                "folder": "", "risk": "", "id": cid, "safe": ""}
        return tuple(cell.get(c, "") for c in self._tree_columns())

    def _append_rows(self, rows, scanned=None):
        for ent, title, uname, typ, cid in rows:
            try:
                vals = self._row_values(title, uname, typ, cid)
            except Exception:
                # 测试桩（列更少 / 无 _col_index）走这条简单回退路径
                vals = ("☐", title, uname, typ, "…", "…", cid)
            iid = self.tree.insert("", "end", values=vals)
            self.channels[iid] = ent
            self._row_order.append(iid)
            self._visible.add(iid)
            try:
                # 角色缓存按频道 id 命中，插入时就能标出创建者/管理员，不必等统计跑完
                self._prime_role(iid, ent)
                self._refresh_safe_cell(iid)
                self._refresh_risk_cell(iid)
            except Exception:
                pass
            try:
                self._fill_folder_cell(iid, ent)   # 分组归属（filters 已在加载前拉好）
            except Exception:
                pass
        if self._filter_active:
            self.apply_filter(verbose=False)      # 新行按当前筛选条件决定是否显示
        else:
            self.update_count()
        self.lbl_stats.configure(
            text=f"已加载 {len(self.channels)} 个" + (f"（已扫描 {scanned} 个会话）" if scanned else "")
        )

    def _finish_list(self):
        n = len(self.channels)
        if n == 0:
            self.lbl_stats.configure(text="")
            self.finish_task("空闲（列表为空）")
            return
        if self._filter_active and not self.tree.get_children():
            self.log_msg("ℹ️ 当前筛选条件下暂无可显示的行：统计获取完成前多为未获取状态，"
                         "可取消勾选「隐藏统计未获取的行」，或等统计跑完。")
        self.finish_task(f"列表加载完成：{n} 个会话")
        # 异常特征统计（零请求：都用会话列表里已给的字段）。
        # 用 getattr 兜一层：测试桩 App 只实现自己关心的那部分接口，
        # 一个统计日志不该把整套加载流程带崩。
        _risk_log = getattr(self, "log_risk_summary", None)
        if callable(_risk_log):
            _risk_log()
        self.log_msg(f"已加载 {n} 个会话，开始读取用户数 / 作品数 / 活跃时间（命中本地缓存的不会发请求）...")
        self.run_async(self._fetch_stats(self._tree_epoch, force=False))

    def populate(self, items):
        """保留的兼容入口：一次性填表（供内部/测试调用）。"""
        self._begin_list()
        self._append_rows(items)
        self._finish_list()

    # ---------------- 安全防护：白名单 / 创建者 / 管理员 ----------------
    # 为什么需要：
    #   1) 创建者与管理员**退不出去**（Telegram 不允许），不提前识别就会白跑一遍并报错；
    #   2) 有些频道虽然能退，但用户不想退 —— 白名单把它们标记为「永不退出」。
    # 三者统一用 protected_reason() 判定：返回原因字符串即受保护，返回 None 才允许退出。
    def _wl(self):
        """白名单集合（对测试桩/早期调用做懒初始化）。"""
        return whitelist_of(self)

    def _cid_of(self, iid):
        """取该行对应的频道 id（字符串）；取不到返回空串。"""
        try:
            cid = getattr((getattr(self, "channels", None) or {}).get(iid), "id", None)
            if cid is None:
                cid = self.tree.set(iid, "id")
        except Exception:
            cid = None
        return str(cid).strip() if cid is not None else ""

    def is_whitelisted(self, iid):
        cid = self._cid_of(iid)
        return bool(cid) and cid in self._wl()

    def _role_of(self, iid):
        return (getattr(self, "_role", None) or {}).get(iid, "")

    def role_known(self, iid):
        """角色是否**已检测过**。_role 里存 "" 表示「检测过、是普通成员」，
        键不存在才是「还没检测」—— 两者用 _role_of 读会得到同一个 ""，
        不区分的话「删除我创建的频道」会把所有未检测的行当成非创建者整批跳过。"""
        return iid in (getattr(self, "_role", None) or {})

    def protected_reason(self, iid):
        """该行受保护的原因（白名单 / 创建者 / 管理员）；不受保护返回 None。"""
        if self.is_whitelisted(iid):
            return "白名单"
        r = self._role_of(iid)
        return ROLE_LABEL.get(r) if r in ROLE_LABEL else None

    def _prime_role(self, iid, ent):
        """插入行时按频道 id 命中角色缓存，立刻标出创建者/管理员（不必等统计跑完）。"""
        try:
            c = (getattr(self, "_role_cache", None) or {}).get(str(getattr(ent, "id", "")))
            if isinstance(c, list) and c:
                if getattr(self, "_role", None) is None:
                    self._role = {}
                self._role[iid] = c[0]
        except Exception:
            pass

    def _refresh_safe_cell(self, iid):
        """重画「安全」列并按状态整行着色。桩树没有该列时静默跳过。"""
        try:
            reason = self.protected_reason(iid)
        except Exception:
            return
        try:
            if "safe" in self._tree_columns():
                if reason == "白名单":
                    txt = "🔒 白名单"
                else:
                    txt = reason or ""
                self.tree.set(iid, "safe", txt)
        except Exception:
            pass
        try:
            if reason == "白名单":
                self.tree.item(iid, tags=("wl",))
            elif reason:
                self.tree.item(iid, tags=("unleaveable",))
            else:
                self.tree.item(iid, tags=())
        except Exception:
            pass

    def _refresh_all_safe_cells(self):
        for iid in list(self.channels):
            self._refresh_safe_cell(iid)

    # ---------------- 异常特征：列回填 / 筛选 / 统计（零请求） ----------------
    # 分类依据**全部来自服务端已经给我们的实体字段**（scam / fake / restricted /
    # restriction_reason / ChannelForbidden / join_request / banned_rights …），
    # 会话列表加载完就已经齐了，所以这一整套不发任何请求，也不会因为
    # 「按名字里的关键词猜」把正常频道大批误判成违规。
    def _refresh_risk_cell(self, iid):
        """回填「异常」列，并记下该行的类别（筛选与计数都从这里读）。桩树无该列时静默跳过。"""
        try:
            ent = self.channels.get(iid)
        except Exception:
            return
        try:
            self._risk[iid] = risk_codes_of(ent) if ent is not None else ()
        except Exception:
            self._risk[iid] = ()
        try:
            if "risk" in self._tree_columns():
                self.tree.set(iid, "risk",
                              risk_cell_text(ent) if ent is not None else "")
        except Exception:
            pass

    def _refresh_all_risk_cells(self):
        for iid in list(self.channels):
            self._refresh_risk_cell(iid)

    def _risk_counts(self):
        """按类别统计当前列表里的异常会话数（已从列表移除的行不再计入）。"""
        counts = {}
        for iid, codes in list(self._risk.items()):
            if iid not in self.channels:
                continue
            for c in codes:
                counts[c] = counts.get(c, 0) + 1
        return counts

    def _risk_hit_count(self, codes=None):
        """命中指定类别（None = 任意异常）的会话数。"""
        want = set(codes) if codes else None
        n = 0
        for iid, cs in list(self._risk.items()):
            if iid not in self.channels or not cs:
                continue
            if want is None or (set(cs) & want):
                n += 1
        return n

    def _rebuild_risk_menu(self):
        """建「🚫 异常 ▾」菜单。计数是**实时**的 —— 菜单弹出前重算（postcommand）。"""
        m = getattr(self, "_risk_menu", None)
        if m is None:
            return
        try:
            m.delete(0, "end")
        except Exception:
            return
        for label, codes in RISK_GROUPS:
            m.add_command(
                label=f"只看：{label}（{self._risk_hit_count(codes)}）",
                command=lambda c=codes: self.set_risk_filter(c))
        m.add_separator()
        cur = getattr(self, "_risk_filter", None)
        m.add_command(label="清除异常筛选" + ("（当前生效）" if cur else ""),
                      command=lambda: self.set_risk_filter(None))
        m.add_command(label=f"勾选全部异常行（{self._risk_hit_count()}）",
                      command=lambda: self.check_all_risk_rows())
        m.add_command(label="把异常统计写进日志", command=self.log_risk_summary)
        m.add_command(label="记住这些异常（写入白名单）", command=self.whitelist_risk_rows)

    def _refresh_risk_menu(self):
        """postcommand 回调：弹出前重算计数。"""
        self._rebuild_risk_menu()

    def set_risk_filter(self, codes):
        """只看某几类异常特征（codes 为空 = 清除该筛选）。只影响显示，不动数据与勾选。"""
        self._risk_filter = tuple(codes) if codes else None
        if self._risk_filter:
            names = "、".join(RISK_LABEL.get(c, c) for c in self._risk_filter)
            self.log_msg(f"🚫 只看异常「{names}」：命中 "
                         f"{self._risk_hit_count(self._risk_filter)} 个"
                         "（依据是服务端字段：封禁 / 违规受限 / 诈骗 / 假冒 / 停用 / 禁言 …）")
        else:
            self.log_msg("🚫 已清除异常筛选。")
        self.apply_filter(verbose=False)

    def check_all_risk_rows(self, codes=None):
        """勾选全部命中异常特征的行（含被当前筛选隐藏的）。

        与「全选」保持同一套规矩：默认跳过受保护行（白名单 / 创建者 / 管理员），
        但**把跳过数明说出来**，不做静默少选。"""
        want = set(codes) if codes else None
        hit = [iid for iid, cs in list(self._risk.items())
               if iid in self.channels and cs
               and (want is None or (set(cs) & want))]
        if not hit:
            self.log_msg("🚫 没有可勾选的行：当前列表里没有命中异常特征的会话。")
            return 0
        skip_protected = var_bool(self, "skip_protected_var", True)
        visible = set(self._visible_ids())
        skipped = 0
        checked = 0
        for iid in hit:
            if skip_protected and self.protected_reason(iid):
                skipped += 1
                try:
                    self.tree.set(iid, "sel", "☐")   # 受保护行绝不带着勾选
                except Exception:
                    pass
                self._sel_hidden.discard(iid)
                continue
            try:
                self.tree.set(iid, "sel", "☑")
            except Exception:
                continue
            checked += 1
            if iid in visible:
                self._sel_hidden.discard(iid)
            else:
                self._sel_hidden.add(iid)   # 勾上了但被筛选藏着 → 状态栏会提示
        self.update_count()
        msg = f"☑ 已勾选 {checked} 个异常会话"
        if skipped:
            msg += f"（另有 {skipped} 个是受保护项，已跳过：白名单 / 创建者 / 管理员）"
        self.log_msg(msg + "。分类依据是服务端字段，不是名字猜测。")
        if getattr(self, "_only_checked", False):
            self.apply_filter(verbose=False)
        return checked

    def whitelist_risk_rows(self, codes=None):
        """把命中异常特征的行加入白名单。

        用途：确认「这批异常里有一部分我还想留着」时，先圈出来再单独处置 ——
        白名单行在任何批量退出 / 删除里都会自动跳过。"""
        want = set(codes) if codes else None
        hit = [iid for iid, cs in list(self._risk.items())
               if iid in self.channels and cs
               and (want is None or (set(cs) & want))]
        if not hit:
            self.log_msg("🚫 没有可加入白名单的行：列表里没有命中异常特征的会话。")
            return 0
        n = self.set_whitelist(hit, True)
        self.log_msg(f"🔒 已把 {n} 个异常会话加入白名单（它们不会再被批量退出 / 删除）。")
        return n

    def log_risk_summary(self):
        """把异常特征统计打进日志：**零请求**，全部来自会话列表已给的字段。"""
        counts = self._risk_counts()
        total = sum(1 for iid in self.channels if iid in self._risk)
        hit = self._risk_hit_count()
        self.log_msg(f"🚫 异常特征扫描：共 {total} 个会话，命中 {hit} 个"
                     "（依据全部来自服务端已给的字段：零请求、不猜名字）")
        if not counts:
            self.log_msg("   · 未发现任何异常特征。")
            return 0
        for c, _short, label in RISK_META:
            n = counts.get(c, 0)
            if n:
                self.log_msg(f"   · {label}：{n}")
        self.log_msg("   · 点「🚫 异常 ▾」可按类别只看 / 一次勾选；"
                     "导出的 CSV 里「异常」列带完整原因。")
        return hit


    def set_whitelist(self, iids, on):
        """批量加入 / 移出白名单。返回实际改动的条数。"""
        wl = self._wl()
        changed = 0
        for iid in iids:
            cid = self._cid_of(iid)
            if not cid:
                continue
            if on and cid not in wl:
                wl.add(cid)
                changed += 1
            elif not on and cid in wl:
                wl.discard(cid)
                changed += 1
        if not changed:
            self._refresh_all_safe_cells()
            self.update_count()
            return 0
        if on:
            # 白名单行永远不该带着勾选 —— 否则会出现「勾了却没退出」的困惑
            for iid in iids:
                try:
                    self.tree.set(iid, "sel", "☐")
                except Exception:
                    pass
                self._sel_hidden.discard(iid)
        self._refresh_all_safe_cells()
        self.save_config()
        self.update_count()
        if self._filter_active:
            self.apply_filter(verbose=False)
        return changed

    def toggle_whitelist(self, iids):
        """已全在白名单里 → 全部移出；否则全部加入。"""
        iids = [i for i in iids if i in self.channels]
        if not iids:
            messagebox.showinfo("提示", "请先勾选（或右键）要加入白名单的频道。")
            return
        all_wl = all(self.is_whitelisted(i) for i in iids)
        n = self.set_whitelist(iids, on=not all_wl)
        if not all_wl:
            self.log_msg(f"🔒 已加入白名单 {n} 个（永不退出，全选会自动跳过）")
        else:
            self.log_msg(f"🔓 已移出白名单 {n} 个")

    def on_toggle_whitelist_checked(self):
        """选择条按钮：对「已勾选」的行批量加入 / 移出白名单。"""
        self.toggle_whitelist(self.selected_items())

    def clear_whitelist(self):
        wl = self._wl()
        n = len(wl)
        if not n:
            messagebox.showinfo("提示", "白名单为空。")
            return
        if not messagebox.askyesno("确认", f"确定清空白名单（{n} 个）吗？\n清空后这些频道将不再受保护。"):
            return
        wl.clear()
        self._refresh_all_safe_cells()
        self.save_config()
        self.update_count()
        if self._filter_active:
            self.apply_filter(verbose=False)
        self.log_msg(f"🧹 已清空白名单（{n} 个）。")

    def refresh_whitelist_info(self):
        try:
            n = len(self._wl())
            self.lbl_whitelist_info.configure(text=f"白名单 {n} 个频道（永不退出）")
            self.btn_clear_whitelist.configure(state="normal" if n else "disabled")
        except Exception:
            pass

    # ---------------- 分组（dialog filter） ----------------
    async def _load_folders(self):
        """拉取分组列表。失败不影响主流程 —— 只是分组列 / 分组筛选用不了。"""
        try:
            res = await self.client(tl_messages.GetDialogFiltersRequest())
        except Exception as e:
            self._filters = []
            self.log_msg(f"ℹ️ 未能读取分组列表（{type(e).__name__}: {e}），分组列与分组筛选暂不可用。")
            self.root.after(0, self.apply_folders_to_ui)
            return
        self._filters = list(getattr(res, "filters", res) or [])
        names, editable, ruled, detail = [], [], 0, []
        for f in self._filters:
            if not is_real_folder(f):
                continue
            t = folder_title(f)
            if not t:
                continue
            if t not in names:
                names.append(t)
            can_edit = is_editable_folder(f)
            if can_edit and t not in editable:
                editable.append(t)
            if folder_rules_on(f):
                ruled += 1
            # 逐个打印规则：分组「显示出了所有」几乎都是规则本身如此，把开关摊开写出来。
            manual = len(manual_peer_keys(f))
            detail.append(f"   · {t} —— 规则：{folder_rules_text(f)}"
                          + (f"，另手工加入 {manual} 个" if manual else "")
                          + ("" if can_edit else "　[共享分组，只能看、不能改成员]"))
        self._folder_names = names
        self._editable_folder_names = editable
        self.log_msg(f"✅ 已读取 {len(names)} 个分组"
                     + (f"（{len(names) - ruled} 个纯手工、{ruled} 个带自动规则）" if names else "。")
                     + ("：\n" + "\n".join(detail) if detail else ""))
        self.root.after(0, self.apply_folders_to_ui)

    def apply_folders_to_ui(self):
        """把分组名单填进筛选下拉与操作菜单。"""
        vals = (FOLDER_ALL, FOLDER_NONE) + tuple(getattr(self, "_folder_names", []) or [])
        try:
            self.cmb_folder.configure(values=vals)
        except Exception:
            pass
        try:
            if self.folder_filter_var.get() not in vals:
                # 选中的分组已被删除（在 Telegram 里改过）—— 回落到「全部分组」，
                # 真正的显隐重算交给随后必然执行的 apply_filter
                self.folder_filter_var.set(FOLDER_ALL)
        except Exception:
            pass
        self._rebuild_folder_menu()

    def _folder_names_of(self, ent):
        """该会话所属的分组名（可能多个 —— 一个会话可以同时在几个分组里）。"""
        key = peer_key_of(ent)
        if key is None or not getattr(self, "_filters", None):
            return []
        st = (getattr(self, "_dlg_state", None) or {}).get(key) or {}
        out = []
        for f in self._filters:
            try:
                if not is_real_folder(f):
                    continue
                t = folder_title(f)
                if t and t not in out and filter_matches(f, key, st):
                    out.append(t)
            except Exception:
                continue
        return out

    def _fill_folder_cell(self, iid, ent):
        try:
            names = self._folder_names_of(ent)
            self.tree.set(iid, "folder", "、".join(names))
        except Exception:
            pass

    def _refresh_folder_cells(self):
        """分组数据变化后，重刷所有行的「分组」列（分组筛选生效时还要重算显隐）。"""
        for iid, ent in list(getattr(self, "channels", {}).items()):
            self._fill_folder_cell(iid, ent)
        if getattr(self, "_filter_active", False):
            self.apply_filter(verbose=False)

    def _match_folder(self, iid):
        want = getattr(self, "_filter_folder", FOLDER_ALL)
        if not want or want == FOLDER_ALL:
            return True
        try:
            txt = str(self.tree.set(iid, "folder") or "").strip()
        except Exception:
            return True
        if want == FOLDER_NONE:
            return not txt
        return want in [x.strip() for x in txt.split("、") if x.strip()]

    def _active_num(self, iid):
        """活跃天数：数字=天数；「无消息」= 极大值（永远算「很久没动」）；未知=None。"""
        try:
            s = str(self.tree.set(iid, "active") or "").strip()
        except Exception:
            return None
        if s == ACTIVE_NONE:
            return ACTIVE_NEVER
        if s in ("", ACTIVE_UNKNOWN, NOT_APPLICABLE, "?"):
            return None
        try:
            return int(float(s))
        except (TypeError, ValueError):
            return None

    def _find_filter_by_name(self, name):
        """按分组名找本地那份 DialogFilter（只用于界面预览，真正写回时现读现改）。"""
        for f in (getattr(self, "_filters", None) or []):
            try:
                if is_real_folder(f) and folder_title(f) == name:
                    return f
            except Exception:
                continue
        return None

    def _folder_scope_iids(self, scope):
        """批量划分分组时「对哪些行生效」。
        - checked：已勾选的行（含被筛选隐藏的勾选）—— 传统的多选路径；
        - visible：**当前筛选 / 搜索结果**（列表里显示出来的行）—— 搜索完直接划分，不必先挨个勾；
        - all    ：全部会话（含被筛选隐藏的）。
        ⚠️ 三档都**不跳过受保护行**：分组归属跟能不能退出是两回事，
        白名单 / 创建者 / 管理员同样要能划进分组。
        """
        if scope == FOLDER_SCOPE_CHECKED:
            return self.selected_items_all()
        if scope == FOLDER_SCOPE_VISIBLE:
            return self._visible_ids()
        return list(self.channels)

    def _refresh_folder_menu(self):
        """菜单即将弹出时才重算内容 —— 这样「已勾选 N 个」「筛选结果 N 个」永远是当下的数字。
        （Tk 菜单是静态的，建好之后不会自己更新。）"""
        try:
            self._rebuild_folder_menu()
        except Exception:
            pass

    def _rebuild_folder_menu(self):
        m = getattr(self, "_folder_menu", None)
        if m is None:
            return
        try:
            m.delete(0, "end")
        except Exception:
            pass
        editable = list(getattr(self, "_editable_folder_names", None) or [])
        if not editable:
            m.add_command(label="（尚未连接，或还没在 Telegram 里建过分组）", state="disabled")
            m.add_separator()
            m.add_command(label="重新读取分组列表", command=self.on_reload_folders)
            return

        m.add_command(label="批量划分到分组…（可选范围）",
                      command=lambda: self._folder_bulk_dialog(True))
        m.add_command(label="批量移出分组…（可选范围）",
                      command=lambda: self._folder_bulk_dialog(False))
        m.add_separator()

        n_checked = len(self.selected_items_all())
        n_visible = len(self._visible_ids())
        for n, scope, label in ((n_checked, FOLDER_SCOPE_CHECKED, "已勾选的行"),
                                (n_visible, FOLDER_SCOPE_VISIBLE, "当前筛选 / 搜索结果")):
            sub_add = tk.Menu(m, tearoff=0)
            sub_del = tk.Menu(m, tearoff=0)
            for nm in editable:
                sub_add.add_command(
                    label=nm,
                    command=lambda n2=nm, s=scope: self.on_folder_action(True, n2, s))
                sub_del.add_command(
                    label=nm,
                    command=lambda n2=nm, s=scope: self.on_folder_action(False, n2, s))
            m.add_cascade(label=f"{label}（{n} 个）加入 ▸", menu=sub_add)
            m.add_cascade(label=f"{label}（{n} 个）移出 ▸", menu=sub_del)
        m.add_separator()
        m.add_command(label="重新读取分组列表", command=self.on_reload_folders)

    def on_reload_folders(self):
        if not self.client:
            messagebox.showinfo("提示", "请先连接账号。")
            return
        self.run_async(self._reload_folders())

    async def _reload_folders(self):
        await self._load_folders()
        self.root.after(0, self._refresh_folder_cells)

    def on_folder_action(self, add=True, folder_name=None, scope=FOLDER_SCOPE_CHECKED):
        """菜单快捷项：直接对「勾选的行」或「当前筛选 / 搜索结果」批量加入 / 移出分组。
        需要自己挑范围时走 _folder_bulk_dialog。"""
        name = folder_name or self.folder_filter_var.get()
        if not name or name in (FOLDER_ALL, FOLDER_NONE):
            messagebox.showinfo("提示", "请先指定一个具体分组（用「📁 分组 ▾ → 批量划分到分组…」）。")
            return
        if not self.client:
            messagebox.showinfo("提示", "请先连接账号。")
            return
        iids = self._folder_scope_iids(scope)
        if not iids:
            messagebox.showinfo("提示", f"「{FOLDER_SCOPE_LABEL.get(scope, scope)}」里没有可操作的行。")
            return
        verb = "加入" if add else "移出"
        extra = ""
        if scope == FOLDER_SCOPE_CHECKED and getattr(self, "_sel_hidden", None):
            extra = f"\n（其中 {len(self._sel_hidden)} 个已被筛选隐藏，会一并处理）"
        if not messagebox.askyesno(
                "确认",
                f"把「{FOLDER_SCOPE_LABEL.get(scope, scope)}」的 {len(iids)} 个会话{verb}"
                f"分组「{name}」？\n{self._kinds_text(self._count_kinds(iids))}{extra}\n"
                "（只改分组归属，不会退出或删除任何会话）"):
            return
        self.run_async(self._set_folder_membership(iids, name, add))

    def _folder_bulk_dialog(self, add=True, scope=None, folder_name=None):
        """「批量划分到分组」对话框：选目标分组 + 选范围 + 实时预览，一步到位。

        存在的理由有两个，都是实打实的：
        1) **搜索 / 筛选完直接整体划分**。以前必须先挨个勾选（或全选），
           而「全选」会跳过白名单 / 创建者 / 管理员 —— 那些行在分组这件事上
           本来毫无理由被跳过，结果是「明明搜出 40 个，只划进去 28 个」。
        2) 把「改的是哪个分组、动的是哪批行、影响多少个」在执行前一次讲清楚。
        """
        if not self.client:
            messagebox.showinfo("提示", "请先连接账号。")
            return
        editable = list(getattr(self, "_editable_folder_names", None) or [])
        if not editable:
            messagebox.showinfo(
                "提示",
                "还没有可用的分组。\n\n"
                "本工具只能把会话放进**已存在**的分组，不会替你在服务端新建分组\n"
                "（新建要自己生成分组 id，撞上其它设备刚建的分组就会把它覆盖掉，风险不值得冒）。\n\n"
                "请先在 Telegram 客户端里建好分组，再点「📁 分组 ▾ → 重新读取分组列表」。")
            return

        verb = "加入" if add else "移出"
        dlg = tk.Toplevel(self.root)
        dlg.title(f"批量{verb}分组")
        dlg.transient(self.root)
        try:
            dlg.geometry("640x380")
        except Exception:
            pass
        outer = ttk.Frame(dlg, padding=12)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"把一批频道 / 群组批量{verb}到指定分组",
                  font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(outer, text="只改分组归属，不会退出、也不会删除任何会话。",
                  foreground="#6b7280").pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="目标分组：").pack(side="left")
        fvar = tk.StringVar(value=folder_name or editable[0])
        ttk.Combobox(row, textvariable=fvar, state="readonly", width=24,
                     values=editable).pack(side="left", padx=4)
        lbl_rule = ttk.Label(row, text="", foreground="#6b7280")
        lbl_rule.pack(side="left", padx=8)

        box = ttk.LabelFrame(outer, text="对哪些行生效", padding=8)
        box.pack(fill="x")
        svar = tk.StringVar(value=scope or FOLDER_SCOPE_CHECKED)
        radios = []
        for key, label in FOLDER_SCOPES:
            rb = ttk.Radiobutton(box, text=f"{label}（{len(self._folder_scope_iids(key))} 个）",
                                 value=key, variable=svar)
            rb.pack(anchor="w", pady=1)
            radios.append(rb)

        lbl_preview = ttk.Label(outer, text="", foreground="#185FA5",
                                wraplength=600, justify="left")
        lbl_preview.pack(anchor="w", pady=(8, 0))
        lbl_warn = ttk.Label(outer, text="", foreground="#a32d2d",
                             wraplength=600, justify="left")
        lbl_warn.pack(anchor="w", pady=(3, 0))

        def update_preview(*_a):
            iids = self._folder_scope_iids(svar.get())
            nm = fvar.get()
            lbl_preview.configure(
                text=f"将{'加入' if add else '移出'}分组「{nm}」：{len(iids)} 个 —— "
                     + self._kinds_text(self._count_kinds(iids)))
            f = self._find_filter_by_name(nm)
            lbl_rule.configure(text=("规则：" + folder_rules_text(f)) if f is not None else "")
            warns = []
            if f is not None and not add:
                ruled = folder_rules_on(f)
                if ruled:
                    warns.append(
                        "⚠️「%s」带自动规则（%s）：靠规则进去的会话，光从手工名单里删掉是没用的，"
                        "会被写入**排除名单**才算真正移出（会占用 100 项配额）。"
                        % (nm, " + ".join(FOLDER_FLAG_LABEL[n] for n in ruled)))
            if svar.get() == FOLDER_SCOPE_ALL and add:
                warns.append(f"⚠️ 范围是「全部会话」：会把整个列表（含被筛选隐藏的行）都{verb}该分组。")
            lbl_warn.configure(text="\n".join(warns))
            ok_btn.configure(state="normal" if iids else "disabled")
            return len(iids)

        for rb in radios:
            rb.configure(command=update_preview)

        result = {"ok": False}
        bar = ttk.Frame(outer)
        bar.pack(fill="x", side="bottom", pady=(10, 0))
        ok_btn = ttk.Button(bar, text=f"确认{verb}")

        def on_ok():
            result["ok"] = True
            dlg.destroy()

        ok_btn.configure(command=on_ok)
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=4)
        ok_btn.pack(side="right", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.bind("<Return>", lambda e: on_ok())
        update_preview()
        try:
            dlg.grab_set()
        except Exception:
            pass
        dlg.wait_window()
        if not result["ok"]:
            return
        iids = self._folder_scope_iids(svar.get())
        if not iids:
            return
        self.run_async(self._set_folder_membership(iids, fvar.get(), add))

    async def _set_folder_membership(self, iids, folder_name, add=True):
        """把勾选的会话加入 / 移出指定分组。

        ⚠️ updateDialogFilter 是**整表替换**，两个硬要求：
        1) 每次现读现改，不能用缓存的快照 —— 否则会把你在 Telegram 客户端的改动覆盖掉；
        2) 在服务器返回的 DialogFilter 对象上**原地改字段**后原样回传。
           自己 new 一个 DialogFilter 再逐字段填，会丢掉 Telethon 这个版本不认识的字段
           （例如 color / title_noanimate），把分组的颜色和样式悄悄清掉。
        """
        self._last_rule_excluded = 0     # 每轮开始清零，避免上一轮的计数残留到日志里
        self.root.after(0, lambda: self.start_task(
            f"正在{'加入' if add else '移出'}分组「{folder_name}」…共 {len(iids)} 个"))
        try:
            res = await self.client(tl_messages.GetDialogFiltersRequest())
            target = None
            for f in list(getattr(res, "filters", res) or []):
                if is_real_folder(f) and folder_title(f) == folder_name:
                    target = f
                    break
            if target is None:
                self.log_msg(f"❌ 分组「{folder_name}」已不存在（可能刚在 Telegram 里改过），请重新读取分组。")
                self.root.after(0, lambda: self.finish_task("分组不存在"))
                return
            if not is_editable_folder(target):
                self.log_msg(f"❌ 「{folder_name}」是 Telegram 的**共享分组（chatlist）**，"
                             "它的成员由服务端维护，本工具只能用 chatlists 那套接口管，"
                             "用 updateDialogFilter 去改会把它弄坏 —— 已拦下，未发送任何请求。")
                self.root.after(0, lambda: self.finish_task("共享分组不支持改成员"))
                return
            peers, bad = [], 0
            for iid in iids:
                ent = self.channels.get(iid)
                if ent is None:
                    continue
                try:
                    peers.append(await self.client.get_input_entity(ent))
                except Exception:
                    bad += 1
            keys = {input_peer_key(p) for p in peers}
            keys.discard(None)
            if not keys:
                self.log_msg("❌ 没有可操作的会话（实体全部解析失败）。")
                self.root.after(0, lambda: self.finish_task("无可用会话"))
                return

            if add:
                # ⚠️ 加入时**什么都不删**。绝不能把已经在 pinned_peers 里的项挪进
                # include_peers —— 那等于顺手把用户的置顶取消了（pinned 只是置顶显示，
                # 但它一旦被挪走，客户端里的置顶就没了）。
                keep = list(getattr(target, "include_peers", None) or [])
                keep_pin = list(getattr(target, "pinned_peers", None) or [])
                have = {input_peer_key(p) for p in keep}
                have |= {input_peer_key(p) for p in keep_pin}
                have.discard(None)
                for p in peers:
                    k = input_peer_key(p)
                    if k is not None and k not in have:
                        keep.append(p)
                        have.add(k)
                # ★ 必须同时把它从 exclude_peers 里摘掉。
                # exclude_peers 的优先级**高于**手工加入（见 filter_matches）：
                # 只要它还在排除名单里，就算写进 include_peers 也进不去 ——
                # 表现为「日志说已加入，Telegram 里就是没有」。
                keep_exc = [p for p in (getattr(target, "exclude_peers", None) or [])
                            if input_peer_key(p) not in keys]
            else:
                # 移出：include_peers 与 pinned_peers 两处都要摘干净，
                # 否则从分组里「移出」了、却还留在置顶列表里。
                keep = [p for p in (getattr(target, "include_peers", None) or [])
                        if input_peer_key(p) not in keys]
                keep_pin = [p for p in (getattr(target, "pinned_peers", None) or [])
                            if input_peer_key(p) not in keys]
                keep_exc = [p for p in (getattr(target, "exclude_peers", None) or [])
                            if input_peer_key(p) not in keys]
                # ★ 关键：靠**自动规则**进来的会话，光从 include_peers 删掉是没用的 ——
                # 规则（比如「所有群组」）下一次仍会把它算进来，用户在 Telegram 里一看还在，
                # 但日志却已经报了「已移出 N 个」。必须把它写进 exclude_peers 才真正移出去。
                need_exc = set()
                for iid in iids:
                    ent = self.channels.get(iid)
                    if ent is None:
                        continue
                    k = peer_key_of(ent)
                    st = (getattr(self, "_dlg_state", None) or {}).get(k) or {}
                    if k is not None and folder_keeps_by_rule(target, st):
                        need_exc.add(k)
                have_exc = {input_peer_key(p) for p in keep_exc}
                have_exc.discard(None)
                for p in peers:
                    k = input_peer_key(p)
                    if k in need_exc and k not in have_exc:
                        keep_exc.append(p)
                        have_exc.add(k)
                self._last_rule_excluded = len(need_exc)
            if not keep and not keep_pin:
                # 服务端会直接报 FILTER_INCLUDE_EMPTY，提前拦下来并说清原因
                self.log_msg(f"⛔ 这样会把分组「{folder_name}」清空 —— Telegram 不允许 include_peers 为空，"
                             "已跳过。若确实不再需要这个分组，请在 Telegram 客户端里删除它。")
                self.root.after(0, lambda: self.finish_task("已跳过（会清空分组）"))
                return
            if len(keep) > FOLDER_SOFT_LIMIT:
                self.log_msg(f"⚠️ 分组「{folder_name}」将有 {len(keep)} 个手工会话，"
                             f"超过常见上限 {FOLDER_SOFT_LIMIT}，服务端可能拒绝。")
            if len(keep_exc) > FOLDER_EXCLUDE_LIMIT:
                self.log_msg(f"⚠️ 分组「{folder_name}」的排除名单将有 {len(keep_exc)} 项，"
                             f"超过常见上限 {FOLDER_EXCLUDE_LIMIT}，服务端可能拒绝。")

            target.include_peers = keep
            target.pinned_peers = keep_pin
            target.exclude_peers = keep_exc
            await self.client(tl_messages.UpdateDialogFilterRequest(id=target.id, filter=target))

            # 顺手把本地那份 _filters 里对应的对象换成刚提交的这份。
            # 不换的话，「分组」列会一直显示改动**之前**的归属 —— 明明加进去了却还是空的，
            # 直到用户再手动「重新读取分组列表」才对上。
            for idx, f in enumerate(getattr(self, "_filters", None) or []):
                if is_real_folder(f) and getattr(f, "id", None) == target.id:
                    self._filters[idx] = target
                    break

            msg = f"✅ 已把 {len(keys)} 个会话{'加入' if add else '移出'}分组「{folder_name}」"
            if bad:
                msg += f"（{bad} 个会话实体解析失败，已跳过）"
            n_rule = int(getattr(self, "_last_rule_excluded", 0) or 0)
            if not add and n_rule:
                msg += (f"\n　 其中 {n_rule} 个原本是被分组「{folder_name}」的**自动规则**收进去的，"
                        f"只从手工名单里删没用 —— 已写入排除名单，这才算真移出。")
            self.log_msg(msg)
            self.root.after(0, lambda: self.finish_task(f"分组操作完成：{len(keys)} 个"))
            self.root.after(0, self._refresh_folder_cells)
        except FloodWaitError as e:
            self.log_msg(f"⏳ 触发限流：需要等待 {e.seconds} 秒，请稍后重试。")
            self.root.after(0, lambda: self.finish_task("限流等待"))
        except Exception as e:
            self.log_msg(f"❌ 分组操作失败: {type(e).__name__}: {e}")
            self.root.after(0, lambda: self.finish_task("分组操作失败"))

    def _set_stat(self, epoch, iid, members, posts, role=None, active_ts=UNSET):
        """安全回填统计列：仅当列表代次未变、且该行仍存在时才写入。
        避免刷新列表 / 退出频道后，后台任务对已失效的行 ID 调用 set() 抛 TclError。
        若筛选生效，同时按新到的数值实时调整该行是否显示。
        active_ts 传 UNSET 表示「本次不动活跃列」（旧调用点与测试桩的 7 列表格没有该列）。"""
        if epoch != self._tree_epoch:
            return
        try:
            if not self.tree.exists(iid):
                return
            if role is not None:
                self._role[iid] = role      # 先落角色再重画，后面的筛选重判才能看到它
                self._refresh_safe_cell(iid)
            self.tree.set(iid, "members", members)
            self.tree.set(iid, "posts", posts)
        except Exception:
            return
        # 活跃列单独兜异常：测试桩的 Treeview 没有这一列，不能因此跳过下面的筛选重判
        if active_ts is not UNSET:
            try:
                self.tree.set(iid, "active", active_text(active_ts))
            except Exception:
                pass
        if not self._filter_active:
            return
        self._recheck_visibility(iid)

    def _apply_role(self, epoch, iid, role):
        """只回填角色（统计已命中缓存时走这条路），不碰成员数 / 作品数两列。"""
        if epoch != self._tree_epoch or role is None:
            return
        try:
            if not self.tree.exists(iid):
                return
        except Exception:
            return
        self._role[iid] = role
        self._refresh_safe_cell(iid)
        if getattr(self, "_filter_active", False):
            self._recheck_visibility(iid)

    def _recheck_visibility(self, iid):
        """筛选生效时，某行数据刚到（统计 / 角色），按新数据重新判断它该不该显示。"""
        try:
            keep = self._row_keep(iid, self._filter_members_lt, self._filter_posts_lt,
                                  bool(self.filter_unknown_var.get()))
            if keep and iid not in self._visible:
                self._reinsert_sorted(iid)
                self._visible.add(iid)
                self._sel_hidden.discard(iid)
                self.update_count()
            elif not keep and iid in self._visible:
                if self._is_checked(iid):
                    self._sel_hidden.add(iid)
                self.tree.detach(iid)
                self._visible.discard(iid)
                self.update_count()
        except Exception:
            pass

    async def _detect_role(self, ent):
        """判断当前账号在该频道的身份：creator / admin / ''（普通成员）。
        失败返回 None（未知），此时不写缓存，下次还会再试。"""
        try:
            perms = await self.client.get_permissions(ent, "me")
        except FloodWaitError as e:
            await asyncio.sleep(int(getattr(e, "seconds", 5)) + 1)
            try:
                perms = await self.client.get_permissions(ent, "me")
            except Exception:
                return None
        except Exception:
            return None
        if perms is None:
            return None
        try:
            if getattr(perms, "is_creator", False):
                return "creator"
            if getattr(perms, "is_admin", False):
                return "admin"
            return ""
        except Exception:
            return None

    async def _fetch_stats(self, epoch, force=False):
        """并发获取成员数(participants_count)与作品数(消息总数)，实时回填表格列。

        可扩展性设计：
        - **本地缓存**：`tg_stats_cache.json` 按频道 id 缓存结果，TTL 内不重复请求（force=True 时忽略缓存）。
        - **快路径**：若对话实体的 `participants_count` 已有值，直接采用，省掉一次 GetFullChannel 请求。
        - **有限并发**：信号量控制并发数（默认 6），比串行快一个数量级；分块提交任务避免一次创建上万协程。
        - **可停止 / 可废弃**：按「停止」置位 threading.Event；用户刷新列表则 epoch 变化，本任务自行退出。
        - **FloodWait 退避**：遇限流 sleep 后重试，而非直接失败。
        """
        from telethon.tl.functions.channels import GetFullChannelRequest

        items = list(self.channels.items())
        total = len(items)
        if total == 0:
            return
        cancel = self._stats_cancel
        if cancel is None or not hasattr(cancel, "clear"):
            cancel = threading.Event()
            self._stats_cancel = cancel
        cancel.clear()               # 新任务开始：清掉上一轮可能遗留的停止信号
        use_cache = bool(self.cache_var.get())
        ttl = self._safe_int(self.ttl_var, 24, 1, 720) * 3600
        role_ttl = ttl               # 角色与统计共用同一缓存有效期
        detect_roles = var_bool(self, "detect_role_var", True)
        role_cache = getattr(self, "_role_cache", None)
        if not isinstance(role_cache, dict):
            role_cache = {}
            try:
                self._role_cache = role_cache
            except Exception:
                pass
        now = time.time()

        todo = []
        cached = 0
        cached_role = 0
        for iid, ent in items:
            key = str(getattr(ent, "id", ""))
            kind = session_kind(ent)
            hit = None
            if use_cache and not force:
                c = self._stats_cache.get(key)
                if isinstance(c, list) and len(c) >= 3:
                    # 新格式 [members, posts, last_ts, 写入时间]；旧格式 [members, posts, 写入时间]
                    if len(c) >= 4:
                        saved_ts, last_ts = c[3], c[2]
                    else:
                        saved_ts, last_ts = c[2], UNSET
                    if (now - saved_ts) < ttl:
                        hit = (c[0], c[1], last_ts)
            role_hit = None
            rc = role_cache.get(key)
            if isinstance(rc, list) and len(rc) >= 2 and use_cache and not force and (now - rc[1]) < role_ttl:
                role_hit = rc[0]
            if hit is not None:
                cached += 1
                self.root.after(0, lambda i=iid, m=hit[0], p=hit[1], a=hit[2], ep=epoch, r=role_hit:
                                self._set_stat(ep, i, m, p, r, a))
            if role_hit is not None:
                cached_role += 1
            need_stats = hit is None
            # 白名单频道不必再花一次请求判角色（安全列已显示「🔒 白名单」）；
            # 并且只有频道/群组才有创建者/管理员概念 —— 对 User 调 get_permissions 必然失败，
            # 450 个私聊/机器人各重试 3 次会直接把自己送进 FloodWait。
            need_role = (detect_roles and role_hit is None
                         and is_member_kind(kind)
                         and key not in whitelist_of(self))
            if need_stats or need_role:
                todo.append((iid, ent, need_stats, need_role))

        role_desc = (f"角色已缓存 {cached_role} 个" if detect_roles else "角色检测已关闭")
        self.log_msg(f"📊 统计任务：共 {total} 个 —— 统计已缓存 {cached} 个，{role_desc}，"
                     f"需发请求 {len(todo)} 个。")
        self.root.after(0, lambda: self._set_stats_running(True))
        if todo:
            self.root.after(0, lambda: self.start_task(f"正在获取统计（{len(todo)} 个待请求）…"))
        else:
            # 全部命中缓存 -> 一个请求都不用发。仍然要走完「开始 / 结束」这一对，
            # 否则按钮会一直停在「统计中」的状态。
            self.root.after(0, lambda: self.start_task("统计命中缓存，无需请求"))
            self.root.after(0, self.refresh_cache_info)
        done = 0
        failed = 0

        async def worker(iid, ent, need_stats, need_role):
            nonlocal done, failed
            if cancel.is_set() or epoch != self._tree_epoch:
                return
            key = str(getattr(ent, "id", ""))
            kind = session_kind(ent)
            label = (getattr(ent, "title", None) or getattr(ent, "first_name", None)
                     or getattr(ent, "id", ""))
            members = posts = None
            last_ts = UNSET
            if need_stats:
                # ---- 成员数：只有频道 / 群组才有这个概念 ----
                if not is_member_kind(kind):
                    members = NOT_APPLICABLE          # 私聊 / 机器人 / 已不在
                else:
                    members = getattr(ent, "participants_count", None)
                    try:
                        members = int(members) if members else None
                    except Exception:
                        members = None
                    # GetFullChannelRequest 只对 Channel 有效；
                    # 普通群（Chat）自带 participants_count，取不到就不取，别发注定失败的请求。
                    if members is None and isinstance(ent, tl_types.Channel):
                        members = await self._retry(lambda: self.client(GetFullChannelRequest(ent)),
                                                    lambda r: int(getattr(r.full_chat, "participants_count", 0) or 0),
                                                    f"成员数 {label}")
                # ---- 作品数 + 活跃：同一个请求顺手拿两个值，不额外增加请求数 ----
                if kind == KIND_GONE:
                    posts, last_ts = NOT_APPLICABLE, NOT_APPLICABLE
                else:
                    async def _posts():
                        msgs = await self.client.get_messages(ent, limit=1)
                        t = getattr(msgs, "total", None)
                        if not t:
                            t = (msgs[-1].id if msgs else 0)
                        when = None
                        try:
                            if len(msgs) and msgs[0].date is not None:
                                when = msgs[0].date
                                if when.tzinfo is None:
                                    when = when.replace(tzinfo=datetime.timezone.utc)
                                when = when.timestamp()
                        except Exception:
                            when = None
                        return int(t or 0), when

                    got = await self._retry(_posts, lambda r: r, f"作品数 {label}")
                    if got is None:
                        posts, last_ts = None, UNSET
                    else:
                        posts, last_ts = got
                if members is None:
                    members = "?"
                if posts is None:
                    posts = "?"
                if members == "?" or posts == "?":
                    failed += 1
                else:
                    # 存时间戳而不是天数：天数隔天就永久偏小，必须在显示时才换算
                    self._stats_cache[key] = [members, posts, last_ts, time.time()]
            # ---- 角色：创建者 / 管理员退不出去，提前识别以便跳过 ----
            role = None
            if need_role:
                try:
                    role = await self._detect_role(ent)
                except Exception:
                    role = None      # 检测失败只影响角色判定，绝不能连累统计回填
                if role is not None:
                    role_cache[key] = [role, time.time()]
            done += 1
            if need_stats:
                self.root.after(0, lambda i=iid, m=members, p=posts, ep=epoch, r=role, a=last_ts:
                                self._set_stat(ep, i, m, p, r, a))
            else:
                self.root.after(0, lambda i=iid, ep=epoch, r=role: self._apply_role(ep, i, r))
            if done % 25 == 0 or done == len(todo):
                n, t = done, len(todo)
                self.log_msg(f"已获取统计 {n}/{t}")
                self.root.after(0, lambda n=n, t=t: self.lbl_stats.configure(text=f"统计进度 {n}/{t}"))
                self.root.after(0, lambda n=n, t=t: self.set_progress(n, t, f"正在获取统计 {n}/{t}"))

        sem = asyncio.Semaphore(max(1, self._safe_int(self.conc_var, 6, 1, 32)))

        async def guarded(iid, ent, need_stats, need_role):
            async with sem:
                await worker(iid, ent, need_stats, need_role)

        CHUNK = 100
        try:
            for i in range(0, len(todo), CHUNK):
                if cancel.is_set() or epoch != self._tree_epoch:
                    break
                await asyncio.gather(*(guarded(*t) for t in todo[i:i + CHUNK]),
                                     return_exceptions=True)
        finally:
            self.root.after(0, lambda: self._set_stats_running(False))
            self.root.after(0, self.refresh_cache_info)
            if cancel.is_set():
                self.root.after(0, lambda: self.finish_task(f"统计已停止：完成 {done}/{len(todo)}"))
                self.log_msg(f"⏹ 已停止统计：完成 {done}/{len(todo)}。")
            elif epoch != self._tree_epoch:
                self.root.after(0, lambda: self.finish_task("统计已废弃（列表已刷新）"))
                self.log_msg("ℹ️ 列表已刷新，上一轮统计任务已终止。")
            else:
                ok = done - failed
                if todo:
                    self.root.after(0, lambda: self.finish_task(
                        f"统计完成：请求 {ok}/{len(todo)} 成功，{cached} 个用缓存"))
                    self.log_msg(f"✅ 统计完成：实际请求 {len(todo)} 个 —— 成功 {ok} 个、失败 {failed} 个；"
                                 f"另有 {cached} 个直接用了本地缓存，共 {total} 个。")
                else:
                    # 关键：不能报「成功 0 个」—— 那看着像全军覆没，实际是一个请求都没发。
                    self.root.after(0, lambda: self.finish_task(
                        f"统计完成：{total} 个全部来自缓存（0 次请求）"))
                    self.log_msg(f"✅ 统计完成：{total} 个全部来自本地缓存，未发任何请求。"
                                 f"（想强制重取请点「获取 / 更新统计」）")
            try:
                self.save_stats_cache()
            except Exception:
                pass
            try:
                self.save_role_cache()
            except Exception:
                pass
            try:
                self.root.after(0, self.refresh_whitelist_info)
            except Exception:
                pass

    async def _retry(self, factory, extract, label, attempts=3):
        """带 FloodWait 退避的重试封装；返回 None 表示失败。"""
        for i in range(attempts):
            try:
                res = await factory()
                return extract(res)
            except FloodWaitError as e:
                wait = int(getattr(e, "seconds", 5)) + 1
                self.log_msg(f"⏳ 触发限流，等待 {wait}s 后重试（{label}）")
                await asyncio.sleep(wait)
            except Exception as e:
                if i == attempts - 1:
                    self.log_msg(f"⚠️ 获取失败（{label}）: {type(e).__name__} - {e}")
                else:
                    await asyncio.sleep(0.5 * (i + 1))
        return None

    def _set_stats_running(self, running):
        try:
            self.btn_stats.configure(state="disabled" if running else "normal")
            self.btn_stats_stop.configure(state="normal" if running else "disabled")
        except Exception:
            pass
        if not running:
            self.lbl_stats.configure(text=f"共 {len(self.channels)} 个")

    def on_fetch_stats(self):
        """手动重新获取统计：忽略缓存（用户明确要求刷新时）。"""
        if not self.client or not self.channels:
            messagebox.showinfo("提示", "请先连接并刷新频道列表。")
            return
        self.log_msg("开始重新获取统计（忽略缓存）...")
        self.run_async(self._fetch_stats(self._tree_epoch, force=True))

    def on_stop_stats(self):
        if self._stats_cancel is not None:
            self._stats_cancel.set()
            self.log_msg("⏹ 正在停止统计 ...")

    def _refresh_headings(self):
        """在列头显示排序方向箭头（▲升序 / ▼降序）。"""
        for c in self._col_index:
            if c == "sel":
                continue
            txt = self._col_titles[c]
            if c == self._sort_col:
                txt += " ▼" if self._sort_rev else " ▲"
            try:
                self.tree.heading(c, text=txt)
            except Exception:
                pass

    def _sort_key(self, col, iid):
        """排序键：数字列按数值；文字列按字典序；未获取到的值恒排最后。"""
        raw = self.tree.set(iid, col)
        s = "" if raw is None else str(raw).strip()
        if s == ACTIVE_NONE:
            # 「无消息」= 比任何天数都久，按数值参与排序（升序排最后、降序排最前），
            # 否则它会掉进下面的文字分支，跟「久没动静」的语义对不上。
            return (1, float(ACTIVE_NEVER), "")
        try:
            return (1, float(s), "")
        except (TypeError, ValueError):
            pass
        if s in ("", "…", "?", "-"):
            return (3, 0.0, "")
        return (2, 0.0, s.lower())

    def _apply_sort(self):
        """按当前 _sort_col/_sort_rev 重排**可见行**（未知值恒排末尾）。"""
        if self._sort_col:
            col = self._sort_col
            iids = list(self.tree.get_children())
            known = [i for i in iids if self._sort_key(col, i)[0] != 3]
            unknown = [i for i in iids if self._sort_key(col, i)[0] == 3]
            known.sort(key=lambda i: self._sort_key(col, i), reverse=self._sort_rev)
            for pos, iid in enumerate(known + unknown):
                self.tree.move(iid, "", pos)
        self._refresh_headings()

    def sort_by(self, col):
        """按列排序：点一次升序，再点一次降序。
        用 tree.move 原地重排行，保持 item id 不变 —— 正在进行的统计回填与勾选状态都不受影响。
        仅对当前可见（未被筛选隐藏）的行排序。"""
        if col == "sel" or col not in self._col_index:
            return
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col, self._sort_rev = col, False
        self._apply_sort()
        extra = "（仅对当前筛选出的行）" if self._filter_active else ""
        self.log_msg(f"已按「{self._col_titles[col]}」{'降序' if self._sort_rev else '升序'}排序{extra}。")

    def _reinsert_sorted(self, iid):
        """把刚恢复显示的行按当前排序列插到正确位置（未排序则追加到末尾）。"""
        if not self._sort_col:
            self.tree.move(iid, "", "end")
            return
        col = self._sort_col
        k = self._sort_key(col, iid)
        if k[0] == 3:                      # 未知值恒排末尾
            self.tree.move(iid, "", "end")
            return
        children = [c for c in self.tree.get_children() if c != iid]
        idx = len(children)
        for i, c in enumerate(children):
            kc = self._sort_key(col, c)
            if self._sort_rev:
                if kc < k:
                    idx = i
                    break
            else:
                if kc > k:
                    idx = i
                    break
        self.tree.move(iid, "", idx)

    # ---------------- 快捷筛选 ----------------
    def _parse_threshold(self, var):
        """把输入框解析成整数阈值；空/非法返回 None。容忍用户输入 <10 之类写法。"""
        s = (var.get() or "").strip()
        if not s:
            return None
        s = s.lstrip("<≤＜= ")
        try:
            return max(0, int(float(s)))
        except (TypeError, ValueError):
            return None

    def _stat_num(self, iid, col):
        """读取某行的统计值并转成数字；未获取（… / ? / 空）返回 None。"""
        raw = self.tree.set(iid, col)
        s = "" if raw is None else str(raw).strip()
        if s in ("", "…", "?", "-"):
            return None
        try:
            return int(float(s))
        except (TypeError, ValueError):
            return None

    def _search_tokens(self):
        try:
            q = self.search_var.get() or ""
        except Exception:
            q = ""
        q = q.replace("，", " ").replace(",", " ")
        return [t.lower() for t in q.split() if t.strip()]

    def _match_search(self, iid):
        """名称 / 用户名 包含全部关键词（多词为 AND，忽略大小写）。"""
        toks = self._search_tokens()
        if not toks:
            return True
        try:
            blob = (str(self.tree.set(iid, "name") or "") + "\n"
                    + str(self.tree.set(iid, "username") or "")).lower()
        except Exception:
            return True
        return all(t in blob for t in toks)

    def _type_selected(self):
        try:
            t = self.type_filter_var.get()
        except Exception:
            t = "全部"
        return t if t in TYPE_CHOICES else "全部"

    def _type_of(self, iid):
        """读某行「类型」列的文本。"""
        try:
            return str(self.tree.set(iid, "type") or "")
        except Exception:
            return ""

    def _match_type(self, iid):
        t = self._type_selected()
        if t == "全部":
            return True
        row = self._type_of(iid)
        if not row:
            return True          # 读不到类型（桩 / 异常）时不误伤，宁可多显示
        return row == t

    def _row_keep(self, iid, m_lt, p_lt, hide_unknown):
        """判断该行在当前筛选条件下是否应显示（多条件为 AND）。
        维度：搜索关键词 / 类型 / 分组 / 只看已勾选 / 用户数 / 作品数 / 活跃天数。"""
        if not self._match_search(iid):
            return False
        if not self._match_type(iid):
            return False
        if not self._match_folder(iid):
            return False
        if not self._match_risk(iid):
            return False
        if getattr(self, "_hide_protected", False) and self.protected_reason(iid):
            return False          # 「隐藏受保护项」：白名单 / 创建者 / 管理员不参与选择与退出
        if getattr(self, "_only_checked", False) and not self._is_checked(iid):
            return False
        a_gt = getattr(self, "_filter_active_gt", None)
        if m_lt is None and p_lt is None and a_gt is None:
            return True
        if m_lt is not None:
            # 「用户数」只对频道/群组有意义：私聊、机器人、已不在一律不参与该条件，
            # 否则一勾「隐藏统计未获取的行」就会把所有私聊整批藏掉。
            if is_member_kind(self._type_of(iid)):
                v = self._stat_num(iid, "members")
                if v is None:
                    return not hide_unknown
                if not (v < m_lt):
                    return False
        if p_lt is not None:
            v = self._stat_num(iid, "posts")
            if v is None:
                return not hide_unknown
            if not (v < p_lt):
                return False
        if a_gt is not None:
            v = self._active_num(iid)
            if v is None:
                return not hide_unknown
            if not (v > a_gt):        # 「活跃天数 > N」= 超过 N 天没有任何消息
                return False
        return True

    def _match_risk(self, iid):
        """异常特征筛选：只要该行命中了所选类别之一就保留。
        数据来自 _risk（插入行时由服务端字段算好），所以这里是纯内存判断。"""
        want = getattr(self, "_risk_filter", None)
        if not want:
            return True
        return bool(set(self._risk.get(iid, ())) & set(want))

    def _threshold_of(self, attr):
        """安全解析某个阈值输入框（属性可能不存在 —— 测试桩只有部分控件）。"""
        var = getattr(self, attr, None)
        if var is None:
            return None
        try:
            return self._parse_threshold(var)
        except Exception:
            return None

    def _sync_filter_preset_name(self):
        """让预设下拉框与当前几个输入框保持一致（不匹配时显示「自定义…」）。"""
        m_lt = self._parse_threshold(self.filter_members_var)
        p_lt = self._parse_threshold(self.filter_posts_var)
        a_gt = self._threshold_of("filter_active_var")
        for nm, m, p, a in FILTER_PRESETS:
            if m == m_lt and p == p_lt and a == a_gt:
                self.filter_preset_var.set(nm)
                return
        self.filter_preset_var.set(FILTER_CUSTOM)

    def on_filter_preset(self):
        """选择预设：把预设阈值填入输入框并立即应用。"""
        name = self.filter_preset_var.get()
        if name == FILTER_CUSTOM:
            return
        for nm, m, p, a in FILTER_PRESETS:
            if nm == name:
                self.filter_members_var.set("" if m is None else str(m))
                self.filter_posts_var.set("" if p is None else str(p))
                try:
                    self.filter_active_var.set("" if a is None else str(a))
                except Exception:
                    pass
                break
        self.apply_filter()

    def apply_filter(self, verbose=True):
        """按「用户数 / 作品数 / 活跃天数 / 类型 / 分组」筛选：不满足条件的行用 detach 隐藏。
        隐藏只影响显示，**行 ID 与数据都保留** —— 统计回填、勾选状态、排序位置都不受影响。"""
        m_lt = self._parse_threshold(self.filter_members_var)
        p_lt = self._parse_threshold(self.filter_posts_var)
        a_gt = self._threshold_of("filter_active_var")
        hide_unknown = bool(self.filter_unknown_var.get())
        self._only_checked = bool(getattr(self, "only_checked_var", None) and self.only_checked_var.get())
        tsel = self._type_selected()
        has_kw = bool(self._search_tokens())
        self._hide_protected = var_bool(self, "hide_protected_var", False)
        try:
            folder = self.folder_filter_var.get() or FOLDER_ALL
        except Exception:
            folder = FOLDER_ALL
        self._filter_folder = folder
        self._filter_active_gt = a_gt
        self._filter_active = (m_lt is not None or p_lt is not None or a_gt is not None
                               or has_kw or tsel != "全部" or self._only_checked
                               or self._hide_protected or folder != FOLDER_ALL
                               or bool(getattr(self, "_risk_filter", None)))
        self._filter_members_lt, self._filter_posts_lt = m_lt, p_lt
        self._sync_filter_preset_name()

        attached = list(self.tree.get_children())
        attached_set = set(attached)
        # 保持现有可见顺序（含已排序列）；新恢复显示的行按插入顺序排在其后
        desired = [i for i in attached
                   if i in self.channels and self._row_keep(i, m_lt, p_lt, hide_unknown)]
        for iid in self._row_order:
            if iid in attached_set or iid not in self.channels:
                continue
            if self._row_keep(iid, m_lt, p_lt, hide_unknown):
                desired.append(iid)
        desired_set = set(desired)

        for iid in attached:
            if iid not in desired_set:
                if iid in self.channels and self._is_checked(iid):
                    self._sel_hidden.add(iid)   # 勾选保留，但退出时不会处理 → 状态栏会提示
                try:
                    self.tree.detach(iid)
                except Exception:
                    pass
        for pos, iid in enumerate(desired):
            try:
                self.tree.move(iid, "", pos)
            except Exception:
                pass
        self._visible = desired_set
        # 隐藏勾选集合只保留「仍被隐藏且仍存在」的行（重新显示出来的行要移出去）
        self._sel_hidden = {i for i in self._sel_hidden
                            if i in self.channels and i not in desired_set}

        # 筛选后重新套用当前排序，保证列头箭头与实际顺序一致（新恢复的行落到正确位置）
        self._apply_sort()

        self.update_count()
        if verbose:
            total = len(self.channels)
            if self._filter_active:
                cond = []
                if m_lt is not None:
                    cond.append(f"用户数<{m_lt}")
                if p_lt is not None:
                    cond.append(f"作品数<{p_lt}")
                if a_gt is not None:
                    cond.append(f"活跃>{a_gt}天")
                if has_kw:
                    cond.append(f"搜索「{self.search_var.get().strip()}」")
                if tsel != "全部":
                    cond.append(f"类型={tsel}")
                if folder != FOLDER_ALL:
                    cond.append(f"分组={folder}")
                if self._only_checked:
                    cond.append("只看已勾选")
                if getattr(self, "_hide_protected", False):
                    cond.append("隐藏受保护项")
                if getattr(self, "_risk_filter", None):
                    cond.append("异常=" + "、".join(
                        RISK_LABEL.get(c, c) for c in self._risk_filter))
                extra = "" if hide_unknown else "（统计未获取的行也一并保留）"
                if self._sel_hidden:
                    extra += f" ⚠️ {len(self._sel_hidden)} 个已勾选的行被隐藏，退出时不会处理"
                self.log_msg(f"🔍 筛选「{' 且 '.join(cond)}」：显示 {len(desired)} / 共 {total} 个{extra}")
            else:
                self.log_msg(f"已清除筛选：显示全部 {total} 个。")
        return len(desired)

    def clear_filter(self):
        self.filter_members_var.set("")
        self.filter_posts_var.set("")
        # 搜索 / 类型 / 分组 / 活跃 / 只看已勾选 也一并复位，
        # 避免「清除」之后仍有行被隐藏、却看不出隐藏原因
        try:
            self.search_var.set("")
            self.type_filter_var.set("全部")
            self.only_checked_var.set(False)
            self.hide_protected_var.set(False)
            self.filter_active_var.set("")
            self.folder_filter_var.set(FOLDER_ALL)
        except Exception:
            pass
        self._only_checked = False
        self._hide_protected = False
        self._filter_active_gt = None
        self._filter_folder = FOLDER_ALL
        self.apply_filter()

    # ---------------- 选择逻辑 ----------------
    # 约定：「全选 / 反选」的范围 = 当前**显示中**的行（筛选生效时就是筛选结果）；
    #       被筛选隐藏的行勾选状态会被保留，但退出时不会处理 —— 用 _sel_hidden 追踪并提示。
    def _is_checked(self, iid):
        try:
            return self.tree.set(iid, "sel") == "☑"
        except Exception:
            return False

    def on_tree_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if item and col == "#1":
            self.tree.set(item, "sel", "☐" if self._is_checked(item) else "☑")
            self._sel_hidden.discard(item)   # 点击的必然是可见行
            self.update_count()
            self._maybe_apply_only_checked()  # 「只看已勾选」时，取消勾选应立即消失

    def _visible_ids(self):
        return list(self.tree.get_children())

    def set_all(self, val, scope="visible", skip_protected=None):
        """勾选 / 取消勾选。
        scope="visible"：仅当前显示中的行（筛选生效时即「筛选结果」）；
        scope="all"    ：含被筛选隐藏的行（用于彻底清空，避免隐藏行残留勾选）。
        全选时默认**自动跳过受保护行**（白名单 / 创建者 / 管理员）—— 它们不是退不掉就是不该退。
        ⚠️ 但「受保护」只对退出 / 删除有意义：分组归属跟能不能退出毫无关系，
        所以分组相关操作传 skip_protected=False，否则白名单里的频道永远勾不上，
        也就永远划不进分组（用户会以为是功能坏了）。
        取消勾选（val=False）不跳，永远能一键清干净。"""
        mark = "☑" if val else "☐"
        if scope == "all":
            iids = list(self.channels)
        else:
            iids = self._visible_ids()
        if skip_protected is None:
            skip_protected = var_bool(self, "skip_protected_var", True)
        skip = bool(val) and bool(skip_protected)
        skipped = 0
        for iid in iids:
            if skip and self.protected_reason(iid):
                skipped += 1
                try:
                    self.tree.set(iid, "sel", "☐")   # 确保受保护行绝不带着勾选
                except Exception:
                    pass
                self._sel_hidden.discard(iid)
                continue
            try:
                self.tree.set(iid, "sel", mark)
            except Exception:
                pass
        if scope == "all":
            self._sel_hidden = set() if not val else (set(self.channels) - set(self._visible_ids()))
        else:
            # 只动了可见行，隐藏行的勾选保持不变
            pass
        self.update_count()
        # 「只看已勾选」开着时清空勾选，列表必须立刻跟着变空 —— 否则显示的是一堆
        # 勾不上、也处理不了的行，看着像功能坏了。（这段以前写在 return 之后，是死代码）
        if getattr(self, "_only_checked", False) and not val:
            self.apply_filter(verbose=False)
            self.log_msg("ℹ️ 「只看已勾选」已开启：勾选被清空后列表为空，点「清除」可恢复显示。")
        return skipped

    def select_all_visible(self):
        """全选当前显示的行（筛选生效时=全选筛选结果），隐藏行不受影响。"""
        shown = len(self.tree.get_children())
        if shown == 0:
            messagebox.showinfo("提示", "当前没有可勾选的行：可能筛选后为空，或列表尚未加载。")
            return
        self.set_all(True, scope="visible")
        scope = "筛选结果" if (self._filter_active or shown != len(self.channels)) else "全部"
        picked = len(self.selected_items())
        extra = ""
        if shown > picked:
            extra = (f"，跳过 {shown - picked} 个受保护项（白名单 / 创建者 / 管理员）"
                     "── 只是不参与退出 / 删除；要划进分组请用「📁 分组 ▾」或 Ctrl+G，不受此限制")
        self.log_msg(f"☑ 已全选{scope} {picked} 个{extra}。")

    def apply_filter_and_select_all(self):
        """应用筛选条件并立即全选筛选结果 —— 一步到位，便于按条件批量退出。
        受保护行同样会被跳过，因此日志里报的是**实际勾上**的数量。"""
        shown = self.apply_filter(verbose=True)
        if not shown:
            messagebox.showinfo("提示", "当前筛选条件下没有可显示的行，未执行全选。")
            return
        self.set_all(True, scope="visible")
        picked = len(self.selected_items())
        if picked:
            self.log_msg(f"☑ 已全选筛选结果 {picked} 个，可直接点「退出 / 删除选中」。")
        else:
            messagebox.showinfo("提示", "筛选结果全部受保护（白名单 / 创建者 / 管理员），没有可勾选的行。")

    def invert(self):
        """反选当前显示的行（隐藏行的勾选状态不变）。受保护行不参与反选，保持未勾选。"""
        skip = var_bool(self, "skip_protected_var", True)
        for iid in self.tree.get_children():
            if skip and self.protected_reason(iid):
                try:
                    self.tree.set(iid, "sel", "☐")
                except Exception:
                    pass
                continue
            try:
                self.tree.set(iid, "sel", "☐" if self._is_checked(iid) else "☑")
            except Exception:
                pass
        self.update_count()

    def selected_items(self):
        """当前显示中被勾选的行 —— 退出/导出以此为准（隐藏行不会被处理）。"""
        return [iid for iid in self.tree.get_children() if self._is_checked(iid)]

    def selected_items_all(self):
        """所有行中被勾选的数量（含被筛选隐藏的）。"""
        return self.selected_items() + [i for i in self.channels if i in self._sel_hidden]

    def _count_kinds(self, iids):
        """按类型数一遍。iids 可以是全部会话，也可以是当前显示出来的行。"""
        counts = {}
        for iid in iids:
            ent = self.channels.get(iid) if hasattr(self.channels, "get") else None
            k = session_kind(ent) if ent is not None else KIND_OTHER
            counts[k] = counts.get(k, 0) + 1
        return counts

    def _kinds_text(self, counts):
        parts = []
        for kind, icon in KIND_ICON:
            n = counts.get(kind, 0)
            if kind == KIND_OTHER and not n:
                continue          # 「其它」为 0 时不占位，免得干扰
            parts.append(f"{icon} {kind} {n} 个")
        return "　".join(parts)

    def _stats_text(self):
        total = len(self.channels)
        visible = list(self.tree.get_children())
        if self._filter_active or len(visible) != total:
            head = f"显示 {len(visible)} / 共 {total} 个"
            body = self._kinds_text(self._count_kinds(visible))
        else:
            head = f"共 {total} 个"
            body = self._kinds_text(self._count_kinds(list(self.channels.keys())))
        return f"{head}　│　{body}" if body else head

    def _update_stats(self):
        """刷新列表上方的类型统计条。任何异常都不能打断列表加载/统计回填。"""
        try:
            var = getattr(self, "stats_var", None)
            if var is None:
                return
            var.set(self._stats_text())
        except Exception:
            pass

    def update_count(self):
        total = len(self.channels)
        shown = len(self.tree.get_children())
        sel = len(self.selected_items())
        # getattr 兜底：本方法可能被加载流程早期/测试桩调用，缺属性时不应打断列表加载
        hidden_sel = len(getattr(self, "_sel_hidden", ()) or ())
        protected = 0
        blocked = 0
        # 没有任何防护数据时直接跳过整表扫描（本方法在统计回填时会被高频调用）
        if getattr(self, "_whitelist", None) or getattr(self, "_role", None):
            try:
                for iid in self.tree.get_children():
                    if self.protected_reason(iid):
                        protected += 1
                        if self._is_checked(iid):
                            blocked += 1
            except Exception:
                pass
        if self._filter_active or shown != total:
            txt = f"显示 {shown} / 共 {total} 个，已选 {sel} 个"
        else:
            txt = f"共 {total} 个，已选 {sel} 个"
        if hidden_sel:
            txt += f"（另有 {hidden_sel} 个勾选被筛选隐藏）"
        if blocked:
            txt += f"（其中 {blocked} 个受保护，退出会跳过）"
        self.lbl_count.configure(text=txt)
        # 搜索命中数 / 待退出数（新布局里的实时反馈）
        try:
            if self._search_tokens() or self._type_selected() != "全部":
                self.lbl_search_hit.configure(text=f"命中 {shown}")
            else:
                self.lbl_search_hit.configure(text="")
        except Exception:
            pass
        try:
            effective = max(0, sel - blocked)
            ptxt = f"待退出 {effective} 个"
            if blocked:
                ptxt += f"（{blocked} 个受保护将跳过）"
            elif hidden_sel:
                ptxt += f"（另 {hidden_sel} 个被隐藏）"
            self.lbl_pending.configure(text=ptxt)
        except Exception:
            pass
        # 按钮文案随范围变化，避免「全选」到底选了什么产生歧义
        filtered = self._filter_active or shown != total
        try:
            self.btn_sel_all.configure(text=f"全选筛选结果({shown})" if filtered else f"全选({shown})")
            self.btn_sel_invert.configure(text="反选筛选结果" if filtered else "反选")
        except Exception:
            pass
        try:
            self.btn_whitelist.configure(text=f"🔒 白名单({sel})" if sel else "🔒 白名单")
        except Exception:
            pass
        try:
            self.refresh_whitelist_info()   # 测试桩可能没有该方法，不能让它打断列表加载
        except Exception:
            pass
        # 列表上方的类型统计条跟着一起刷新。
        # getattr 兜底：测试桩可能只绑了 update_count 而没绑 _update_stats，
        # 不能因为它缺失就把整条列表加载链打断（同上面 refresh_whitelist_info 的处理）。
        stats_fn = getattr(self, "_update_stats", None)
        if stats_fn is not None:
            stats_fn()

    def _on_select_all_key(self, event=None):
        """Ctrl+A：全选当前显示的行。"""
        self.select_all_visible()
        return "break"

    # ---------------- 退出 ----------------
    # ---------------- 安全防护：退出前预览与跳过 ----------------
    def _row_snapshot(self, iid):
        def g(c):
            try:
                return str(self.tree.set(iid, c) or "")
            except Exception:
                return ""
        return {"iid": iid, "name": g("name") or self._cid_of(iid) or "(未知)",
                "username": g("username"), "type": g("type"),
                "members": g("members"), "posts": g("posts"), "id": g("id")}

    def build_leave_plan(self, iids):
        """把待处理清单拆成「将退出」与「受保护跳过」两组。
        「退出时跳过受保护项」关闭时不做跳过，全部照退（用户明确要求时才有意义）。"""
        skip = var_bool(self, "skip_protected_var", True)
        to_leave, blocked, seen = [], [], set()
        for iid in iids:
            if iid in seen:
                continue
            seen.add(iid)
            try:
                if not self.tree.exists(iid):
                    continue
            except Exception:
                continue
            snap = self._row_snapshot(iid)
            reason = self.protected_reason(iid) if skip else None
            if reason:
                snap["reason"] = reason
                blocked.append(snap)
            else:
                to_leave.append(snap)
        return to_leave, blocked

    def _exit_breakdown(self, rows):
        """待处理清单里「真退出」与「删除会话」各多少个 —— 用来把动作说清楚。

        优先看实体的真实类型（`session_kind`），拿不到才退回行里的「类型」列文本；
        两者在列表刷新后始终一致，但实体更可靠（类型列是渲染出来的字符串）。"""
        n_leave = n_del = 0
        for r in rows:
            ent = self.channels.get(r.get("iid"))
            kind = session_kind(ent) if ent is not None else r.get("type")
            act = exit_action_of(kind)
            if act == EXIT_LEAVE:
                n_leave += 1
            elif act == EXIT_DELETE:
                n_del += 1
        return n_leave, n_del

    def _exit_headline(self, total, n_leave, n_del):
        """一句话说清这批勾选会被怎么处理 —— 「私聊会被删除」绝不能藏着不说。"""
        if n_del and n_leave:
            return (f"将处理 {total} 个会话：退出 {n_leave} 个频道 / 群组，"
                    f"删除 {n_del} 个私聊 / 机器人会话。")
        if n_del:
            return (f"将删除 {n_del} 个私聊 / 机器人会话"
                    f"（Telegram 对它们没有「退出」，只能删除会话）。")
        return f"确定要退出选中的 {n_leave} 个频道 / 群组？"

    def confirm_leave(self, to_leave, blocked, notes=""):
        """退出前确认。默认弹「待退出清单」预览窗；关掉预览选项后退化为普通确认框。
        返回 True 表示用户确认执行。"""
        reason_txt = ""
        if blocked:
            reason_txt = f"\n\n将跳过 {len(blocked)} 个受保护项（白名单 / 创建者 / 管理员）。"
        if not var_bool(self, "preview_leave_var", True):
            n_leave, n_del = self._exit_breakdown(to_leave)
            parts = []
            if n_leave:
                parts.append(f"退出 {n_leave} 个频道 / 群组")
            if n_del:
                parts.append(f"删除 {n_del} 个私聊 / 机器人会话（它们没有「退出」）")
            msg = "确定要" + "、".join(parts) + "吗？" + reason_txt
            if notes:
                msg += "\n\n" + notes
            return bool(messagebox.askyesno("确认", msg))
        return self._preview_leave_dialog(to_leave, blocked, notes)

    def _plan_dialog(self, title, headline, to_do, blocked, notes="",
                     ok_text=None, blocked_tab=None, danger=False,
                     remember_attr=None, confirm_word=None):
        """通用「执行前清单」预览窗：左右两页分别列出「将执行」与「已跳过」。

        - remember_attr="preview_leave_var" -> 显示「以后不再显示此清单」（退出用）
        - confirm_word="删除"              -> 额外要求**手打确认词**，且确认按钮
          初始为不可点（销毁用）。这是不可恢复操作的最后一道闸，不存在绕过开关。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        try:
            dlg.geometry("680x520" if confirm_word else "680x470")
        except Exception:
            pass

        outer = ttk.Frame(dlg, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=headline, font=("", 10, "bold")).pack(anchor="w")
        if blocked:
            ttk.Label(outer, text=f"另有 {len(blocked)} 个将被跳过（原因见第二个标签页）。",
                      foreground="#a32d2d").pack(anchor="w", pady=(3, 0))
        if notes:
            ttk.Label(outer, text=notes, foreground="#6b7280",
                      wraplength=640, justify="left").pack(anchor="w", pady=(3, 0))
        if danger:
            ttk.Label(outer, text="⚠️ 不可恢复：删除后所有成员都再也看不到它。",
                      foreground="#a32d2d").pack(anchor="w", pady=(3, 0))

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True, pady=8)
        nb.add(self._plan_tree(nb, to_do, check_col=False),
               text=f"将执行（{len(to_do)}）")
        if blocked:
            nb.add(self._plan_tree(nb, blocked, check_col=True),
                   text=blocked_tab or f"已跳过（{len(blocked)}）")

        remember_var = None
        if remember_attr:
            remember_var = tk.BooleanVar(value=var_bool(self, remember_attr, True))
            ttk.Checkbutton(outer, text="以后不再显示此清单",
                            variable=remember_var).pack(anchor="w")

        result = {"ok": False}
        ok_btn = ttk.Button(outer, text=ok_text or f"确认执行 {len(to_do)} 个",
                            state="disabled" if confirm_word else "normal")

        if confirm_word:
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=(8, 0))
            ttk.Label(row, text=f"确认无误，请输入「{confirm_word}」：").pack(side="left")
            word_var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=word_var, width=10)
            entry.pack(side="left", padx=6)
            entry.focus_set()

            def _sync(*_a):
                ok_btn.configure(
                    state="normal" if word_var.get().strip() == confirm_word else "disabled")
            word_var.trace_add("write", _sync)

        def on_ok():
            result["ok"] = True
            if remember_var is not None:
                try:
                    getattr(self, remember_attr).set(bool(remember_var.get()))
                    self.save_config()
                except Exception:
                    pass
            dlg.destroy()

        ok_btn.configure(command=on_ok)
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=4)
        ok_btn.pack(side="right", padx=4)
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        if not confirm_word:
            dlg.bind("<Return>", lambda e: on_ok())
        try:
            dlg.grab_set()
        except Exception:
            pass
        dlg.wait_window()
        return result["ok"]

    def _preview_leave_dialog(self, to_leave, blocked, notes=""):
        """待退出清单预览窗：左右两栏分别列出「将处理」与「已跳过（受保护）」。"""
        n_leave, n_del = self._exit_breakdown(to_leave)
        extra = ""
        if n_del:
            extra = (f"其中 {n_del} 个是私聊 / 机器人（Telegram 没有「退出」，"
                     f"将改为删除会话，只影响你自己这边的记录）。")
        return self._plan_dialog(
            "退出前确认",
            self._exit_headline(len(to_leave), n_leave, n_del),
            to_leave, blocked, (notes + "\n" + extra).strip() if extra else notes,
            ok_text=f"确认处理 {len(to_leave)} 个",
            blocked_tab=f"已跳过 · 受保护（{len(blocked)}）",
            remember_attr="preview_leave_var")

    def _plan_tree(self, parent, rows, check_col=False):
        """清单里的小表格：名称 / 用户名 / 类型 / 用户数 / 作品数 / ID（+ 跳过原因）。"""
        wrap = ttk.Frame(parent)
        cols = ("name", "username", "type", "members", "posts", "id")
        titles = ("名称", "用户名", "类型", "用户数", "作品数", "ID")
        if check_col:
            cols = cols + ("reason",)
            titles = titles + ("跳过原因",)
        tv = ttk.Treeview(wrap, columns=cols, show="headings", height=10)
        for c, t in zip(cols, titles):
            tv.heading(c, text=t)
            tv.column(c, width=110 if c == "name" else 80, anchor="w")
        for r in rows:
            vals = [r.get(c, "") for c in cols]
            tv.insert("", "end", values=vals)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        return wrap

    def leave_items(self, iids, auto_confirm=False):
        if not iids:
            self._warn_no_selection("没有可退出 / 可删除的会话。")
            return
        to_leave, blocked = self.build_leave_plan(iids)
        notes = self._scope_notes("本次不会处理")
        if not to_leave:
            names = "、".join(b["name"] for b in blocked[:6])
            more = f"，另有 {len(blocked) - 6} 个" if len(blocked) > 6 else ""
            messagebox.showinfo(
                "提示",
                f"勾选的 {len(iids)} 个全部受保护，没有可处理的会话。\n\n"
                f"受保护项：{names}{more}\n"
                "（白名单可右键或点「🔒 白名单」移出；创建者 / 管理员在 Telegram 里无法退出）",
            )
            self.log_msg(f"⏭ 勾选的 {len(iids)} 个全部受保护，已跳过。")
            return
        if not auto_confirm and not self.confirm_leave(to_leave, blocked, notes):
            self.log_msg("已取消退出。")
            return
        if blocked:
            self.log_msg(f"⏭ 受保护跳过 {len(blocked)} 个："
                         + "、".join(f"{b['name']}（{b['reason']}）" for b in blocked[:8])
                         + ("…" if len(blocked) > 8 else ""))
        interval = max(1, int(self.interval_var.get()))
        try:
            jitter = max(0, int(self.jitter_var.get()))
        except Exception:
            jitter = 0
        self._set_run_buttons_disabled()
        self.run_async(self._leave([x["iid"] for x in to_leave], interval, jitter))

    # ---- 破坏性操作共用的提示文案（退出 / 删除会话 / 销毁 三处一致，避免口径漂移）----
    def _warn_no_selection(self, empty_msg="没有勾选任何会话。"):
        if self._sel_hidden:
            messagebox.showinfo(
                "提示",
                f"当前显示中没有勾选的行，但有 {len(self._sel_hidden)} 个勾选项被筛选隐藏了。\n"
                "如需处理它们：点「清除」筛选后再「全选」。",
            )
        else:
            messagebox.showinfo("提示", empty_msg)

    def _scope_notes(self, verb="本次不会处理"):
        if self._sel_hidden:
            return (f"另有 {len(self._sel_hidden)} 个勾选项被当前筛选隐藏，{verb}。"
                    "（要一并处理请先点「清除」筛选，再「全选」）")
        if self._filter_active:
            return "仅处理当前筛选显示出来的行。"
        return ""

    def on_leave(self):
        self.leave_items(self.selected_items())

    def on_kw_leave(self):
        kw_text = self.kw_var.get().strip()
        if not kw_text:
            messagebox.showinfo("提示", "请先输入关键词。")
            return
        kws = [k.strip().lower() for k in re.split(r"[,，\s]+", kw_text) if k.strip()]
        if not kws:
            return
        matched = []
        for iid in self.tree.get_children():
            name = (self.tree.set(iid, "name") or "").lower()
            uname = (self.tree.set(iid, "username") or "").lower()
            if any(k in name or k in uname for k in kws):
                matched.append(iid)
        if not matched:
            messagebox.showinfo("提示", "没有名称 / 用户名匹配关键词的频道。")
            return
        self.log_msg(f"🔍 关键词匹配到 {len(matched)} 个频道，准备退出。")
        self.leave_items(matched)

    def on_export(self, mode="visible"):
        """导出 CSV：visible=当前显示的行（筛选结果） / all=全部（含被隐藏） / checked=已勾选。"""
        if mode == "all":
            iids = [i for i in self.channels if self.tree.exists(i)]
            label = "全部"
        elif mode == "checked":
            iids = [i for i in self.selected_items_all() if self.tree.exists(i)]
            label = "已勾选"
        else:
            iids = list(self.tree.get_children())
            label = "当前显示"
        rows = []
        for iid in iids:
            try:
                rows.append((
                    self.tree.set(iid, "name"),
                    self.tree.set(iid, "username"),
                    self.tree.set(iid, "type"),
                    self.tree.set(iid, "members"),
                    self.tree.set(iid, "posts"),
                    self.tree.set(iid, "active"),
                    self.tree.set(iid, "folder"),
                    self.tree.set(iid, "id"),
                    self.protected_reason(iid) or "",
                    risk_detail_text(self.channels.get(iid)),
                ))
            except Exception:
                pass
        if not rows:
            messagebox.showinfo("提示", f"没有可导出的行（范围：{label}）。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("全部文件", "*.*")],
            initialfile={"visible": "tg_channels_visible.csv",
                         "all": "tg_channels_all.csv",
                         "checked": "tg_channels_checked.csv"}.get(mode, "tg_channels.csv"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["名称", "用户名", "类型", "用户数", "作品数", "活跃(天前)",
                            "分组", "ID", "安全防护", "异常特征"])
                w.writerows(rows)
            self.log_msg(f"✅ 已导出{label} {len(rows)} 条到 {path}")
            messagebox.showinfo("完成", f"已导出{label} {len(rows)} 条到：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    async def _run_destructive(self, sel, interval, jitter, do_one, verb,
                               task_label=None, progress_verb=None, defer_drop=False):
        """退出 / 删除会话 / 销毁 三种破坏性批量操作的**公共执行器**。

        统一处理：限流退避重试、进度上报、成功行的行清理、按钮复位、结果汇总。
        do_one(ent, name) -> (是否成功, 日志文案)。
        抽出来的理由：这三条链路的失败处理必须完全一致 —— 任何一份漏了
        `_drop_row`，统计任务都会继续去请求一个已经不存在的会话。

        返回 (成功数, 失败数, 宣告成功的 [(iid, ent, name), ...])。
        第三个返回值是给「删后复核」用的**唯一可靠来源**：行清理是 `root.after`
        排给 Tk 主循环的，调用方在这个协程里回头看 `self.channels` 时那些行
        **还没被摘掉**（v1.3.1 第一版就是这么写的，复核因此从来没跑过），
        所以「谁被宣告成功了」必须当场记下来，不能事后靠状态反推。

        defer_drop=True：**先别摘行**，把「摘不摘」的决定权交给调用方 ——
        删除 / 退出要先跟服务端复核，确认真的没了才把行移走（否则就是
        「界面说删掉了、客户端里还在」）。"""
        pv = progress_verb or verb
        total = len(sel)
        done = 0
        made = []
        if task_label:
            self.root.after(0, lambda t=task_label: self.start_task(t))

        ok = fail = 0
        for iid in sel:
            ent = self.channels.get(iid)
            if not ent:
                done += 1
                continue
            name = self.tree.set(iid, "name") or str(ent.id)
            try:
                success, line = await do_one(ent, name)
                if line:            # 空串 = 「结论等复核完再报」，此时不许先claim
                    self.log_msg(line)
                if success:
                    ok += 1
                    made.append((iid, ent, name))
                    if not defer_drop:
                        self.root.after(0, lambda i=iid: self._drop_row(i))
                else:
                    fail += 1
            except FloodWaitError as e:
                self.log_msg(f"⏳ 触发限流，等待 {e.seconds} 秒后重试: {name}")
                secs = int(e.seconds)
                n, t = done + 1, total
                self.root.after(0, lambda n=n, t=t, s=secs: self.set_progress(
                    n, t, f"限流等待 {s}s 后重试（{n}/{t}）"))
                try:
                    await asyncio.sleep(e.seconds)
                    success, line = await do_one(ent, name)
                    if line:
                        self.log_msg(f"{line}（限流重试）")
                    if success:
                        ok += 1
                        made.append((iid, ent, name))
                        if not defer_drop:
                            self.root.after(0, lambda i=iid: self._drop_row(i))
                    else:
                        fail += 1
                except Exception as e2:
                    fail += 1
                    self.log_msg(f"❌ {verb}失败: {name} -> {e2}")
            except Exception as e:
                fail += 1
                self.log_msg(f"❌ {verb}失败: {name} -> {e}")
            done += 1
            n, t = done, total
            self.root.after(0, lambda n=n, t=t: self.set_progress(n, t, f"正在{pv} {n}/{t}"))
            if done < total:
                wait = interval + (random.uniform(0, jitter) if jitter > 0 else 0)
                await asyncio.sleep(max(1.0, wait))
        if not defer_drop:
            # defer_drop 时**不能**在这里报总账：调用方还要跟服务端复核，
            # 有些「成功」会被复核推翻。等复核完由调用方报一次最终结果。
            self.log_msg(f"🏁 完成：成功 {ok} 个，失败 {fail} 个。")
            self.root.after(0, lambda o=ok, f=fail, v=verb: self.finish_task(
                f"{v}完成：成功 {o} 个，失败 {f} 个"))
        self.root.after(0, self._set_run_buttons_normal)
        self.root.after(0, self.update_count)
        return ok, fail, made

    def _drop_row(self, iid):
        """删掉一行并同步清理所有引用它的映射 —— 漏掉任何一处，
        统计任务都还会去请求一个已经不存在的会话。"""
        try:
            if self.tree.exists(iid):
                self.tree.delete(iid)
        except Exception:
            pass
        self.channels.pop(iid, None)
        self._visible.discard(iid)
        self._sel_hidden.discard(iid)
        self._role.pop(iid, None)
        self._risk.pop(iid, None)
        try:
            self._row_order.remove(iid)
        except ValueError:
            pass

    def _set_run_buttons(self, normal):
        for name in ("btn_leave", "btn_kw_leave", "btn_destroy"):
            try:
                getattr(self, name).configure(state="normal" if normal else "disabled")
            except Exception:
                pass

    def _set_run_buttons_normal(self):
        self._set_run_buttons(True)

    def _set_run_buttons_disabled(self):
        self._set_run_buttons(False)

    async def _leave(self, sel, interval, jitter=0):
        """退出选中会话：频道 / 群组真的退出，私聊 / 机器人改为删除会话。

        分派在 leave_or_delete_entity 里（Telegram 对私聊不提供「退出」，
        build_leave_request 收到 User 会显式报错 —— 那是原语层的正确行为，
        不该让用户看到，所以在这里先翻译成动作）。"""
        async def _do_leave(ent, name):
            kind = session_kind(ent)
            status, note = await leave_or_delete_entity(self.client, ent, kind)
            if status == LEAVE_BLOCKED:
                return False, f"⛔ 无法退出（{note}）: {name}"
            if status == LEAVE_GONE:
                return True, f"➖ 无需退出（{note}）: {name}"
            if exit_action_of(kind) == EXIT_DELETE:
                # 私聊在「退出」里也是删会话：结论等复核完由 _finish_destructive 统一报
                return True, ""
            return True, f"✅ 已退出: {name}"

        _ok, _fail, made = await self._run_destructive(
            sel, interval, jitter, _do_leave, "退出",
            task_label=f"正在处理选中的会话（共 {len(sel)} 个）…", defer_drop=True)

        # 退出也走同一套复核：私聊在「退出」里同样是删会话，不核对就报成功
        # 会变成同一个谎 —— 两条路要么都说真话，要么都在骗人。
        await self._finish_destructive(made, "退出", _ok, _fail)


    # ---------------- 删除会话 / 销毁自己创建的群组·频道 ----------------
    def _roles_missing(self, iids):
        """勾选范围里是否还有「角色未检测」且属于频道/群组的行 —— 决定要不要先补一次检测。"""
        for iid in iids:
            ent = self.channels.get(iid)
            if ent is None or not is_member_kind(session_kind(ent)):
                continue
            if not self.role_known(iid):
                return True
        return False

    async def _ensure_roles(self, iids):
        """破坏性操作前补齐创建者身份：只对「角色未知且属于频道/群组」的行发请求。

        没有这一步，用户刚刷完列表就点「删除我创建的频道」，会因为角色还没检测
        而整批被跳过，看起来像功能坏了。"""
        need, seen = [], set()
        for iid in iids:
            if iid in seen:
                continue
            seen.add(iid)
            ent = self.channels.get(iid)
            if ent is None or not is_member_kind(session_kind(ent)):
                continue
            if self.role_known(iid):
                continue
            need.append((iid, ent))
        if not need:
            return 0
        self.log_msg(f"正在识别 {len(need)} 个候选的创建者身份（决定能否删除）…")
        role_cache = getattr(self, "_role_cache", None)
        if not isinstance(role_cache, dict):
            role_cache = {}
            self._role_cache = role_cache
        sem = asyncio.Semaphore(max(1, self._safe_int(self.conc_var, 6, 1, 32)))
        done = 0

        async def one(iid, ent):
            nonlocal done
            async with sem:
                role = None
                try:
                    role = await self._detect_role(ent)
                except Exception:
                    role = None
                if role is not None:
                    role_cache[str(getattr(ent, "id", ""))] = [role, time.time()]
                    ep = self._tree_epoch
                    self.root.after(0, lambda i=iid, r=role, e=ep: self._apply_role(e, i, r))
                done += 1
                n, t = done, len(need)
                self.root.after(0, lambda n=n, t=t: self.set_progress(
                    n, t, f"识别创建者身份 {n}/{t}"))

        try:
            await asyncio.gather(*(one(i, e) for i, e in need), return_exceptions=True)
        finally:
            try:
                self.save_role_cache()
            except Exception:
                pass
        return len(need)

    def delete_protect_reason(self, iid):
        """「删除会话」的跳过理由：**只看白名单**。

        不能直接复用 protected_reason —— 那个还会把「创建者 / 管理员」也算成受保护，
        而创建者恰恰**退不出去**（见 LEAVE_BLOCKED），删除会话是他们把该会话
        从列表里清掉的唯一手段。用 protected_reason 去拦，等于把最需要这个功能的人
        挡在门外。白名单则不同：它承诺的是「永不退出」，而删除会话会永久清掉
        本地聊天记录，对白名单里的频道同样是不可逆损失，所以仍然跳过。
        """
        return "白名单" if self.is_whitelisted(iid) else None

    def build_delete_plan(self, iids):
        """「删除会话（含聊天记录）」的执行清单：**任何类型**都能删（私聊/机器人也行）。

        受保护项按「退出时跳过受保护项」开关处理，但判定走 delete_protect_reason
        （只认白名单，不认创建者/管理员）。
        """
        skip = var_bool(self, "skip_protected_var", True)
        to_del, blocked, seen = [], [], set()
        for iid in iids:
            if iid in seen:
                continue
            seen.add(iid)
            try:
                if not self.tree.exists(iid):
                    continue
            except Exception:
                continue
            snap = self._row_snapshot(iid)
            reason = self.delete_protect_reason(iid) if skip else None
            if reason:
                snap["reason"] = reason
                blocked.append(snap)
            else:
                to_del.append(snap)
        return to_del, blocked

    def build_destroy_plan(self, iids, want_kind):
        """「删除我创建的频道 / 群组」的执行清单。

        门禁是**类型 + 创建者身份**，刻意不套用 protected_reason：
        - 只能处理 want_kind 这一种类型 —— 否则「想删频道」可能把群组一起带走；
        - 角色必须是 creator —— Telegram 侧也这么要求，提前过滤掉必然失败的请求；
        - ⚠️ **不用保护名单拦**：创建者本来就不允许退出，白名单 + 自己创建等于
          永久卡死，销毁恰恰是唯一出路。真正的门禁是上面的类型 + creator，
          外加确认窗里那道「必须手打确认词」。
        """
        to_del, blocked, seen = [], [], set()
        for iid in iids:
            if iid in seen:
                continue
            seen.add(iid)
            try:
                if not self.tree.exists(iid):
                    continue
            except Exception:
                continue
            snap = self._row_snapshot(iid)
            typ = self._type_of(iid)
            role = self._role_of(iid)
            if not can_destroy_kind(typ):
                snap["reason"] = f"类型不符（{typ} 没有创建者，本操作只处理{want_kind}）"
                blocked.append(snap)
            elif typ != want_kind:
                snap["reason"] = f"类型不符（{typ}，本操作只处理{want_kind}）"
                blocked.append(snap)
            elif not self.role_known(iid):
                snap["reason"] = "创建者身份未检测（可先「获取统计」再试）"
                blocked.append(snap)
            elif role != "creator":
                snap["reason"] = f"你不是创建者（{ROLE_LABEL.get(role) or '普通成员'}）"
                blocked.append(snap)
            else:
                to_del.append(snap)
        return to_del, blocked

    # ---- 删除会话（含聊天记录）----
    def on_delete_dialogs(self):
        """删除会话（含聊天记录）：从**我这边**的列表移除并清空本地聊天记录。"""
        iids = self.selected_items()
        if not iids:
            self._warn_no_selection()
            return
        to_del, blocked = self.build_delete_plan(iids)
        if not to_del:
            names = "、".join(b["name"] for b in blocked[:6])
            more = f"，另有 {len(blocked) - 6} 个" if len(blocked) > 6 else ""
            messagebox.showinfo(
                "提示",
                f"勾选的 {len(iids)} 个会话全部受保护，没有可删除的。\n\n"
                f"受保护项：{names}{more}\n"
                "（白名单可右键或点「🔒 白名单」移出；也可关掉「退出时跳过受保护项」）")
            self.log_msg(f"⏭ 勾选的 {len(iids)} 个全部受保护，已跳过删除。")
            return
        notes = self._scope_notes("本次不会删除")
        if not self._confirm_delete_dialogs(to_del, blocked, notes):
            self.log_msg("已取消删除。")
            return
        self.log_msg(f"🗑 开始删除 {len(to_del)} 个会话（含聊天记录）"
                     + (f"，跳过 {len(blocked)} 个受保护项" if blocked else ""))
        self._set_run_buttons_disabled()
        self.run_async(self._delete_dialogs([x["iid"] for x in to_del],
                                            self._interval(), self._jitter()))

    def _confirm_delete_dialogs(self, to_del, blocked, notes=""):
        """删除会话前确认：同样出清单预览，但**不给**「以后不再显示」的选项 ——
        这是会永久清掉本地聊天记录的不可恢复操作，不值得为省一次点击留后门。

        ⚠️ 必须逐类讲清楚，因为两类的后果根本不同：私聊只是删掉我这边那份记录；
        频道 / 群组则**必须退出**才会从列表消失（Telegram 的硬约束）——
        退出不可恢复、要重新加入才能再看，跟「删个聊天」不是一回事。"""
        chats = sum(1 for x in to_del
                    if str(x.get("type") or "") in (KIND_CHANNEL, KIND_GROUP))
        users = len(to_del) - chats
        lines = []
        if users:
            lines.append(f"· 私聊 / 机器人 {users} 个：只删掉【你这边】的会话与聊天记录，"
                         "对方完全不受影响")
        if chats:
            lines.append(f"· 频道 / 群组 {chats} 个：**会退出它们** —— Telegram 里这类会话"
                         "只有退出才会从列表消失；退出不可恢复，要看只能重新加入")
        note = "说明：\n" + "\n".join(lines)
        return self._plan_dialog(
            "删除会话前确认",
            f"确定要处理选中的 {len(to_del)} 个会话？",
            to_del, blocked, (notes + "\n" if notes else "") + note,
            ok_text=f"确认处理 {len(to_del)} 个",
            blocked_tab=f"已跳过 · 受保护（{len(blocked)}）")

    async def _delete_dialogs(self, sel, interval, jitter):
        kinds = {}
        for iid in sel:
            ent = self.channels.get(iid)
            k = session_kind(ent) if ent is not None else KIND_OTHER
            kinds[k] = kinds.get(k, 0) + 1
        member_like = sum(v for k, v in kinds.items() if is_member_kind(k))
        if member_like:
            self.log_msg(
                f"ℹ️ 其中 {member_like} 个是频道 / 群组：Telegram 里这类会话"
                "**只有退出才会从列表消失**，所以本次对它们是执行退出（和确认清单里列的一致）。")

        async def _do(ent, name):
            # ⚠️ 收藏夹（is_self）连请求都不要发：Telegram 不会摘掉这个会话条目，
            # 但请求里「删记录」的那一半**真的会把收藏内容清空** —— 那是不可逆的数据损失，
            # 而用户勾「删除会话」时根本没意识到会丢收藏。宁可直接拦住并说清出路。
            if isinstance(ent, tl_types.User) and getattr(ent, "is_self", False):
                return False, ("⛔ 无法删除（这是你自己的「收藏夹」：Telegram 不允许删除这个会话；"
                               "本次**没有**发送任何请求，以免误清空你收藏的内容。"
                               "确实要清空请到客户端里用「清空聊天记录」）: %s" % name)
            status, note = await delete_history_entity(self.client, ent)
            if status == DEL_BLOCKED:
                return False, f"⛔ 无法删除（{note}）: {name}"
            if status == DEL_GONE:
                return True, f"➖ 无需删除（{note}）: {name}"
            if isinstance(ent, tl_types.User):
                # ⚠️ 私聊先**不许**说「已删除」：请求只是发出去了，
                # 服务端可能安静地收下却什么都不做。结论等复核完由
                # _finish_destructive 统一报（空串 = 先别写日志）。
                return True, ""
            verb = "已退出并移除会话" if is_member_kind(session_kind(ent)) else "已删除会话"
            return True, f"🗑 {verb}: {name}"

        # ⚠️ 复核对象只能取执行器**当场记下**的成功名单。曾经想「跑完之后不在
        # self.channels 里的就是成功的」——那是错的：行清理是 root.after 排给 Tk
        # 主循环的，这个协程里回头去看时那些行还没被摘掉，claimed 恒为空，
        # 复核代码一次都没执行过（测试就是这么把它抓出来的）。
        _ok, _fail, made = await self._run_destructive(
            sel, interval, jitter, _do, "删除",
            task_label=f"正在删除会话（共 {len(sel)} 个）…", defer_drop=True)

        await self._finish_destructive(made, "删除", _ok, _fail)

    async def _finish_destructive(self, made, verb, ok, fail):
        """复核 + 只摘掉「服务端确认没了」的行，最后报**一次**真实的总账。

        没确认的行**留在列表里**：让用户当场看到哪几个没成，而不是先看到「已删除」、
        回客户端一看还在（那正是这次要根治的毛病）。

        总账必须等复核完再报：执行阶段说「成功 4 个」，复核可能推翻其中 1 个 ——
        先报 4 再改口，等于又制造一次误导。

        返回 (确认仍在 [(iid, name)...], 无法确认 [iid...])。"""
        kept, unver = await self._verify_dialog_removal(made)
        kept_ids = {iid for iid, _n in kept}
        for iid, ent, name in made:
            if isinstance(ent, tl_types.User):
                # 私聊的「到底删掉没有」现在才有答案 —— 到这一步才写日志，
                # 免得日志先写了「已删除」再改口。
                if iid in kept_ids:
                    self.log_msg(f"⚠️ 未能删除（服务端确认仍然存在）: {name}"
                                 f"{dialog_remove_tag(ent)}")
                elif iid in unver:
                    self.log_msg(f"❓ 未能复核（无法确认删掉没有）: {name}")
                else:
                    self.log_msg(f"🗑 已删除会话（服务端已复核确认）: {name}")
            if iid in kept_ids or iid in unver:
                continue        # 没被确认的一律留在列表，别让界面看起来「都成了」
            self.root.after(0, lambda i=iid: self._drop_row(i))
        real_ok = ok - len(kept) - len(unver)
        real_fail = fail + len(kept)
        bits = [f"成功 {real_ok} 个", f"失败 {real_fail} 个"]
        if unver:
            bits.append(f"未能确认 {len(unver)} 个")
        if kept:
            note = f"（{len(kept)} 个经服务端复核确认**没删掉**，已留在列表里）"
        elif unver:
            note = "（未能确认的也留在列表里，点「🔄 刷新列表」即可核对真实结果）"
        else:
            note = "。"
        self.log_msg("🏁 完成：" + "，".join(bits) + note)
        self.root.after(0, lambda o=real_ok, f=real_fail, v=verb: self.finish_task(
            f"{v}完成：成功 {o} 个，失败 {f} 个"))
        self.root.after(0, self.update_count)
        return kept, unver

    async def _verify_dialog_removal(self, made):
        """删除 / 退出之后**回头问服务端**：这些私聊真的从我这边消失了吗？

        返回 (服务端确认仍在的 [(iid, name)], 无法确认的 [iid])。
        两种情况调用方都**不得**把行摘掉、也不得计入成功。

        为什么非做不可：`messages.deleteHistory` 服务端会安静地收下请求却什么都不做，
        而代码只要「没抛异常」就报成功 —— 「提示已删除、客户端里还在」就是这个来的。
        一次不成还会按 DIALOG_REMOVE_RETRY_STEPS 换几种**写法确实不同**的方式重试
        （v1.3.2 起多了针对「对方账号已注销」的 revoke=True 与「临时封禁→解封→再删」），
        每一步都重新问服务端一次；
        全部试完仍在的，如实报出来并给出原因（并补一条只读实测：记录究竟清空了没有），
        绝不为了让日志好看而写成「已删除」。

        只复核私聊 / 机器人：频道 / 群组退出后已不可访问，再用 peers 去查会整批报错，
        反而把真正的信号淹掉（它们的成败由退出请求本身决定，服务端拒绝就会抛错）。"""
        targets = [(iid, ent, name) for iid, ent, name in made
                   if isinstance(ent, tl_types.User)]
        if not targets:
            return [], set()
        pending = list(targets)
        unver = set()          # 连 peer_id 都取不到 -> 无法判定，既不算成功也不算失败
        dialogs = {}
        for idx, (step, label) in enumerate([("request", "")] + list(DIALOG_REMOVE_RETRY_STEPS)):
            if idx > 0:
                names = "、".join(str(n) for _i, _e, n in pending[:10])
                more = f"，另有 {len(pending) - 10} 个" if len(pending) > 10 else ""
                self.log_msg(f"↻ 服务端仍返回这 {len(pending)} 个会话（{names}{more}）—— "
                             f"改用下一步写法重试：{label}")
                self.root.after(0, lambda: self.set_progress(
                    0, 0, "正在换写法重试（服务端复核未通过）…"))
                # block_cycle 一次要发 3 个请求（封禁 / 解封 / 删除），60 个会话就是 180 个。
                # 封禁类接口对「连续操作」很敏感，所以这一档**逐个限速**，别把服务端惹火；
                # 其余档位保持原来的节奏（一次 sleep(0) 让出事件循环即可）。
                per_peer = max(1.0, self._interval()) if step == "block_cycle" else 0.0
                for _iid, ent, _name in pending:
                    try:
                        skip = await retry_remove_dialog(
                            self.client, ent, step, dialogs.get(safe_peer_id(ent)))
                        if skip:
                            self.log_msg(f"   · {skip}")
                    except FloodWaitError as e:
                        # 被限流了就老实等，别把整批都撞成失败
                        wait = min(int(getattr(e, "seconds", 30) or 30), 180)
                        self.log_msg(f"   · ⏳ 被限流（{_name}），等待 {wait} 秒后继续")
                        await asyncio.sleep(wait)
                    except Exception as e:      # noqa: BLE001
                        self.log_msg(f"   · 重试失败（{_name}）: {e}")
                    await asyncio.sleep(per_peer)   # 让出事件循环（限量档顺带限速）
                # 重试也要限速，别把服务端惹火
                if pending:
                    await asyncio.sleep(max(1.0, self._interval()))

            still, why = await dialogs_still_present(
                self.client, [ent for _i, ent, _n in pending])
            if still is None:
                self.log_msg(f"❓ 复核没做成（{why}）：这 {len(pending)} 个私聊到底删掉没有"
                             "**无法确认** —— 我不把它当成功，也不当失败，"
                             "这些行会留在列表里，点「🔄 刷新列表」即可核对真实结果。")
                return [], unver | {iid for iid, _e, _n in pending}
            dialogs = still
            nxt = []
            for iid, ent, name in pending:
                pid = safe_peer_id(ent)
                if pid is None:
                    unver.add(iid)
                elif pid in still:
                    nxt.append((iid, ent, name))
            pending = nxt
            if not pending:
                if idx == 0:
                    self.log_msg(f"✅ 服务端复核通过：这 {len(targets)} 个私聊 / 机器人会话"
                                 "已从你的账号里移除（是服务端确认过的，不只是「请求发出去了」）。")
                    self.log_msg("   如果客户端里**还看得到**：先点「🔄 刷新列表」，"
                                 "仍不消失就重启或重新登录一次客户端 —— "
                                 "Telegram 桌面端对这种会话的本地列表刷新很慢，"
                                 "但服务端这边已经确认删掉了。")
                else:
                    self.log_msg(f"✅ 换写法后服务端确认已移除（本步生效：{label}）—— "
                                 f"共 {len(targets)} 个，用了 {idx} 次重试。")
                return [], unver

        # 所有写法都试过，服务端仍然说「在」
        names = "、".join(str(n) for _i, _e, n in pending[:20])
        more = f"，另有 {len(pending) - 20} 个" if len(pending) > 20 else ""
        self.log_msg(f"⚠️ 服务端**确认这些会话仍然存在**"
                     f"（已换 {len(DIALOG_REMOVE_RETRY_STEPS)} 种写法重试，均无效）："
                     f"{names}{more}")
        hints = []
        for _iid, ent, name in pending:
            h = dialog_remove_hint(ent)
            if h and h not in hints:
                hints.append(h)
        for h in hints[:3]:
            self.log_msg("   · " + h)
        # ⚠️ 实测这一段**必须无条件跑**。v1.3.1 把它写成「命中硬限制提示就跳过诊断」，结果最需要它的
        # 场景（命中「收藏夹 / 已注销」提示的那一批）反而一次都没执行 —— 用户拿到的日志里
        # 只有两句推测，没有一条实测数字，等于又用「猜」代替了「核对」，于是根本分不清
        # 到底是「请求完全没生效」还是「记录删了、条目被服务端留着」。
        cleared = remain = unknown = 0
        for _iid, ent, _name in pending[:5]:
            total, err = await dialog_history_cleared(self.client, ent)
            if err:
                unknown += 1
            elif total == 0:
                cleared += 1
            else:
                remain += 1
        if cleared and not remain:
            self.log_msg(f"   · 实测（只读 getHistory，抽查 {cleared} 个）：记录**已经清空**（0 条）"
                         "—— 删除请求生效了，是服务端**不肯摘掉会话条目**。")
            self.log_msg("     这正是 Telegram 对「已注销账号 / 你自己的收藏夹」这类会话的做法："
                         "记录能删，条目留着。换写法也未必有用，所以工具不会谎报成功。")
        elif remain and not cleared:
            self.log_msg(f"   · 实测（只读 getHistory，抽查 {remain} 个）：记录**仍在**（还有消息）"
                         "—— 删除请求完全没有生效，属于还需要继续查的类型。")
        else:
            self.log_msg(f"   · 实测（只读 getHistory，抽查 {cleared + remain + unknown} 个）："
                         f"记录已经清空 {cleared} 个 / 记录仍在 {remain} 个 / 无法判定 {unknown} 个。")
        self.log_msg("   这几行**已经留在列表里**（没有当作成功）。请把程序目录下的 "
                     "tg_leave_log.txt 发我 —— 里面记了每一步的写法、服务端返回和上面的诊断。")
        return [(iid, name) for iid, _e, name in pending], unver

    # ---- 销毁自己创建的频道 / 群组 ----
    def on_destroy_created(self, want_kind):
        """删除【我创建的】频道 / 群组（对所有成员生效、不可恢复）。"""
        if not self.client:
            messagebox.showinfo("提示", "请先连接并刷新列表。")
            return
        iids = self.selected_items()
        if not iids:
            self._warn_no_selection()
            return
        self._destroy_entry(list(iids), want_kind)

    def _destroy_entry(self, iids, want_kind):
        """销毁入口的公共前半段：角色没检测就先补（async），再交回主线程出清单。"""
        if self._roles_missing(iids):
            self.log_msg("ℹ️ 有候选项的创建者身份尚未检测，先识别再列清单…")
            self.run_async(self._destroy_after_roles(iids, want_kind))
        else:
            self._destroy_confirm_ui(iids, want_kind)

    async def _destroy_after_roles(self, iids, want_kind):
        self.root.after(0, lambda: self.start_task("正在识别创建者身份…"))
        try:
            await self._ensure_roles(iids)
        finally:
            self.root.after(0, lambda: self.finish_task("空闲"))
        self.root.after(0, lambda: self._destroy_confirm_ui(iids, want_kind))

    def _destroy_confirm_ui(self, iids, want_kind):
        """在主线程出清单并弹确认窗 —— Tk 的 Toplevel 只能在主线程开，
        所以角色检测（async）与确认窗（Tk）之间必须用 root.after 交回主线程。"""
        to_del, blocked = self.build_destroy_plan(iids, want_kind)
        if not to_del:
            reasons = {}
            for b in blocked:
                reasons[b["reason"]] = reasons.get(b["reason"], 0) + 1
            detail = "\n".join(f"· {k}：{v} 个" for k, v in reasons.items())
            messagebox.showinfo(
                "提示",
                f"勾选的 {len(iids)} 个里没有可删除的{want_kind}。\n\n{detail}\n\n"
                f"「删除我创建的{want_kind}」只处理【你本人创建】的{want_kind}；"
                "其它会话请用「🗑 删除会话（含聊天记录）」或「🚪 退出 / 删除选中」。")
            self.log_msg("⏭ 「删除自建%s」无可执行项（" % want_kind
                         + "；".join(f"{k} {v} 个" for k, v in reasons.items()) + "）")
            return
        if not self._destroy_dialog(to_del, blocked, want_kind):
            self.log_msg("已取消删除。")
            return
        self.log_msg(f"🔥 开始删除自建{want_kind} {len(to_del)} 个"
                     + (f"，跳过 {len(blocked)} 个" if blocked else "")
                     + "（不可恢复，已手打确认词）")
        self._set_run_buttons_disabled()
        self.run_async(self._destroy([x["iid"] for x in to_del], self._interval(),
                                     self._jitter(), want_kind))

    def _destroy_dialog(self, to_del, blocked, want_kind):
        """销毁确认窗：清单 + **必须手打确认词**。不可恢复操作的最后一道闸。"""
        return self._plan_dialog(
            f"危险操作 · 删除我创建的{want_kind}",
            f"确定要彻底删除这 {len(to_del)} 个{want_kind}吗？",
            to_del, blocked,
            f"这些{want_kind}会从【所有成员】的列表里消失，聊天记录一并清除，且无法恢复。",
            ok_text=f"确认删除 {len(to_del)} 个{want_kind}",
            blocked_tab=f"已跳过（{len(blocked)}）",
            danger=True, confirm_word=DESTROY_CONFIRM_WORD)

    async def _destroy(self, sel, interval, jitter, want_kind):
        async def _do(ent, name):
            status, note = await destroy_entity(self.client, ent)
            if status == DEL_BLOCKED:
                return False, f"⛔ 无法删除（{note}）: {name}"
            if status == DEL_GONE:
                return True, f"➖ 无需删除（{note}）: {name}"
            return True, f"🔥 已彻底删除{want_kind}: {name}"

        await self._run_destructive(
            sel, interval, jitter, _do, "删除",
            task_label=f"正在删除自建{want_kind}（共 {len(sel)} 个）…")

    def _interval(self):
        try:
            return max(1, int(self.interval_var.get()))
        except Exception:
            return 2

    def _jitter(self):
        try:
            return max(0, int(self.jitter_var.get()))
        except Exception:
            return 0

    # ---------------- 关闭 ----------------
    def on_close(self):
        try:
            # 先把界面状态（窗口大小/列宽/面板开合/主题）刷进配置，再销毁窗口
            self.save_config()
        except Exception:
            pass
        try:
            if self._stats_cancel is not None:
                self._stats_cancel.set()   # 关窗时停止仍在跑的统计任务
            if self._list_cancel is not None:
                self._list_cancel.set()    # 关窗时停止仍在跑的分页加载
        except Exception:
            pass
        try:
            self.save_stats_cache()
            self.save_role_cache()
        except Exception:
            pass
        try:
            if self.code_future is not None and not self.code_future.done():
                self.loop.call_soon_threadsafe(self.code_future.cancel)
        except Exception:
            pass
        try:
            # 关闭时才断开**所有**账号 —— 平时切换账号是绝不断开的（那样才叫「保持登录态」）
            for key, cli in list(getattr(self, "_clients", {}).items()):
                try:
                    if cli is not None:
                        self.run_async(cli.disconnect())
                except Exception:
                    pass
            if self.client is not None:
                self.run_async(self.client.disconnect())
            if self.loop is not None:
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        self.root.destroy()


def _show_crash(msg):
    """把启动/运行错误以系统弹窗 + 日志文件两种方式兜底展示（不依赖 tkinter）。"""
    try:
        import ctypes

        try:
            ctypes.windll.user32.MessageBoxW(0, msg, "TG 频道批量退出工具 - 错误", 0x10)
        except Exception:
            pass
    except Exception:
        pass
    try:
        with open(os.path.join(BASE_DIR, "tg_crash.log"), "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


def _report_callback_exception(exc, val, tb):
    """tkinter 回调（按钮点击等）里抛出的异常默认会被吞掉且静默退出，这里改成弹窗+日志。
    同一类错误短时间内重复发生时只记日志、不再反复弹窗，避免连环弹窗刷屏。"""
    import traceback as _tb

    msg = "界面操作出错（已被捕获，程序继续运行）：\n\n" + "".join(_tb.format_exception(exc, val, tb))
    key = f"{exc.__name__}: {val}"
    now = time.time()
    if key == _report_callback_exception._last_key and now - _report_callback_exception._last_time < 5:
        pass  # 重复错误：仅写日志
    else:
        _report_callback_exception._last_key = key
        _report_callback_exception._last_time = now
        try:
            messagebox.showerror("界面错误", msg)
        except Exception:
            _show_crash(msg)
    try:
        with open(os.path.join(BASE_DIR, "tg_crash.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


_report_callback_exception._last_key = ""
_report_callback_exception._last_time = 0.0


# ---------------- 自检（打包后冒烟用：exe --selftest） ----------------
# 存在的理由：窗口版 exe 没有控制台，「双击打不开」时只能靠猜。
# 这个入口把「解释器 / tkinter / telethon 有没有被正确打进包里」以及几条关键
# 请求构造、UI 骨架能否建起来，变成一次可脚本化的**退出码**检查（0=通过）。
# 结果同时写进 tg_selftest.log（万一没有可用的标准输出）。
SELFTEST_LOG = os.path.join(BASE_DIR, "tg_selftest.log")


def _selftest_say(lines, text):
    lines.append(text)
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.write(text + "\n")
                stream.flush()
        except Exception:
            pass


def selftest():
    """打包后冒烟自检。返回退出码：0=通过，1=失败。"""
    global BASE_DIR, CONFIG_FILE, SESSION_FILE, STATS_CACHE_FILE, ROLE_CACHE_FILE
    global LEGACY_SESSION_FILE, LEGACY_STATS_CACHE_FILE, LEGACY_ROLE_CACHE_FILE
    lines, ok = [], True
    try:
        _selftest_say(lines, "python %s  frozen=%s" % (
            sys.version.split()[0], getattr(sys, "frozen", False)))
        _selftest_say(lines, "exe = %s" % sys.executable)

        import telethon
        _selftest_say(lines, "telethon %s" % telethon.__version__)

        # 关键请求类必须在包里（少一个就是「打包漏了 hidden-import」）
        for cls in (tl_channels.LeaveChannelRequest, tl_channels.DeleteChannelRequest,
                    tl_messages.DeleteHistoryRequest, tl_messages.DeleteChatRequest,
                    tl_messages.DeleteChatUserRequest, tl_messages.GetDialogFiltersRequest,
                    tl_messages.UpdateDialogFilterRequest):
            assert cls is not None, cls

        ch = tl_types.Channel(id=1, title="t", photo=None, date=None, broadcast=True)
        gp = tl_types.Chat(id=2, title="g", photo=None, participants_count=1,
                           date=None, version=0)
        usr = tl_types.User(id=3, is_self=False, access_hash=1, first_name="u")
        assert isinstance(build_leave_request(ch), tl_channels.LeaveChannelRequest)
        assert isinstance(build_leave_request(gp), tl_messages.DeleteChatUserRequest)
        assert isinstance(build_destroy_request(ch), tl_channels.DeleteChannelRequest)
        assert isinstance(build_destroy_request(gp), tl_messages.DeleteChatRequest)
        for bad in (usr, tl_types.InputUserSelf()):
            try:
                build_destroy_request(bad)
                raise AssertionError("未知类型应当报错而不是兜底")
            except ValueError:
                pass
        dh = build_delete_history_request(ch)
        assert dh.max_id == 0 and dh.just_clear is False and dh.revoke is False

        # 类型 / 分组键 / 活跃天数：三条最容易在重构中被打断的纯逻辑
        assert session_kind(ch) == KIND_CHANNEL
        assert session_kind(tl_types.Channel(id=9, title="g", photo=None, date=None,
                                            broadcast=False)) == KIND_GROUP
        assert session_kind(usr) == KIND_USER
        assert (peer_key_of(ch), peer_key_of(gp), peer_key_of(usr)) == (
            ("channel", 1), ("chat", 2), ("user", 3))
        assert active_text(None) == ACTIVE_NONE
        assert active_text(NOT_APPLICABLE) == NOT_APPLICABLE
        assert active_text(time.time() - 3 * 86400) == "3"

        flt = tl_types.DialogFilter(id=1, title="x", pinned_peers=[], include_peers=[],
                                    exclude_peers=[], broadcasts=True)
        assert is_real_folder(flt) and not is_real_folder(tl_types.DialogFilterDefault())
        assert folder_title(flt) == "x"
        _st_ch = {"kind": KIND_CHANNEL, "contact": False, "is_broadcast": True,
                  "muted": False, "unread": 0, "archived": False}
        _st_u = {"kind": KIND_USER, "contact": False, "is_broadcast": False,
                 "muted": False, "unread": 0, "archived": False}
        assert filter_matches(flt, ("channel", 1), _st_ch) is True

        # ⚠️ 分组规则必须「按会话类别各查各的开关」。曾经的写法用
        # `non_contacts and not contact` 判定，而 Channel/Chat 没有 contact 属性，
        # 于是所有群组、所有频道都被收进「非联系人」分组 —— 就是「某些分组显示所有」的根因。
        nc = tl_types.DialogFilter(id=2, title="nc", pinned_peers=[], include_peers=[],
                                   exclude_peers=[], non_contacts=True)
        assert filter_matches(nc, ("user", 3), _st_u) is True
        assert filter_matches(nc, ("channel", 1), _st_ch) is False
        assert folder_flag_of({"kind": KIND_CHANNEL}) == "broadcasts"
        assert folder_flag_of({"kind": KIND_GROUP}) == "groups"
        assert folder_flag_of({"kind": KIND_USER, "contact": True}) == "contacts"
        # 共享分组（chatlist）只能看、不能用 updateDialogFilter 改成员
        assert is_editable_folder(flt)
        assert not is_editable_folder(tl_types.DialogFilterChatlist(
            id=9, title=tl_types.TextWithEntities(text="shared", entities=[]),
            pinned_peers=[], include_peers=[]))

        # ---- 分组「加入 / 移出」的两个静默失败点（v1.3.0 修）----
        # 1) exclude_peers 优先级**高于**手工加入：不先把它摘干净，写进 include_peers 也进不去；
        # 2) 靠自动规则进来的会话，不写 exclude_peers 就永远移不出去（规则下次又把它捞回来）。
        assert folder_keeps_by_rule(flt, _st_ch) is True    # flt 开了 broadcasts
        assert folder_keeps_by_rule(flt, _st_u) is False    # 频道分组不收用户
        _ip_ch = tl_types.InputPeerChannel(channel_id=1, access_hash=0)
        _ex = tl_types.DialogFilter(id=3, title="ex", pinned_peers=[],
                                    include_peers=[_ip_ch], exclude_peers=[_ip_ch],
                                    broadcasts=True)
        assert filter_matches(_ex, ("channel", 1), _st_ch) is False, "排除名单必须优先级最高"
        _mn = tl_types.DialogFilter(id=4, title="mn", pinned_peers=[],
                                    include_peers=[tl_types.InputPeerUser(user_id=3, access_hash=1)],
                                    exclude_peers=[], broadcasts=True)
        assert filter_matches(_mn, ("user", 3), _st_u) is True, "手工加入的应当属于，哪怕规则不命中"
        assert folder_keeps_by_rule(_mn, _st_u) is False, "但光靠规则并不命中 -> 移出时必须写排除名单"
        assert folder_keeps_by_rule(flt, {"kind": KIND_GROUP}) is False, "开关按类别各查各的"

        # 批量划分的三档范围：搜索 / 搜索结果必须是其中一档（用户明确要求的）
        assert [s for s, _l in FOLDER_SCOPES] == [
            FOLDER_SCOPE_CHECKED, FOLDER_SCOPE_VISIBLE, FOLDER_SCOPE_ALL]
        assert FOLDER_SCOPE_VISIBLE in FOLDER_SCOPE_LABEL
        assert FOLDER_EXCLUDE_LIMIT > 0
        assert DESTROY_CONFIRM_WORD

        # 「退出」必须能吃掉全部会话类型：私聊 / 机器人 -> 删除会话，
        # 而不是像以前那样抛 ValueError 让整批勾选失败。
        assert exit_action_of(KIND_CHANNEL) == EXIT_LEAVE
        assert exit_action_of(KIND_GROUP) == EXIT_LEAVE
        assert exit_action_of(KIND_USER) == EXIT_DELETE
        assert exit_action_of(KIND_BOT) == EXIT_DELETE
        assert exit_action_of(KIND_GONE) == EXIT_SKIP
        try:
            build_leave_request(usr)
            raise AssertionError("原语层对 User 仍应显式报错")
        except ValueError:
            pass

        _sent = []

        class _EchoClient:
            async def __call__(self, req):
                _sent.append(req)
                return "ok"

        assert asyncio.run(leave_or_delete_entity(_EchoClient(), usr))[0] == LEAVE_OK
        assert isinstance(_sent[-1], tl_messages.DeleteHistoryRequest), _sent[-1]
        assert asyncio.run(leave_or_delete_entity(_EchoClient(), ch))[0] == LEAVE_OK
        assert isinstance(_sent[-1], tl_channels.LeaveChannelRequest), _sent[-1]

        # ---- 「删除会话」按类型分派（v1.3.1 修的核心）----
        # 以前对所有类型一律发 DeleteHistoryRequest：服务端不报错、会话却纹丝不动，
        # 日志却已经写下「已删除」—— 这就是「提示成功、客户端会话还在」的真因。
        _r_u = build_remove_dialog_requests(usr)
        _r_g = build_remove_dialog_requests(gp)
        _r_c = build_remove_dialog_requests(ch)
        assert len(_r_u) == 1 and isinstance(_r_u[0], tl_messages.DeleteHistoryRequest)
        assert _r_u[0].just_clear is False and _r_u[0].revoke is False
        assert len(_r_g) == 1 and isinstance(_r_g[0], tl_messages.DeleteChatUserRequest)
        assert _r_g[0].revoke_history is False
        assert len(_r_c) == 1 and isinstance(_r_c[0], tl_channels.LeaveChannelRequest)
        try:
            build_remove_dialog_requests(tl_types.InputUserSelf())
            raise AssertionError("未知类型应当报错，而不是构造出必然失败的请求")
        except ValueError:
            pass
        # 删完必须能回头问服务端 —— 「真的删掉了」的唯一凭据，不能靠「没抛异常」
        assert callable(dialogs_still_present)
        assert asyncio.run(dialogs_still_present(None, [])) == ({}, "")

        # 「删了、服务端却说还在」时的备选写法：必须是**真的不同**的写法，
        # 否则「重试」只是把同一条必然无效的请求再发一遍。
        assert [s for s, _l in DIALOG_REMOVE_RETRY_STEPS] == [
            "explicit_peer", "max_id", "delete_dialog", "revoke", "block_cycle"]
        assert all(_l for _s, _l in DIALOG_REMOVE_RETRY_STEPS)
        # v1.3.2：用户发回的日志证明「对方账号已注销」的会话只会被删记录、条目被留下 ——
        # revoke / block_cycle 这两档就是专门冲它去的，且都带闸门，不误伤正常在用的联系人。
        assert is_ghost_user(tl_types.User(id=2, is_self=False, access_hash=1, deleted=True))
        assert is_ghost_user(tl_types.User(id=3, is_self=False, access_hash=1))
        assert not is_ghost_user(tl_types.User(
            id=4, is_self=False, access_hash=1, first_name="a"))
        assert not is_ghost_user(tl_types.User(id=5, is_self=True, access_hash=1))
        assert dialog_remove_tag(tl_types.User(id=5, is_self=True, access_hash=1)) == "（收藏夹）"
        assert dialog_remove_tag(tl_types.User(
            id=6, is_self=False, access_hash=1, deleted=True)) == "（对方账号已注销）"
        _live = tl_types.User(id=7, is_self=False, access_hash=1, first_name="live")
        assert asyncio.run(retry_remove_dialog(_EchoClient(), _live, "revoke")) is not None
        assert asyncio.run(retry_remove_dialog(_EchoClient(), _live, "block_cycle")) is not None
        _sent.clear()
        assert asyncio.run(retry_remove_dialog(
            _EchoClient(), tl_types.User(id=8, is_self=False, access_hash=1, deleted=True),
            "revoke")) is None
        assert _sent and _sent[-1].revoke is True
        _sent.clear()
        assert asyncio.run(retry_remove_dialog(
            _EchoClient(), tl_types.User(id=9, is_self=False, access_hash=1, deleted=True),
            "block_cycle")) is None
        assert [type(r).__name__ for r in _sent] == [
            "BlockRequest", "UnblockRequest", "DeleteHistoryRequest"], _sent
        # 已知硬限制要能识别出来（收藏夹 / 已注销的账号）—— 提示里要写清代价与出路
        assert "收藏夹" in dialog_remove_hint(tl_types.User(
            id=1, is_self=True, access_hash=1, first_name="me"))
        assert "不摘会话条目" in dialog_remove_hint(tl_types.User(
            id=10, is_self=False, access_hash=1, deleted=True))
        assert "注销" in dialog_remove_hint(tl_types.User(
            id=2, is_self=False, access_hash=1, first_name="x", deleted=True))
        assert dialog_remove_hint(usr) == ""
        assert safe_peer_id(usr) == get_peer_id(usr)
        assert safe_peer_id(None) is None

        # 日志必须落盘：用户报故障时界面日志早关了，只剩这个文件可查
        assert log_file_path() == os.path.join(BASE_DIR, LOG_FILE_NAME)
        append_log_file("__selftest__ 日志落盘自检")
        try:
            with open(log_file_path(), encoding="utf-8") as _lf:
                _logtxt = _lf.read()
        except OSError:
            _logtxt = ""
        assert "__selftest__" in _logtxt, "日志没有落盘：%s" % log_file_path()

        # 版本号与更新日志必须互相对得上：改了 APP_VERSION 却忘了写更新日志，
        # 界面就会出现「标题是 v1.1.2、日志停在 v1.1.1」这种前言不搭后语。
        assert re.match(r"^v\d+(\.\d+)+$", APP_VERSION), APP_VERSION
        assert CHANGELOG, "更新日志为空"
        assert len(CHANGELOG) >= 2, "更新日志至少保留一个历史版本"
        assert CHANGELOG[0][0] == APP_VERSION, (
            "APP_VERSION=%s 与更新日志首条=%s 不一致（发版时忘了加条目？）"
            % (APP_VERSION, CHANGELOG[0][0]))
        assert APP_VERSION in changelog_text()
        for _ver, _date, _items in CHANGELOG:
            assert re.match(r"^v\d+(\.\d+)+$", _ver) and _items, (_ver, _items)
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", _date), _date
        _vers = [tuple(int(x) for x in re.findall(r"\d+", v)) for v, _d, _i in CHANGELOG]
        assert _vers == sorted(_vers, reverse=True), _vers

        _selftest_say(lines, "请求构造 / 类型判定 / 分组规则（按类别各查各的开关）/ 活跃换算 OK")
        _selftest_say(lines, "删除会话：按类型分派（私聊删会话 · 频道群组退出）/ 删后服务端复核 + 5 步换写法重试 / 日志落盘 OK")
        _selftest_say(lines, "版本 %s，更新日志 %d 条版本记录" % (APP_VERSION, len(CHANGELOG)))

        # 界面骨架：真的建一次主窗口再销毁 —— 有些精简包能 import tkinter 但建窗就崩，
        # 只 import 是测不出来的。用临时目录接管四个数据文件，确保**不碰**用户的
        # 登录态与配置（自检绝不能动真实账号数据）。
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="tg_selftest_")
        # ⚠️ BASE_DIR 也要一起重定向：多账号的 session / 缓存路径都是从 BASE_DIR 拼的，
        # 少了这一步，自检就会把账号文件写到用户真实的程序目录里。
        BASE_DIR = tmpdir
        CONFIG_FILE = os.path.join(tmpdir, "cfg.json")
        LEGACY_SESSION_FILE = os.path.join(tmpdir, "tg_session")
        LEGACY_STATS_CACHE_FILE = os.path.join(tmpdir, "stats.json")
        LEGACY_ROLE_CACHE_FILE = os.path.join(tmpdir, "role.json")
        SESSION_FILE = LEGACY_SESSION_FILE
        STATS_CACHE_FILE = LEGACY_STATS_CACHE_FILE
        ROLE_CACHE_FILE = LEGACY_ROLE_CACHE_FILE
        r = tk.Tk()
        r.withdraw()
        try:
            app = App(r)
            r.update_idletasks()
            cols = tuple(str(c) for c in app.tree.cget("columns"))
            assert cols == ("sel", "name", "username", "type", "members",
                            "posts", "active", "folder", "risk", "id", "safe"), cols
            assert app.btn_leave is not None and app.btn_destroy is not None
            # 按钮文案必须写明它同时会删除会话（私聊 / 机器人退不了，只能删）
            assert "退出" in str(app.btn_leave.cget("text"))
            assert "删除" in str(app.btn_leave.cget("text")), app.btn_leave.cget("text")
            assert app.cmb_folder is not None
            # 列表上方的类型统计条（控件在、在表格上方、能算出文案）
            assert app.lbl_kind_stats is not None and app.stats_var is not None
            assert int(app.lbl_kind_stats.grid_info()["row"]) < int(app.tree.grid_info()["row"])
            app._update_stats()
            assert app.stats_var.get() != "", "统计条文案为空"
            # 版本号必须真的出现在界面上（标题栏 + 状态栏），不是只有代码里有常量
            assert APP_VERSION in str(r.title()), r.title()
            assert app.lbl_version is not None, "状态栏缺版本号标签"
            assert str(app.lbl_version.cget("text")) == APP_VERSION, app.lbl_version.cget("text")
            assert app.btn_changelog is not None and callable(app.on_show_changelog)
            # 多账号：账号条 + 账号页 + 按账号命名的 session / 缓存路径
            assert app.account_combo is not None and app.account_var is not None
            assert app.acc_tree is not None and app.phone_entry is not None
            assert app.btn_connect is not None
            assert account_key_from_phone("+8613800138000") == "8613800138000"
            assert account_key_from_phone("861 3800 1380") == "86138001380"
            assert account_key_from_phone("") == DEFAULT_ACCOUNT_KEY
            assert account_session_base({"key": "k1"}).endswith("tg_session_k1")
            # 旧账号记录里写明 session 基名时要照用（迁移来的那条指向 tg_session）
            assert account_session_base({"key": "k1", "session": "tg_session"}).endswith("tg_session")
            assert account_cache_file("stats_cache", "k1").endswith("tg_stats_cache_k1.json")
            assert app._stats_cache_path().endswith(".json")
            assert app._role_cache_path().endswith(".json")
            assert app._per_account() == {} and app._whitelist_of() == set()
            assert app._selected_account_key() is None
            _selftest_say(lines, "多账号：账号注册表 / 按账号 session 与缓存路径 / 切换入口控件 OK")
            # 批量划分分组：按钮 + 菜单 + 三档范围解析 + 按名字找分组
            assert app.btn_folder is not None, "缺少「📁 分组」按钮"
            assert app._folder_menu is not None, "缺少分组操作菜单"
            assert callable(app._folder_bulk_dialog) and callable(app.on_folder_action)
            assert app._find_filter_by_name("这个分组不存在") is None
            for _sc in (FOLDER_SCOPE_CHECKED, FOLDER_SCOPE_VISIBLE, FOLDER_SCOPE_ALL):
                assert isinstance(app._folder_scope_iids(_sc), list), _sc
            # 空列表下三档都必须返回空（未连接 / 未加载时不能抛）
            assert app._folder_scope_iids(FOLDER_SCOPE_ALL) == []
            _selftest_say(lines, "批量划分分组：分组按钮 / 操作菜单 / 三档范围（勾选·筛选结果·全部）OK")
            # 异常特征提取：按钮 + 菜单 + 分类器（全部来自服务端字段，零请求）
            assert app.btn_risk is not None, "缺少「🚫 异常」按钮"
            assert app._risk_menu is not None, "缺少异常筛选菜单"
            assert risk_codes_of(tl_types.ChannelForbidden(
                id=1, access_hash=1, title="x", broadcast=True)) == (RISK_FORBIDDEN,)
            assert RISK_GHOST in risk_codes_of(tl_types.User(
                id=2, is_self=False, access_hash=1, deleted=True))
            assert risk_codes_of(tl_types.Channel(
                id=3, title="ok", photo=None, date=None)) == ()
            assert app._risk_hit_count() == 0 and app._risk_counts() == {}
            _selftest_say(lines, "异常特征：🚫 异常按钮 / 分类菜单 / 零请求分类器（封禁·违规·诈骗·禁言…）OK")
        finally:
            r.destroy()
        _selftest_say(lines, "界面骨架 OK（列 = %s）" % (",".join(cols),))
    except Exception:
        ok = False
        _selftest_say(lines, "[自检失败]\n" + __import__("traceback").format_exc())
    _selftest_say(lines, "SELFTEST " + ("PASS" if ok else "FAIL"))
    try:
        with open(SELFTEST_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0 if ok else 1


def main():
    import sys

    if "--selftest" in sys.argv:
        return sys.exit(selftest())
    if "--version" in sys.argv or "-V" in sys.argv:
        console_write(f"{APP_NAME} {APP_VERSION}\n")
        return
    if "--changelog" in sys.argv:
        console_write(changelog_text())
        return
    print("[启动] 正在初始化界面 ...", file=sys.stderr, flush=True)
    try:
        root = tk.Tk()
        # 任何按钮/界面回调里的异常都弹窗显示，而不是静默崩溃
        root.report_callback_exception = _report_callback_exception
        App(root)
        print("[启动] 主窗口已创建，进入主循环", file=sys.stderr, flush=True)
        root.mainloop()
    except Exception:
        import traceback

        tb = traceback.format_exc()
        print("[致命错误]\n" + tb, file=sys.stderr, flush=True)
        _show_crash("程序启动/运行出错：\n\n" + tb)
        raise


if __name__ == "__main__":
    main()
