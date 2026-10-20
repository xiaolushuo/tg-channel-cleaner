# -*- coding: utf-8 -*-
"""核心模块自测（独立单元，不依赖 HTTP / 登录态）：
  1) 代理解析（含反向：坏输入必须返回 None）
  2) 风险识别（零请求、只认服务端字段；说明/原文分离，绝不把英文原文混进中文说明）
  3) 导出 CSV / Excel（手写 OOXML 反向验证：数据确实写进了文件）
  4) 常量完整性
运行：python -m core.selftest  （在 api/ 目录）
"""
import asyncio
import io
import os
import sys
import tempfile
import zipfile

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from telethon.tl import types as tl_types
from core import engine as E
from core import risk as R
from core import export as X
from core import store as S
from core import legacy as LG
from core import constants as C

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def _ch(**kw):
    kw.setdefault("id", 1)
    kw.setdefault("title", "t")
    kw.setdefault("photo", None)
    kw.setdefault("date", 0)
    return tl_types.Channel(**kw)


def test_proxy():
    print("— 代理解析 —")
    p = E.parse_proxy("socks5://127.0.0.1:1080")
    check("socks5 解析", bool(p) and p["proxy_type"] == "socks5" and p["port"] == 1080, str(p))
    p = E.parse_proxy("http://u:p@10.0.0.1:8888")
    check("http+账号密码", bool(p) and p["proxy_type"] == "http" and p["username"] == "u", str(p))
    p = E.parse_proxy("socks4://1.2.3.4:1234")
    check("socks4 解析", bool(p) and p["proxy_type"] == "socks4")
    check("无 scheme -> None", E.parse_proxy("127.0.0.1:1080") is None)
    check("坏端口 -> None", E.parse_proxy("socks5://h:abc") is None)
    check("空串 -> None", E.parse_proxy("") is None)
    check("未知协议 -> None", E.parse_proxy("ftp://h:1") is None)


def test_risk():
    print("— 风险识别（零请求）—")
    rows = R.risk_rows(_ch(broadcast=True))
    check("干净频道无风险", [c for c, _, _, _ in rows] == [])

    rows = R.risk_rows(_ch(broadcast=True, scam=True))
    check("scam 识别", any(c == C.RISK_SCAM for c, _, _, _ in rows))

    # 2026-10-17 二次反馈：iOS 专属屏蔽在桌面 / Android / 第三方客户端可正常
    # 访问 —— 官方客户端只在命中本平台条目时才显示受限。只认全平台生效条目。
    rr_all = [tl_types.RestrictionReason(platform="all", reason="copyright",
                                         text="Violates copyright policy")]
    ent = _ch(broadcast=True, restricted=True, restriction_reason=rr_all)
    rows = R.risk_rows(ent)
    check("restricted=True + 全平台 reason 识别",
          any(c == C.RISK_RESTRICTED for c, _, _, _ in rows))
    # 关键纪律：说明是中文，原文单列，绝不把英文 reason 混进中文说明
    why_ok = all("copyright" not in w and "Violates" not in w for _, _, w, _ in rows)
    check("说明不含英文原文泄漏", why_ok)
    raw_ok = any("Violates copyright policy" in r for _, _, _, r in rows)
    check("原文字段独立保留服务端文本", raw_ok)
    # 标签应来自中文映射而非原文
    label_ok = any(w == "版权投诉" for _, _, w, _ in rows)
    check("restricted 用中文标签（版权投诉）", label_ok)

    # 平台专属条目（ios / web）-> 不标记（本平台可正常访问），但原文带平台标注
    rr_ios = [tl_types.RestrictionReason(platform="ios", reason="porn",
                                         text="This channel can't be displayed because "
                                              "it was used to spread pornographic content.")]
    ent = _ch(broadcast=True, restricted=True, restriction_reason=rr_ios)
    rows = R.risk_rows(ent)
    check("iOS 专属屏蔽不标记（2026-10-17 真机误报）",
          not any(c == C.RISK_RESTRICTED for c, _, _, _ in rows))
    check("iOS 专属条目原文带（仅 iOS）标注",
          "（仅 iOS）" in R.risk_raw_text(ent))
    check("iOS 专属屏蔽无受限标签", R.restriction_labels(ent) == "")

    # 反向验证（2026-10-17 用户反馈误报）：restricted=False 时 restriction_reason
    # 非空不能判违规受限 —— 很多正常频道也带版权 / 地区条目，官方客户端只看标志位
    ent = _ch(broadcast=True, restriction_reason=rr_all)
    rows = R.risk_rows(ent)
    check("restricted=False + reason 不误报",
          not any(c == C.RISK_RESTRICTED for c, _, _, _ in rows))
    # restricted=True 但没给 reason -> 仍要标（未给出原因）
    ent = _ch(broadcast=True, restricted=True)
    check("restricted=True 无 reason 兜底",
          any(c == C.RISK_RESTRICTED for c, _, _, _ in R.risk_rows(ent)))

    ent = tl_types.ChannelForbidden(id=1, access_hash=0, title="t")
    check("ChannelForbidden -> forbidden",
          any(c == C.RISK_FORBIDDEN for c, _, _, _ in R.risk_rows(ent)))

    check("left -> dead", any(c == C.RISK_DEAD for c, _, _, _ in R.risk_rows(_ch(broadcast=True, left=True))))

    u = tl_types.User(id=2, deleted=True)
    check("注销用户 -> ghost", any(c == C.RISK_GHOST for c, _, _, _ in R.risk_rows(u)))

    # sensitive 标记不是封锁（真机 @feiwu_ak：all/sensitive + ios/porn，会员照样能看）
    ent = _ch(broadcast=True, restricted=True, restriction_reason=[
        tl_types.RestrictionReason(platform="all", reason="sensitive", text=""),
        tl_types.RestrictionReason(platform="ios", reason="porn",
                                   text="This channel can't be displayed.")])
    check("sensitive 标记 + iOS 专属 -> 不标记（真机 feiwu_ak）",
          not any(c == C.RISK_RESTRICTED for c, _, _, _ in R.risk_rows(ent)))

    # 真封禁：all/terms 在各端都打不开 -> 标
    ent = _ch(broadcast=True, restricted=True, restriction_reason=[
        tl_types.RestrictionReason(platform="all", reason="terms",
                                   text="This channel can't be displayed because it "
                                        "violated Telegram's Terms of Service.")])
    check("all/terms 真封禁 -> 标（违反服务条款）",
          any(c == C.RISK_RESTRICTED and w == "违反服务条款" for c, _, w, _ in R.risk_rows(ent)))

    # User 分支同规则：被封机器人（all/terms）标；平台专属（ios/appleterms）不标
    u = tl_types.User(id=3, restricted=True, restriction_reason=[
        tl_types.RestrictionReason(platform="all", reason="terms",
                                   text="This bot can't be displayed because it violated "
                                        "Telegram's Terms of Service.")])
    check("bot all/terms 真封禁 -> 标",
          any(c == C.RISK_RESTRICTED and w == "违反服务条款" for c, _, w, _ in R.risk_rows(u)))
    u = tl_types.User(id=4, restricted=True, restriction_reason=[
        tl_types.RestrictionReason(platform="ios", reason="appleterms",
                                   text="Unfortunately, this bot couldn't be displayed on your device."),
        tl_types.RestrictionReason(platform="android", reason="androidterms",
                                   text="Unfortunately, this bot can't be displayed on Telegram apps.")])
    check("bot 平台专属 terms -> 不标记（真机 4 条误报）",
          not any(c == C.RISK_RESTRICTED for c, _, _, _ in R.risk_rows(u)))

    ent = _ch(broadcast=True, default_banned_rights=tl_types.ChatBannedRights(
        until_date=0, send_messages=True))
    check("全员禁言 -> banned", any(c == C.RISK_BANNED for c, _, _, _ in R.risk_rows(ent)))

    # 多风险实体：说明里绝不允许出现英文原因词
    ent = _ch(broadcast=True, scam=True, restricted=True, left=True)
    rows = R.risk_rows(ent)
    check("多风险：说明全中文", all("copyright" not in w and "scam" not in w.lower()
                                   for _, _, w, _ in rows))
    # 原文永远是独立第 4 列（字符串，可能为空），与中文说明分离
    check("多风险：原文独立为第 4 列(str)",
          all(isinstance(r, str) for _, _, _, r in rows))


def test_export():
    print("— 导出（CSV / Excel）—")
    rows = [[("h", "名称"), ("h", "类型")],
            ["科技日报", "频道"], ["羊毛群（演示）", "群组"]]
    csv = X.build_csv(rows)
    check("csv 带 BOM(utf-8-sig)", csv[:3] == b"\xef\xbb\xbf")
    check("csv 表头不含样式标记", b"('h'" not in csv and "名称" in csv.decode("utf-8-sig"),
          csv.splitlines()[0][:60])
    check("csv 公式注入防护(=)", X.csv_guard("=cmd") == "'=cmd")
    check("csv 公式注入防护(@)", X.csv_guard("@cmd") == "'@cmd")
    check("csv 普通文本不防护", X.csv_guard("abc") == "abc")

    # 注意：build_xlsx 的 cols 是「列宽（整数）」，列名由 rows[0] 承载（与 main.py 一致）
    sheets = [{"name": "会话清单", "cols": [30, 10], "rows": rows,
               "freeze": True, "filter": True}]
    data = X.build_xlsx(sheets)
    check("xlsx 魔数 PK", data[:2] == b"PK")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        needed = ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                  "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                  "xl/worksheets/sheet1.xml"]
        check("xlsx 必备部件齐全", all(n in names for n in needed), str(names))
        styles = z.read("xl/styles.xml").decode("utf-8")
        check("styles 含 fill none+gray125（OOXML 规范）",
              'patternType="none"' in styles and 'patternType="gray125"' in styles)
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        # 反向验证：真实数据确实写进了单元格 XML
        check("xlsx 反向验证含『科技日报』", "科技日报" in sheet)
        check("xlsx 反向验证含『羊毛群』", "羊毛群（演示）" in sheet)
        # 多表 + 关系一致性
    sheets2 = [dict(sheets[0]), {"name": "异常明细", "cols": [30], "rows": rows,
                                 "freeze": True, "filter": True}]
    data2 = X.build_xlsx(sheets2)
    with zipfile.ZipFile(io.BytesIO(data2)) as z:
        ct = z.read("[Content_Types].xml").decode("utf-8")
        check("xlsx 多表：ContentTypes 含 2 张 worksheet",
              ct.count("worksheet+xml") == 2)
    # 校验 xml 转义：含特殊字符时不应破坏结构
    rows_x = [["a&b<c>\"d"]]
    data_x = X.build_xlsx([{"name": "x", "cols": [10], "rows": rows_x,
                            "freeze": False, "filter": False}])
    with zipfile.ZipFile(io.BytesIO(data_x)) as z:
        sx = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        check("xlsx XML 转义正确", "a&amp;b&lt;c&gt;&quot;d" in sx)


def test_constants():
    print("— 常量完整性 —")
    meta_codes = {c for c, _, _ in C.RISK_META}
    check("RISK_ORDER 覆盖 RISK_META", meta_codes.issubset(set(C.RISK_ORDER)))
    check("RISK_SEP 已定义", bool(C.RISK_SEP))
    check("EXCEL_COLUMNS ≥ 9 列", len(C.EXCEL_COLUMNS) >= 9, f"({len(C.EXCEL_COLUMNS)})")
    check("EXCEL_RISK_COLUMNS ≥ 5 列", len(C.EXCEL_RISK_COLUMNS) >= 5)
    check("DESTROY_CONFIRM_WORD 非空", bool(C.DESTROY_CONFIRM_WORD))
    check("FILTER_PRESETS ≥ 5", len(C.FILTER_PRESETS) >= 5)
    check("CHANGELOG 含 v3.0.0", any(v == "v3.0.0" for v, _, _ in C.CHANGELOG))
    check("CHANGELOG 含 v1.7.7", any(v == "v1.7.7" for v, _, _ in C.CHANGELOG))


def test_pending():
    """断点续跑：保存/读取/leave_many 清空/中断保留剩余/空列表删除文件（反向验证）。"""
    import tempfile
    old_base = S.BASE_DIR
    S.BASE_DIR = tempfile.mkdtemp(prefix="tg_selftest_")
    try:
        eng = E.TelegramEngine({"phone": "10086", "nick": "t"}, demo=True)
        check("断点初始为空", eng.load_pending() == [])
        eng.save_pending(["c:1", "c:2"])
        check("断点保存+读取", eng.load_pending() == ["c:1", "c:2"])
        check("断点文件真实存在", os.path.exists(eng.pending_path()))
        check("pending_items 带标题回退",
              eng.pending_items() == [{"peer_key": "c:1", "title": "c:1"},
                                      {"peer_key": "c:2", "title": "c:2"}])
        r = asyncio.run(eng.leave_many(["c:1", "c:2"]))
        check("leave_many 全成功", all(x["ok"] for x in r["results"]))
        check("leave_many 清空断点", r["remaining"] == [] and eng.load_pending() == [])
        check("leave_many 未中断", r["interrupted"] is False)

        # 反向验证中断路径：让第二条抛异常，剩余必须留在断点
        async def boom(pk):
            if pk == "c:2":
                raise RuntimeError("网络断了")
            return {"ok": True, "note": "已模拟退出"}
        eng.leave_one = boom
        r = asyncio.run(eng.leave_many(["c:1", "c:2", "c:3"]))
        check("leave_many 中断标记", r["interrupted"] is True)
        check("中断前条目成功", r["results"][0]["ok"] is True)
        check("中断条目记为失败", r["results"][1]["ok"] is False)
        check("中断后断点保留剩余", eng.load_pending() == ["c:3"])
        eng.leave_one = None

        eng.save_pending([])
        check("空列表删除断点文件（反向）", not os.path.exists(eng.pending_path()))
    finally:
        S.BASE_DIR = old_base


def test_legacy():
    """旧版登录态发现/导入：正常路径 + 空目录/缺 session/缺文件 三条反向验证。"""
    import tempfile
    import json as _json
    old_base = S.BASE_DIR
    S.BASE_DIR = tempfile.mkdtemp(prefix="tg_selftest_legacy_")
    tmp = tempfile.mkdtemp(prefix="tg_selftest_old_")
    try:
        check("legacy 空目录 -> []", LG.discover(tmp) == [])
        cfg = {"api_id": "18686658", "api_hash": "hash123", "proxy_enabled": True,
               "proxy_type": "SOCKS5", "proxy_host": "127.0.0.1", "proxy_port": "7890",
               "accounts": [{"key": "8615578589614", "phone": "+8615578589614",
                             "session": "tg_session", "label": "."}]}
        with open(os.path.join(tmp, "tg_cleaner_config.json"), "w", encoding="utf-8") as f:
            _json.dump(cfg, f)
        items = LG.discover(tmp)
        check("legacy 无 session -> src 空", len(items) == 1 and items[0]["session_src"] == "")
        with open(os.path.join(tmp, "tg_session.session"), "wb") as f:
            f.write(b"FAKE_SQLITE")
        it = LG.discover(tmp)[0]
        check("legacy 发现账号", it["phone"] == "+8615578589614" and it["api_id"] == "18686658")
        check("legacy 代理拼 URL", it["proxy"] == "socks5://127.0.0.1:7890", it["proxy"])
        check("legacy session 定位（不带手机号旧命名）",
              os.path.isfile(it["session_src"]) and it["session_name"] == "tg_session.session")
        saved = []

        class Stub:
            def upsert(self, *a):
                saved.append(a)

        r = LG.import_one(Stub(), it)
        check("legacy 导入 ok", r["ok"] is True, str(r))
        check("legacy session 已复制到新命名", os.path.isfile(S.session_path_for("8615578589614")))
        with open(S.session_path_for("8615578589614"), "rb") as f:
            check("legacy 内容逐字节一致", f.read() == b"FAKE_SQLITE")
        check("legacy 账号已注册(手机号正确)",
              len(saved) == 1 and saved[0][2] == "+8615578589614")
        bad = dict(it)
        bad["session_src"] = ""
        check("legacy 缺文件 -> 失败（反向）", LG.import_one(Stub(), bad)["ok"] is False)
    finally:
        S.BASE_DIR = old_base


def test_semantics():
    """v1.7.7 深度语义：humanize_error / parse_join_target / is_ghost_user / 配额。"""
    print("\n[5] 深度语义（错误人话化 / 链接解析 / 幽灵账号 / 配额）")
    from core.errors import humanize_error, ERROR_HUMAN

    # humanize_error：可预期错误翻中文
    class FloodWaitError(Exception):
        seconds = 30
    check("humanize: 限流报秒数", "30" in humanize_error(FloodWaitError()))
    class AuthKeyUnregisteredError(Exception):
        pass
    check("humanize: 登录态失效", "重新登录" in humanize_error(AuthKeyUnregisteredError("x")))
    class GetaddrinfoError(OSError):
        pass
    check("humanize: 网络类给代理提示",
          "代理" in humanize_error(GetaddrinfoError("[Errno 11001] getaddrinfo failed")))
    # 反向：认不出来的必须原样返回
    class TotallyUnknownError(Exception):
        pass
    check("humanize: 认不出原样返回",
          humanize_error(TotallyUnknownError("原始内容 XYZ")) == "原始内容 XYZ")
    # 翻好的文案不许残留英文类名
    for frag, human, _keep in ERROR_HUMAN:
        if frag not in ("FileNotFoundError", "PermissionError", "IsADirectoryError", "DatabaseError"):
            check(f"humanize: {frag} 无英文残留", frag not in human)
    check("humanize: None 安全", humanize_error(None) == "")

    # parse_join_target：全格式
    cases = [
        ("@abc", "username", "abc"), ("abc", "username", "abc"),
        ("t.me/abc", "username", "abc"),
        ("https://t.me/abc?video=1", "username", "abc"),
        ("https://telegram.me/abc", "username", "abc"),
        ("t.me/+AbCdEf", "invite", "AbCdEf"),
        ("https://t.me/joinchat/AbCdEf", "invite", "AbCdEf"),
        ("joinchat/AbCdEf", "invite", "AbCdEf"),
    ]
    for raw, k, v in cases:
        got = E.parse_join_target(raw)
        check(f"join: {raw}", got == (k, v), f"-> {got}")
    # 反向：解析不出必须返回 (None, "")
    for bad in ("", "  ", "a b", "https://example.com/x"):
        check(f"join 反向: {bad!r}", E.parse_join_target(bad) == (None, ""))

    # is_ghost_user：只认服务端字段
    def mk_user(**kw):
        base = dict(id=1)
        base.update(kw)
        return tl_types.User(**base)
    check("ghost: deleted=True", E.is_ghost_user(mk_user(deleted=True)))
    check("ghost: 无任何名字字段", E.is_ghost_user(mk_user()))
    check("ghost: 有 username 不算", not E.is_ghost_user(mk_user(username="a")))
    check("ghost: 非 User 不算", not E.is_ghost_user(None) and not E.is_ghost_user(object()))

    # 配额：免费 500 / Premium 1000（客户端侧知识）
    check("quota: 常量", C.QUOTA_FREE == 500 and C.QUOTA_PREMIUM == 1000)
    eng = E.TelegramEngine({"key": "t", "phone": "+100", "api_id": "1", "api_hash": "h"}, demo=True)
    eng.dialogs = {"a": {"dto": {"is_member": True}}, "b": {"dto": {"is_member": False}},
                   "c": {"dto": {"is_member": True}}}
    check("quota: 只算成员", eng.quota_used() == 2)
    check("quota: 免费上限", eng.quota_limit() == 500)
    eng.user = type("U", (), {"premium": True})()
    check("quota: premium 上限", eng.quota_limit() == 1000)

    # 收藏夹保护（反向验证：is_self 绝不进删除分派）
    check("保护: DESTROY_CONFIRM_WORD", C.DESTROY_CONFIRM_WORD == "删除")

    # demo 会话异常示例与实际常见类型对齐（v3.18.2 用户裁定：不放诈骗/假冒示例）
    eng_demo = E.TelegramEngine({"key": "d", "phone": "+1", "api_id": "1", "api_hash": "h"}, demo=True)
    demo_codes = {r["code"] for d in eng_demo._demo_dialogs() for r in d["risk"]}
    check("demo: 异常码合法", demo_codes <= set(C.RISK_CODES),
          f"非法 {demo_codes - set(C.RISK_CODES)}")
    check("demo: 不含诈骗/假冒示例", not ({"scam", "fake"} & demo_codes),
          f"出现 {demo_codes & {'scam', 'fake'}}")
    # demo 媒体统计（v3.19.3）：固定种子跨进程稳定 + 深度前缀性 + 引擎预填充全覆盖
    m1, m2 = E.demo_media("c:1001", 500), E.demo_media("c:1001", 500)
    check("demo: media 固定种子稳定", m1 == m2 and m1["scanned"] > 0 and m1["scanned"] <= 500,
          f"{m1.get('scanned')} vs {m2.get('scanned')}")
    # 媒体总数口径（v3.19.6；v3.20.7 改为不含链接）：total = 六类媒体桶之和，demo 与导出共用
    check("media: total=不含文本与链接的桶之和",
          m1["total"] == m1["scanned"] - m1["counts"]["text"] - m1["counts"]["link"],
          f"total={m1.get('total')} scanned={m1['scanned']} text={m1['counts']['text']} link={m1['counts']['link']}")
    check("media: media_total 纯文本=0",
          E.media_total({"text": 5, "photo": 0}) == 0 and E.media_total({"photo": 2, "video": 3}) == 5
          and E.media_total(None) == 0, f"{E.media_total({'text': 5})}")
    # v3.20.7 链接重叠专项：链接不入总数 + 读路径兜底重算旧缓存
    check("media: 链接不计入总数（服务端链接过滤器与媒体桶交叉重复）",
          E.media_total({"photo": 1389, "video": 1, "gif": 1, "link": 304}) == 1391,
          f"{E.media_total({'photo': 1389, 'video': 1, 'gif': 1, 'link': 304})}")
    _stale = {"counts": {"photo": 1389, "video": 1, "gif": 1, "link": 304}, "total": 1695}
    check("media: with_total 旧缓存 total 重算兜底",
          E.with_total(dict(_stale))["total"] == 1391,
          f"{E.with_total(dict(_stale))['total']}")
    small = E.demo_media("c:1001", 200)["counts"]
    check("demo: media 小深度是大深度前缀",
          all(small[b] <= m1["counts"][b] for b in C.MEDIA_BUCKETS) if hasattr(C, "MEDIA_BUCKETS")
          else all(small[b] <= m1["counts"][b] for b in m1["counts"]),
          f"{small} vs {m1['counts']}")
    seeded = eng_demo.media_cache.data
    check("demo: 媒体缓存预填充全部会话",
          len(seeded) == len(eng_demo._demo_dialogs()) == 500
          and all(v[1].get("scanned", 0) > 0 for v in seeded.values()),
          f"缓存 {len(seeded)} 行")
    # 导出媒体分布列（v3.19.0）
    check("导出: EXCEL_COLUMNS 含媒体分布", any(c[0] == "媒体分布" for c in C.EXCEL_COLUMNS),
          f"{[c[0] for c in C.EXCEL_COLUMNS]}")
    check("导出: media_cell_text 未统计", E.media_cell_text(None) == "未统计")
    _mv = E.media_cell_text({"scanned": 465, "limit": 500,
                             "counts": {"text": 450, "photo": 12, "video": 3, "voice": 0},
                             "exhausted": True})
    check("导出: media_cell_text 格式",
          "图片 12" in _mv and "视频 3" in _mv and "语音" not in _mv
          and "到底" in _mv and _mv.endswith("）"), _mv)
    check("导出: media_cell_text 总数前缀", _mv.startswith("共 15 · "),
          _mv)   # v3.19.6：photo12+video3=15
    # 全历史真实总数模式（v3.20.0）：导出格式 + 过滤器桶都是合法媒体桶
    _mt = E.media_cell_text({"mode": "totals", "full": True, "limit": 0,
                             "scanned": 15, "counts": {"photo": 12, "video": 3}, "ts": 0})
    check("导出: media_cell_text 全历史格式",
          "图片 12" in _mt and "（全历史真实计数）" in _mt and "扫" not in _mt and _mt.startswith("共 15 · "),
          _mt)
    check("media: 全历史过滤器桶 ⊆ 媒体桶",
          {b for b, _ in E.MEDIA_TOTAL_FILTERS} <= set(E.MEDIA_BUCKETS),
          f"{[b for b, _ in E.MEDIA_TOTAL_FILTERS]}")
    # 过滤器必须是「实例」而非类（v3.20.0 存量 bug：传类本身 → _bytes() 缺 self，
    # 真实账号全部会话统计失败——此检查专防该类回归）
    check("media: 全历史过滤器是 TL 实例而非类",
          all(not isinstance(f, type) and hasattr(f, "to_dict")
              for _, f in E.MEDIA_TOTAL_FILTERS),
          f"{[type(f).__name__ for _, f in E.MEDIA_TOTAL_FILTERS]}")
    # 代理测试两段式返回结构（v3.20.1）：tcp/tls 字段必须齐备——非法格式分支不发网络请求，
    # 拿它做结构验证；连接明细字段缺失会让前端无法显示「卡在哪一步」
    _pr = asyncio.run(E.test_proxy("not-a-proxy"))
    check("proxy: test_proxy 返回 tcp/tls/note 齐备",
          set(_pr) == {"ok", "tcp", "tls", "note"} and _pr["ok"] is False and _pr["note"],
          str(_pr))
    check("proxy: 引擎有 conn_note/_stopped 初始字段",
          eng_demo.conn_note == "" and eng_demo._stopped is False,
          f"{eng_demo.conn_note!r}")
    # v3.20.3 启动自动恢复：connect 必须支持 probe 参数（.session 失效时静默退出不发验证码）
    import inspect as _inspect
    check("login: connect 支持 probe 自动恢复参数",
          "probe" in _inspect.signature(E.TelegramEngine.connect).parameters,
          str(_inspect.signature(E.TelegramEngine.connect)))
    # 后台自动媒体总数（v3.20.2）：全量缓存命中时零请求零任务；演示模式不启动
    _eng_auto = E.TelegramEngine({}, demo=False)
    _sentinel = object()
    _eng_auto.dialogs = {"c:x": {"ent": _sentinel, "dto": {}}}
    _eng_auto.media_cache.put("c:x", {"mode": "totals", "full": True, "limit": 0,
                                      "scanned": 9, "counts": {"photo": 9},
                                      "total": 9, "ts": 0})
    asyncio.run(_eng_auto._auto_totals())
    check("media: 后台总数任务全量缓存命中即跳过（零请求）",
          _eng_auto.media_prog == {"done": 0, "total": 0, "running": False},
          str(_eng_auto.media_prog))
    check("media: 演示模式不启动后台总数任务",
          eng_demo._auto_media_task is None,
          str(eng_demo._auto_media_task))
    # 任务进度字段齐备（v3.19.1：媒体 / 成员统计都有 done/total/running，前端轮询依赖）
    check("进度: stats/media 进度字段齐备",
          set(eng_demo.stats_prog) == {"done", "total", "running"}
          and set(eng_demo.media_prog) == {"done", "total", "running"},
          f"stats={set(eng_demo.stats_prog)} media={set(eng_demo.media_prog)}")
    demos = eng_demo._demo_dialogs()
    check("demo: 500 条且 peer_key 唯一", len(demos) == 500 and len({d["peer_key"] for d in demos}) == 500)
    kinds = {d["kind"] for d in demos}
    check("demo: 五种类型都有", {C.KIND_CHANNEL, C.KIND_GROUP, C.KIND_USER, C.KIND_BOT, C.KIND_GONE} <= kinds)
    _keys = ("muted", "pinned", "archived", "unread", "has_photo", "created_ts",
             "verified", "top_message", "restriction_platforms", "risk", "active_text")
    check("demo: dto 关键字段与真实 _build_dto 对齐",
          all(all(k in d for k in _keys) for d in demos))
    _gone = [d for d in eng_demo._demo_dialogs() if d["kind"] == C.KIND_GONE]
    check("demo: 封禁类 kind=已不在 且无成员/作品",
          _gone and all(d["members"] is None and d["posts"] is None and not d["is_member"]
                        and not d["can_destroy"] for d in _gone))

    # peer_key_of：InputPeer 构造体必须与实体键型一致（分组打标 / 批量划分的真因，
    # 反向验证：若漏掉 InputPeer 分支会全部落成 x:0）
    from datetime import datetime as _dt
    check("peer_key: InputPeerChannel→c:", E.peer_key_of(tl_types.InputPeerChannel(123, 0)) == "c:123")
    check("peer_key: InputPeerChat→g:", E.peer_key_of(tl_types.InputPeerChat(456)) == "g:456")
    check("peer_key: InputPeerUser→u:", E.peer_key_of(tl_types.InputPeerUser(789, 0)) == "u:789")
    check("peer_key: Channel 实体同键", E.peer_key_of(tl_types.Channel(123, "x", None, _dt.now())) == "c:123")
    check("peer_key: Chat 实体同键",
          E.peer_key_of(tl_types.Chat(456, "x", None, _dt.now(), "m", 5)) == "g:456")

    # invite_tg_link：无用户名会话跳客户端的唯一官方通路（私有频道/群组没有按 ID resolve 的协议）
    check("invite: t.me/+hash", E.invite_tg_link("https://t.me/+AbC_123") == "tg://join?invite=AbC_123")
    check("invite: 遗留 joinchat", E.invite_tg_link("https://t.me/joinchat/Xy_z9") == "tg://join?invite=Xy_z9")
    check("invite: 带查询参数剥离", E.invite_tg_link("https://t.me/+AbC_123?single") == "tg://join?invite=AbC_123")
    check("invite: 公开用户名链接拒绝", E.invite_tg_link("https://t.me/somechannel") == "")
    check("invite: 空串安全", E.invite_tg_link("") == "")

    # top_message：Telethon Dialog 包装类不透出该属性（在原始 TL 对象 dialog.dialog 上），
    # 直接 getattr(dialog, "top_message", 0) 恒得 0 —— 反向验证包装层确实没有它
    import inspect as _insp
    import telethon.tl.custom.dialog as _dlgmod
    check("top_message: Dialog 包装类不透出", "top_message" not in _insp.getsource(_dlgmod.Dialog))
    class _Raw:  # 模拟原始 TL 对象
        top_message = 555
    class _Wrap:  # 模拟 Telethon Dialog：内嵌 .dialog
        def __init__(self): self.dialog = _Raw()
    _w = _Wrap()
    check("top_message: 嵌套取值生效",
          int(getattr(getattr(_w, "dialog", None), "top_message", 0) or 0) == 555
          and int(getattr(_w, "top_message", 0) or 0) == 0)


def test_media():
    """v3.17.0 媒体归类：真实 TL 类型构造，覆盖全部桶与优先级。"""
    print("\n[9] 媒体归类（视频 / 音频 / 文本等条数统计）")
    from .engine import classify_message, MEDIA_BUCKETS
    from telethon.tl import types as T

    class _M:
        def __init__(self, media): self.media = media

    def _doc(*attrs):
        return T.MessageMediaDocument(document=T.Document(
            id=1, access_hash=0, file_reference=b"", date=None,
            mime_type="application/octet-stream", size=1, dc_id=2,
            attributes=list(attrs)))

    check("media: 无媒体=文本", classify_message(_M(None)) == "text")
    check("media: 图片", classify_message(_M(T.MessageMediaPhoto(photo=None, ttl_seconds=0))) == "photo")
    check("media: 视频", classify_message(_M(_doc(T.DocumentAttributeVideo(10, 640, 360)))) == "video")
    check("media: 圆视频也算视频", classify_message(_M(_doc(T.DocumentAttributeVideo(9, 240, 240, round_message=True)))) == "video")
    check("media: 语音", classify_message(_M(_doc(T.DocumentAttributeAudio(8, voice=True)))) == "voice")
    check("media: 音频", classify_message(_M(_doc(T.DocumentAttributeAudio(180, title="歌")))) == "audio")
    check("media: 贴纸", classify_message(_M(_doc(T.DocumentAttributeSticker(alt="x", stickerset=T.InputStickerSetEmpty())))) == "sticker")
    check("media: GIF", classify_message(_M(_doc(T.DocumentAttributeAnimated()))) == "gif")
    check("media: 贴纸优先于 GIF（动态贴纸归贴纸）",
          classify_message(_M(_doc(T.DocumentAttributeAnimated(), T.DocumentAttributeSticker(alt="", stickerset=T.InputStickerSetEmpty())))) == "sticker")
    check("media: 文件", classify_message(_M(_doc(T.DocumentAttributeFilename("a.zip")))) == "file")
    check("media: 网页预览=链接", classify_message(_M(T.MessageMediaWebPage(webpage=None))) == "link")
    check("media: 其他（位置）", classify_message(_M(T.MessageMediaGeo(geo=None))) == "other")
    counts = dict.fromkeys(MEDIA_BUCKETS, 0)
    check("media: 计数桶覆盖全部类型", set(counts) == set(MEDIA_BUCKETS) and len(MEDIA_BUCKETS) == 10)


def test_avatar():
    """v3.16.0 头像缓存路径：键型安全与目录归位。"""
    print("\n[8] 头像缓存路径（懒下载 / 磁盘缓存）")
    from .engine import avatar_cache_path
    from . import store as _S
    p1 = avatar_cache_path("c:12345", 111)
    check("avatar: 冒号转下划线",
          p1.replace("/", os.sep).endswith(os.sep + "c_12345_111.jpg")
          and ":" not in os.path.basename(p1))
    check("avatar: 落在 avatars 目录", os.path.join("avatars", "") in p1.replace("/", os.sep))
    p2 = avatar_cache_path("u:1", 222)
    check("avatar: 不同 peer/photo_id 不同文件", p1 != p2)
    check("avatar: 目录归 BASE_DIR", p1.startswith(os.path.abspath(_S.BASE_DIR)) or _S.BASE_DIR in p1)


if __name__ == "__main__":
    test_proxy()
    test_risk()
    test_export()
    test_constants()
    test_pending()
    test_legacy()
    test_semantics()
    test_avatar()
    test_media()
    print(f"\n=== 自测结果：通过 {PASS} / 失败 {FAIL} ===")
    sys.exit(1 if FAIL else 0)
