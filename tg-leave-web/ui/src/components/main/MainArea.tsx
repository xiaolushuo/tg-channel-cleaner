import { useCallback, useEffect, useMemo, useRef, useState, memo } from "react";
import type { ReactNode } from "react";
import {
  ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Star, Trash2, Copy,
  ExternalLink, X, DoorOpen, SlidersHorizontal, Pin, BadgeCheck, Archive, BellOff, Inbox, Columns3,
} from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "@/store/AppContext";
import { motion, AnimatePresence } from "framer-motion";
import { MenuButton } from "@/components/ui/menu";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  Dialog, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
} from "@/components/ui/dialog";
import type { Dialog as Dlg, Folder, HistoryItem, MediaSummary } from "@/lib/types";
import { cn, fmtNum } from "@/lib/utils";
import type { LeavePrefs, StatsPrefs } from "./LeftPanel";
import { OverviewCard } from "./OverviewCard";

const RISK_SEP = " · ";
const ROLE_LABEL: Record<string, string> = { creator: "👑 创建者", admin: "🛡 管理员" };

export const TYPE_CHOICES = ["全部", "频道", "群组", "私聊", "机器人", "已不在"];
const FOLDER_ALL = "全部分组";
const FOLDER_NONE = "未分组";

const KIND_BADGE: Record<string, string> = {
  "频道": "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  "群组": "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  "私聊": "bg-zinc-500/10 text-zinc-600 dark:text-zinc-300",
  "机器人": "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  "已不在": "bg-warning/10 text-warning",
};

export const FILTER_PRESETS: { label: string; m: number | null; p: number | null; a: number | null }[] = [
  { label: "不筛选（显示全部）", m: null, p: null, a: null },
  { label: "用户数 < 10", m: 10, p: null, a: null },
  { label: "用户数 < 50", m: 50, p: null, a: null },
  { label: "用户数 < 100", m: 100, p: null, a: null },
  { label: "作品数 < 10", m: null, p: 10, a: null },
  { label: "作品数 < 50", m: null, p: 50, a: null },
  { label: "作品数 < 100", m: null, p: 100, a: null },
  { label: "活跃 > 30 天没消息", m: null, p: null, a: 30 },
  { label: "活跃 > 90 天没消息", m: null, p: null, a: 90 },
  { label: "活跃 > 180 天没消息", m: null, p: null, a: 180 },
  { label: "活跃 > 365 天没消息", m: null, p: null, a: 365 },
  { label: "空会话（作品数 = 0）", m: null, p: 1, a: null },
  { label: "空会话且 >90 天没消息", m: null, p: 1, a: 90 },
  { label: "用户数<10 且 作品数<10", m: 10, p: 10, a: null },
  { label: "用户数<100 且 作品数<10", m: 100, p: 10, a: null },
  { label: "用户数<10 且 作品数<100", m: 10, p: 100, a: null },
  { label: "用户数<50 且 活跃>90 天", m: 50, p: null, a: 90 },
  { label: "作品数<10 且 活跃>180 天", m: null, p: 10, a: 180 },
];
const FILTER_CUSTOM = "自定义…";
const PAGE_SIZES = [20, 50, 100, 200];

type SortKey = "name" | "username" | "type" | "members" | "posts" | "active" | "unread" | "folder" | "risk" | "media";

// 媒体总数口径（v3.20.7）：不含文本与链接的桶之和（链接与媒体桶服务端交叉重复，
// 相加虚高会超过作品数；官方客户端也分页签各算）。优先用后端 total（同口径现算）
const mediaTotal = (m?: MediaSummary): number =>
  m ? (typeof m.total === "number" ? m.total
    : Object.entries(m.counts).filter(([k]) => !MEDIA_TOTAL_EXCLUDE.has(k)).reduce((s, [, v]) => s + v, 0)) : -1;

export function MainArea(props: {
  baseFiltered: Dlg[];              // 顶栏搜索后的行
  allDialogs: Dlg[];
  loggedIn: boolean;
  busy: boolean;
  statsPrefs: StatsPrefs;
  leavePrefs: LeavePrefs;
  setLeavePrefs: (p: LeavePrefs) => void;
  onShowShortcuts: () => void;
  onVisibleCount: (n: number) => void;
  kwOpen: boolean;
  setKwOpen: (v: boolean) => void;
  kwText: string;
  setKwText: (v: string) => void;
}) {
  const {
    loggedIn, toast, applyWhitelist, leaveMany, doDestroy,
    batchRunning, folders, loadFolders, applyFolder, quotaText, syncDialogs,
    loadHistory, clearHistory, mediaRunning, mediaMap,
  } = useApp();

  /* ------- 选中状态（本组件内部管理） ------- */
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const toggleOne = useCallback((pk: string) => {
    setSelected((prev) => { const n = new Set(prev); n.has(pk) ? n.delete(pk) : n.add(pk); return n; });
  }, []);

  /* ------- 筛选状态 ------- */
  const [typeFilter, setTypeFilter] = useState("全部");
  const [folderFilter, setFolderFilter] = useState(FOLDER_ALL);
  const [onlyChecked, setOnlyChecked] = useState(false);
  const [presetIdx, setPresetIdx] = useState(0);
  const [mLt, setMLt] = useState("");
  const [pLt, setPLt] = useState("");
  const [aGt, setAGt] = useState("");
  const [hideUnknown, setHideUnknown] = useState(true);
  const [hideProtected, setHideProtected] = useState(false);
  const [riskCodes, setRiskCodes] = useState<Set<string>>(new Set());
  const [muteFilter, setMuteFilter] = useState<"all" | "muted" | "unmuted">("all");   // v3.19.6 静音筛选
  // 排序持久化（v3.19.0）：刷新后保留上次的排序键与方向
  const [sortKey, setSortKey] = useState<SortKey | null>(() => {
    try {
      const s = JSON.parse(localStorage.getItem("tg_sort") || "null");
      return s?.k || null;
    } catch { return null; }
  });
  const [sortDir, setSortDir] = useState<"asc" | "desc">(() => {
    try {
      const s = JSON.parse(localStorage.getItem("tg_sort") || "null");
      return s?.dir === "desc" ? "desc" : "asc";
    } catch { return "asc"; }
  });
  useEffect(() => {
    localStorage.setItem("tg_sort", JSON.stringify(sortKey ? { k: sortKey, dir: sortDir } : null));
  }, [sortKey, sortDir]);

  /* ------- 分页 ------- */
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(() => {
    const n = parseInt(localStorage.getItem("tg_page_size") || "50", 10);
    return [20, 50, 100, 200].includes(n) ? n : 50;
  });
  useEffect(() => {
    localStorage.setItem("tg_page_size", String(pageSize));
  }, [pageSize]);

  // 列显隐（名称 / 勾选框 / 操作三列固定，其余可开关；选择持久化到本机）
  const COL_DEFAULTS: Record<string, boolean> = { username: true, kind: true, members: true, posts: true, active: true, unread: true, mute: true, created: true, folders: true, media: true, risk: true };
  const COL_DEFS: { key: string; label: string }[] = [
    { key: "username", label: "用户名" },
    { key: "kind", label: "类型" },
    { key: "members", label: "用户数" },
    { key: "posts", label: "作品数" },
    { key: "active", label: "活跃" },
    { key: "unread", label: "未读" },
    { key: "mute", label: "静音" },
    { key: "created", label: "创建" },
    { key: "folders", label: "分组" },
    { key: "media", label: "媒体" },
    { key: "risk", label: "异常" },
  ];
  const [cols, setCols] = useState<Record<string, boolean>>(() => {
    try { return { ...COL_DEFAULTS, ...JSON.parse(localStorage.getItem("tg_cols") || "{}") }; }
    catch { return { ...COL_DEFAULTS }; }
  });
  const toggleCol = (k: string) => setCols((p) => {
    const n = { ...p, [k]: !p[k] };
    // 至少保留一列内容，避免表格全空
    if (!COL_DEFS.some((c) => n[c.key])) return p;
    localStorage.setItem("tg_cols", JSON.stringify(n));
    return n;
  });
  const [jumpText, setJumpText] = useState("");
  const [mediaDlgOpen, setMediaDlgOpen] = useState(false);   // 媒体统计深度对话框（v3.18.0）
  const [mediaKeys, setMediaKeys] = useState<string[]>([]);
  const [mediaDetail, setMediaDetail] = useState<Dlg | null>(null);   // 媒体分布详情对话框（v3.19.0）
  const openMediaDetail = useCallback((d: Dlg) => setMediaDetail(d), []);

  const applyPreset = (idx: number) => {
    setPresetIdx(idx);
    const p = FILTER_PRESETS[idx];
    if (p) {
      setMLt(p.m == null ? "" : String(p.m));
      setPLt(p.p == null ? "" : String(p.p));
      setAGt(p.a == null ? "" : String(p.a));
    }
  };
  const onThreshold = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setPresetIdx(FILTER_PRESETS.length);
  };
  const clearFilters = () => {
    setTypeFilter("全部"); setFolderFilter(FOLDER_ALL); setOnlyChecked(false);
    setPresetIdx(0); setMLt(""); setPLt(""); setAGt("");
    setRiskCodes(new Set()); setHideProtected(false); setMuteFilter("all");
  };

  // 头部筛选（类型/分组/只看已勾选）
  const headerFiltered = useMemo(() => props.baseFiltered.filter((d) => {
    if (typeFilter !== "全部" && d.kind !== typeFilter) return false;
    if (folderFilter === FOLDER_NONE && d.folders) return false;
    if (folderFilter !== FOLDER_ALL && folderFilter !== FOLDER_NONE
      && !(d.folders || "").includes(folderFilter)) return false;
    if (onlyChecked && !selected.has(d.peer_key)) return false;
    return true;
  }), [props.baseFiltered, selected, typeFilter, folderFilter, onlyChecked]);

  // 快捷筛选 + 异常 + 隐藏受保护 + 排序
  // 对齐 v1.7.7 _row_keep：①没设任何阈值时「隐藏统计未获取」不参与过滤；
  // ②「用户数」只对频道/群组生效；③未获取值 → return not hide_unknown
  const visible = useMemo(() => {
    const m = parseInt(mLt, 10), p = parseInt(pLt, 10), a = parseInt(aGt, 10);
    const hasM = !Number.isNaN(m), hasP = !Number.isNaN(p), hasA = !Number.isNaN(a);
    const memberKind = (d: Dlg) => d.kind === "频道" || d.kind === "群组";
    let rows = headerFiltered.filter((d) => {
      if (hasM && memberKind(d)) {
        if (d.members == null) { if (hideUnknown) return false; }
        else if (!(d.members < m)) return false;
      }
      if (hasP) {
        if (d.posts == null) { if (hideUnknown) return false; }
        else if (!(d.posts < p)) return false;
      }
      if (hasA) {
        if (d.active_days == null) { if (hideUnknown) return false; }
        else if (!(d.active_days > a)) return false;
      }
      if (riskCodes.size > 0 && !d.risk.some((r) => riskCodes.has(r.code))) return false;
      if (hideProtected && d.is_protected) return false;
      if (muteFilter === "muted" && !d.muted) return false;
      if (muteFilter === "unmuted" && d.muted) return false;
      return true;
    });
    if (sortKey) {
      const dir = sortDir === "asc" ? 1 : -1;
      const val = (d: Dlg): string | number => {
        switch (sortKey) {
          case "name": return d.title;
          case "username": return d.username || "";
          case "type": return d.kind;
          case "members": return d.members ?? -1;
          case "posts": return d.posts ?? -1;
          case "active": return d.active_days ?? 1e12;
          case "unread": return d.unread ?? 0;
          case "folder": return d.folders || "";
          case "risk": return d.risk_cell || "";
          case "media": return mediaTotal(mediaMap[d.peer_key]);   // 未统计=-1 沉底
        }
      };
      rows = [...rows].sort((x, y) => {
        const vx = val(x), vy = val(y);
        if (typeof vx === "string" || typeof vy === "string")
          return String(vx).localeCompare(String(vy), "zh") * dir;
        return (vx - vy) * dir;
      });
    }
    return rows;
  }, [headerFiltered, mLt, pLt, aGt, hideUnknown, riskCodes, hideProtected, muteFilter,
      sortKey, sortDir, mediaMap]);

  // 上报可见行数（搜索命中数）
  useEffect(() => { props.onVisibleCount(visible.length); }, [visible.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // 筛选变化 → 回第一页；可见集变小 → 收拢页码
  useEffect(() => { setPage(0); },
    [typeFilter, folderFilter, onlyChecked, mLt, pLt, aGt, riskCodes, hideProtected, muteFilter, props.baseFiltered]);
  const totalPages = Math.max(1, Math.ceil(visible.length / pageSize));
  useEffect(() => { if (page > totalPages - 1) setPage(totalPages - 1); }, [page, totalPages]);
  const pageRows = useMemo(
    () => visible.slice(page * pageSize, page * pageSize + pageSize),
    [visible, page, pageSize]);

  const selectedRows = props.allDialogs.filter((d) => selected.has(d.peer_key));
  const protectedCount = selectedRows.filter((d) => d.is_protected).length;
  const actionable = selectedRows.filter((d) => !d.is_protected);

  const allSelected = pageRows.length > 0 && pageRows.every((d) => selected.has(d.peer_key));
  const someSelected = pageRows.some((d) => selected.has(d.peer_key));
  const selectAllVisible = () => setSelected((prev) => {
    const n = new Set(prev);
    visible.forEach((d) => !d.is_protected && n.add(d.peer_key));
    return n;
  });
  const invert = () => setSelected((prev) => {
    const n = new Set(prev);
    visible.forEach((d) => { if (!d.is_protected) n.has(d.peer_key) ? n.delete(d.peer_key) : n.add(d.peer_key); });
    return n;
  });
  const clearAll = () => setSelected(() => new Set());

  // Ctrl+A 全选当前筛选结果的全部行（跨页）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a"
        && !["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName)) {
        e.preventDefault();
        setSelected((prev) => {
          const n = new Set(prev);
          visible.forEach((d) => !d.is_protected && n.add(d.peer_key));
          return n;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible]);

  // PageUp / PageDown 翻页
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName)) return;
      if (e.key === "PageUp") { e.preventDefault(); setPage((p) => Math.max(0, p - 1)); }
      else if (e.key === "PageDown") { e.preventDefault(); setPage((p) => Math.min(totalPages - 1, p + 1)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [totalPages]);

  const doJump = () => {
    const n = parseInt(jumpText, 10);
    if (!Number.isNaN(n)) setPage(Math.min(totalPages, Math.max(1, n)) - 1);
    setJumpText("");
  };

  // 异常类型（类别来自服务端字段，零请求；chips 直接点击筛选）
  const riskItems = useMemo(() => {
    const c: Record<string, { short: string; n: number }> = {};
    props.allDialogs.forEach((d) => d.risk.forEach((r) => {
      c[r.code] = { short: r.short, n: (c[r.code]?.n || 0) + 1 };
    }));
    return Object.entries(c);
  }, [props.allDialogs]);

  const toggleWhitelist = () => {
    if (!actionable.length) { toast("先勾选要处理的行", "warn"); return; }
    const anyOff = actionable.some((d) => !d.whitelisted);
    applyWhitelist(actionable.map((d) => d.peer_key), anyOff);
  };

  // 退出（预览确认 → 批量执行，带节奏）
  const [confirmOpen, setConfirmOpen] = useState(false);
  const runLeave = () => {
    if (!actionable.length) return;
    setConfirmOpen(false);
    leaveMany(actionable.map((d) => d.peer_key), props.leavePrefs.interval, props.leavePrefs.jitter);
    clearAll();
  };
  const onLeaveClick = () => {
    if (!actionable.length) { toast("先勾选要退出的行（受保护项自动跳过）", "warn"); return; }
    if (protectedCount) toast(`已自动跳过 ${protectedCount} 个受保护项（白名单 / 创建者 / 管理员）`, "info");
    if (props.leavePrefs.preview) setConfirmOpen(true);
    else runLeave();
  };

  // 销毁
  const [destroyOpen, setDestroyOpen] = useState(false);
  const [destroyKind, setDestroyKind] = useState<"频道" | "群组">("频道");
  const [destroyWord, setDestroyWord] = useState("");
  const destroyTargets = useMemo(
    () => actionable.filter((d) => d.kind === destroyKind && d.role === "creator"),
    [actionable, destroyKind]);
  const openDestroy = (kind: "频道" | "群组") => {
    const rows = actionable.filter((d) => d.kind === kind);
    if (!rows.length) { toast(`勾选里没有我创建的${kind}`, "warn"); return; }
    const creators = rows.filter((d) => d.role === "creator");
    if (!creators.length) { toast(`勾选的${kind}都不是你创建的（销毁仅创建者可用）`, "warn"); return; }
    if (creators.length < rows.length)
      toast(`已自动只选你创建的 ${creators.length} 个${kind}（其余不是创建者）`, "info");
    setDestroyKind(kind);
    setDestroyWord("");
    setDestroyOpen(true);
  };
  const runDestroy = () => {
    setDestroyOpen(false);
    doDestroy(destroyTargets.map((d) => d.peer_key), destroyWord);
    clearAll();
  };

  // 更多：静音 / 归档（智能切换 + 如实回报，不谎报成功）
  const muteArchive = async (kind: "mute" | "archive") => {
    if (!actionable.length) { toast("先勾选要处理的行", "warn"); return; }
    // 对号式语义：全部已处于该状态 → 取消；否则补齐开启
    const allOn = kind === "mute"
      ? actionable.every((d) => d.muted)
      : actionable.every((d) => d.archived);
    const on = !allOn;
    const label = `${on ? "" : "取消"}${kind === "mute" ? "静音" : "归档"}`;
    const keys = actionable.map((d) => d.peer_key);
    try {
      const r = kind === "mute" ? await api.mute(keys, on) : await api.archive(keys, on);
      if (r.failed.length) {
        const bad = r.failed.slice(0, 3).map((f) => f.note).join("；");
        toast(`${label}：成功 ${r.ok}/${r.total}，失败 ${r.failed.length}（如：${bad}）`, "warn");
      } else {
        toast(`${label} ${r.ok} 个`, "success");
      }
      await syncDialogs();   // 后端已回写 dto，同步后徽标/状态如实反映
    } catch (e: any) { toast(e?.message || "操作失败", "error"); }
  };
  const allMuted = actionable.length > 0 && actionable.every((d) => d.muted);
  const allArchived = actionable.length > 0 && actionable.every((d) => d.archived);

  // 行内静音切换（单行，点击「静音」列徽标即切换）—— 与批量同一套如实回报逻辑
  const [muteBusy, setMuteBusy] = useState<Set<string>>(new Set());
  const toggleMuteOne = useCallback(async (pk: string) => {
    const d = props.allDialogs.find((x) => x.peer_key === pk);
    if (!d) return;
    setMuteBusy((p) => { if (p.has(pk)) return p; const n = new Set(p); n.add(pk); return n; });
    try {
      const r = await api.mute([pk], !d.muted);
      if (r.failed.length) toast(`静音失败：${r.failed[0].note}`, "error");
      else toast(d.muted ? `已取消静音「${d.title}」` : `已静音「${d.title}」`, "success");
      await syncDialogs();
    } catch (e: any) { toast(e?.message || "操作失败", "error"); }
    setMuteBusy((p) => { const n = new Set(p); n.delete(pk); return n; });
  }, [props.allDialogs, syncDialogs, toast]);

  // 划分 TG 分组（批量加入 / 移出 Telegram 文件夹）
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderBusy, setFolderBusy] = useState(false);
  const openFolder = () => {
    if (!actionable.length) { toast("先勾选要划分的行", "warn"); return; }
    loadFolders();
    setFolderOpen(true);
  };
  // 对号式：按已选会话与分组的成员关系自动打 ✓（全在=实心、部分=半选、不在=空），
  // 点行即切换：全在 → 移出；不在/部分 → 补齐加入。执行后重拉成员关系，对号即时更新
  const runFolder = async (f: Folder, add: boolean) => {
    setFolderBusy(true);
    try {
      await applyFolder(f.id, actionable.map((d) => d.peer_key), add);
      await loadFolders();   // 重新拉成员关系，对号即时更新
    } finally { setFolderBusy(false); }
  };

  // 关键词退出对话框（状态挂在 MainWindow）
  const kwText = props.kwText;
  const kwMatches = useMemo(() => {
    const words = kwText.split(/[\s,，、]+/).filter(Boolean).map((w) => w.toLowerCase());
    if (!words.length) return [];
    return props.allDialogs.filter((d) => {
      if (d.is_protected || d.whitelisted) return false;
      const s = `${d.title} ${d.username || ""}`.toLowerCase();
      return words.some((w) => s.includes(w));
    });
  }, [kwText, props.allDialogs]);

  const sortBy = (k: SortKey) => {
    if (sortKey === k) setSortDir((v) => (v === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir(k === "name" || k === "username" ? "asc" : "desc"); }
  };
  const arrow = (k: SortKey) => (sortKey === k ? (sortDir === "asc" ? " ↑" : " ↓") : "");
  // 读屏可感知的排序状态（WCAG：可排序列应有 aria-sort）
  const sortAttr = (k: SortKey): "ascending" | "descending" | undefined =>
    sortKey === k ? (sortDir === "asc" ? "ascending" : "descending") : undefined;

  const showGuide = !loggedIn && props.allDialogs.length === 0;
  const folderOptions = useMemo(() => {
    const s = new Set<string>();
    props.allDialogs.forEach((d) => (d.folders || "").split(/[·,，、|]/).forEach((x) => {
      const t = x.trim(); if (t) s.add(t);
    }));
    return [...s];
  }, [props.allDialogs]);

  // 激活中的筛选 chips（可单个移除）
  const chips: { label: string; remove: () => void }[] = [];
  if (typeFilter !== "全部") chips.push({ label: `类型：${typeFilter}`, remove: () => setTypeFilter("全部") });
  if (folderFilter !== FOLDER_ALL) chips.push({ label: `分组：${folderFilter}`, remove: () => setFolderFilter(FOLDER_ALL) });
  if (onlyChecked) chips.push({ label: "只看已勾选", remove: () => setOnlyChecked(false) });
  if (!Number.isNaN(parseInt(mLt, 10))) chips.push({ label: `用户数 < ${mLt}`, remove: () => { setMLt(""); setPresetIdx(FILTER_PRESETS.length); } });
  if (!Number.isNaN(parseInt(pLt, 10))) chips.push({ label: `作品数 < ${pLt}`, remove: () => { setPLt(""); setPresetIdx(FILTER_PRESETS.length); } });
  if (!Number.isNaN(parseInt(aGt, 10))) chips.push({ label: `活跃 > ${aGt} 天`, remove: () => { setAGt(""); setPresetIdx(FILTER_PRESETS.length); } });
  if (hideProtected) chips.push({ label: "已隐藏受保护", remove: () => setHideProtected(false) });
  if (muteFilter === "muted") chips.push({ label: "只看已静音", remove: () => setMuteFilter("all") });
  if (muteFilter === "unmuted") chips.push({ label: "只看未静音", remove: () => setMuteFilter("all") });

  const moreActive = chips.length + riskCodes.size;

  // 导出（CSV / Excel，范围：选中 / 当前筛选结果）—— 对齐 v1.7.7 导出三 sheet
  const [exporting, setExporting] = useState(false);
  const doExport = async (fmt: "csv" | "xlsx", rows: Dlg[], scope: string) => {
    if (!rows.length) return;
    setExporting(true);
    try {
      await api.exportFile(fmt, rows.map((d) => d.peer_key), scope, quotaText);
      toast(`已导出 ${scope}（${rows.length} 个，${fmt.toUpperCase()}）`, "success");
    } catch (e: any) { toast(e?.message || "导出失败", "error"); }
    setExporting(false);
  };

  // 退出历史 + 重进（对齐 v1.7.7「🕘 历史」：记录 + 清空 + 粘贴链接重进）
  const [histOpen, setHistOpen] = useState(false);
  const [histItems, setHistItems] = useState<HistoryItem[]>([]);
  const [histBusy, setHistBusy] = useState(false);
  const [rejoinVal, setRejoinVal] = useState("");
  const openHistory = async () => {
    setHistOpen(true);
    setHistBusy(true);
    try { setHistItems(await loadHistory()); } catch { /* ignore */ }
    setHistBusy(false);
  };
  const runRejoin = async () => {
    const raw = rejoinVal.trim();
    if (!raw) return;
    setHistBusy(true);
    try {
      const r = await api.rejoinRaw(raw);
      if (r.state === "ok") { toast("重进成功", "success"); setRejoinVal(""); await openHistory(); }
      else if (r.state === "already") toast("已经在其中", "info");
      else toast(r.note || "重进失败", "error");
    } catch (e: any) { toast(e?.message || "重进失败", "error"); }
    setHistBusy(false);
  };

  // 快照对比（保存当前列表 → 对比两槽位：退出批量前后谁消失了 / 改名了 / 涨跌了）
  const [snapOpen, setSnapOpen] = useState(false);
  const [snapSlots, setSnapSlots] = useState<({ taken: string; count: number } | null)[]>([null, null]);
  const [snapBusy, setSnapBusy] = useState(false);
  const [snapDiff, setSnapDiff] = useState<Awaited<ReturnType<typeof api.compareSnapshots>>["diff"] | null>(null);
  const titleOf = useCallback((pk: string) =>
    props.allDialogs.find((d) => d.peer_key === pk)?.title || pk, [props.allDialogs]);
  const openSnap = async () => {
    setSnapOpen(true);
    setSnapBusy(true);
    setSnapDiff(null);
    try { setSnapSlots((await api.snapshotSlots()).slots); } catch { /* ignore */ }
    setSnapBusy(false);
  };
  const saveSlot = async (slot: number) => {
    setSnapBusy(true);
    try {
      const r = await api.saveSnapshot(slot);
      toast(`已保存快照到槽位 ${slot === 0 ? "A" : "B"}（${r.count} 个会话）`, "success");
      setSnapSlots((await api.snapshotSlots()).slots);
    } catch (e: any) { toast(e?.message || "保存失败", "error"); }
    setSnapBusy(false);
  };
  const runCompare = async () => {
    setSnapBusy(true);
    try { setSnapDiff((await api.compareSnapshots(0, 1)).diff); }
    catch (e: any) { toast(e?.message || "对比失败", "error"); }
    setSnapBusy(false);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
      {/* 概览：类型分布 / 活跃分布 / 异常分类统计（异常行可点击联动筛选） */}
      <OverviewCard dialogs={props.allDialogs} activeRisk={riskCodes}
        onToggleRisk={(code) => setRiskCodes((prev) => {
          const n = new Set(prev); n.has(code) ? n.delete(code) : n.add(code); return n;
        })} />

      {/* 行 2：账号 + 筛选工具栏（单行；阈值与开关收进「更多筛选」） */}
      <div className="rounded-xl border border-border bg-card px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <AccountBar />
          <span className="h-5 w-px bg-border" />
          <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
            className="h-8 w-24 text-sm" title="按类型筛选">
            {TYPE_CHOICES.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
          <Select value={folderFilter} onChange={(e) => setFolderFilter(e.target.value)}
            className="h-8 w-32 text-sm" title="按 Telegram 分组筛选">
            {[FOLDER_ALL, FOLDER_NONE, ...folderOptions].map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
          <Select value={muteFilter} onChange={(e) => setMuteFilter(e.target.value as "all" | "muted" | "unmuted")}
            className="h-8 w-28 text-sm" title="按静音状态筛选">
            <option value="all">静音：全部</option>
            <option value="muted">已静音</option>
            <option value="unmuted">未静音</option>
          </Select>
          <Select value={presetIdx} onChange={(e) => applyPreset(Number(e.target.value))}
            className="h-8 w-52 text-sm"
            title="常用筛选预设（各条件之间是「且」的关系）">
            {FILTER_PRESETS.map((p, i) => <option key={p.label} value={i}>{p.label}</option>)}
            <option value={FILTER_PRESETS.length}>{FILTER_CUSTOM}</option>
          </Select>
          <MoreFilters
            mLt={mLt} setMLt={onThreshold(setMLt)}
            pLt={pLt} setPLt={onThreshold(setPLt)}
            aGt={aGt} setAGt={onThreshold(setAGt)}
            hideUnknown={hideUnknown} setHideUnknown={setHideUnknown}
            onlyChecked={onlyChecked} setOnlyChecked={setOnlyChecked}
            hideProtected={hideProtected} setHideProtected={setHideProtected}
            activeCount={moreActive}
          />
        </div>
        {(chips.length > 0 || riskItems.length > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-border/60 pt-2">
            {chips.map((c, i) => (
              <span key={i} className="inline-flex h-6 items-center gap-1 rounded-full bg-primary/10 pl-2.5 pr-1 text-xs text-primary">
                {c.label}
                <button type="button" onClick={c.remove}
                  className="rounded-full p-0.5 hover:bg-primary/20"><X className="h-3 w-3" /></button>
              </span>
            ))}
            {chips.length > 0 && riskItems.length > 0 && <span className="mx-1 h-4 w-px bg-border" />}
            {riskItems.map(([code, v]) => {
              const active = riskCodes.has(code);
              return (
                <button key={code} type="button"
                  onClick={() => setRiskCodes((prev) => {
                    const n = new Set(prev); n.has(code) ? n.delete(code) : n.add(code); return n;
                  })}
                  title={`只看「${v.short}」的会话（再点一次取消）`}
                  className={cn("inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors",
                    active
                      ? "border-red-500/50 bg-red-500/10 font-medium text-red-500"
                      : "border-border text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  {v.short}
                  <span className="tabular-nums opacity-70">{v.n}</span>
                </button>
              );
            })}
            {moreActive > 0 && (
              <button type="button" onClick={clearFilters}
                className="ml-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
                清除全部
              </button>
            )}
          </div>
        )}
      </div>

      {/* 行 3：会话表（卡片 + 分页） */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card">
        {/* 表头条：全选/反选/清空 + 每页条数 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2">
          <span className="text-sm text-muted-foreground">
            共 <b className="text-foreground"><AnimatedNumber value={props.allDialogs.length} /></b> 个会话
          </span>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={selectAllVisible}
              title="筛选生效时只选当前显示出来的行（跨页）">全选</Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={invert}>反选</Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={clearAll}
              disabled={!selectedRows.length}>清空</Button>
            <span className="mx-1 h-4 w-px bg-border" />
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={openHistory}
              title="退出历史（记录每次批量退出的条目，可粘贴链接重进误退的会话）">🕘 历史</Button>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={openSnap}
              title="保存当前列表快照，批量退出后对比：谁消失了 / 改名了 / 涨跌了">快照</Button>
            <MenuButton
              label={<><Columns3 className="h-3.5 w-3.5" /> 列</>}
              className="h-7 px-2 text-xs"
              title="显示 / 隐藏表格列（选择保存在本机；名称与操作列固定显示）"
              items={COL_DEFS.map((c) => ({
                label: `${cols[c.key] ? "✓ " : "　 "}${c.label}`,
                onClick: () => toggleCol(c.key),
              }))} />
            <MenuButton
              label={<>{exporting ? "导出…" : "导出"}</>}
              className="h-7 px-2 text-xs"
              disabled={exporting || (visible.length === 0 && selectedRows.length === 0)}
              title="导出会话清单（Excel 含概览 / 清单 / 异常明细三个 sheet；范围为当前筛选结果或已勾选）"
              items={[
                { label: `已勾选 ${selectedRows.length} 个 → Excel`, disabled: !selectedRows.length,
                  onClick: () => doExport("xlsx", selectedRows, `已勾选 ${selectedRows.length} 个`) },
                { label: `已勾选 ${selectedRows.length} 个 → CSV`, disabled: !selectedRows.length,
                  onClick: () => doExport("csv", selectedRows, `已勾选 ${selectedRows.length} 个`) },
                "-",
                { label: `筛选结果 ${visible.length} 条 → Excel`, disabled: !visible.length,
                  onClick: () => doExport("xlsx", visible, `筛选结果 ${visible.length} 条`) },
                { label: `筛选结果 ${visible.length} 条 → CSV`, disabled: !visible.length,
                  onClick: () => doExport("csv", visible, `筛选结果 ${visible.length} 条`) },
              ]} />
            <Select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
              className="h-7 w-24 px-1.5 text-xs"
              title="每页显示条数（数据量大时建议 50）">
              {PAGE_SIZES.map((s) => <option key={s} value={s}>{s} 条/页</option>)}
            </Select>
          </div>
        </div>

        {/* 表格 */}
        <div className={cn("relative min-h-0 flex-1 overflow-auto", selectedRows.length > 0 && "pb-16")}>
          <table className="w-full min-w-[960px] border-collapse text-sm">
            <thead className="sticky top-0 z-30 border-b border-border/60 bg-muted/80 text-[11px] tracking-wide text-muted-foreground backdrop-blur">
              <tr>
                <th scope="col" className="sticky left-0 z-20 w-12 bg-muted px-3 py-2.5">
                  <Checkbox checked={allSelected} indeterminate={!allSelected && someSelected}
                    onCheckedChange={() => (allSelected ? clearAll() : selectAllVisible())}
                    title={allSelected ? "取消全选（当前页）" : "全选当前显示的行（跨页）"} />
                </th>
                <th scope="col" aria-sort={sortAttr("name")} className="sticky left-12 z-20 cursor-pointer bg-muted px-3 py-2.5 text-left font-medium hover:text-foreground" onClick={() => sortBy("name")}>名称{arrow("name")}</th>
                {cols.username && <th scope="col" aria-sort={sortAttr("username")} className="cursor-pointer px-3 py-2.5 text-left font-medium hover:text-foreground" onClick={() => sortBy("username")}>用户名{arrow("username")}</th>}
                {cols.kind && <th scope="col" aria-sort={sortAttr("type")} className="cursor-pointer px-3 py-2.5 text-center font-medium hover:text-foreground" onClick={() => sortBy("type")}>类型{arrow("type")}</th>}
                {cols.members && <th scope="col" aria-sort={sortAttr("members")} className="cursor-pointer px-3 py-2.5 text-right font-medium hover:text-foreground" onClick={() => sortBy("members")}>用户数{arrow("members")}</th>}
                {cols.posts && <th scope="col" aria-sort={sortAttr("posts")} className="cursor-pointer px-3 py-2.5 text-right font-medium hover:text-foreground" onClick={() => sortBy("posts")} title="频道全部消息数（服务端 history 总数，含纯文本帖）——所以媒体数（只有媒体消息）不会超过它">作品数{arrow("posts")}</th>}
                {cols.active && <th scope="col" aria-sort={sortAttr("active")} className="cursor-pointer px-3 py-2.5 text-center font-medium hover:text-foreground" onClick={() => sortBy("active")}>活跃{arrow("active")}</th>}
                {cols.unread && <th scope="col" aria-sort={sortAttr("unread")} className="cursor-pointer px-3 py-2.5 text-right font-medium hover:text-foreground" onClick={() => sortBy("unread")} title="按未读条数排序">未读{arrow("unread")}</th>}
                {cols.mute && <th scope="col" className="px-3 py-2.5 text-center font-medium" title="点击行内图标可切换单行静音；勾选后用悬浮条「静音」批量切换"><BellOff className="mx-auto h-3.5 w-3.5" /></th>}
                {cols.created && <th scope="col" className="px-3 py-2.5 text-right font-medium">创建</th>}
                {cols.folders && <th scope="col" aria-sort={sortAttr("folder")} className="cursor-pointer px-3 py-2.5 text-left font-medium hover:text-foreground" onClick={() => sortBy("folder")}>分组{arrow("folder")}</th>}
                {cols.media && <th scope="col" aria-sort={sortAttr("media")} className="cursor-pointer px-3 py-2.5 text-left font-medium hover:text-foreground" onClick={() => sortBy("media")}
                  title="按媒体总数排序（点第二次换方向）。总数 = 媒体消息数（不含链接；链接与媒体在官方客户端也是分页签各算）。默认口径 = 最近 N 条消息；要官方客户端一致的真实总数请勾选后「更多 ▾ → 统计媒体 → 全部历史」">媒体{arrow("media")}</th>}
                {cols.risk && <th scope="col" aria-sort={sortAttr("risk")} className="cursor-pointer px-3 py-2.5 text-left font-medium hover:text-foreground" onClick={() => sortBy("risk")}>异常{arrow("risk")}</th>}
                <th scope="col" className="px-3 py-2.5 text-center font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {props.busy && props.allDialogs.length === 0
                ? Array.from({ length: 8 }, (_, i) => {
                    const n = 3 + COL_DEFS.filter((c) => cols[c.key]).length;
                    return (
                      <tr key={`sk-${i}`} className="border-b border-border/50">
                        {Array.from({ length: n }, (_, j) => (
                          <td key={j} className="px-3 py-2.5">
                            <div className="h-3 animate-pulse rounded bg-muted"
                              style={{ width: `${45 + ((i * 17 + j * 29) % 40)}%` }} />
                          </td>
                        ))}
                      </tr>
                    );
                  })
                : pageRows.map((d) => (
                <Row key={d.peer_key} d={d} selected={selected.has(d.peer_key)} cols={cols}
                  onToggleRow={toggleOne} onMuteRow={toggleMuteOne} onOpenMedia={openMediaDetail}
                  muteBusy={muteBusy.has(d.peer_key)} />
              ))}
            </tbody>
          </table>

          {/* 空态 / 加载态 */}
          {showGuide && <GuideCard onShowShortcuts={props.onShowShortcuts} />}
          {!showGuide && visible.length === 0 && !props.busy && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 bg-card/80 px-6 text-center">
              <Inbox className="h-10 w-10 text-muted-foreground/30" />
              {props.allDialogs.length === 0 ? (
                <>
                  <div className="text-sm font-medium">账号下暂无会话</div>
                  <div className="max-w-md text-xs leading-5 text-muted-foreground">尚未拉取或自动拉取失败 —— 点顶部「⟳」重新拉取</div>
                </>
              ) : (
                <>
                  <div className="text-sm font-medium">当前筛选条件下没有匹配的行</div>
                  <div className="max-w-md text-xs leading-5 text-muted-foreground">统计完成前很多行是「未获取」状态：可在「更多筛选」取消勾选「隐藏未获取」，或点「清除全部」复位</div>
                </>
              )}
            </div>
          )}
          {props.busy && (
            <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-background px-4 py-1.5 text-xs text-muted-foreground shadow">
              正在拉取会话列表…（已获取 {props.allDialogs.length} 个）
            </div>
          )}
        </div>

        {/* 分页脚 */}
        <div className="flex items-center gap-2 border-t border-border px-3.5 py-2 text-xs text-muted-foreground">
          <span>
            符合条件 <b className="text-foreground">{visible.length}</b> 条
            {visible.length > 0 && <> · 第 {page * pageSize + 1}–{Math.min(visible.length, (page + 1) * pageSize)} 条</>}
          </span>
          <div className="ml-auto flex items-center gap-0.5">
            <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page === 0}
              onClick={() => setPage(0)} title="第一页"><ChevronsLeft className="h-3.5 w-3.5" /></Button>
            <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))} title="上一页（PageUp）"><ChevronLeft className="h-3.5 w-3.5" /></Button>
            <span className="px-1 tabular-nums">第 <b className="text-foreground">{page + 1}</b> / {totalPages} 页</span>
            <input value={jumpText} inputMode="numeric"
              onChange={(e) => setJumpText(e.target.value.replace(/\D/g, ""))}
              onKeyDown={(e) => { if (e.key === "Enter") doJump(); }}
              placeholder="跳至"
              title="输入页码后回车跳转"
              className="h-7 w-12 rounded-md border border-input bg-transparent px-1.5 text-center text-xs" />
            <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} title="下一页（PageDown）"><ChevronRight className="h-3.5 w-3.5" /></Button>
            <Button size="icon" variant="ghost" className="h-7 w-7" disabled={page >= totalPages - 1}
              onClick={() => setPage(totalPages - 1)} title="最后一页"><ChevronsRight className="h-3.5 w-3.5" /></Button>
          </div>
        </div>
      </div>

      {/* 悬浮操作条：有勾选时出现（Gmail / Linear 风格） */}
      <AnimatePresence>
      {selectedRows.length > 0 && (
        <motion.div key="fab" initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 18 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          className="fixed bottom-16 left-1/2 z-40 flex -translate-x-1/2 flex-wrap items-center gap-2 rounded-2xl border border-border bg-popover px-4 py-2.5 shadow-xl">
          <span className="inline-flex h-6 items-center rounded-full bg-primary px-2.5 text-xs font-medium text-primary-foreground">
            已选 {selectedRows.length}
          </span>
          {protectedCount > 0 && (
            <span className="text-xs text-warning" title="白名单 / 创建者 / 管理员在执行时会被自动跳过">受保护 {protectedCount}</span>
          )}
          <span className="mx-1 h-5 w-px bg-border" />
          <Button size="sm" variant="primary" className="h-8" disabled={!actionable.length || batchRunning}
            onClick={onLeaveClick}
            title="退出频道 / 群组；私聊与机器人会被删除会话。删完都会回头向服务端复核（节奏在右侧「退出」页设置）">
            <DoorOpen className="h-4 w-4" /> 退出 / 删除
          </Button>
          <Button size="sm" variant="outline" className="h-8" disabled={!actionable.length}
            onClick={() => muteArchive("mute")}
            title={allMuted ? "选中项全部已静音：点击批量取消静音" : "批量静音选中项（已静音的保持不变）"}>
            <BellOff className="h-4 w-4" /> {allMuted ? "取消静音" : "静音"}
          </Button>
          <MenuButton label={<><SlidersHorizontal className="h-4 w-4" /> 更多</>} className="h-8" items={[
            { label: "加入 / 移出白名单", onClick: toggleWhitelist },
            { label: "划分到 TG 分组…", onClick: openFolder },
            { label: allArchived ? "取消归档选中（移回主列表）" : "归档选中（移出主列表）", onClick: () => muteArchive("archive") },
            { label: mediaRunning ? "媒体统计执行中…" : "统计媒体（选深度）…",
              onClick: () => { setMediaKeys(actionable.map((x) => x.peer_key)); setMediaDlgOpen(true); },
              disabled: mediaRunning },
            "-",
            { label: "删除我创建的频道（不可恢复）…", onClick: () => openDestroy("频道") },
            { label: "删除我创建的群组（不可恢复）…", onClick: () => openDestroy("群组") },
          ]} title="白名单 / 分组 / 归档 / 媒体统计 / 销毁我创建的频道或群组" />
          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={clearAll} title="取消选择">
            <X className="h-4 w-4" />
          </Button>
        </motion.div>
      )}
      </AnimatePresence>

      {/* 对话框 */}
      <MediaScanDialog open={mediaDlgOpen} onOpenChange={setMediaDlgOpen} keys={mediaKeys} />
      <MediaDetailDialog d={mediaDetail} onClose={() => setMediaDetail(null)} />
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogHeader>
          <DialogTitle>待退出清单（{actionable.length} 个）</DialogTitle>
          <DialogDescription>
            退出频道 / 群组；私聊与机器人会删除会话。删完都会回头向服务端复核，没确认的行会留在列表里。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-64 overflow-y-auto rounded-md border border-border p-2 text-sm">
          {actionable.slice(0, 100).map((d) => (
            <div key={d.peer_key} className="flex items-center gap-2 py-0.5">
              <span className="text-muted-foreground">{d.kind}</span>
              <span className="truncate">{d.title}</span>
            </div>
          ))}
          {actionable.length > 100 && <div className="text-muted-foreground">…另有 {actionable.length - 100} 个</div>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setConfirmOpen(false)}>取消</Button>
          <Button variant="destructive" onClick={runLeave}>确认退出 {actionable.length} 个</Button>
        </DialogFooter>
      </Dialog>

      <Dialog open={destroyOpen} onOpenChange={setDestroyOpen}>
        <DialogHeader>
          <DialogTitle className="text-destructive">销毁我创建的{destroyKind}（不可恢复）</DialogTitle>
          <DialogDescription>
            将销毁 {destroyTargets.length} 个你创建的{destroyKind}，对所有成员消失，<b className="text-destructive">不可恢复</b>。请手打「删除」确认。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-40 overflow-y-auto rounded-md border border-border p-2 text-sm">
          {destroyTargets.map((d) => <div key={d.peer_key} className="truncate py-0.5">{d.title}</div>)}
        </div>
        <Input autoFocus placeholder='请输入「删除」' value={destroyWord} onChange={(e) => setDestroyWord(e.target.value)} />
        <DialogFooter>
          <Button variant="ghost" onClick={() => setDestroyOpen(false)}>取消</Button>
          <Button variant="destructive" disabled={destroyWord !== "删除"} onClick={runDestroy}>
            确认销毁 {destroyTargets.length} 个
          </Button>
        </DialogFooter>
      </Dialog>

      <Dialog open={props.kwOpen} onOpenChange={props.setKwOpen}>
        <DialogHeader>
          <DialogTitle>按关键词批量退出</DialogTitle>
          <DialogDescription>
            名称 / 用户名包含任一关键词即匹配。白名单与受保护的会话<b>不会</b>被选中。
          </DialogDescription>
        </DialogHeader>
        <Input autoFocus value={kwText} onChange={(e) => props.setKwText(e.target.value)} placeholder="如：羊毛 广告, 推广" />
        <p className="text-sm text-muted-foreground">
          {kwText.trim() ? (
            <>匹配到 <span className="font-semibold text-destructive">{kwMatches.length}</span> 个会话
              {kwMatches.length > 0 && (
                <>：{kwMatches.slice(0, 5).map((d) => d.title).join("、")}{kwMatches.length > 5 ? " …" : ""}</>
              )}
            </>
          ) : "输入关键词后显示匹配数量"}
        </p>
        <DialogFooter>
          <Button variant="ghost" onClick={() => props.setKwOpen(false)}>取消</Button>
          <Button variant="destructive" disabled={!kwMatches.length}
            onClick={() => {
              props.setKwOpen(false);
              leaveMany(kwMatches.map((d) => d.peer_key), props.leavePrefs.interval, props.leavePrefs.jitter);
            }}>
            退出这 {kwMatches.length} 个
          </Button>
        </DialogFooter>
      </Dialog>

      <Dialog open={folderOpen} onOpenChange={setFolderOpen}>
        <DialogHeader>
          <DialogTitle>划分到 TG 分组（已选 {actionable.length} 个）</DialogTitle>
          <DialogDescription>
            对号 = 已选会话在该分组内（实心全部在内，横线部分在内）。点整行切换：全部在内 → 移出；否则补齐加入。共享分组（Chatlist）只读。
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-72 overflow-y-auto">
          {(folders || []).map((f) => {
            const inCount = actionable.filter((d) => f.include.includes(d.peer_key)).length;
            const all = inCount === actionable.length;
            const some = inCount > 0 && !all;
            const disabled = folderBusy || !f.editable;
            return (
              <div key={f.id} role="button" tabIndex={0}
                onClick={() => { if (!disabled) runFolder(f, !all); }}
                onKeyDown={(e) => { if (!disabled && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); runFolder(f, !all); } }}
                className={cn("mb-1.5 flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2 transition-colors",
                  all ? "border-primary/40 bg-primary/5" : "border-border hover:bg-muted/60",
                  disabled && "cursor-not-allowed opacity-60")}>
                {some ? (
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-primary bg-primary text-[10px] font-bold leading-none text-primary-foreground">−</span>
                ) : (
                  <Checkbox checked={all} onCheckedChange={() => {}} className="pointer-events-none" aria-label={`切换分组 ${f.title}`} />
                )}
                <span className="min-w-0 flex-1 truncate text-sm">
                  {f.title}
                  {!f.editable && <span className="ml-1.5 text-xs text-muted-foreground">（共享，只读）</span>}
                </span>
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground"
                  title="已选会话中在该分组内的数量 / 已选总数">
                  {inCount}/{actionable.length}
                </span>
              </div>
            );
          })}
          {(folders || []).length === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              账号下还没有 TG 分组。先在 Telegram 客户端里建一个（设置 → 文件夹），再回来点这里。
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setFolderOpen(false)}>关闭</Button>
        </DialogFooter>
      </Dialog>

      {/* 退出历史 + 重进 */}
      <Dialog open={histOpen} onOpenChange={setHistOpen}>
        <DialogHeader>
          <DialogTitle>退出历史（{histItems.length} 条）</DialogTitle>
          <DialogDescription>
            记录每次退出 / 重进操作。误退了？把 @用户名 / t.me/用户名 / t.me/+邀请HASH 粘到下面即可重进。
          </DialogDescription>
        </DialogHeader>
        <div className="flex gap-1.5">
          <Input autoFocus value={rejoinVal} onChange={(e) => setRejoinVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && rejoinVal.trim() && !histBusy) runRejoin(); }}
            placeholder="如 @somename 或 https://t.me/+AbCdEf…" />
          <Button variant="outline" disabled={histBusy || !rejoinVal.trim()} onClick={runRejoin}>重进</Button>
        </div>
        <div className="max-h-64 overflow-y-auto rounded-md border border-border p-2 text-sm">
          {histBusy && histItems.length === 0 && <div className="text-muted-foreground">加载中…</div>}
          {!histBusy && histItems.length === 0 && (
            <div className="text-muted-foreground">（还没有记录：批量退出后这里会留痕）</div>
          )}
          {histItems.map((it, i) => (
            <div key={i} className="flex items-baseline gap-2 py-0.5">
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{it.t}</span>
              <span className={cn("shrink-0 rounded px-1.5 text-[11px]",
                it.action === "rejoin" ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                  : "bg-red-500/10 text-red-500")}>
                {it.action === "rejoin" ? "重进" : "退出"}
              </span>
              <span className="min-w-0 truncate">{it.title || it.value || it.peer_key}</span>
              {it.note && <span className="min-w-0 truncate text-xs text-muted-foreground">{it.note}</span>}
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setHistOpen(false)}>关闭</Button>
          <Button variant="outline" disabled={histBusy || !histItems.length}
            onClick={async () => {
              if (!window.confirm(`清空全部 ${histItems.length} 条历史记录？记录清空后不可恢复（不影响已退出的会话）。`)) return;
              setHistBusy(true);
              try { await clearHistory(); setHistItems([]); } catch { /* ignore */ }
              setHistBusy(false);
            }}>清空历史</Button>
        </DialogFooter>
      </Dialog>

      {/* 快照对比 */}
      <Dialog open={snapOpen} onOpenChange={setSnapOpen}>
        <DialogHeader>
          <DialogTitle>快照对比</DialogTitle>
          <DialogDescription>
            批量退出前把当前列表存到槽位 A，退出后存到 B（或直接与当前对比），即可看出哪些会话消失了。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2">
          {[0, 1].map((i) => {
            const s = snapSlots[i];
            return (
              <div key={i} className="rounded-lg border border-border p-2.5 text-sm">
                <div className="mb-1 font-medium">槽位 {i === 0 ? "A" : "B"}</div>
                {s ? (
                  <p className="text-xs text-muted-foreground">{s.taken} · {s.count} 个会话</p>
                ) : (
                  <p className="text-xs text-muted-foreground">（空）</p>
                )}
                <Button size="sm" variant="outline" className="mt-2 w-full" disabled={snapBusy}
                  onClick={() => saveSlot(i)}>保存当前列表</Button>
              </div>
            );
          })}
        </div>
        <Button variant="outline" disabled={snapBusy} onClick={runCompare}>对比 A → B</Button>
        {snapDiff && (
          <div className="max-h-56 space-y-2 overflow-y-auto rounded-md border border-border p-2 text-sm">
            <div>
              <b className="text-red-500">已消失 {snapDiff.removed.length}</b>
              {snapDiff.removed.length > 0 && (
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {snapDiff.removed.slice(0, 30).map(titleOf).join("、")}
                  {snapDiff.removed.length > 30 ? " …" : ""}
                </div>
              )}
            </div>
            <div>
              <b className="text-emerald-500">新增 {snapDiff.added.length}</b>
              {snapDiff.added.length > 0 && (
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {snapDiff.added.slice(0, 30).map(titleOf).join("、")}
                  {snapDiff.added.length > 30 ? " …" : ""}
                </div>
              )}
            </div>
            {snapDiff.renamed.length > 0 && (
              <div>
                <b>改名 {snapDiff.renamed.length}</b>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {snapDiff.renamed.slice(0, 15).map(([, o, n]) => `${o} → ${n}`).join("；")}
                  {snapDiff.renamed.length > 15 ? " …" : ""}
                </div>
              </div>
            )}
            {snapDiff.member_changed.length > 0 && (
              <div>
                <b>人数变动 {snapDiff.member_changed.length}</b>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {snapDiff.member_changed.slice(0, 15).map(([k, a, b]) => `${titleOf(k)}：${a} → ${b}`).join("；")}
                  {snapDiff.member_changed.length > 15 ? " …" : ""}
                </div>
              </div>
            )}
            {snapDiff.risk_new.length > 0 && (
              <div>
                <b className="text-warning">新出现异常 {snapDiff.risk_new.length}</b>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {snapDiff.risk_new.slice(0, 15).map(([k, c]) => `${titleOf(k)}（${c}）`).join("；")}
                  {snapDiff.risk_new.length > 15 ? " …" : ""}
                </div>
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => setSnapOpen(false)}>关闭</Button>
        </DialogFooter>
      </Dialog>
    </div>
  );
}

/* ---------------- 更多筛选（popover） ---------------- */
function MoreFilters(props: {
  mLt: string; setMLt: (v: string) => void;
  pLt: string; setPLt: (v: string) => void;
  aGt: string; setAGt: (v: string) => void;
  hideUnknown: boolean; setHideUnknown: (v: boolean) => void;
  onlyChecked: boolean; setOnlyChecked: (v: boolean) => void;
  hideProtected: boolean; setHideProtected: (v: boolean) => void;
  activeCount: number;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <Button type="button" variant="outline" className={cn("h-8 gap-1.5 px-2.5 text-sm", open && "bg-accent")}
        onClick={() => setOpen((v) => !v)}
        title="用户数 / 作品数 / 活跃天数阈值，以及隐藏开关">
        <SlidersHorizontal className="h-3.5 w-3.5" /> 更多筛选
        {props.activeCount > 0 && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
            {props.activeCount}
          </span>
        )}
      </Button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 space-y-3 rounded-lg border border-border bg-popover p-3 shadow-md">
          <div className="space-y-2">
            <label className="flex items-center justify-between gap-2 text-sm">
              <span className="text-muted-foreground">用户数 &lt;</span>
              <Input className="h-8 w-24" inputMode="numeric" value={props.mLt}
                onChange={(e) => props.setMLt(e.target.value.replace(/\D/g, ""))} />
            </label>
            <label className="flex items-center justify-between gap-2 text-sm">
              <span className="text-muted-foreground">作品数 &lt;</span>
              <Input className="h-8 w-24" inputMode="numeric" value={props.pLt}
                onChange={(e) => props.setPLt(e.target.value.replace(/\D/g, ""))} />
            </label>
            <label className="flex items-center justify-between gap-2 text-sm">
              <span className="text-muted-foreground">活跃(天) &gt;</span>
              <Input className="h-8 w-24" inputMode="numeric" value={props.aGt}
                onChange={(e) => props.setAGt(e.target.value.replace(/\D/g, ""))} />
            </label>
          </div>
          <div className="space-y-2 border-t border-border/60 pt-2.5">
            <label className="flex items-center gap-2 text-sm" title="统计未获取 = 还没点「统计」或该类型没有这项数据">
              <Checkbox checked={props.hideUnknown} onCheckedChange={(v) => props.setHideUnknown(!!v)} />
              隐藏统计未获取的行
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={props.onlyChecked} onCheckedChange={(v) => props.setOnlyChecked(!!v)} />
              只看已勾选
            </label>
            <label className="flex items-center gap-2 text-sm" title="白名单 / 创建者 / 管理员不参与选择与退出">
              <Checkbox checked={props.hideProtected} onCheckedChange={(v) => props.setHideProtected(!!v)} />
              隐藏受保护
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------- 账号条 ---------------- */
function AccountBar() {
  const { accounts, loggedIn, switchAccount, user } = useApp();
  const list = accounts?.accounts || [];
  const current = accounts?.current || "";
  const label = (key: string) => {
    const a = list.find((x) => x.key === key);
    return a ? (a.nick || a.phone || a.key) : key;
  };
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-xs text-muted-foreground">账号</span>
      <Select value={current} onChange={(e) => switchAccount(e.target.value)}
        className="h-8 w-44 text-sm" title="切换账号（不会退出登录，各账号数据独立）">
        {list.length === 0 && <option value="">（未添加账号）</option>}
        {list.map((a) => <option key={a.key} value={a.key}>{label(a.key)}</option>)}
      </Select>
      {loggedIn && user && (
        <span className="hidden text-xs text-muted-foreground xl:inline">
          {user.first_name || ""}{user.last_name || ""} {user.username ? `@${user.username}` : ""}
        </span>
      )}
    </div>
  );
}

/* 媒体统计列（v3.17.0；v3.20.5 重做为「总数 + 迷你堆叠分布条」） */
const MEDIA_ICON: Record<string, string> = {
  photo: "🖼", video: "🎬", voice: "🎙", audio: "🎵", sticker: "✨",
  gif: "🎞", file: "📎", link: "🔗", other: "📦",
};
const MEDIA_LABEL: Record<string, string> = {
  text: "文本", photo: "图片", video: "视频", voice: "语音", audio: "音频",
  sticker: "贴纸", gif: "GIF", file: "文件", link: "链接", other: "其他",
};
/* v3.20.7 总数口径：不含链接（服务端链接过滤器会把配文带网址的媒体消息重复计入，
   相加会虚高甚至超过作品数；官方客户端也是媒体/链接分页签各算各的）。后端 media_total 同口径 */
const MEDIA_TOTAL_EXCLUDE = new Set(["text", "link"]);
/* 各类型的分布条颜色（亮暗主题通用的中间调，与类型图标一一对应） */
const MEDIA_COLOR: Record<string, string> = {
  photo: "#f59e0b", video: "#8b5cf6", voice: "#10b981", audio: "#06b6d4",
  sticker: "#ec4899", gif: "#f472b6", file: "#64748b", link: "#3b82f6", other: "#a1a1aa",
};
/* 大数缩写：1234 → 1.2k，23456 → 2.3w（窄列不换行的关键） */
const fmtShort = (n: number) =>
  n >= 10000 ? `${(n / 10000).toFixed(n % 10000 ? 1 : 0)}w`
  : n >= 1000 ? `${(n / 1000).toFixed(n % 1000 ? 1 : 0)}k`
  : String(n);

function MediaCell({ m, onOpen }: { m: MediaSummary | undefined; onOpen: () => void }) {
  if (!m) return (
    <span className="block h-1 w-full max-w-[120px] rounded-full border border-dashed border-border/60"
      title="未统计 —— 后台会自动补齐；也可勾选后用「更多 ▾ → 统计媒体」立即获取" />
  );
  if (m.error && !m.scanned) return (
    <span className="inline-flex items-center rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive"
      title={m.error}>!</span>
  );
  const total = mediaTotal(m);
  const linkN = m.counts?.link || 0;
  const tip = m.mode === "totals"
    ? `媒体共 ${total} 条${linkN ? ` + 链接 ${linkN} 条（单列）` : ""} · 全部历史真实计数（与官方客户端媒体面板同口径，贴纸 / 纯文本不在口径内；链接不与媒体相加）。点击查看完整分布`
    : `媒体共 ${total} 条${linkN ? ` + 链接 ${linkN} 条（单列）` : ""} · 统计口径 = 最近 ${m.limit} 条消息（实际扫到 ${m.scanned}${m.exhausted ? "，已到底" : ""}）；要与官方客户端一致请用「统计媒体 → 全部历史」。点击查看完整分布`;
  const entries = Object.entries(m.counts)
    .filter(([k, v]) => k !== "text" && v > 0)
    .sort((a, b) => b[1] - a[1]);
  const sum = entries.reduce((s, [, v]) => s + v, 0) || 1;
  const top = entries.slice(0, 2);
  return (
    <button type="button" onClick={onOpen} title={tip}
      className="group/media flex w-full max-w-[120px] cursor-pointer flex-col gap-1 rounded-md px-1 py-0.5 text-left transition-colors hover:bg-muted/70">
      <span className="flex items-baseline justify-between gap-1.5 leading-none">
        <span className="text-[12px] font-semibold tabular-nums text-foreground/90">
          {total.toLocaleString("zh-CN")}
        </span>
        <span className="truncate text-[10px] tabular-nums text-muted-foreground">
          {top.length
            ? top.map(([k, v]) => `${MEDIA_ICON[k] || ""}${fmtShort(v)}`).join(" ")
            : "纯文本"}
        </span>
      </span>
      <span className="flex h-1 w-full gap-px overflow-hidden rounded-full bg-muted/80">
        {entries.length
          ? entries.map(([k, v]) => (
            <span key={k} className="h-full transition-[filter] group-hover/media:brightness-110"
              style={{ width: `${(v / sum) * 100}%`, background: MEDIA_COLOR[k] || "#a1a1aa" }} />
          ))
          : <span className="h-full w-full bg-muted-foreground/25" />}
      </span>
    </button>
  );
}

/* 媒体分布详情对话框（v3.19.0）：点击「媒体」格弹出，含全部类型计数与百分比条（触屏友好） */
function MediaDetailDialog({ d, onClose }: { d: Dlg | null; onClose: () => void }) {
  const { mediaMap } = useApp();
  const m = d ? mediaMap[d.peer_key] : undefined;
  const total = m ? Math.max(1, m.scanned) : 1;
  const rows = m
    ? Object.entries(m.counts).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
    : [];
  return (
    <Dialog open={!!d} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogHeader>
        <DialogTitle className="truncate">媒体分布 · {d?.title || ""}</DialogTitle>
        <DialogDescription>
          {m
            ? <>媒体共 <b className="text-foreground">{mediaTotal(m).toLocaleString("zh-CN")}</b> 条{m.counts?.link ? ` + 链接 ${m.counts.link.toLocaleString("zh-CN")} 条（单列，不与媒体相加）` : ""}{m.mode === "totals"
              ? " · 全部历史真实计数（与官方客户端媒体面板同口径；贴纸 / 纯文本不在该口径内）"
              : <> · 最近 {m.limit} 条，实际扫到 {m.scanned} 条{m.exhausted ? "（已到底：会话历史不足深度）" : ""}。统计口径是「最近 N 条消息」，不是频道全部历史；要与官方客户端一致请用「统计媒体 → 全部历史（真实总数）」。</>}</>
            : "该会话还没有统计数据"}
        </DialogDescription>
      </DialogHeader>
      {m ? (
        <div className="max-h-[55vh] space-y-2.5 overflow-y-auto pr-1 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex items-center gap-2.5">
              <span className="w-20 shrink-0 text-xs text-muted-foreground">{MEDIA_ICON[k] || ""} {MEDIA_LABEL[k] || k}</span>
              <div className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted/70">
                <div className="h-full rounded-full transition-all"
                  style={{ width: `${Math.max(1.5, Math.round((v / total) * 100))}%`, background: MEDIA_COLOR[k] || "#a1a1aa" }} />
              </div>
              <span className="w-28 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                <b className="font-semibold text-foreground/85">{v.toLocaleString("zh-CN")}</b> · {Math.round((v / total) * 100)}%
              </span>
            </div>
          ))}
          {m.error && <p className="pt-1 text-xs text-warning" title={m.error}>部分失败：{m.error}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          勾选该行后用「更多 ▾ → 统计媒体（选深度）…」获取最近消息的类型分布。
        </p>
      )}
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>关闭</Button>
      </DialogFooter>
    </Dialog>
  );
}

/* ---------------- 媒体统计深度对话框（v3.18.0） ---------------- */
const MEDIA_DEPTHS = [200, 500, 1000, 2000, 5000];
const MEDIA_CONCS = [1, 2, 4, 8];

function SegBtn(props: { active: boolean; onClick: () => void; children: ReactNode; title?: string }) {
  return (
    <button type="button" onClick={props.onClick} title={props.title}
      className={cn("h-8 min-w-11 rounded-md border px-2.5 text-xs tabular-nums transition-colors",
        props.active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-input bg-transparent text-foreground hover:bg-muted")}>
      {props.children}
    </button>
  );
}

function MediaScanDialog(props: { open: boolean; onOpenChange: (v: boolean) => void; keys: string[] }) {
  const { runMedia, mediaRunning, toast } = useApp();
  const [mode, setMode] = useState<"scan" | "totals">(() =>
    localStorage.getItem("tg_media_mode") === "totals" ? "totals" : "scan");
  const [depth, setDepth] = useState(() => {
    const n = parseInt(localStorage.getItem("tg_media_depth") || "500", 10);
    return n >= 1 && n <= 5000 ? n : 500;
  });
  const [conc, setConc] = useState(() => {
    const n = parseInt(localStorage.getItem("tg_media_conc") || "2", 10);
    return MEDIA_CONCS.includes(n) ? n : 2;
  });
  const [force, setForce] = useState(false);
  const pickMode = (m: "scan" | "totals") => { setMode(m); localStorage.setItem("tg_media_mode", m); };
  const pickDepth = (n: number) => { setDepth(n); localStorage.setItem("tg_media_depth", String(n)); };
  const pickConc = (n: number) => { setConc(n); localStorage.setItem("tg_media_conc", String(n)); };
  const start = () => {
    if (mode === "totals") {
      props.onOpenChange(false);
      runMedia(props.keys, { limit: 500, conc, force, mode: "totals" });
      return;
    }
    const n = Math.round(depth);
    if (!(n >= 1 && n <= 5000)) { toast("深度需在 1 ~ 5000 条之间", "warn"); return; }
    pickDepth(n);
    props.onOpenChange(false);
    runMedia(props.keys, { limit: n, conc, force });
  };
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogHeader>
        <DialogTitle>统计媒体（{props.keys.length} 个会话）</DialogTitle>
        <DialogDescription>
          两种口径：扫「最近 N 条」消息，或向服务端查询「全部历史」的真实总数（对齐官方客户端媒体面板数字）。
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <div className="mb-1.5 font-medium">统计口径</div>
          <div className="flex flex-wrap items-center gap-1.5">
            <SegBtn active={mode === "scan"} onClick={() => pickMode("scan")}
              title="逐条扫描最近 N 条消息，含文本占比，深度可选">最近 N 条（快）</SegBtn>
            <SegBtn active={mode === "totals"} onClick={() => pickMode("totals")}
              title="按类型向服务端查询全部历史的真实条数，数字与官方客户端媒体面板一致；每会话 7 个请求">全部历史（真实总数）</SegBtn>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {mode === "scan"
              ? "只反映最近 N 条，数字天然比全部历史小；要看与官方客户端一致的真实总数请选「全部历史」。"
              : "每会话 7 个请求（图片 / 视频 / GIF / 语音 / 音频 / 文件 / 链接各一），不逐条扫消息；贴纸与纯文本不在官方媒体面板口径内，故不计数。"}
          </p>
        </div>
        {mode === "scan" && (
        <div>
          <div className="mb-1.5 font-medium">扫描深度（每个会话最近多少条）</div>
          <div className="flex flex-wrap items-center gap-1.5">
            {MEDIA_DEPTHS.map((n) => (
              <SegBtn key={n} active={depth === n} onClick={() => pickDepth(n)}>{n}</SegBtn>
            ))}
            <input value={depth} inputMode="numeric"
              onChange={(e) => setDepth(parseInt(e.target.value.replace(/\D/g, "") || "0", 10) || 0)}
              title="自定义深度（1 ~ 5000 条）"
              className="h-8 w-20 rounded-md border border-input bg-transparent px-2 text-xs tabular-nums" />
            <span className="text-xs text-muted-foreground">条（1 ~ 5000）</span>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            深度越大越耗时（约 1~2 秒 / 每会话每 500 条）；会话消息不足时扫到底即止。
          </p>
        </div>
        )}
        <div>
          <div className="mb-1.5 font-medium">并发数（一次同时统计几个会话）</div>
          <div className="flex flex-wrap items-center gap-1.5">
            {MEDIA_CONCS.map((n) => (
              <SegBtn key={n} active={conc === n} onClick={() => pickConc(n)}
                title={n >= 6 ? "并发高速度快，但更容易触发 Telegram 限流" : undefined}>{n}</SegBtn>
            ))}
            <span className="text-xs text-muted-foreground">被限流 / 频繁等待时建议调低到 1~2</span>
          </div>
        </div>
        <label className="flex cursor-pointer items-center gap-2" title="忽略本地缓存，全部重新向服务端统计">
          <Checkbox checked={force} onCheckedChange={(v: boolean) => setForce(!!v)} />
          强制重新统计（忽略 24 小时缓存）
        </label>
        <p className="text-xs text-muted-foreground">
          统计期间可随时用状态栏「停止」中止；历史对成员隐藏的会话会如实标注失败原因，已拿到的部分照常返回。
        </p>
      </div>
      <DialogFooter>
        <Button variant="ghost" onClick={() => props.onOpenChange(false)}>取消</Button>
        <Button variant="primary" disabled={mediaRunning || !props.keys.length} onClick={start}>
          开始统计{mode === "totals" ? "（全部历史）" : depth && depth >= 1 && depth <= 5000 ? `（最近 ${Math.round(depth)} 条）` : ""}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}

/* 数字滚动动画：数值变化时 300ms 补间（KPI 观感） */function AnimatedNumber({ value }: { value: number }) {
  const [shown, setShown] = useState(value);
  const raf = useRef(0);
  const from = useRef(value);
  useEffect(() => {
    if (shown === value) return;
    const start = performance.now();
    const a = from.current = shown;
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / 300);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(a + (value - a) * eased));
      if (k < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return <>{shown.toLocaleString("zh-CN")}</>;
}

/* ---------------- 空态引导卡（三步开始） ---------------- */
/* 未登录 / 空列表欢迎引导（v3.19.7；v3.19.8 支持整屏 page 变体供未登录态使用） */
export function GuideCard(props: { onShowShortcuts: () => void; variant?: "page" | "overlay" }) {
  const { startLogin } = useApp();
  const page = props.variant === "page";
  const steps = [
    { n: "1", icon: "🔑", title: "申请 API 凭据",
      desc: "到 my.telegram.org 申请 api_id / api_hash，填到右侧「账号」页（只需一次）" },
    { n: "2", icon: "📱", title: "登录账号",
      desc: "右侧「账号」页填手机号 → 「添加账号并登录」→ 按提示输验证码；旧版用户可直接「导入旧版登录态」免验证码" },
    { n: "3", icon: "🧹", title: "开始清理",
      desc: "点顶部「⟳」刷新列表 → 用筛选 / 排序找到目标 → 勾选 → 底部悬浮条「退出 / 删除」" },
  ];
  return (
    <div className={page
      ? "flex min-h-0 flex-1 items-start justify-center overflow-y-auto p-6 sm:p-10"
      : "absolute inset-0 overflow-y-auto p-8"}>
      <div className={cn("mx-auto max-w-2xl space-y-4", page && "w-full rounded-2xl border border-border bg-card/90 p-6 shadow-lg backdrop-blur sm:p-8")}>
        <div>
          <div className="text-xl font-semibold">欢迎使用 TG Cleaner</div>
          <p className="mt-1 text-sm text-muted-foreground">
            还没有登录 —— 按下面三步连接你的 Telegram 账号；或者先点「先看演示」，零凭据体验全部功能（模拟数据，不连 Telegram）。
          </p>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-3">
          {steps.map((s) => (
            <div key={s.n} className="rounded-xl border border-border bg-card p-3.5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{s.n}</span>
                <span>{s.icon} {s.title}</span>
              </div>
              <p className="mt-1.5 text-xs leading-5 text-muted-foreground">{s.desc}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => startLogin()}>连接 / 登录</Button>
          <Button variant="outline" className="border-amber-500/40 text-amber-600 hover:bg-amber-500/10 hover:text-amber-600 dark:text-amber-400 dark:hover:text-amber-400"
            onClick={() => startLogin({ demo: true })}
            title="模拟数据，不连 Telegram，无需任何凭据">
            ▶ 先看演示（零凭据）
          </Button>
          <Button variant="ghost" onClick={props.onShowShortcuts}>快捷键（F1）</Button>
        </div>
        <p className="text-xs text-muted-foreground">
          提示：删 / 退之后都会回头向服务端复核，没确认的行会留在列表里，不会谎报成功。
        </p>
      </div>
    </div>
  );
}

/* ---------------- 行 ---------------- */
const Row = memo(function Row({ d, selected, onToggleRow, onMuteRow, onOpenMedia, muteBusy, cols }: {
  d: Dlg; selected: boolean; onToggleRow: (pk: string) => void; onMuteRow: (pk: string) => void;
  onOpenMedia: (d: Dlg) => void; muteBusy: boolean; cols: Record<string, boolean>;
}) {
  const { applyWhitelist, leaveMany, toast, mediaMap } = useApp();
  const isChat = d.kind !== "私聊" && d.kind !== "机器人";
  // t.me/<username> 对频道/群组/机器人/用户都成立，只要有用户名就给复制与浏览器打开
  const link = d.username ? `https://t.me/${d.username}` : "";
  // 点击名称在 Telegram 客户端打开（官方 deep link 文档 core.telegram.org/api/links）：
  // 频道/群组：①有用户名 tg://resolve ②无用户名超级群（c: 键）tg://privatepost（自带最后消息 ID，零请求）
  //   ③其余（普通小群 / 无 top_message）→ 导出邀请链接 tg://join?invite=<hash>
  // 私聊/机器人：有用户名同样走 tg://resolve；无用户名用 tg://user?id=<id>（tdesktop 的 mention 链接，唤起资料页）
  const clientLink = isChat
    ? (d.username ? `tg://resolve?domain=${d.username}`
      : d.peer_key.startsWith("c:") && (d.top_message ?? 0) > 0
        ? `tg://privatepost?channel=${d.id}&post=${d.top_message}`
        : "")
    : (d.username ? `tg://resolve?domain=${d.username}`
      : d.id ? `tg://user?id=${d.id}` : "");
  const [inviting, setInviting] = useState(false);
  const [imgFail, setImgFail] = useState(false);
  const [imgOk, setImgOk] = useState(false);   // 头像加载完成前显示占位底色，加载完淡入
  // 头像失败重试（v3.19.6）：FloodWait / 网络抖动一次 404 不再永久回退字母占位，
  // 前端退避重试 2 次（后端 download 也有重试），仍失败才回退
  const imgFailN = useRef(0);
  const imgTimer = useRef<number | undefined>(undefined);
  const [imgTry, setImgTry] = useState(0);
  useEffect(() => () => window.clearTimeout(imgTimer.current), []);
  const onImgError = () => {
    if (imgFailN.current >= 2) { setImgFail(true); return; }
    imgFailN.current += 1;
    window.clearTimeout(imgTimer.current);
    imgTimer.current = window.setTimeout(() => setImgTry((v) => v + 1), 600 * imgFailN.current);
  };
  const openByInvite = async () => {
    if (inviting) return;
    setInviting(true);
    try {
      const r = await api.exportInvite(d.peer_key);
      if (r?.tg) { window.location.href = r.tg; toast("已唤起 Telegram 客户端", "success"); }
      else if (r?.link) { await navigator.clipboard.writeText(r.link); toast("无法直达，已复制邀请链接，请粘贴到客户端打开", "warn"); }
      else toast("该会话导不出邀请链接", "error");
    } catch (e: any) { toast(e?.message || "获取邀请链接失败", "error"); }
    setInviting(false);
  };
  const safeText = d.whitelisted ? "🔒 白名单" : ROLE_LABEL[d.role] || "";
  const risks = d.risk_cell ? d.risk_cell.split(RISK_SEP) : [];
  const [confirmLeave, setConfirmLeave] = useState(false);
  const copyLink = async () => {
    try { await navigator.clipboard.writeText(link); toast("链接已复制", "success"); }
    catch { toast("复制失败", "error"); }
  };
  return (
    <tr className={cn("group border-b border-border/50 transition-colors hover:bg-muted/40",
      selected && "bg-primary/[0.06]")}>
      <td className={cn("sticky left-0 z-10 bg-card px-3 py-1.5 text-center group-hover:bg-muted/40",
        selected && "bg-primary/[0.06] group-hover:bg-primary/[0.07]")}>
        <Checkbox checked={selected} onCheckedChange={() => onToggleRow(d.peer_key)} />
      </td>
      <td className={cn("sticky left-12 z-10 max-w-[300px] bg-card px-3 py-1.5 group-hover:bg-muted/40",
        selected && "bg-primary/[0.06] group-hover:bg-primary/[0.07]")}>
        <div className="flex items-center gap-1.5">
          {d.has_photo && !imgFail ? (
            <span className="relative flex h-6 w-6 shrink-0 overflow-hidden rounded-full bg-muted">
              <img src={`/api/avatar?pk=${encodeURIComponent(d.peer_key)}&r=${imgTry}`} alt="" loading="lazy"
                className={cn("h-6 w-6 rounded-full object-cover transition-opacity duration-200",
                  imgOk ? "opacity-100" : "opacity-0")}
                onLoad={() => setImgOk(true)}
                onError={onImgError} />
            </span>
          ) : (
            <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
              KIND_BADGE[d.kind] || "bg-muted text-muted-foreground")}>
              {(d.title || "?").trim().charAt(0).toUpperCase()}
            </span>
          )}
          {clientLink ? (
            <a href={clientLink} title={`在 Telegram 客户端中打开${d.username ? ` @${d.username}` : `（ID: ${d.id}）`}${d.gigagroup ? " · 万人广播群" : ""}${d.signature ? " · 签名频道" : ""}`}
              className="truncate font-medium hover:text-primary hover:underline">{d.title}</a>
          ) : isChat ? (
            <a href="#" title="私有会话：通过邀请链接唤起 Telegram 客户端"
              onClick={(e) => { e.preventDefault(); openByInvite(); }}
              className={cn("truncate font-medium hover:text-primary hover:underline", inviting && "opacity-60")}>{d.title}</a>
          ) : (
            <span className="truncate font-medium" title={d.title}>{d.title}</span>
          )}
          {(d.pinned || d.archived) && (
            <span className="flex shrink-0 items-center gap-0.5 text-muted-foreground">
              {d.pinned && <span title="已置顶"><Pin className="h-3 w-3" /></span>}
              {d.archived && <span title="已归档（不在主列表）"><Archive className="h-3 w-3" /></span>}
            </span>
          )}
          {d.verified && <span title="官方认证"><BadgeCheck className="h-3.5 w-3.5 shrink-0 text-sky-500" /></span>}
          {safeText && (
            <span className="shrink-0 rounded-full bg-warning/10 px-1.5 py-0 text-[11px] text-warning"
              title="受保护：白名单 / 创建者 / 管理员，执行时自动跳过">{safeText}</span>
          )}
        </div>
      </td>
      {cols.username && (
      <td className="max-w-[200px] px-3 py-1.5">
        {(d.username || d.id) ? (
          <span className="flex min-w-0 items-baseline gap-1.5" title={d.id ? `ID: ${d.id}${d.username ? ` · @${d.username}` : ""}` : (d.username ? `@${d.username}` : "")}>
            {d.username
              ? <span className="truncate text-[12px] leading-tight text-foreground/75">@{d.username}</span>
              : <span className="truncate text-muted-foreground">—</span>}
            {d.id && (
              <button className="shrink-0 font-mono text-[11px] leading-tight text-muted-foreground/50 transition-colors hover:text-foreground"
                title="点击复制 ID"
                onClick={(e) => {
                  e.stopPropagation();
                  navigator.clipboard.writeText(String(d.id)).then(() => toast(`已复制 ID ${d.id}`, "success"));
                }}>{d.id}</button>
            )}
          </span>
        ) : <span className="text-muted-foreground">—</span>}
      </td>
      )}
      {cols.kind && (
      <td className="px-3 py-1.5 text-center">
        <span className={cn("inline-flex h-5 items-center rounded-full px-2 text-[11px] font-medium", KIND_BADGE[d.kind] || "bg-muted text-muted-foreground")}>
          {d.kind}
        </span>
      </td>
      )}
      {cols.members && <td className="px-3 py-1.5 text-right text-[13px] tabular-nums text-foreground/85">{fmtNum(d.members)}</td>}
      {cols.posts && <td className="px-3 py-1.5 text-right text-[13px] tabular-nums text-foreground/85">{fmtNum(d.posts)}</td>}
      {cols.active && <td className="px-3 py-1.5 text-center text-muted-foreground" title="最近一条消息的时间（含进/出频道等服务消息）">{d.active_text || "—"}</td>}
      {cols.unread && (
      <td className="px-3 py-1.5 text-right">
        {/* v3.20.6：未读 0 也明确显示「0」（淡灰）——之前渲染成空白，看起来像没取到数据，
            与「活跃=今天」放一起像自相矛盾；实际 0 = 该会话在你账号上已全部读过（服务器原样下发） */}
        {d.unread ? (
          <span className="inline-flex min-w-5 justify-center rounded-full bg-primary/10 px-1.5 text-[11px] font-medium tabular-nums text-primary"
            title={`${d.unread} 条未读`}>{d.unread > 999 ? "999+" : d.unread}</span>
        ) : (
          <span className="text-[12px] tabular-nums text-muted-foreground/40" title="无未读 —— 该会话在你账号上已全部读过（含官方客户端同步的已读状态）">0</span>
        )}
      </td>
      )}
      {cols.mute && (
      <td className="px-3 py-1.5 text-center">
        <button
          className={cn("inline-flex h-5 w-5 items-center justify-center rounded-full transition-colors",
            d.muted
              ? "bg-warning/15 text-warning hover:bg-warning/25"
              : "text-muted-foreground/30 hover:bg-muted hover:text-muted-foreground",
            muteBusy && "opacity-40")}
          disabled={muteBusy}
          title={muteBusy ? "切换中…" : d.muted ? "已静音 —— 点击取消静音" : "未静音 —— 点击静音"}
          onClick={(e) => { e.stopPropagation(); onMuteRow(d.peer_key); }}>
          <BellOff className="h-3.5 w-3.5" />
        </button>
      </td>
      )}
      {cols.created && (
      <td className="px-3 py-1.5 text-right font-mono text-[11px] text-muted-foreground/70"
        title={d.created_ts ? `创建于 ${new Date(d.created_ts * 1000).toLocaleString("zh-CN")}` : ""}>
        {d.created_ts ? new Date(d.created_ts * 1000).toLocaleDateString("zh-CN") : "—"}
      </td>
      )}
      {cols.folders && <td className="max-w-[110px] truncate px-3 py-1.5 text-muted-foreground" title={d.folders}>{d.folders || "—"}</td>}
      {cols.media && <td className="px-3 py-1.5"><MediaCell m={mediaMap[d.peer_key]} onOpen={() => onOpenMedia(d)} /></td>}
      {cols.risk && (
      <td className="px-3 py-1.5">
        <div className="flex flex-wrap gap-1">
          {risks.length === 0
            ? <span className="text-xs text-muted-foreground">—</span>
            : risks.map((r, i) => (
              <span key={i} title={r}
                className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-1.5 py-0.5 text-[11px] leading-none text-warning">
                <span className="h-1 w-1 shrink-0 rounded-full bg-warning" />{r}
              </span>
            ))}
        </div>
      </td>
      )}
      <td className="px-3 py-1.5">
        <div className="flex items-center justify-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
          {link && (
            <>
              <Button size="icon" variant="ghost" className="h-7 w-7" title="复制 t.me 链接" onClick={copyLink}>
                <Copy className="h-3.5 w-3.5" />
              </Button>
              <Button size="icon" variant="ghost" className="h-7 w-7" title="在浏览器打开"
                onClick={() => window.open(link, "_blank")}>
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
          <Button size="icon" variant="ghost" className="h-7 w-7" title="白名单" disabled={d.is_protected && !d.whitelisted}
            onClick={() => applyWhitelist([d.peer_key], !d.whitelisted)}>
            <Star className={cn("h-3.5 w-3.5", d.whitelisted && "fill-primary text-primary")} />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 hover:text-red-500" title="退出 / 删除（单行）" disabled={d.is_protected}
            onClick={() => setConfirmLeave(true)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </td>

      {/* 单行退出确认（对齐悬浮条的 preview 原则：不可逆操作必须确认） */}
      {confirmLeave && (
        <Dialog open={confirmLeave} onOpenChange={setConfirmLeave}>
          <DialogHeader>
            <DialogTitle>退出 / 删除该会话？</DialogTitle>
            <DialogDescription>
              {d.kind}「{d.title}」将从你的账号退出（私聊与机器人会删除会话记录）。操作后本工具会向服务端复核，未确认的行会留在列表里。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmLeave(false)}>取消</Button>
            <Button variant="destructive" onClick={() => { setConfirmLeave(false); leaveMany([d.peer_key], 0, 0); }}>
              确认退出 / 删除
            </Button>
          </DialogFooter>
        </Dialog>
      )}
    </tr>
  );
});
