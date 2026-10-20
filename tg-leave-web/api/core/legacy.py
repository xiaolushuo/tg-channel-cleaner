# -*- coding: utf-8 -*-
"""旧版（Python 桌面版 v1.7.7）登录态发现与导入。

旧版目录特征：`tg_cleaner_config.json`（顶层 api_id/api_hash/proxy_*，
accounts[] 含 key/phone/session）+ 与 accounts[].session 同名的 `<名>.session` 文件
（旧版默认 `tg_session.session`，不带手机号后缀）。

导入 = 复制 .session（及 journal/wal/shm）到新数据目录 `tg_session_<key>.session`
+ 注册账号。Telethon SQLite 会话原样复用，重新连接时**无需验证码**。
"""
import json
import os
import shutil

from . import store as S


def default_search_dirs():
    """常见旧版位置：项目旁 dist、项目根、当前目录。"""
    web_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parent = os.path.dirname(web_root)
    cands = [
        os.path.join(parent, "dist"),   # 本项目旁边的旧版 dist（用户实际场景）
        os.path.join(web_root, "dist"),
        web_root,
        os.getcwd(),
    ]
    out, seen = [], set()
    for d in cands:
        d = os.path.abspath(d)
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def proxy_url_of(cfg):
    """旧版 proxy_* 字段 -> URL（socks5://host:port 或 http://user:pass@host:port）。"""
    if not cfg.get("proxy_enabled"):
        return ""
    ptype = (cfg.get("proxy_type") or "SOCKS5").strip().lower()
    if ptype not in ("socks5", "socks4", "http"):
        return ""
    host, port = (cfg.get("proxy_host") or "").strip(), (cfg.get("proxy_port") or "").strip()
    if not host or not port:
        return ""
    url = f"{ptype}://"
    if cfg.get("proxy_user"):
        url += f"{cfg['proxy_user']}:{cfg.get('proxy_pass', '')}@"
    return url + f"{host}:{port}"


def discover(base_dir):
    """读 base_dir 里的旧版配置，返回可导入账号列表（不落盘）。"""
    cfg_path = os.path.join(base_dir, "tg_cleaner_config.json")
    if not os.path.isfile(cfg_path):
        return []
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []
    proxy = proxy_url_of(cfg)
    out = []
    for acc in cfg.get("accounts", []) or []:
        phone = (acc.get("phone") or "").strip()
        if not phone:
            continue
        name = (acc.get("session") or "").strip() or "tg_session"
        src = os.path.join(base_dir, name if name.endswith(".session") else name + ".session")
        if not os.path.isfile(src):
            # 兜底：该目录下首个 .session
            try:
                alts = sorted(fn for fn in os.listdir(base_dir) if fn.endswith(".session"))
            except OSError:
                alts = []
            src = os.path.join(base_dir, alts[0]) if alts else src
        out.append({
            "key": acc.get("key") or S.account_key_from_phone(phone),
            "phone": phone,
            "nick": acc.get("label") or acc.get("username") or "",
            "api_id": str(cfg.get("api_id", "")),
            "api_hash": str(cfg.get("api_hash", "")),
            "proxy": proxy,
            "session_src": src if os.path.isfile(src) else "",
            "session_name": os.path.basename(src) if os.path.isfile(src) else name + ".session",
        })
    return out


def import_one(accounts_store, legacy):
    """复制 .session 并注册账号。返回 dict（含 ok/note/key）。"""
    src = legacy.get("session_src") or ""
    if not src or not os.path.isfile(src):
        return {"ok": False, "key": legacy.get("key", ""),
                "note": "找不到旧版 .session 文件"}
    key = S.account_key_from_phone(legacy["phone"])
    dst = S.session_path_for(key)
    # 目标不存在或比源旧才复制（不覆盖更新的会话）
    if not (os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)):
        shutil.copy2(src, dst)
        for extra in ("-journal", "-wal", "-shm"):
            p = src + extra
            if os.path.isfile(p):
                try:
                    shutil.copy2(p, dst + extra)
                except Exception:
                    pass
    accounts_store.upsert(legacy.get("api_id", ""), legacy.get("api_hash", ""),
                          legacy["phone"], legacy.get("proxy", ""),
                          legacy.get("nick", ""))
    return {"ok": True, "key": key,
            "note": f"已导入 {os.path.basename(src)} -> {os.path.basename(dst)}"}
