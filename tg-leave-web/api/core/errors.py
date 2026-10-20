"""异常 → 用户看得懂的一句话（移植自 v1.7.7 humanize_error）。

纪律：**认不出来的异常原样返回** —— 不假装认识，方便照原样去搜；
翻好的文案里不许残留英文类名（selftest 有反向验证）。"""

# (类名片段, 人话, 是否把原文也附上)
ERROR_HUMAN = (
    ("AuthKeyUnregisteredError", "登录态已失效（session 被撤销，或在别处退出过）—— 请重新登录这个账号", False),
    ("AuthKeyDuplicatedError", "同一份 session 在别处同时登录过，登录态已被 Telegram 撤销 —— 删掉它重新登录", False),
    ("SessionRevokedError", "这个登录态已被撤销，请重新登录", False),
    ("SessionPasswordNeededError", "该账号开了两步验证，需要填两步密码", False),
    ("PhoneCodeInvalidError", "验证码不对", False),
    ("PhoneCodeExpiredError", "验证码已过期，点「发送验证码」重发一次", False),
    ("PhoneNumberInvalidError", "手机号格式不对（要带国家码，例如 +8613800138000）", False),
    ("PhoneNumberBannedError", "这个手机号已被 Telegram 封禁", False),
    ("ApiIdInvalidError", "api_id / api_hash 不正确 —— 去 my.telegram.org 核对后再填", False),
    ("ApiIdPublishedFloodError", "这个 api_id 被限流了 —— 换一个应用，或过一段时间再试", False),
    ("UserAlreadyParticipantError", "已经是成员了，无需重复加入", False),
    ("UsernameNotOccupiedError", "这个用户名已经不存在（频道可能改名或已注销）", False),
    ("UsernameInvalidError", "用户名格式不对", False),
    ("ChannelPrivateError", "该频道已不可访问（被封 / 设为私密 / 你被拉黑）", False),
    ("ChannelInvalidError", "这个频道的实体已失效 —— 先点「刷新列表」再试", False),
    ("InviteHashExpiredError", "邀请链接已过期", False),
    ("InviteHashInvalidError", "邀请链接无效", False),
    ("ChatAdminRequiredError", "这个操作需要管理员权限", False),
    ("UserBannedInChannelError", "你在这个频道里被封禁了", False),
    ("ChatWriteForbiddenError", "你没有在这个群里发言的权限", False),
    ("PeerIdInvalidError", "服务端不认识这个会话（实体已失效）—— 先点「刷新列表」再试", False),
    ("FileNotFoundError", "找不到文件", True),
    ("PermissionError", "没有权限（文件可能被其它程序占用）", True),
    ("IsADirectoryError", "那是个目录，不是文件", True),
    ("DatabaseError", "本地 session 文件有问题 —— 把它删掉重新登录即可", False),
)

# 网络 / 代理类特征词（OSError/ConnectionError 类名不统一，只能从文案认）。
ERROR_NET_HINTS = ("getaddrinfo", "timed out", "timeout", "connection refused",
                   "connection reset", "network is unreachable", "cannot connect",
                   "connect failed", "10054", "10060", "11001", "proxy",
                   "unreachable host", "eof occurred")


def humanize_error(e):
    """异常 → 一句中文（含下一步）。认不出来就原样返回。"""
    if e is None:
        return ""
    name = type(e).__name__
    text = str(e or "").strip()
    low = text.lower()
    if name == "FloodWaitError" or "flood_wait" in low:
        secs = getattr(e, "seconds", None)
        return (f"被 Telegram 限流了，需要等 {secs} 秒（等完再试即可）" if secs
                else "被 Telegram 限流了，过一会儿再试")
    for frag, human, keep_raw in ERROR_HUMAN:
        if frag in name:
            return f"{human}：{text}" if (keep_raw and text) else human
    if any(h in low for h in ERROR_NET_HINTS):
        return "连不上 Telegram（网络或代理不通）—— 检查代理设置后再试"
    return text or name
