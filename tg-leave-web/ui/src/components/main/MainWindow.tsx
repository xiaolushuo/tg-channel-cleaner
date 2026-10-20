import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp, useToasts } from "@/store/AppContext";
import { HeaderBar } from "./HeaderBar";
import { CodeStrip } from "./CodeStrip";
import { StatusBar } from "./StatusBar";
import { LogPanel } from "./LogPanel";
import { LeftPanel, type LeavePrefs, type StatsPrefs } from "./LeftPanel";
import { MainArea, GuideCard } from "./MainArea";
import { AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";

const DEFAULT_LEAVE: LeavePrefs = {
  interval: 2, jitter: 0, skipProtected: true, detectRole: true, preview: true,
};
const DEFAULT_STATS: StatsPrefs = { conc: 6, ttlHours: 24, useCache: true };

function loadPrefs<T>(key: string, def: T): T {
  try { return { ...def, ...JSON.parse(localStorage.getItem(key) || "{}") }; }
  catch { return def; }
}

export function MainWindow() {
  const {
    version, loggedIn, demo, user, loginStage, dialogs, busy, fetchNote, quotaText, bg,
    batchRunning, statsRunning, statsProgress, mediaRunning, mediaProgress, submitCode, submitPassword, refreshDialogs,
    fetchStats, startLogin, switchMode,
    stopAll, pendingItems, resumePending, clearPending,
  } = useApp();

  // 设置面板开合持久化（外观页「记住面板开合」为真实行为，不再假勾选）
  const [leftVisible, setLeftVisibleState] = useState<boolean>(
    () => localStorage.getItem("tg_panel_left") !== "0");
  const setLeftVisible = useCallback((v: boolean) => {
    setLeftVisibleState(v);
    try { localStorage.setItem("tg_panel_left", v ? "1" : "0"); } catch { /* ignore */ }
  }, []);
  const [logVisible, setLogVisible] = useState(false);   // 右侧日志抽屉，默认收起
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(0);
  const [kwOpen, setKwOpen] = useState(false);
  const [kwText, setKwText] = useState("");
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [statsPrefs, setStatsPrefs] = useState<StatsPrefs>(() => loadPrefs("tg_stats_prefs", DEFAULT_STATS));
  const [leavePrefs, setLeavePrefsState] = useState<LeavePrefs>(() => loadPrefs("tg_leave_prefs", DEFAULT_LEAVE));
  const searchRef = useRef<HTMLInputElement>(null);

  const setLeavePrefs = useCallback((p: LeavePrefs) => {
    setLeavePrefsState(p);
    localStorage.setItem("tg_leave_prefs", JSON.stringify(p));
  }, []);
  const setStatsPrefsSave = useCallback((p: StatsPrefs) => {
    setStatsPrefs(p);
    localStorage.setItem("tg_stats_prefs", JSON.stringify(p));
  }, []);

  // 登录成功（含导入 session 自动登录 / 演示模式）后自动收起右侧设置栏
  const prevStage = useRef(loginStage);
  useEffect(() => {
    if (loginStage === "logged_in" && prevStage.current !== "logged_in") {
      setLeftVisible(false);
    }
    prevStage.current = loginStage;
  }, [loginStage]);

  // 顶栏搜索：输入即筛（名称 / 用户名 / 数字 ID；去掉 @ 前缀也能搜）
  const baseFiltered = useMemo(() => {
    const q = search.trim().toLowerCase().replace(/^@/, "");
    if (!q) return dialogs;
    return dialogs.filter((d) => `${d.title} ${d.username || ""} ${d.id ?? ""}`.toLowerCase().includes(q));
  }, [dialogs, search]);

  // 快捷键（对齐 v1.7.7：F1 帮助 / F5 刷新 / Ctrl+F 搜索 / Esc 清搜索）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "F1") { e.preventDefault(); setShortcutsOpen(true); }
      else if (e.key === "F5") { e.preventDefault(); if (loggedIn) refreshDialogs(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault(); searchRef.current?.focus();
      } else if (e.key === "Escape" && document.activeElement === searchRef.current) {
        setSearch("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [loggedIn, refreshDialogs]);

  // 对齐 v1.7.7：列表加载完成后自动读取用户数 / 作品数（命中本地缓存不发请求）
  const prevBusy = useRef(false);
  const sawFetch = useRef(false);      // 本次会话是否走过「拉取」路径（busy 翻转过）
  useEffect(() => { if (busy) sawFetch.current = true; }, [busy]);
  useEffect(() => {
    if (prevBusy.current && !busy && loggedIn && !batchRunning && !statsRunning) {
      const keys = dialogs.filter((d) => d.kind === "频道" || d.kind === "群组").map((d) => d.peer_key);
      if (keys.length) {
        fetchStats(keys, { conc: statsPrefs.conc, ttl_hours: statsPrefs.ttlHours, use_cache: statsPrefs.useCache });
      }
    }
    prevBusy.current = busy;
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  // F5 恢复兜底（v3.19.5）：登录态直接恢复时列表秒回、busy 没有翻转，上面的自动统计
  // /媒体链不会触发——这里补跑一次（走过拉取路径则交给上面的 busy 翻转负责，不重复）
  const bootKicked = useRef(false);
  useEffect(() => {
    if (!loggedIn) { bootKicked.current = false; sawFetch.current = false; return; }
    if (sawFetch.current || bootKicked.current || busy || fetchNote
        || batchRunning || statsRunning || mediaRunning) return;
    const keys = dialogs.filter((d) => d.kind === "频道" || d.kind === "群组").map((d) => d.peer_key);
    if (!keys.length) return;
    bootKicked.current = true;
    fetchStats(keys, { conc: statsPrefs.conc, ttl_hours: statsPrefs.ttlHours, use_cache: statsPrefs.useCache });
  }, [loggedIn, busy, fetchNote, dialogs]); // eslint-disable-line react-hooks/exhaustive-deps

  // 媒体总数后台自动拉取（v3.20.2）：改由后端在列表拉完后自动跑全量 totals（零操作、
  // 断点续跑、进度走 /api/media/progress 状态栏可见）；前端不再触发自动扫描链，
  // 手动深度扫描仍走「更多 ▾ → 统计媒体」。

  const userName = user ? [user.first_name, user.last_name].filter(Boolean).join(" ")
    || (user.username ? `@${user.username}` : "") : "";

  // 统计进度（v3.19.1）：优先用后端 /api/stats/progress 的真实 N/M；轮询未就绪时退回本地反推
  const statsTotal = useMemo(
    () => dialogs.filter((d) => d.kind === "频道" || d.kind === "群组").length, [dialogs]);
  const statsDone = useMemo(
    () => dialogs.filter((d) => (d.kind === "频道" || d.kind === "群组") && d.members != null).length,
    [dialogs]);
  const statsLive = statsRunning && statsProgress && statsProgress.total > 0
    ? `${statsProgress.done}/${statsProgress.total}` : "";
  const statsProgressText = statsLive || `${statsDone}/${statsTotal}`;

  const mediaProg = mediaRunning && mediaProgress && mediaProgress.total > 0
    ? `${mediaProgress.done}/${mediaProgress.total}` : "";
  const taskText = fetchNote
    || (batchRunning ? "批量退出执行中…（可点「停止」中止，剩余项保留断点）"
      : mediaRunning ? `媒体统计执行中${mediaProg ? `（${mediaProg}）` : ""}…（可点「停止」中止）`
      : statsRunning ? `统计执行中（${statsProgressText}）…（可点「停止」中止）` : "空闲");

  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      {/* 自定义背景（外观页设置；has-bg 时根层已透明化） */}
      {bg && (
        <div className="pointer-events-none fixed inset-0 -z-10">
          <img src={bg.src} alt="" className="h-full w-full object-cover" />
          <div className="absolute inset-0" style={{ backgroundColor: "hsl(var(--background))", opacity: bg.dim }} />
        </div>
      )}
      <HeaderBar
        stage={loginStage} userName={userName} version={version?.version} demo={demo}
        loggedIn={loggedIn} onSwitchMode={switchMode}
        leftVisible={leftVisible} onToggleLeft={() => setLeftVisible(!leftVisible)}
        onConnect={() => startLogin()}
        onRefresh={() => refreshDialogs()}
        refreshing={busy}
        search={search} onSearch={setSearch} searchHit={search ? visibleCount : 0}
        onToggleLog={() => setLogVisible((v) => !v)} logVisible={logVisible}
        searchRef={searchRef}
      />
      {loginStage !== "logged_in" && (
        <CodeStrip stage={loginStage} onSubmit={(v) => {
          if (loginStage === "need_password") submitPassword(v);
          else submitCode(v);
        }} />
      )}

      <div className="flex min-h-0 flex-1">
        {loginStage !== "logged_in" && dialogs.length === 0 ? (
          /* 未登录：整屏欢迎引导（v3.19.8）——不再渲染筛选栏 / 空表格 / 分页等零功能噪音 */
          <GuideCard variant="page" onShowShortcuts={() => setShortcutsOpen(true)} />
        ) : (
          <MainArea
            baseFiltered={baseFiltered}
            allDialogs={dialogs}
            loggedIn={loggedIn}
            busy={busy}
            statsPrefs={statsPrefs}
            leavePrefs={leavePrefs}
            setLeavePrefs={setLeavePrefs}
            onShowShortcuts={() => setShortcutsOpen(true)}
            onVisibleCount={setVisibleCount}
            kwOpen={kwOpen} setKwOpen={setKwOpen}
            kwText={kwText} setKwText={setKwText}
          />
        )}
        <AnimatePresence>{leftVisible && (
          <LeftPanel
            statsPrefs={statsPrefs} setStatsPrefs={setStatsPrefsSave}
            leavePrefs={leavePrefs} setLeavePrefs={setLeavePrefs}
            onKeywordLeave={(kw) => { setKwText(kw); setKwOpen(true); }}
            onShowShortcuts={() => setShortcutsOpen(true)}
          />
        )}</AnimatePresence>
      </div>

      <StatusBar
        taskText={taskText}
        running={busy || batchRunning || statsRunning || mediaRunning}
        progressText={statsRunning ? `统计 ${statsProgressText}` : mediaProg ? `媒体 ${mediaProg}` : ""}
        quotaText={quotaText}
        version={version?.version}
        onShowChangelog={() => setChangelogOpen(true)}
        pendingCount={pendingItems.length}
        onOpenTasks={() => setTasksOpen(true)}
        onStop={() => stopAll()}
      />
      <AnimatePresence>{logVisible && <LogPanel onClose={() => setLogVisible(false)} />}</AnimatePresence>

      {/* 未完成任务（断点续跑）：批量退出中断后剩余项落盘，可继续或放弃 */}
      <Dialog open={tasksOpen} onOpenChange={setTasksOpen}>
        <DialogHeader>
          <DialogTitle>未完成的退出任务（{pendingItems.length} 个）</DialogTitle>
          <DialogDescription>
            上次批量退出被停止 / 中断，剩余项已保存在断点。可按右侧「退出」页的节奏继续执行，或放弃这批任务。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-64 overflow-y-auto rounded-md border border-border p-2 text-sm">
          {pendingItems.slice(0, 100).map((it) => (
            <div key={it.peer_key} className="truncate py-0.5">{it.title || it.peer_key}</div>
          ))}
          {pendingItems.length > 100 && <div className="text-muted-foreground">…另有 {pendingItems.length - 100} 个</div>}
          {pendingItems.length === 0 && <div className="text-muted-foreground">（断点是空的）</div>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setTasksOpen(false)}>关闭</Button>
          <Button variant="outline" disabled={!pendingItems.length || batchRunning}
            onClick={async () => { await clearPending(); setTasksOpen(false); }}>
            放弃任务
          </Button>
          <Button variant="primary" disabled={!pendingItems.length || batchRunning}
            onClick={async () => {
              setTasksOpen(false);
              await resumePending(leavePrefs.interval, leavePrefs.jitter);
            }}>
            继续执行（间隔 {leavePrefs.interval}s）
          </Button>
        </DialogFooter>
      </Dialog>

      {/* 更新日志 */}
      <Dialog open={changelogOpen} onOpenChange={setChangelogOpen}>
        <DialogHeader>
          <DialogTitle>{version?.name || "TG 频道批量退出工具"} {version?.version}</DialogTitle>
          <DialogDescription>当前版本置顶；下方为历史版本。</DialogDescription>
        </DialogHeader>
        <div className="max-h-80 space-y-3 overflow-y-auto rounded-md border border-border p-3 text-sm">
          {(version?.changelog || []).map((c, i) => (
            <div key={c.version}>
              <div className={i === 0 ? "font-semibold text-primary" : "font-semibold"}>
                {c.version} <span className="ml-2 text-xs font-normal text-muted-foreground">{c.date}</span>
                {i === 0 && <span className="ml-2 text-xs text-muted-foreground">（当前版本）</span>}
              </div>
              <ul className="mt-1 list-disc space-y-0.5 pl-6 text-muted-foreground">
                {c.notes.map((n, j) => <li key={j}>{n}</li>)}
              </ul>
            </div>
          ))}
          {!version && <div className="text-muted-foreground">（后端未连接，读不到更新日志）</div>}
        </div>
        <DialogFooter>
          <Button variant="default" onClick={() => setChangelogOpen(false)}>关闭</Button>
        </DialogFooter>
      </Dialog>

      {/* 快捷键 */}
      <Dialog open={shortcutsOpen} onOpenChange={setShortcutsOpen}>
        <DialogHeader>
          <DialogTitle>快捷键一览</DialogTitle>
        </DialogHeader>
        <div className="space-y-1 text-sm">
          {[
            ["F1", "显示本帮助"],
            ["F5", "刷新会话列表"],
            ["Ctrl+F", "聚焦搜索框"],
            ["Esc", "清空搜索（搜索框聚焦时）"],
            ["Ctrl+A", "全选当前显示的行（跨页）"],
            ["双击账号", "切换到该账号（右侧「账号」页）"],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-3">
              <kbd className="min-w-20 rounded border border-border bg-muted px-2 py-0.5 text-center text-xs">{k}</kbd>
              <span className="text-muted-foreground">{v}</span>
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="default" onClick={() => setShortcutsOpen(false)}>关闭</Button>
        </DialogFooter>
      </Dialog>

      <ToastViewport />
    </div>
  );
}

const TOAST_STYLE: Record<string, string> = {
  info: "border-border bg-card text-foreground",
  success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-500",
  error: "border-red-500/40 bg-red-500/10 text-red-500",
  warn: "border-warning/40 bg-warning/10 text-warning",
};

function ToastViewport() {
  const { toasts, dismissToast } = useToasts();
  return (
    <div className="fixed bottom-5 right-5 z-[60] flex w-96 max-w-[90vw] flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} onClick={() => dismissToast(t.id)}
          className={`cursor-pointer rounded-lg border px-4 py-3 text-sm shadow-lg animate-slide-up ${TOAST_STYLE[t.type] || TOAST_STYLE.info}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}
