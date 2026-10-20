import React, {
  createContext, useContext, useEffect, useRef, useState, useCallback, useMemo,
} from "react";
import { api } from "@/lib/api";
import { LogSocket } from "@/lib/loghub";
import type {
  AccountsResp, Dialog, Folder, HistoryItem, LogEntry, MediaSummary, VersionResp,
} from "@/lib/types";

type Theme = "light" | "dark";
export type LoginStage =
  | "idle" | "connecting" | "need_code" | "need_password" | "logged_in" | "failed";

export interface LoginForm {
  api_id: string;
  api_hash: string;
  phone: string;
  proxy: string;
}

interface ToastItem { id: number; msg: string; type: "info" | "success" | "error" | "warn"; }

export interface BgPrefs { src: string; dim: number; }

interface AppCtx {
  version?: VersionResp;
  accounts: AccountsResp | null;
  loggedIn: boolean;
  demo: boolean;
  user: any;
  loginStage: LoginStage;
  loginNote: string;
  loginForm: LoginForm;
  setLoginForm: (f: LoginForm) => void;
  dialogs: Dialog[];
  busy: boolean;              // 后端正在拉取会话列表
  fetchNote: string;          // 拉取进度提示（状态栏任务文案）
  batchRunning: boolean;      // 批量退出执行中
  statsRunning: boolean;      // 统计执行中
  mediaRunning: boolean;      // 媒体统计执行中
  mediaProgress: { done: number; total: number } | null;   // 媒体统计进度（v3.19.0）
  statsProgress: { done: number; total: number } | null;   // 成员统计进度（v3.19.1：用户数/作品数/活跃）
  mediaMap: Record<string, MediaSummary>;
  quotaText: string;
  theme: Theme;
  pendingItems: { peer_key: string; title: string }[];
  folders: Folder[];
  bg: BgPrefs | null;
  setBg: (b: BgPrefs | null) => void;

  bootstrap: () => Promise<void>;
  loadAccounts: () => Promise<void>;
  switchAccount: (key: string) => Promise<void>;
  switchMode: (target: "demo" | "real") => Promise<void>;   // 演示 ⇄ 正常一键互切（v3.19.2）
  removeAccount: (key: string, delete_files: boolean) => Promise<void>;
  startLogin: (p?: Partial<LoginForm> & { demo?: boolean; key?: string }) => Promise<void>;
  submitCode: (code: string) => Promise<void>;
  submitPassword: (pwd: string) => Promise<void>;
  refreshDialogs: () => Promise<void>;   // 刷新列表（触发后端重新拉取并轮询到完成）
  syncDialogs: () => Promise<unknown>;   // 只读一次当前列表（不触发拉取）
  fetchStats: (keys: string[], opts?: { force?: boolean; conc?: number; ttl_hours?: number; use_cache?: boolean }) => Promise<void>;
  stopStats: () => void;
  loadMedia: () => Promise<void>;
  runMedia: (keys: string[], opts?: { limit?: number; conc?: number; force?: boolean; silent?: boolean; mode?: "scan" | "totals" }) => Promise<void>;
  stopAll: () => Promise<void>;
  leaveMany: (keys: string[], interval?: number, jitter?: number) => Promise<void>;
  resumePending: (interval?: number, jitter?: number) => Promise<void>;
  clearPending: () => Promise<void>;
  loadPending: () => Promise<void>;
  doDestroy: (keys: string[], word: string) => Promise<void>;
  doRejoin: (kind: string, value: string) => Promise<void>;
  applyWhitelist: (keys: string[], on: boolean) => Promise<void>;
  loadFolders: () => Promise<void>;
  applyFolder: (folder_id: number, keys: string[], add: boolean) => Promise<void>;
  loadHistory: () => Promise<HistoryItem[]>;
  clearHistory: () => Promise<number>;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  toast: (msg: string, type?: ToastItem["type"]) => void;
}

// 日志 / Toast 独立 Context：这两者高频变化（每条 WS 日志、每个提示都会变），
// 放主 Context 会让整棵树跟着重渲染 —— 拆开后只有真正消费它们的组件重渲染
interface LogsCtx {
  logs: LogEntry[];
  logConnected: boolean;
  clearLogs: () => void;
}
interface ToastsCtx {
  toasts: ToastItem[];
  dismissToast: (id: number) => void;
}

const Ctx = createContext<AppCtx | null>(null);
export const useApp = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp 必须在 AppProvider 内使用");
  return v;
};

const LogsContext = createContext<LogsCtx | null>(null);
export const useLogs = () => {
  const v = useContext(LogsContext);
  if (!v) throw new Error("useLogs 必须在 AppProvider 内使用");
  return v;
};

const ToastsContext = createContext<ToastsCtx | null>(null);
export const useToasts = () => {
  const v = useContext(ToastsContext);
  if (!v) throw new Error("useToasts 必须在 AppProvider 内使用");
  return v;
};

const EMPTY_FORM: LoginForm = { api_id: "", api_hash: "", phone: "", proxy: "" };

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [version, setVersion] = useState<VersionResp>();
  const [accounts, setAccounts] = useState<AccountsResp | null>(null);
  const [loggedIn, setLoggedIn] = useState(false);
  const [demo, setDemo] = useState(false);
  const [user, setUser] = useState<any>(null);
  const [loginStage, setLoginStage] = useState<LoginStage>("idle");
  const [loginNote, setLoginNote] = useState("");   // 后端分阶段连接说明（v3.20.1：卡在哪一步一眼可见）
  const [loginForm, setLoginForm] = useState<LoginForm>(EMPTY_FORM);
  const [dialogs, setDialogs] = useState<Dialog[]>([]);
  const [busy, setBusy] = useState(false);
  const [fetchNote, setFetchNote] = useState("");
  const [batchRunning, setBatchRunning] = useState(false);
  const [statsRunning, setStatsRunning] = useState(false);
  const [statsProgress, setStatsProgress] = useState<{ done: number; total: number } | null>(null);
  const [mediaRunning, setMediaRunning] = useState(false);
  const [mediaProgress, setMediaProgress] = useState<{ done: number; total: number } | null>(null);
  const [mediaMap, setMediaMap] = useState<Record<string, MediaSummary>>({});
  const [quotaText, setQuotaText] = useState("");
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logConnected, setLogConnected] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [pendingItems, setPendingItems] = useState<{ peer_key: string; title: string }[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);

  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem("tg_theme") as Theme) || "light",
  );

  // 自定义背景（仅存本机 localStorage；持久化失败降级为会话级，见下方 effect）
  const [bg, setBgState] = useState<BgPrefs | null>(() => {
    try { return JSON.parse(localStorage.getItem("tg_bg") || "null"); } catch { return null; }
  });
  const setBg = useCallback((b: BgPrefs | null) => setBgState(b), []);

  const logSocket = useRef<LogSocket | null>(null);
  const toastSeq = useRef(0);
  const statsAbort = useRef<AbortController | null>(null);
  const mediaAbort = useRef<AbortController | null>(null);
  const refreshingRef = useRef(false);
  const lastFetchErr = useRef(sessionStorage.getItem("tg_fetch_err") || "");
  const logConnectedRef = useRef(false);
  const loginSeq = useRef(0);   // 登录轮询代号：快速切换账号时旧轮询自动作废，防止串状态

  // 主题
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");
    localStorage.setItem("tg_theme", theme);
  }, [theme]);
  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggleTheme = useCallback(() => setThemeState((t) => (t === "dark" ? "light" : "dark")), []);

  // 日志
  const addLog = useCallback((e: LogEntry) => {
    setLogs((prev) => {
      const next = prev.length >= 2000 ? prev.slice(-1999) : prev.slice();
      next.push(e);
      return next;
    });
  }, []);
  const clearLogs = useCallback(() => setLogs([]), []);

  // Toast
  const toast = useCallback((msg: string, type: ToastItem["type"] = "info") => {
    const id = ++toastSeq.current;
    setToasts((p) => [...p, { id, msg, type }]);
    setTimeout(() => setToasts((p) => p.filter((t) => t.id !== id)), 5000);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((p) => p.filter((t) => t.id !== id)), []);

  // 背景持久化（放 toast 之后定义 —— 失败时需要弹提示）
  useEffect(() => {
    document.documentElement.classList.toggle("has-bg", !!bg);
    try {
      if (bg) localStorage.setItem("tg_bg", JSON.stringify(bg));
      else localStorage.removeItem("tg_bg");
    } catch {
      // 配额溢出（1920px JPEG dataURL 可能超出 localStorage 上限）：
      // 降级为仅本次会话生效，绝不让未捕获异常炸掉渲染树
      try { localStorage.removeItem("tg_bg"); } catch { /* ignore */ }
      toast("背景图过大，未能保存到本机（仅本次会话生效，重开页面后恢复默认）", "warn");
    }
  }, [bg, toast]);

  const connectLogs = useCallback(() => {
    if (logSocket.current) return;
    logConnectedRef.current = true;
    logSocket.current = new LogSocket(addLog, setLogConnected);
    logSocket.current.connect();
  }, [addLog]);

  const loadAccounts = useCallback(async () => {
    try { setAccounts(await api.accounts()); } catch { /* ignore */ }
  }, []);

  const removeAccount = useCallback(async (key: string, delete_files: boolean) => {
    try {
      await api.removeAccount(key, delete_files);
      if (delete_files) toast("已移除账号并删除其登录态", "success");
      else toast("已移除账号", "success");
      await loadAccounts();
    } catch (e: any) { toast(e?.message || "移除账号失败", "error"); }
  }, [loadAccounts, toast]);

  const loadPending = useCallback(async () => {
    try { setPendingItems((await api.pending()).items); } catch { /* 未登录时忽略 */ }
  }, []);

  const loadFolders = useCallback(async () => {
    try { setFolders((await api.folders()).folders); } catch { /* ignore */ }
  }, []);

  // 同步一次会话列表（不触发后端拉取）
  const syncDialogs = useCallback(async () => {
    const d = await api.dialogs();
    setDialogs(d.items);
    setQuotaText(d.quota_text || "");
    setBusy(d.busy);
    if (d.fetch_error && d.fetch_error !== lastFetchErr.current) {
      lastFetchErr.current = d.fetch_error;
      try { sessionStorage.setItem("tg_fetch_err", d.fetch_error); } catch { /* ignore */ }
      toast(`拉取会话失败：${d.fetch_error}`, "error");
    }
    return d;
  }, [toast]);

  // 轮询直到后端拉取完成；期间把已有结果逐步上屏（大账号几百页也不至于一直空白）
  const pollDialogs = useCallback(async (refresh: boolean) => {
    setBusy(true);
    try {
      if (refresh) { lastFetchErr.current = ""; try { sessionStorage.removeItem("tg_fetch_err"); } catch { /* ignore */ } await api.refreshDialogs(); }
      for (let i = 0; i < 3600; i++) {
        const d = await syncDialogs();
        if (d.busy) {
          setFetchNote(`正在拉取会话列表…（已获取 ${d.items.length} 个）`);
          await new Promise((r) => setTimeout(r, 800));
        } else {
          setFetchNote("");
          break;
        }
      }
    } catch (e: any) {
      toast(e?.message || "获取会话失败", "error");
    } finally {
      setBusy(false);
      setFetchNote("");
    }
  }, [syncDialogs, toast]);

  // 刷新列表（防并发双拉）
  const refreshDialogs = useCallback(async () => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    try { await pollDialogs(true); } finally { refreshingRef.current = false; }
  }, [pollDialogs]);

  // 启动：版本 + 账号；服务端已有登录态就直接恢复界面并自动拉取。
  // v3.20.3：后端刚重启时正在自动恢复（probe connect），此处轮询等待而不是直接判「未登录」
  const bootstrap = useCallback(async () => {
    try { setVersion(await api.version()); } catch { /* ignore */ }
    const restore = async (s: { user: any; demo?: boolean }) => {
      setLoggedIn(true);
      setDemo(!!s.demo);
      setUser(s.user || null);
      setLoginStage("logged_in");
      connectLogs();
      loadPending();
      loadFolders();
      loadMedia();
      // 先同步一次：引擎里已有列表就秒恢复（F5 不重复全量拉）；
      // 为空（后端刚重启 / 首次）则自动触发拉取，不再让用户手点「刷新列表」
      const d = await syncDialogs();
      if (d.items.length === 0) await pollDialogs(true);
    };
    try {
      const acc = await api.accounts();
      setAccounts(acc);
      if (acc.logged_in) {
        const s = await api.loginState();
        await restore(s);
        return;
      }
      // 未登录但配置过账号：可能是后端启动恢复进行中（连接最长 30 秒），轮询等待
      if (acc.accounts?.length) {
        setLoginStage("connecting");
        for (let i = 0; i < 35; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          const s = await api.loginState();
          setLoginNote(s.note || "");
          if (s.logged_in) { await restore(s); return; }
          if (s.state === "failed") break;   // 恢复失败（线路不通等），交给用户手动处理
        }
        setLoginStage("idle");
        setLoginNote("");
      }
    } catch { /* ignore */ }
  }, [connectLogs, loadFolders, loadPending, pollDialogs, syncDialogs]);

  // 登录流程
  const startLogin = useCallback(async (p?: Partial<LoginForm> & { demo?: boolean; key?: string }) => {
    const payload = {
      demo: p?.demo ?? false,
      key: p?.key ?? "",
      api_id: p?.api_id ?? loginForm.api_id,
      api_hash: p?.api_hash ?? loginForm.api_hash,
      phone: p?.phone ?? loginForm.phone,
      proxy: p?.proxy ?? loginForm.proxy,
    };
    setLoginStage("connecting");
    setLoginNote("");
    const seq = ++loginSeq.current;   // 新一次登录开始：旧轮询全部作废
    try {
      await api.loginStart(payload);
    } catch (e: any) {
      setLoginStage("failed");
      toast(e?.message || "登录启动失败", "error");
      return;
    }
    // 轮询登录状态（验证码 / 两步密码可能要等用户输入，给足 5 分钟）
    let tries = 0;
    const tick = async () => {
      if (seq !== loginSeq.current) return;   // 已被更新的一次登录取代，立即静默退出
      try {
        const s = await api.loginState();
        if (seq !== loginSeq.current) return;
        setLoginNote(s.note || "");
        if (s.logged_in) {
          setLoginNote("");
          setLoggedIn(true);
          setDemo(!!s.demo);
          setUser(s.user);
          setLoginStage("logged_in");
          await loadAccounts();
          connectLogs();
          loadPending();
          loadFolders();
          loadMedia();
          await pollDialogs(true);
          return;
        }
        setLoginStage(s.state as LoginStage);
        if (s.state === "failed") { toast("登录失败，详情见运行日志", "error"); return; }
        if (tries++ < 600) setTimeout(tick, 500);
        else setLoginStage("failed");
      } catch {
        if (seq !== loginSeq.current) return;
        if (tries++ < 600) setTimeout(tick, 500);
      }
    };
    setTimeout(tick, 400);
  }, [loginForm, toast, loadAccounts, connectLogs, loadFolders, loadPending, pollDialogs]);

  // 切换账号：一步到位 —— 切换后立即自动复连（登录态自动复用，无需验证码），
  // 不再让用户自己回头点「连接 / 登录」；需要验证码的账号会自动进入验证条
  const switchAccount = useCallback(async (key: string) => {
    try {
      await api.switchAccount(key);
      setDialogs([]);
      setPendingItems([]);
      setFolders([]);
      toast("已切换账号，正在自动连接…", "info");
      await startLogin({ key });
    } catch (e: any) { toast(e?.message || "切换账号失败", "error"); }
  }, [startLogin, toast]);

  const submitCode = useCallback(async (code: string) => {
    try { await api.loginCode(code); setLoginStage("connecting"); } catch (e: any) { toast(e?.message || "验证码提交失败", "error"); }
  }, [toast]);
  const submitPassword = useCallback(async (pwd: string) => {
    try { await api.loginPassword(pwd); setLoginStage("connecting"); } catch (e: any) { toast(e?.message || "密码提交失败", "error"); }
  }, [toast]);

  // 统计（可停止：「停止」会向后端发 cancel，统计循环收到即中止剩余项）
  const fetchStats = useCallback(async (keys: string[], opts?: { force?: boolean; conc?: number; ttl_hours?: number; use_cache?: boolean }) => {
    if (!keys.length) return;
    setStatsRunning(true);
    const ac = new AbortController();
    statsAbort.current = ac;
    setStatsProgress({ done: 0, total: keys.length });
    // 进度轮询（600ms）：后端 /api/stats/progress，任务结束自动停
    const timer = window.setInterval(async () => {
      try {
        const p = await api.statsProgress();
        if (p.total > 0) setStatsProgress({ done: p.done, total: p.total });
      } catch { /* 忽略瞬时失败 */ }
    }, 600);
    try {
      const r = await api.stats(keys, opts?.force ?? false, opts?.conc ?? 1,
        opts?.ttl_hours ?? 24, opts?.use_cache ?? true, ac.signal);
      setDialogs((prev) => prev.map((d) => {
        const s = r.stats[d.peer_key];
        if (!s) return d;
        return {
          ...d,
          members: s.members ?? d.members,
          posts: s.posts ?? d.posts,
          role: s.role || d.role,
          active_text: s.active_text || d.active_text,
          // 每次按当前角色重算（不再保留旧值单调递增），白名单仍独立生效
          is_protected: d.whitelisted || ["creator", "admin"].includes(s.role || d.role),
        };
      }));
      toast(`统计完成：${Object.keys(r.stats).length} 个会话`, "success");
    } catch (e: any) {
      if (e?.name === "AbortError") toast("统计已停止", "info");
      else toast(e?.message || "统计失败", "error");
    } finally {
      window.clearInterval(timer);
      setStatsRunning(false);
      setStatsProgress(null);
      statsAbort.current = null;
    }
  }, [toast]);

  const stopStats = useCallback(() => {
    if (statsAbort.current) statsAbort.current.abort();
  }, []);

  // 媒体统计（v3.17.0）：扫描每个会话最近 N 条消息按类型计数
  const loadMedia = useCallback(async () => {
    try {
      const r = await api.mediaAll();
      setMediaMap(r.media || {});
    } catch { /* 静默：列显示 — */ }
  }, []);

  const runMedia = useCallback(async (keys: string[], opts?: { limit?: number; conc?: number; force?: boolean; silent?: boolean; mode?: "scan" | "totals" }) => {
    if (!keys.length) { if (!opts?.silent) toast("先勾选要统计的行", "warn"); return; }
    const limit = Math.max(1, Math.min(Math.round(opts?.limit ?? 500), 5000));
    const conc = Math.max(1, Math.min(Math.round(opts?.conc ?? 2), 8));
    const force = !!opts?.force;
    const silent = !!opts?.silent;   // 自动扫描（v3.19.4）不打扰：完成/中止不弹 toast
    const mode: "scan" | "totals" = opts?.mode === "totals" ? "totals" : "scan";
    setMediaRunning(true);
    setMediaProgress({ done: 0, total: keys.length });
    const ac = new AbortController();
    mediaAbort.current = ac;
    // 进度轮询（600ms）：后端 /api/media/progress，任务结束自动停
    const timer = window.setInterval(async () => {
      try {
        const p = await api.mediaProgress();
        if (p.total > 0) setMediaProgress({ done: p.done, total: p.total });
      } catch { /* 忽略瞬时失败 */ }
    }, 600);
    try {
      const r = await api.media(keys, limit, force, conc, 24, true, ac.signal, mode);
      setMediaMap((prev) => ({ ...prev, ...r.media }));
      if (silent) return;
      const errs = Object.values(r.media).filter((mm: any) => mm.error).length;
      toast(`媒体统计完成：${Object.keys(r.media).length} 个会话（${mode === "totals" ? "全部历史真实计数" : `最近 ${limit} 条`}）${errs ? `，${errs} 个失败` : ""}`,
        errs ? "warn" : "success");
    } catch (e: any) {
      if (silent) return;   // 自动扫描失败静默：逐行错误已落在媒体格「!」与运行日志里
      if (e?.name === "AbortError") toast("媒体统计已停止", "info");
      else toast(e?.message || "媒体统计失败", "error");
    } finally {
      window.clearInterval(timer);
      setMediaRunning(false);
      setMediaProgress(null);
      mediaAbort.current = null;
    }
  }, [toast]);

  const stopAll = useCallback(async () => {
    stopStats();
    try { await api.cancelBatch(); } catch { /* ignore */ }
    toast("停止指令已发出", "info");
  }, [stopStats, toast]);

  // 后台自动媒体总数观察者（v3.20.2）：后端拉完列表自动跑全量 totals，不经 runMedia——
  // 这里轮询 /api/media/progress：发现 running 且前端空闲则接管显示「N/M」；
  // 跑完（running 翻 false）自动刷新媒体列数字。手动 runMedia 期间不接管（它自带轮询）。
  const mediaRunningRef = useRef(false);
  useEffect(() => { mediaRunningRef.current = mediaRunning; }, [mediaRunning]);
  const autoMediaOwned = useRef(false);
  useEffect(() => {
    if (!loggedIn || demo) { autoMediaOwned.current = false; return; }
    let alive = true;
    let ticks = 0;
    const timer = window.setInterval(async () => {
      try {
        const p = await api.mediaProgress();
        if (!alive) return;
        if (p.running && !mediaRunningRef.current) {
          autoMediaOwned.current = true;
          setMediaRunning(true);
          setMediaProgress({ done: p.done, total: p.total });
        } else if (p.running && autoMediaOwned.current) {
          // 运行中持续刷新 N/M（v3.20.4 修：此前只在接管瞬间设一次，数字一直不动，
          // 用户只能刷新页面看新快照）；每 15 秒顺带拉一次媒体列，已完成会话的
          // 数字边跑边出现，不必等整个任务跑完
          setMediaProgress({ done: p.done, total: p.total });
          if (++ticks % 10 === 0) void loadMedia();
        } else if (!p.running && autoMediaOwned.current) {
          autoMediaOwned.current = false;
          setMediaRunning(false);
          setMediaProgress(null);
          void loadMedia();   // 后台跑完：媒体列数字即时出现，无需手动刷新
        }
      } catch { /* 忽略瞬时失败 */ }
    }, 1500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [loggedIn, demo, loadMedia]);

  // 模式切换（v3.19.2）：演示 ⇄ 正常一键互切。
  // 切换前先中止进行中的批量任务（后端换引擎即作废，前端也要复位运行态，否则按钮永久禁用）
  const switchMode = useCallback(async (target: "demo" | "real") => {
    if (target === "demo" ? demo : !demo) return;   // 已在目标模式
    statsAbort.current?.abort();
    mediaAbort.current?.abort();
    try { await api.cancelBatch(); } catch { /* ignore */ }
    setDialogs([]); setPendingItems([]); setFolders([]); setMediaMap({});
    if (target === "demo") {
      await startLogin({ demo: true });
    } else {
      const first = accounts?.accounts?.[0];
      if (first) {
        await switchAccount(first.key);
      } else {
        setLoginStage("idle");
        toast("还没有真实账号：请打开设置面板填写 API 凭据登录", "warn");
      }
    }
  }, [demo, accounts, startLogin, switchAccount, toast]);

  // 批量退出（带断点续跑 + 节奏）
  const leaveMany = useCallback(async (keys: string[], interval = 0, jitter = 0) => {
    if (!keys.length) return;
    setBatchRunning(true);
    try {
      const r = await api.leaveMany(keys, interval, jitter);
      const okN = r.results.filter((x) => x.ok).length;
      const failN = r.results.length - okN;
      setPendingItems(r.remaining);
      if (failN) {
        const bad = r.results.filter((x) => !x.ok).slice(0, 3)
          .map((x) => `${x.note || "失败"}`).join("；");
        toast(`批量退出：成功 ${okN}，失败 ${failN}${bad ? `（如：${bad}）` : ""}`, "warn");
      } else {
        toast(`批量退出完成：成功 ${okN} 个`, "success");
      }
      if (r.interrupted) {
        toast(r.stopped ? "已手动停止，剩余项可点底部状态栏「未完成任务」继续" : "任务中断，剩余项可点底部状态栏「未完成任务」继续", "warn");
      }
      try { await syncDialogs(); } catch { /* ignore */ }
    } catch (e: any) {
      toast(e?.message || "批量退出失败", "error");
    } finally {
      setBatchRunning(false);
    }
  }, [toast, syncDialogs]);

  const resumePending = useCallback(async (interval = 0, jitter = 0) => {
    setBatchRunning(true);
    try {
      const r = await api.resumePending(interval, jitter);
      if (!r.results.length) { toast(r.note || "没有待续跑的任务", "info"); return; }
      const okN = r.results.filter((x) => x.ok).length;
      const failN = r.results.length - okN;
      setPendingItems(r.remaining);
      toast(`续跑完成：成功 ${okN}${failN ? `，失败 ${failN}` : ""}` +
        (r.interrupted ? "（再次中断，剩余可继续）" : ""), failN ? "warn" : "success");
      await syncDialogs();
    } catch (e: any) {
      toast(e?.message || "续跑失败", "error");
    } finally {
      setBatchRunning(false);
    }
  }, [toast, syncDialogs]);

  const clearPending = useCallback(async () => {
    await api.clearPending();
    setPendingItems([]);
    toast("已放弃上次未完成的任务", "info");
  }, [toast]);

  const doDestroy = useCallback(async (keys: string[], word: string) => {
    try {
      const r = await api.destroy(keys, word);
      const ok = r.results.filter((x) => x.ok).length;
      toast(`已销毁 ${ok}/${keys.length} 个`, ok ? "success" : "warn");
      await syncDialogs();
    } catch (e: any) { toast(e?.message || "销毁失败", "error"); }
  }, [toast, syncDialogs]);

  const doRejoin = useCallback(async (kind: string, value: string) => {
    try {
      const r = await api.rejoin(kind, value);
      if (r.state === "ok") toast("重进成功", "success");
      else if (r.state === "already") toast("已经在其中", "info");
      else toast(r.note || "重进失败", "error");
    } catch (e: any) { toast(e?.message || "重进失败", "error"); }
  }, [toast]);

  const applyWhitelist = useCallback(async (keys: string[], on: boolean) => {
    try {
      await api.whitelist(keys, on);
      setDialogs((prev) => prev.map((d) => (keys.includes(d.peer_key)
        ? { ...d, whitelisted: on, is_protected: on || d.role === "creator" || d.role === "admin" }
        : d)));
      toast(on ? `已加入白名单 ${keys.length} 个` : `已移出白名单 ${keys.length} 个`, "success");
    } catch (e: any) { toast(e?.message || "白名单操作失败", "error"); }
  }, [toast]);

  const applyFolder = useCallback(async (folder_id: number, keys: string[], add: boolean) => {
    try {
      const r = await api.applyFolder(folder_id, keys, add);
      toast(r.note || `已${add ? "加入" : "移出"}分组 ${r.ok} 个（跳过 ${r.skipped}）`, "success");
      await loadFolders();
      await syncDialogs();
    } catch (e: any) { toast(e?.message || "分组操作失败", "error"); }
  }, [toast, loadFolders, syncDialogs]);

  const loadHistory = useCallback(async () => {
    return (await api.history()).items;
  }, []);

  const clearHistory = useCallback(async () => {
    const r = await api.clearHistory();
    toast(`已清空退出历史（${r.cleared} 条）`, "info");
    return r.cleared;
  }, [toast]);

  useEffect(() => () => logSocket.current?.close(), []);

  const value: AppCtx = useMemo(() => ({
    version, accounts, loggedIn, demo, user, loginStage, loginNote, loginForm, setLoginForm,
    dialogs, busy, fetchNote, batchRunning, statsRunning, statsProgress, mediaRunning, mediaProgress, mediaMap, quotaText,
    theme, pendingItems, folders, bg, setBg,
    bootstrap, loadAccounts, switchAccount, switchMode, removeAccount,
    startLogin, submitCode, submitPassword,
    refreshDialogs, syncDialogs, fetchStats, stopStats, stopAll, loadMedia, runMedia,
    leaveMany, resumePending, clearPending, loadPending, doDestroy, doRejoin, applyWhitelist,
    loadFolders, applyFolder, loadHistory, clearHistory,
    setTheme, toggleTheme, toast,
  }), [version, accounts, loggedIn, demo, user, loginStage, loginNote, loginForm,
    dialogs, busy, fetchNote, batchRunning, statsRunning, statsProgress, mediaRunning, mediaProgress, mediaMap, quotaText,
    theme, pendingItems, folders, bg, setBg,
    bootstrap, loadAccounts, switchAccount, switchMode, removeAccount,
    startLogin, submitCode, submitPassword,
    refreshDialogs, syncDialogs, fetchStats, stopStats, stopAll, loadMedia, runMedia,
    leaveMany, resumePending, clearPending, loadPending, doDestroy, doRejoin, applyWhitelist,
    loadFolders, applyFolder, loadHistory, clearHistory,
    setTheme, toggleTheme, toast]);

  const logsValue: LogsCtx = useMemo(() => ({
    logs, logConnected, clearLogs,
  }), [logs, logConnected, clearLogs]);

  const toastsValue: ToastsCtx = useMemo(() => ({
    toasts, dismissToast,
  }), [toasts, dismissToast]);

  return (
    <Ctx.Provider value={value}>
      <LogsContext.Provider value={logsValue}>
        <ToastsContext.Provider value={toastsValue}>
          {children}
        </ToastsContext.Provider>
      </LogsContext.Provider>
    </Ctx.Provider>
  );
}
