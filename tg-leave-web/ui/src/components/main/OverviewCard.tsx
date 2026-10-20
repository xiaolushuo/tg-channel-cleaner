import { useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { Dialog as Dlg } from "@/lib/types";
import { cn } from "@/lib/utils";

const DONUT_COLORS = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#14b8a6"];

/**
 * 概览模块：只保留三块 —— 类型分布 / 活跃分布 / 异常分类统计。
 * 异常行可点击，与列表里的异常 chips 联动筛选（再点取消）。
 */
export function OverviewCard({ dialogs, activeRisk, onToggleRisk }: {
  dialogs: Dlg[];
  activeRisk?: Set<string>;
  onToggleRisk?: (code: string) => void;
}) {
  const [open, setOpen] = useState(true);

  const { kindCounts, buckets, riskList } = useMemo(() => {
    const kindCounts: Record<string, number> = {};
    dialogs.forEach((d) => { kindCounts[d.kind] = (kindCounts[d.kind] || 0) + 1; });
    const buckets = [
      { label: "今天", n: 0 }, { label: "一周内", n: 0 }, { label: "一月内", n: 0 },
      { label: "更久", n: 0 }, { label: "未知", n: 0 },
    ];
    dialogs.forEach((d) => {
      const a = d.active_days;
      if (a == null) buckets[4].n++;
      else if (a <= 0) buckets[0].n++;
      else if (a <= 7) buckets[1].n++;
      else if (a <= 30) buckets[2].n++;
      else buckets[3].n++;
    });
    const riskMap: Record<string, { code: string; short: string; n: number }> = {};
    dialogs.forEach((d) => d.risk.forEach((r) => {
      const it = riskMap[r.code] || { code: r.code, short: r.short, n: 0 };
      it.n++;
      riskMap[r.code] = it;
    }));
    const riskList = Object.values(riskMap).sort((a, b) => b.n - a.n);
    return { kindCounts, buckets, riskList };
  }, [dialogs]);

  if (dialogs.length === 0) return null;

  return (
    <div className="shrink-0 rounded-xl border border-border bg-card px-3.5 py-2.5">
      <button type="button" className="flex items-center gap-1 rounded-md px-1 py-0.5 text-xs font-semibold hover:bg-accent"
        onClick={() => setOpen((v) => !v)} title={open ? "收起概览" : "展开概览"}>
        <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", !open && "-rotate-90")} />
        概览
      </button>
      {open && (
        <div className="mt-2.5 grid grid-cols-1 items-start gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
          <Donut items={Object.entries(kindCounts).map(([k, n]) => ({ label: k, n }))} title="类型分布" />
          <Bars items={buckets} title="活跃分布" />
          <RiskBars items={riskList} active={activeRisk} onToggle={onToggleRisk} />
        </div>
      )}
    </div>
  );
}

function RiskBars({ items, active, onToggle }: {
  items: { code: string; short: string; n: number }[];
  active?: Set<string>;
  onToggle?: (code: string) => void;
}) {
  const total = items.reduce((s, x) => s + x.n, 0);
  return (
    <div className="text-xs">
      <div className="mb-2 text-muted-foreground">
        异常分类统计{items.length > 0 && <span className="ml-1 opacity-70">（点击行可筛选，再点取消）</span>}
      </div>
      {items.length === 0 ? (
        <div className="text-muted-foreground">没有检测到异常会话</div>
      ) : (
        <div className="space-y-1">
          {items.map((x) => {
            const on = active?.has(x.code);
            return (
              <button key={x.code} type="button" onClick={() => onToggle?.(x.code)}
                title={`只看「${x.short}」的会话（再点一次取消）`}
                className={cn("flex w-full items-center gap-2 rounded px-1 py-0.5 transition-colors hover:bg-accent",
                  on && "bg-red-500/10 text-red-500")}>
                <span className={cn("w-16 shrink-0 truncate text-right", on ? "font-medium text-red-500" : "text-muted-foreground")}>{x.short}</span>
                <div className="h-3 flex-1 overflow-hidden rounded bg-muted">
                  <div className={cn("h-full rounded", on ? "bg-red-500" : "bg-red-500/70")}
                    style={{ width: `${Math.max(4, (x.n / total) * 100)}%` }} />
                </div>
                <span className="w-8 shrink-0 text-right font-semibold tabular-nums">{x.n}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function Donut({ items, title }: { items: { label: string; n: number }[]; title: string }) {
  const total = items.reduce((s, x) => s + x.n, 0);
  const R = 40, C = 2 * Math.PI * R;
  let acc = 0;
  return (
    <div className="flex items-center gap-4">
      <svg width="110" height="110" viewBox="0 0 110 110">
        <circle cx="55" cy="55" r={R} fill="none" stroke="hsl(var(--muted))" strokeWidth="15" />
        {total > 0 && items.map((x, i) => {
          const frac = x.n / total;
          const dash = `${frac * C} ${C}`;
          const offset = -acc * C;
          acc += frac;
          return (
            <circle key={x.label} cx="55" cy="55" r={R} fill="none"
              stroke={DONUT_COLORS[i % DONUT_COLORS.length]} strokeWidth="15"
              strokeDasharray={dash} strokeDashoffset={offset}
              transform="rotate(-90 55 55)" />
          );
        })}
        <text x="55" y="52" textAnchor="middle" className="fill-foreground text-base font-bold">{total}</text>
        <text x="55" y="68" textAnchor="middle" className="fill-muted-foreground text-[9px]">{title}</text>
      </svg>
      <div className="space-y-1 text-xs">
        {items.map((x, i) => (
          <div key={x.label} className="flex items-center gap-2">
            <span className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
            <span>{x.label}</span>
            <span className="font-semibold">{x.n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Bars({ items, title }: { items: { label: string; n: number }[]; title: string }) {
  const max = Math.max(1, ...items.map((x) => x.n));
  return (
    <div>
      <div className="mb-2 text-xs text-muted-foreground">{title}</div>
      <div className="space-y-1.5">
        {items.map((x) => (
          <div key={x.label} className="flex items-center gap-2 text-xs">
            <span className="w-12 shrink-0 text-right text-muted-foreground">{x.label}</span>
            <div className="h-4 flex-1 overflow-hidden rounded bg-muted">
              <div className="h-full rounded bg-primary/70 transition-all"
                style={{ width: `${(x.n / max) * 100}%` }} />
            </div>
            <span className="w-10 font-semibold">{x.n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
