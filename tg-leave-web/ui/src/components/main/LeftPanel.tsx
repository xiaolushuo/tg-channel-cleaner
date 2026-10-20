import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store/AppContext";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export interface StatsPrefs { conc: number; ttlHours: number; useCache: boolean; }
export interface LeavePrefs {
  interval: number; jitter: number;
  skipProtected: boolean; detectRole: boolean; preview: boolean;
}

const TABS = ["账号", "网络", "统计", "设备", "退出", "外观"] as const;

/** 右侧设置栏（对齐 v1.7.7 Notebook 六页签：账号/网络/统计/设备/退出/外观）。 */
export function LeftPanel(props: {
  statsPrefs: StatsPrefs;
  setStatsPrefs: (p: StatsPrefs) => void;
  leavePrefs: LeavePrefs;
  setLeavePrefs: (p: LeavePrefs) => void;
  onKeywordLeave: (kw: string) => void;
  onShowShortcuts: () => void;
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("账号");
  return (
    <motion.div
      initial={{ x: 48, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 48, opacity: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="flex h-full w-72 shrink-0 flex-col border-l border-border bg-card max-lg:fixed max-lg:inset-y-14 max-lg:right-0 max-lg:z-40 max-lg:shadow-2xl">
      <div className="flex flex-wrap gap-1 border-b border-border p-1.5">
        {TABS.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              tab === t ? "bg-background text-foreground shadow-sm border border-border"
                : "text-muted-foreground hover:text-foreground",
            )}>
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {tab === "账号" && <AccountTab />}
        {tab === "网络" && <NetworkTab />}
        {tab === "统计" && <StatsTab prefs={props.statsPrefs} setPrefs={props.setStatsPrefs} />}
        {tab === "设备" && <DevicesTab />}
        {tab === "退出" && (
          <LeaveTab prefs={props.leavePrefs} setPrefs={props.setLeavePrefs}
            onKeywordLeave={props.onKeywordLeave} />
        )}
        {tab === "外观" && <AppearanceTab onShowShortcuts={props.onShowShortcuts} />}
      </div>
    </motion.div>
  );
}

/* ---------------- 账号 ---------------- */
function AccountTab() {
  const {
    accounts, loggedIn, loginForm, setLoginForm, startLogin,
    switchAccount, removeAccount, loadAccounts, toast,
  } = useApp();
  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [legacyBusy, setLegacyBusy] = useState(false);

  const importLegacy = async () => {
    setLegacyBusy(true);
    try {
      const r = await api.legacyImport();
      const n = r.imported.length, m = r.failed.length;
      if (n + m === 0) {
        toast("没有在常见位置找到旧版登录态（tg_cleaner_config.json + .session）", "warn");
      } else if (n > 0) {
        toast(`旧版登录态导入成功 ${n} 个${m ? `，失败 ${m}` : ""}，已填入表单，点「连接 / 登录」即可`, "success");
        await loadAccounts();
        const first = r.imported[0];
        setLoginForm({
          api_id: first.api_id || "", api_hash: first.api_hash || "",
          phone: first.phone || "", proxy: first.proxy || "",
        });
      } else {
        toast(`导入失败：${r.failed[0]?.note || "未知原因"}`, "error");
      }
    } catch (err: any) {
      toast(`导入失败：${err?.message || err}`, "error");
    } finally { setLegacyBusy(false); }
  };

  const list = accounts?.accounts || [];
  const current = accounts?.current || "";
  const cur = list.find((a) => a.key === current);

  const addAndLogin = async () => {
    if (!loginForm.phone.trim()) { toast("请先填手机号", "warn"); return; }
    setBusy(true);
    try {
      await api.upsertAccount({
        api_id: loginForm.api_id, api_hash: loginForm.api_hash,
        phone: loginForm.phone.trim(), proxy: loginForm.proxy, nick: "",
      });
      await loadAccounts();
      await startLogin({ phone: loginForm.phone.trim() });
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs leading-5 text-muted-foreground">
        账号列表（可添加多个，随时切换；切换时不会退出登录）
      </p>
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">账号</th>
              <th className="px-2 py-1.5 text-left font-medium">手机号</th>
              <th className="px-2 py-1.5 text-left font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 && (
              <tr><td colSpan={3} className="px-2 py-3 text-center text-muted-foreground">（还没有账号）</td></tr>
            )}
            {list.map((a) => (
              <tr key={a.key}
                onClick={() => setSelected(a.key)}
                onDoubleClick={() => switchAccount(a.key)}
                className={cn("cursor-pointer border-t border-border/60",
                  selected === a.key && "bg-primary/10")}>
                <td className="px-2 py-1.5">{a.nick || a.key}</td>
                <td className="px-2 py-1.5">{a.phone}</td>
                <td className="px-2 py-1.5">{a.key === current && loggedIn ? "● 已连接" : a.key === current ? "当前" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" disabled={!selected || selected === current}
          onClick={() => switchAccount(selected)}>切换到选中</Button>
        <Button size="sm" variant="outline" disabled={!selected}
          onClick={() => {
            if (window.confirm(`确定从列表移除账号「${list.find((a) => a.key === selected)?.nick || selected}」吗？（登录态保留，可重新添加）`))
              removeAccount(selected, false);
          }}>移除账号</Button>
        <Button size="sm" variant="outline" disabled={!selected}
          onClick={() => {
            if (window.confirm(`⚠️ 将移除账号「${list.find((a) => a.key === selected)?.nick || selected}」并删除其登录态（.session 文件），不可恢复。确定吗？`))
              removeAccount(selected, true);
          }}>移除并删登录态</Button>
      </div>

      <div className="h-px bg-border" />

      <div className="space-y-1.5">
        <div className="grid grid-cols-2 gap-1.5">
          <div className="space-y-1">
            <Label>API ID</Label>
            <Input value={loginForm.api_id} onChange={(e) => setLoginForm({ ...loginForm, api_id: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label>手机号</Label>
            <Input value={loginForm.phone} onChange={(e) => setLoginForm({ ...loginForm, phone: e.target.value })}
              placeholder="8613800000000" />
          </div>
        </div>
        <div className="space-y-1">
          <Label>API HASH</Label>
          <Input value={loginForm.api_hash} onChange={(e) => setLoginForm({ ...loginForm, api_hash: e.target.value })} />
        </div>
        <Button size="sm" variant="primary" className="w-full" onClick={() => startLogin()}>
          连接 / 登录当前账号
        </Button>
        <Button size="sm" variant="outline" className="w-full" disabled={busy} onClick={addAndLogin}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} ＋ 保存为新账号并登录
        </Button>
        <p className="text-xs leading-5 text-muted-foreground">
          在 https://my.telegram.org 创建应用后获取 API ID / HASH。
          API 与代理是全局设置，所有账号共用；登录态按账号分别保存，
          所以切换账号不需要重新输验证码。
        </p>
      </div>

      {cur && (
        <p className="text-xs text-muted-foreground">
          当前账号：{cur.nick || cur.key}（{cur.phone}）{loggedIn ? " · 已连接" : ""}
        </p>
      )}
      <Button size="sm" variant="outline" className="w-full" disabled={!current}
        onClick={() => {
          if (window.confirm(`⚠️ 将删除当前账号「${cur?.nick || cur?.key || current}」的登录态（.session 文件），下次连接需要重新收验证码。确定吗？`))
            removeAccount(current, true);
        }}>
        删除当前账号的登录态
      </Button>
      <Button size="sm" variant="outline" className="w-full" disabled={legacyBusy} onClick={importLegacy}
        title="自动发现旧版 Python 工具目录里的 .session 并导入（免验证码）">
        {legacyBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} 导入旧版登录态（免验证码）
      </Button>
      <Button size="sm" variant="ghost" className="w-full" onClick={() => startLogin({ demo: true })}>
        <Sparkles className="h-3.5 w-3.5" /> 演示模式（无凭据体验）
      </Button>
    </div>
  );
}

/* ---------------- 网络 ---------------- */
interface ProxyPrefs { enabled: boolean; type: string; host: string; port: string; user: string; pass: string; }

function loadProxyPrefs(): ProxyPrefs {
  try { return { enabled: false, type: "SOCKS5", host: "", port: "", user: "", pass: "", ...JSON.parse(localStorage.getItem("tg_proxy_prefs") || "{}") }; }
  catch { return { enabled: false, type: "SOCKS5", host: "", port: "", user: "", pass: "" }; }
}

export function composeProxy(p: ProxyPrefs): string {
  if (!p.enabled || !p.host || !p.port) return "";
  const auth = p.user ? `${encodeURIComponent(p.user)}:${encodeURIComponent(p.pass)}@` : "";
  const scheme = p.type === "HTTP" ? "http" : p.type === "SOCKS4" ? "socks4" : "socks5";
  return `${scheme}://${auth}${p.host}:${p.port}`;
}

function NetworkTab() {
  const { setLoginForm, loginForm, toast } = useApp();
  const [p, setP] = useState<ProxyPrefs>(loadProxyPrefs);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState("");

  // 登录表单里已有代理（旧版导入 / 已保存账号）时，把它拆回结构化字段
  useEffect(() => {
    if (loginForm.proxy && !p.host) {
      try {
        const u = new URL(loginForm.proxy);
        const scheme = (u.protocol.replace(":", "") || "socks5").toLowerCase();
        setP((prev) => {
          const next: ProxyPrefs = {
            ...prev,
            enabled: true,
            type: scheme === "http" ? "HTTP" : scheme === "socks4" ? "SOCKS4" : "SOCKS5",
            host: u.hostname,
            port: u.port,
            user: decodeURIComponent(u.username || ""),
            pass: decodeURIComponent(u.password || ""),
          };
          localStorage.setItem("tg_proxy_prefs", JSON.stringify(next));
          return next;
        });
      } catch { /* 不是 URL 就不动 */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loginForm.proxy]);

  const save = (next: ProxyPrefs) => {
    setP(next);
    localStorage.setItem("tg_proxy_prefs", JSON.stringify(next));
    setLoginForm({ ...loginForm, proxy: composeProxy(next) });
  };

  const test = async () => {
    setTesting(true); setResult("");
    try {
      const r = await api.proxyTest(composeProxy(p));
      setResult(r.note || (r.ok ? "通过" : "失败"));
      toast(r.ok ? `代理可用：${r.note}` : `代理不可用：${r.note}`, r.ok ? "success" : "error");
    } catch (e: any) { setResult(e?.message || "测试失败"); } finally { setTesting(false); }
  };

  return (
    <div className="space-y-2.5 text-sm">
      <label className="flex items-center gap-2">
        <Checkbox checked={p.enabled} onCheckedChange={(v) => save({ ...p, enabled: !!v })} />
        启用代理
      </label>
      <div className="grid grid-cols-3 items-center gap-2">
        <span className="text-right text-muted-foreground">类型</span>
        <select value={p.type} onChange={(e) => save({ ...p, type: e.target.value })}
          className="col-span-2 h-8 rounded-md border border-input bg-transparent px-2 text-sm">
          <option>SOCKS5</option><option>SOCKS4</option><option>HTTP</option>
        </select>
        <span className="text-right text-muted-foreground">主机</span>
        <Input className="col-span-2 h-8" value={p.host} onChange={(e) => save({ ...p, host: e.target.value })} placeholder="127.0.0.1" />
        <span className="text-right text-muted-foreground">端口</span>
        <Input className="col-span-2 h-8" value={p.port} onChange={(e) => save({ ...p, port: e.target.value.replace(/\D/g, "") })} placeholder="7890" />
        <span className="text-right text-muted-foreground">用户名</span>
        <Input className="col-span-2 h-8" value={p.user} onChange={(e) => save({ ...p, user: e.target.value })} />
        <span className="text-right text-muted-foreground">密码</span>
        <Input className="col-span-2 h-8" type="password" value={p.pass} onChange={(e) => save({ ...p, pass: e.target.value })} />
      </div>
      <Button size="sm" variant="outline" className="w-full" disabled={testing} onClick={test}>
        {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : null} 测试代理（TCP + 出网到 Telegram）
      </Button>
      {result && <p className="text-xs text-muted-foreground">{result}</p>}
      <p className="text-xs leading-5 text-muted-foreground">
        国内网络通常需要代理（如 127.0.0.1:7890）。
        测试会先连本地端口，再经代理访问 api.telegram.org:443。
      </p>
    </div>
  );
}

/* ---------------- 统计 ---------------- */
function StatsTab(props: { prefs: StatsPrefs; setPrefs: (p: StatsPrefs) => void }) {
  const { toast } = useApp();
  const [count, setCount] = useState(0);
  const reload = useCallback(async () => {
    try { setCount((await api.statsCacheInfo()).count); } catch { /* ignore */ }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  const clear = async () => {
    try {
      const r = await api.clearStatsCache();
      toast(`已清空统计缓存（${r.cleared} 条）`, "info");
      reload();
    } catch (e: any) { toast(e?.message || "清空失败", "error"); }
  };
  const p = props.prefs;
  return (
    <div className="space-y-2.5 text-sm">
      <div className="grid grid-cols-3 items-center gap-2">
        <span className="text-right text-muted-foreground">并发数</span>
        <Input className="col-span-2 h-8" inputMode="numeric" value={String(p.conc)}
          onChange={(e) => props.setPrefs({ ...p, conc: Math.min(32, Math.max(1, parseInt(e.target.value.replace(/\D/g, "") || "1", 10))) })} />
        <span className="text-right text-muted-foreground">缓存有效期(小时)</span>
        <Input className="col-span-2 h-8" inputMode="numeric" value={String(p.ttlHours)}
          onChange={(e) => props.setPrefs({ ...p, ttlHours: Math.min(720, Math.max(1, parseInt(e.target.value.replace(/\D/g, "") || "1", 10))) })} />
      </div>
      <label className="flex items-center gap-2">
        <Checkbox checked={p.useCache} onCheckedChange={(v) => props.setPrefs({ ...p, useCache: !!v })} />
        使用本地统计缓存
      </label>
      <p className="text-xs text-muted-foreground">缓存 {count} 条</p>
      <Button size="sm" variant="outline" className="w-full" onClick={clear}>清空统计缓存</Button>
      <p className="text-xs leading-5 text-muted-foreground">
        「用户数 / 作品数」会对每个频道各发 1~2 次请求，
        并发越大越快、也越容易触发限流；命中缓存则完全不发请求。
      </p>
    </div>
  );
}

/* ---------------- 设备 ---------------- */
function DevicesTab() {
  const { toast } = useApp();
  const [devices, setDevices] = useState<any[]>([]);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState("");

  const refresh = async () => {
    setBusy(true);
    try {
      const r = await api.devices();
      setDevices(r.devices);
      setSel(new Set());
      setInfo(`共 ${r.devices.length} 个设备会话（含本机）`);
      toast(`登录设备：共 ${r.devices.length} 个（本机 1 个，其它 ${Math.max(0, r.devices.length - 1)} 个）`, "info");
    } catch (e: any) { toast(e?.message || "拉取设备失败", "error"); } finally { setBusy(false); }
  };
  const kill = async (hashes: number[], label: string) => {
    if (!hashes.length) { toast("没有可踢出的设备（先点「刷新设备」）；本机那条永远不会被踢。", "warn"); return; }
    if (!window.confirm(`要踢出这 ${hashes.length} 个设备吗（${label}）？\n被踢的设备需要重新登录；本机的会话不受影响。`)) return;
    setBusy(true);
    try {
      for (const h of hashes) await api.killDevices([h]);
      toast(`踢出完成`, "success");
      refresh();
    } catch (e: any) { toast(e?.message || "踢出失败", "error"); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-2.5 text-sm">
      <p className="text-xs leading-5 text-muted-foreground">当前账号的登录设备（本机那条不会被踢）</p>
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="w-8" />
              <th className="px-2 py-1.5 text-left font-medium">设备</th>
              <th className="px-2 py-1.5 text-left font-medium">系统</th>
              <th className="px-2 py-1.5 text-left font-medium">IP</th>
              <th className="px-2 py-1.5 text-left font-medium">最近活跃</th>
            </tr>
          </thead>
          <tbody>
            {devices.length === 0 && (
              <tr><td colSpan={5} className="px-2 py-3 text-center text-muted-foreground">（先点「刷新设备」）</td></tr>
            )}
            {devices.map((d) => (
              <tr key={d.hash} className="border-t border-border/60">
                <td className="px-2 py-1.5">
                  <Checkbox disabled={d.current}
                    checked={sel.has(d.hash)}
                    onCheckedChange={(v) => setSel((prev) => {
                      const n = new Set(prev); v ? n.add(d.hash) : n.delete(d.hash); return n;
                    })} />
                </td>
                <td className="px-2 py-1.5">{d.device}{d.current ? "（本机）" : ""}</td>
                <td className="px-2 py-1.5">{d.system}</td>
                <td className="px-2 py-1.5">{d.ip}</td>
                <td className="px-2 py-1.5">{d.active}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="outline" disabled={busy} onClick={refresh}>刷新设备</Button>
        <Button size="sm" variant="outline" disabled={busy || !sel.size}
          onClick={() => kill([...sel], "选中的")}>踢出选中</Button>
        <Button size="sm" variant="outline" disabled={busy}
          onClick={() => kill(devices.filter((d) => !d.current).map((d) => d.hash), "其它全部")}>踢出其它全部</Button>
      </div>
      {info && <p className="text-xs text-muted-foreground">{info}</p>}
      <p className="text-xs leading-5 text-muted-foreground">
        提示：踢设备只影响登录态，不会删消息；被踢的设备需要重新登录。
      </p>
    </div>
  );
}

/* ---------------- 退出 ---------------- */
function LeaveTab(props: {
  prefs: LeavePrefs; setPrefs: (p: LeavePrefs) => void;
  onKeywordLeave: (kw: string) => void;
}) {
  const { dialogs, applyWhitelist } = useApp();
  const [kw, setKw] = useState("");
  const p = props.prefs;
  const wlCount = dialogs.filter((d) => d.whitelisted).length;
  return (
    <div className="space-y-2.5 text-sm">
      <div className="grid grid-cols-3 items-center gap-2">
        <span className="text-right text-muted-foreground">退出间隔(秒)</span>
        <Input className="col-span-2 h-8" inputMode="numeric" value={String(p.interval)}
          onChange={(e) => props.setPrefs({ ...p, interval: Math.min(120, Math.max(1, parseInt(e.target.value.replace(/\D/g, "") || "1", 10))) })} />
        <span className="text-right text-muted-foreground">随机抖动(秒)</span>
        <Input className="col-span-2 h-8" inputMode="numeric" value={String(p.jitter)}
          onChange={(e) => props.setPrefs({ ...p, jitter: Math.min(60, Math.max(0, parseInt(e.target.value.replace(/\D/g, "") || "0", 10))) })} />
      </div>
      <p className="text-xs text-muted-foreground">间隔 + 0~抖动 的随机等待，可降低被限流概率。</p>

      <div className="h-px bg-border" />

      <p className="text-xs leading-5">
        按关键词批量退<br />
        <span className="text-muted-foreground">（名称/用户名包含任一关键词即匹配，逗号或空格分隔）</span>
      </p>
      <Input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="如：羊毛 广告, 推广" />
      <Button size="sm" variant="outline" className="w-full" disabled={!kw.trim()}
        onClick={() => props.onKeywordLeave(kw.trim())}>
        按关键词退出
      </Button>

      <div className="h-px bg-border" />

      <p className="text-xs font-semibold">安全防护</p>
      <label className="flex items-start gap-2">
        <Checkbox checked={p.skipProtected} onCheckedChange={(v) => props.setPrefs({ ...p, skipProtected: !!v })} />
        <span>退出时自动跳过 白名单 / 创建者 / 管理员</span>
      </label>
      <label className="flex items-start gap-2">
        <Checkbox checked={p.detectRole} onCheckedChange={(v) => props.setPrefs({ ...p, detectRole: !!v })} />
        <span>获取统计时顺带检测创建者 / 管理员（每个多 1 次请求，结果会被缓存）</span>
      </label>
      <label className="flex items-start gap-2">
        <Checkbox checked={p.preview} onCheckedChange={(v) => props.setPrefs({ ...p, preview: !!v })} />
        <span>退出前显示待退出清单，确认后才执行</span>
      </label>
      <p className="text-xs text-muted-foreground">白名单 {wlCount} 个频道（永不退出）</p>
      <Button size="sm" variant="outline" className="w-full" disabled={!wlCount}
        onClick={() => applyWhitelist(dialogs.filter((d) => d.whitelisted).map((d) => d.peer_key), false)}>
        清空白名单
      </Button>
      <p className="text-xs leading-5 text-muted-foreground">
        创建者 / 管理员在 Telegram 里无法直接退出（创建者需先转让或删除频道），
        不跳过会白跑一遍并报错。白名单用于标记「我就是要保留」的频道。
      </p>
    </div>
  );
}

/* ---------------- 外观 ---------------- */
function AppearanceTab(props: { onShowShortcuts: () => void }) {
  const { theme, setTheme, bg, setBg, toast } = useApp();
  const fileRef = useRef<HTMLInputElement>(null);

  // 选图 → 缩到最长边 1920 → JPEG 压缩 → 存 localStorage（不上传）
  // 两档压缩：首压超过 ~2.6MB（localStorage 配额保险线）自动降质 + 再缩边重压
  const pickBg = (file: File) => {
    if (!file.type.startsWith("image/")) { toast("请选择图片文件", "warn"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        try {
          const draw = (maxSide: number) => {
            const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(img.width * scale));
            canvas.height = Math.max(1, Math.round(img.height * scale));
            canvas.getContext("2d")?.drawImage(img, 0, 0, canvas.width, canvas.height);
            return canvas;
          };
          let src = draw(1920).toDataURL("image/jpeg", 0.82);
          if (src.length > 2_600_000) src = draw(1280).toDataURL("image/jpeg", 0.55);
          setBg({ src, dim: bg?.dim ?? 0.45 });
          toast("背景已更新", "success");
        } catch { toast("图片处理失败（可能太大）", "error"); }
      };
      img.onerror = () => toast("图片读取失败", "error");
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  };

  return (
    <div className="space-y-2.5 text-sm">
      <label className="flex items-center gap-2">
        <Checkbox checked={theme === "dark"} onCheckedChange={(v) => setTheme(v ? "dark" : "light")} />
        深色模式
      </label>
      <p className="text-xs text-muted-foreground">设置面板的开合状态会自动记住（刷新页面后保持）。</p>

      <div className="h-px bg-border" />

      <Label>背景图片（整个界面）</Label>
      <input ref={fileRef} type="file" accept="image/*" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) pickBg(f); e.target.value = ""; }} />
      <div className="flex gap-1.5">
        <Button size="sm" variant="outline" className="flex-1" onClick={() => fileRef.current?.click()}>
          选择图片…
        </Button>
        <Button size="sm" variant="outline" disabled={!bg} onClick={() => setBg(null)}>
          清除背景
        </Button>
      </div>
      {bg && (
        <>
          <div className="h-20 rounded-md border border-border bg-cover bg-center"
            style={{ backgroundImage: `url(${bg.src})` }} title="当前背景预览" />
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="shrink-0">遮罩浓度</span>
            <input type="range" min={0} max={80} value={Math.round((bg?.dim ?? 0.45) * 100)}
              onChange={(e) => bg && setBg({ ...bg, dim: Number(e.target.value) / 100 })}
              className="flex-1 accent-primary" />
            <span className="w-8 text-right tabular-nums">{Math.round((bg?.dim ?? 0.45) * 100)}%</span>
          </div>
        </>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        图片会自动压缩到最长边 1920px，仅保存在本机浏览器（localStorage），不会上传。
        调低遮罩浓度背景更清晰，调高则文字更好读。
      </p>

      <div className="h-px bg-border" />

      <Button size="sm" variant="outline" className="w-full" onClick={props.onShowShortcuts}>
        快捷键一览（F1）
      </Button>
      <p className="text-xs leading-5 text-muted-foreground">
        快捷键：Ctrl+F 搜索 / Ctrl+A 全选 / F5 刷新 / Esc 清搜索 / F1 全部
      </p>
    </div>
  );
}
