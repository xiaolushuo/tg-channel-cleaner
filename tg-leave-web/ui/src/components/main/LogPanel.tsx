import { useEffect, useMemo, useRef, useState } from "react";
import { Copy, Download, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useLogs } from "@/store/AppContext";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { LogEntry } from "@/lib/types";

const LEVEL_META: Record<string, { label: string; dot: string; text: string }> = {
  info: { label: "信息", dot: "bg-blue-500", text: "text-foreground/80" },
  success: { label: "成功", dot: "bg-emerald-500", text: "text-emerald-500" },
  warn: { label: "警告", dot: "bg-warning", text: "text-warning" },
  error: { label: "错误", dot: "bg-red-500", text: "text-red-500" },
};
const LEVEL_ORDER = ["info", "success", "warn", "error"];

/**
 * 运行日志 —— 右侧抽屉（默认收起，顶栏 ScrollText 图标开关）。
 * 级别 Tab + 计数、文本过滤、自动滚动、复制 / 导出 / 清空。
 */
export function LogPanel(props: { onClose: () => void }) {
  const { logs, clearLogs } = useLogs();
  const [tab, setTab] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [autoscroll, setAutoscroll] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: logs.length };
    for (const e of logs) c[e.level] = (c[e.level] || 0) + 1;
    return c;
  }, [logs]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return logs.filter((e: LogEntry) =>
      (tab === "all" || e.level === tab) && (!q || e.message.toLowerCase().includes(q)));
  }, [logs, tab, query]);

  useEffect(() => {
    if (autoscroll && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [shown, autoscroll]);

  const fmt = (list: LogEntry[]) =>
    list.map((e) => `[${e.t}] [${e.level}] ${e.message}`).join("\n");
  const copyAll = async () => {
    try { await navigator.clipboard.writeText(fmt(shown)); } catch { /* ignore */ }
  };
  const exportTxt = () => {
    const blob = new Blob([fmt(shown)], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "tg_leave_log.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  const tabs = ["all", ...LEVEL_ORDER.filter((k) => counts[k])];

  return (
    <motion.div
      initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="fixed bottom-8 right-0 top-14 z-30 flex w-[420px] max-w-[92vw] flex-col border-l border-border bg-card shadow-2xl">
      {/* 头部：标题 + 工具 */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-semibold">运行日志</span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="过滤…"
          className="h-7 w-32 rounded-md border border-input bg-transparent px-2 text-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Checkbox checked={autoscroll} onCheckedChange={(v) => setAutoscroll(!!v)} className="h-3.5 w-3.5" />
          自动滚动
        </label>
        <div className="ml-auto flex items-center gap-0.5">
          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={copyAll} title="复制全部（当前过滤结果）">
            <Copy className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={exportTxt} title="导出为 txt">
            <Download className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7 hover:text-red-500" onClick={clearLogs} title="清空日志">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
          <Button size="icon" variant="ghost" className="h-7 w-7" title="收起日志面板" onClick={props.onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      {/* 级别 Tab */}
      <div className="flex shrink-0 flex-wrap items-center gap-0.5 border-b border-border px-3 py-1.5">
        {tabs.map((k) => (
          <button key={k} type="button" onClick={() => setTab(k)}
            className={cn(
              "inline-flex h-6 items-center gap-1.5 rounded px-2 text-xs transition-colors",
              tab === k ? "bg-muted shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}>
            {k !== "all" && <span className={cn("h-1.5 w-1.5 rounded-full", LEVEL_META[k]?.dot || "bg-zinc-400")} />}
            {k === "all" ? "全部" : LEVEL_META[k]?.label || k}
            <span className="tabular-nums opacity-60">{counts[k] || 0}</span>
          </button>
        ))}
      </div>
      {/* 日志体 */}
      <div
        ref={boxRef}
        className="min-h-0 flex-1 overflow-y-auto bg-background/70 p-2.5 font-mono text-xs leading-5"
      >
        {shown.length === 0 ? (
          <div className="text-muted-foreground">（没有符合过滤条件的日志）</div>
        ) : (
          shown.map((e, i) => (
            <div key={i} className="group flex gap-2 whitespace-pre-wrap break-all">
              <span className="shrink-0 select-none text-muted-foreground/60">{e.t}</span>
              <span className={cn("min-w-0", LEVEL_META[e.level]?.text || LEVEL_META.info.text)}>{e.message}</span>
            </div>
          ))
        )}
      </div>
    </motion.div>
  );
}
