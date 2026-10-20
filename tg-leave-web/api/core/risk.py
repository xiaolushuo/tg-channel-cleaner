# -*- coding: utf-8 -*-
"""风险识别（移植自 Python 版，零请求、只认服务端字段）。"""
from telethon.tl import types as tl_types
from .constants import (
    RISK_FORBIDDEN, RISK_RESTRICTED, RISK_SCAM, RISK_FAKE, RISK_DEAD, RISK_BANNED,
    RISK_NOJOIN, RISK_NEEDJOIN, RISK_GHOST, RISK_ORDER, RISK_SHORT, RISK_SEP,
    RESTRICTION_REASON_LABEL, RESTRICTION_TEXT_HINTS,
)


# RestrictionReason.platform -> 平台中文标注（空 / all = 对所有平台生效）
PLATFORM_LABEL = {"ios": "iOS", "android": "Android", "web": "Web", "webk": "Web(K)",
                  "macos": "macOS", "tdesktop": "桌面端"}


def _platform_of(reason_obj):
    return str(getattr(reason_obj, "platform", "") or "").strip().lower()


def restriction_reasons(ent):
    """restriction_reason -> [(短标签, 服务端原文, 平台), ...]。代码优先、文案兜底。"""
    out = []
    for r in (getattr(ent, "restriction_reason", None) or []):
        reason = str(getattr(r, "reason", "") or "").strip()
        text = str(getattr(r, "text", "") or "").strip()
        platform = _platform_of(r)
        label = RESTRICTION_REASON_LABEL.get(reason.lower())
        if not label:
            low = text.lower()
            if any(h in low for h in RESTRICTION_TEXT_HINTS):
                label = "服务端标注受限"
            else:
                label = reason or "受限"
        out.append((label, text, platform))
    return out


def _restriction_entries(ent):
    """只保留「真封锁」的限制条目：全平台生效（platform 为空或 all）且不是 sensitive。

    - iOS / Android / Web 专属屏蔽（platform=ios 等）在桌面 / 第三方客户端完全可看，
      官方客户端也只在命中本平台条目时才显示受限 —— 当异常是大面积误报（真机 308 条 ios）。
    - reason=sensitive 是「敏感内容标记」（服务端对内容的分级标注，文本为空），
      会员在客户端里照样能看 —— 不是封锁，不算异常（真机 4 条，用户实名反馈误报）。
    - 剩下如 all/terms（违反服务条款）才是各端都打不开的真封禁，保留标记。
    """
    out = []
    for e in restriction_reasons(ent):
        label, _text, platform = e
        if platform not in ("", "all"):
            continue
        if label == "敏感内容":  # reason=sensitive
            continue
        out.append(e)
    return out


def restriction_labels(ent):
    """受限原因的中文短标签（只算全平台生效的），多条用「、」连接。"""
    labels = []
    for label, _text, _p in _restriction_entries(ent):
        if label and label not in labels:
            labels.append(label)
    return "、".join(labels)


def risk_raw_text(ent):
    """服务端原文（刻意单独抽取，绝不混进中文说明）。带平台标注。"""
    parts = []
    for _label, text, platform in restriction_reasons(ent):
        t = (text or "").strip()
        if not t:
            continue
        tag = ""
        p = PLATFORM_LABEL.get(platform)
        if platform and platform != "all" and p:
            tag = f"（仅 {p}）"
        piece = t + tag
        if piece not in parts:
            parts.append(piece)
    return " / ".join(parts)


def restriction_platforms(ent):
    """该实体限制条目涉及的平台集合（如 ["ios"]）；无限制返回 []。诊断用。"""
    if not getattr(ent, "restriction_reason", None):
        return []
    seen = []
    for _l, _t, p in restriction_reasons(ent):
        if p and p not in seen:
            seen.append(p)
    return seen


def _send_banned(rights):
    """这份 ChatBannedRights 是否禁止发言。"""
    return bool(rights) and bool(getattr(rights, "send_messages", False))


def risk_of(ent):
    """提取一个会话的异常特征，返回 [(类别码, 人话原因), ...]；空列表 = 没发现异常。

    全部来自服务端已给字段，零请求。
    """
    out = []
    if ent is None:
        return out
    if isinstance(ent, tl_types.ChannelForbidden):
        return [(RISK_FORBIDDEN, "频道已不可访问（被封 / 被踢 / 已删）")]
    if isinstance(ent, tl_types.ChatForbidden):
        return [(RISK_FORBIDDEN, "群已不可访问（被封 / 被踢 / 已删）")]
    if isinstance(ent, tl_types.Channel):
        if getattr(ent, "scam", False):
            out.append((RISK_SCAM, "Telegram 官方标记为诈骗"))
        if getattr(ent, "fake", False):
            out.append((RISK_FAKE, "Telegram 官方标记为假冒"))
        # 违规受限：只认 restricted 标志位 + 「真封锁」条目（全平台生效且非 sensitive）。
        if getattr(ent, "restricted", False):
            labels = restriction_labels(ent)
            if labels:
                out.append((RISK_RESTRICTED, labels))
            elif not restriction_reasons(ent):
                # 有 restricted 标志但一条 reason 都没有 —— 兜底提示
                out.append((RISK_RESTRICTED, "被标记受限（未给出原因）"))
            # reasons 只有 sensitive 标记 / 其它平台专属 → 本端可正常访问，不标记
        if getattr(ent, "left", False):
            out.append((RISK_DEAD, "你已不在其中（会话条目残留）"))
        if getattr(ent, "join_request", False):
            out.append((RISK_NOJOIN, "加入需管理员批准，不能直接关注"))
        elif getattr(ent, "join_to_send", False):
            out.append((RISK_NEEDJOIN, "必须先加入才能发言"))
        if _send_banned(getattr(ent, "banned_rights", None)):
            out.append((RISK_BANNED, "你被禁止发言"))
        elif _send_banned(getattr(ent, "default_banned_rights", None)):
            out.append((RISK_BANNED, "全员默认被禁止发言"))
    elif isinstance(ent, tl_types.Chat):
        if getattr(ent, "deactivated", False):
            out.append((RISK_DEAD, "群已停用（通常是封禁）"))
        if getattr(ent, "left", False):
            out.append((RISK_DEAD, "你已不在其中"))
        if _send_banned(getattr(ent, "default_banned_rights", None)):
            out.append((RISK_BANNED, "全员默认被禁止发言"))
    elif isinstance(ent, tl_types.User):
        if getattr(ent, "deleted", False):
            out.append((RISK_GHOST, "对方账号已注销（客户端里是灰头像 / 纯数字名）"))
        if getattr(ent, "scam", False):
            out.append((RISK_SCAM, "Telegram 官方标记为诈骗"))
        if getattr(ent, "fake", False):
            out.append((RISK_FAKE, "Telegram 官方标记为假冒"))
        # 与 Channel 同规则：只认「真封锁」条目（全平台生效且非 sensitive）。
        # 真封禁的机器人 / 账号（all/terms）→ 标签用具体原因；平台专属 → 不标记。
        if getattr(ent, "restricted", False):
            labels = restriction_labels(ent)
            if labels:
                out.append((RISK_RESTRICTED, labels))
            elif not restriction_reasons(ent):
                out.append((RISK_RESTRICTED, "账号被标记受限（未给出原因）"))
    return out


def risk_codes_of(ent):
    """去重、按严重度排序的类别码。"""
    codes = set(c for c, _why in risk_of(ent))
    return tuple(c for c in RISK_ORDER if c in codes)


def risk_rows(ent):
    """一个会话的全部异常特征 -> [(类别码, 短标签, 说明, 服务端原文), ...]。

    所有呈现点都从这里取：表格列 / 日志 / CSV / Excel 明细。
    """
    raw = risk_raw_text(ent)
    return [(code, RISK_SHORT.get(code, code), why, raw) for code, why in risk_of(ent)]


def risk_cell_text(ent):
    """表格「异常」列：全部类别的短标签并排。"""
    return RISK_SEP.join(short for _c, short, _w, _r in risk_rows(ent))
