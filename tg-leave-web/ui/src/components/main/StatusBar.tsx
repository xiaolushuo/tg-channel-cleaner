import { Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 状态栏：运行指示 + 任务状态（运行中可就地停止）+ 未完成任务入口 | 右侧：版本号 / 更新日志。 */
export function StatusBar(props: {
  taskText: string;
  running: boolean;
  progressText?: string;
  quotaText?: string;
  version?: string;
  onShowChangelog: () => void;
  pendingCount?: number;      // 断点未完成任务数（>0 时显示入口）
  onOpenTasks?: () => void;   // 打开「未完成任务」对话框
  onStop?: () => void;        // 停止当前批量 / 统计任务
}) {
  return (
    <div className="flex h-8 shrink-0 items-center gap-3 border-t border-border bg-card px-4 text-xs">
      <span className="inline-flex items-center gap-1.5 text-muted-foreground">
        <span className={cn("h-1.5 w-1.5 rounded-full",
          props.running ? "animate-pulse bg-blue-500" : "bg-emerald-500/70")} />
        {props.taskText}
      </span>
      {props.progressText && <span className="text-blue-500">{props.progressText}</span>}
      {props.running && props.onStop && (
        <Button size="sm" variant="outline" className="h-5 gap-1 px-2 text-[11px] text-red-500 hover:bg-red-500/10 hover:text-red-500"
          onClick={props.onStop} title="发出停止指令：当前条处理完后中止，剩余项保留在断点（统计则中断前端等待，结果仍会落缓存）">
          <Pause className="h-3 w-3" /> 停止
        </Button>
      )}
      {(props.pendingCount ?? 0) > 0 && props.onOpenTasks && (
        <Button size="sm" variant="outline"
          className="h-5 gap-1 border-warning/50 px-2 text-[11px] text-warning hover:bg-warning/10"
          onClick={props.onOpenTasks}
          title="上次批量任务有未完成的剩余项（断点已落盘），可继续执行或放弃">
          <Play className="h-3 w-3" /> 未完成任务 {props.pendingCount}
        </Button>
      )}
      <div className="flex-1" />
      {props.quotaText && <span className="text-muted-foreground">{props.quotaText}</span>}
      <button type="button" onClick={props.onShowChangelog}
        className="rounded px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
        更新日志
      </button>
      <span className="font-mono text-muted-foreground">{props.version || ""}</span>
    </div>
  );
}
