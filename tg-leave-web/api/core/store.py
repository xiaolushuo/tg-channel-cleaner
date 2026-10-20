# -*- coding: utf-8 -*-
"""本地持久化（账号 / 配置 / 统计缓存 / 角色缓存 / 历史 / 快照 / 白名单）。

移植自 Python 版的多账号数据文件命名：每个账号一套文件，按账号 key 分文件。
"""
import json
import os
import time

BASE_DIR = os.environ.get("TGLEAVE_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")

DEFAULT_ACCOUNT_KEY = "default"
ACCOUNT_SESSION_PREFIX = "tg_session_"
CONFIG_FILE = os.path.join(BASE_DIR, "tg_cleaner_config.json")


def account_key_from_phone(phone):
    """手机号 -> 稳定的账号 key（只取数字）。"""
    return "".join(ch for ch in (phone or "") if ch.isdigit()) or DEFAULT_ACCOUNT_KEY


def ensure_dir():
    os.makedirs(BASE_DIR, exist_ok=True)


def session_path_for(key):
    ensure_dir()
    return os.path.join(BASE_DIR, f"{ACCOUNT_SESSION_PREFIX}{key}.session")


def cache_file(kind, key):
    ensure_dir()
    return os.path.join(BASE_DIR, f"tg_{kind}_{key}.json")


# ---------------- 账号 ----------------
class AccountStore:
    def __init__(self):
        self.accounts = []        # list[dict]
        self.current = ""
        self.load()

    def load(self):
        try:
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    root = json.load(f)
                self.accounts = root.get("accounts", []) or []
                self.current = root.get("current", "")
        except Exception:
            self.accounts, self.current = [], ""
        if self.accounts and not any(
                account_key_from_phone(a.get("phone", "")) == self.current for a in self.accounts):
            self.current = account_key_from_phone(self.accounts[0].get("phone", ""))

    def save(self):
        ensure_dir()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"accounts": self.accounts, "current": self.current}, f,
                      ensure_ascii=False, indent=2)

    def current_account(self):
        key = self.current or (account_key_from_phone(self.accounts[0]["phone"]) if self.accounts else "")
        return next((a for a in self.accounts
                     if account_key_from_phone(a.get("phone", "")) == key), None)

    def by_key(self, key):
        return next((a for a in self.accounts
                     if account_key_from_phone(a.get("phone", "")) == key), None)

    def upsert(self, api_id, api_hash, phone, proxy="", nick=""):
        key = account_key_from_phone(phone)
        old = self.by_key(key)
        if old:
            old["api_id"] = api_id or old.get("api_id", "")
            old["api_hash"] = api_hash or old.get("api_hash", "")
            old["proxy"] = proxy or old.get("proxy", "")
            if nick:
                old["nick"] = nick
        else:
            self.accounts.append({
                "api_id": api_id, "api_hash": api_hash, "phone": phone,
                "proxy": proxy, "nick": nick})
            self.current = key
        if not self.current:
            self.current = key
        self.save()
        return key

    def switch_to(self, key):
        if self.by_key(key):
            self.current = key
            self.save()
            return True
        return False

    def remove(self, key, delete_files=False):
        acc = self.by_key(key)
        if not acc:
            return False
        self.accounts = [a for a in self.accounts if a is not acc]
        if self.current == key:
            self.current = account_key_from_phone(self.accounts[0]["phone"]) if self.accounts else ""
        self.save()
        if delete_files:
            sp = session_path_for(key)
            for p in (sp, sp + "-journal", cache_file("stats", key),
                      cache_file("role", key), cache_file("history", key),
                      cache_file("snapshot", key), cache_file("whitelist", key)):
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except Exception:
                    pass
        return True


# ---------------- 统计 / 角色缓存 ----------------
class CacheStore:
    def __init__(self, kind, key, ttl_hours=24):
        self.path = cache_file(kind, key)
        self.ttl = ttl_hours * 3600.0
        self.data = {}        # key -> (ts, value)
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
        except Exception:
            self.data = {}

    def save(self):
        ensure_dir()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except Exception:
            pass

    def get(self, k, ttl_hours=None):
        """ttl_hours=None 用构造时的默认 TTL；传入则本次查询生效（对齐 v1.7.7 的缓存有效期设置）。"""
        if k in self.data:
            ts, val = self.data[k]
            limit = (ttl_hours * 3600.0) if ttl_hours is not None else self.ttl
            if (time.time() - ts) <= limit:
                return val
        return None

    def put(self, k, v):
        self.data[k] = (time.time(), v)
        self.save()


# ---------------- 操作历史 ----------------
class HistoryStore:
    LIMIT = 2000

    def __init__(self, key):
        self.path = cache_file("history", key)
        self.items = []
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, encoding="utf-8") as f:
                    self.items = json.load(f)
        except Exception:
            self.items = []

    def save(self):
        ensure_dir()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.items[-self.LIMIT:], f, ensure_ascii=False)
        except Exception:
            pass

    def add(self, entry):
        self.items.append(entry)
        if len(self.items) > self.LIMIT:
            self.items = self.items[-self.LIMIT:]
        self.save()

    @staticmethod
    def rejoin_kind(value):
        v = (value or "").strip()
        if v.startswith("https://t.me/") or v.startswith("http://t.me/") or v.startswith("t.me/"):
            return "invite", v.split("/")[-1]
        if v.startswith("+"):
            return "invite", v[1:]
        return "username", v.lstrip("@")


# ---------------- 快照 ----------------
class SnapshotStore:
    SLOTS = 3

    def __init__(self, key):
        self.path = cache_file("snapshot", key)
        self.slots = [None, None, None]
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, encoding="utf-8") as f:
                    self.slots = json.load(f)
                while len(self.slots) < self.SLOTS:
                    self.slots.append(None)
        except Exception:
            self.slots = [None, None, None]

    def save(self):
        ensure_dir()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.slots, f, ensure_ascii=False)
        except Exception:
            pass

    def save_into(self, index, snapshot):
        self.slots[index] = snapshot
        self.save()

    def load_slot(self, index):
        return self.slots[index] if 0 <= index < self.SLOTS else None


# ---------------- 白名单 ----------------
class WhitelistStore:
    def __init__(self, key):
        self.path = cache_file("whitelist", key)
        self.set = set()
        self.load()

    def load(self):
        try:
            if os.path.isfile(self.path):
                with open(self.path, encoding="utf-8") as f:
                    self.set = set(json.load(f))
        except Exception:
            self.set = set()

    def save(self):
        ensure_dir()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(sorted(self.set), f, ensure_ascii=False)
        except Exception:
            pass

    def has(self, k):
        return k in self.set

    def add(self, k):
        self.set.add(k)
        self.save()

    def remove(self, k):
        self.set.discard(k)
        self.save()

    def toggle(self, k):
        if k in self.set:
            self.set.discard(k)
            self.save()
            return False
        self.set.add(k)
        self.save()
        return True
