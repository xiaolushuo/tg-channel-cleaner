import { Search, RefreshCw, PanelRight, ScrollText, Sun, Moon, PlayCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useApp } from "@/store/AppContext";
import type { LoginStage } from "@/store/AppContext";

export const STATUS_META: Record<LoginStage, { text: string; dot: string; pill: string }> = {
  idle: { text: "未连接", dot: "bg-zinc-400", pill: "border-border bg-muted/50 text-muted-foreground" },
  connecting: { text: "正在连接…", dot: "bg-warning animate-pulse", pill: "border-warning/30 bg-warning/10 text-warning" },
  need_code: { text: "等待验证码", dot: "bg-warning", pill: "border-warning/30 bg-warning/10 text-warning" },
  need_password: { text: "等待两步密码", dot: "bg-warning", pill: "border-warning/30 bg-warning/10 text-warning" },
  logged_in: { text: "已连接", dot: "bg-emerald-500", pill: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" },
  failed: { text: "连接失败", dot: "bg-red-500", pill: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400" },
};

/**
 * 顶栏（现代 SaaS 风）：品牌 · 状态胶囊 · 居中搜索 · 图标化操作。
 * 低频「停止」收进菜单；运行中会变为醒目样式。
 */
export function HeaderBar(props: {
  stage: LoginStage;
  userName: string;
  version?: string;
  demo?: boolean;
  loggedIn: boolean;
  onSwitchMode: (m: "demo" | "real") => void;
  leftVisible: boolean;
  onToggleLeft: () => void;
  onConnect: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  search: string;
  onSearch: (v: string) => void;
  searchHit: number;
  onToggleLog: () => void;
  logVisible: boolean;
  searchRef: React.RefObject<HTMLInputElement | null>;
}) {
  const meta = STATUS_META[props.stage];
  const statusExtra =
    props.stage === "logged_in" && props.userName ? ` · ${props.userName}` : "";
  const { theme, toggleTheme, loginNote } = useApp();
  // 连接中 / 失败时显示后端分阶段说明（v3.20.1）：卡在哪一步、建议怎么处理，一眼可见
  const noteText = loginNote && (props.stage === "connecting" || props.stage === "failed")
    ? loginNote : "";

  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-4">
      {/* 品牌（左侧只放品牌，视图开关统一归右侧操作区） */}
      <div className="flex select-none items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 via-violet-500 to-cyan-400 text-[13px] shadow-sm">⚡</div>
        <span className="text-[15px] font-semibold tracking-tight">TG Cleaner</span>
        {props.version && (
          <Badge variant="outline" className="hidden px-1.5 py-0 font-mono text-[10px] text-muted-foreground md:inline-flex">
            {props.version}
          </Badge>
        )}
        {props.loggedIn ? (
          /* 模式切换（v3.19.2）：演示 ⇄ 正常，一键互切（登录后可见） */
          <div className="hidden h-6 items-center rounded-full border border-border bg-muted/60 p-0.5 text-[11px] font-medium sm:flex"
            title="演示模式 = 模拟数据不连 Telegram；正常模式 = 连接真实账号">
            <button type="button" onClick={() => props.onSwitchMode("demo")}
              className={cn("rounded-full px-2 py-0.5 transition-colors",
                props.demo
                  ? "bg-amber-500 text-amber-950 shadow-sm"
                  : "text-muted-foreground hover:text-foreground")}
              aria-pressed={!!props.demo}>
              演示
            </button>
            <button type="button" onClick={() => props.onSwitchMode("real")}
              className={cn("rounded-full px-2 py-0.5 transition-colors",
                !props.demo
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")}
              aria-pressed={!props.demo}>
              正常
            </button>
          </div>
        ) : (
          <>
            <Badge variant="outline" className="hidden px-1.5 py-0 text-[10px] text-muted-foreground md:inline-flex">
              未登录
            </Badge>
            {/* 未登录也能一键进演示（v3.19.7）：零凭据体验全部功能 */}
            <Button size="sm" variant="outline"
              className="hidden h-6 border-amber-500/40 px-2 text-[11px] text-amber-600 hover:bg-amber-500/10 hover:text-amber-600 dark:text-amber-400 dark:hover:text-amber-400 sm:inline-flex"
              onClick={() => props.onSwitchMode("demo")}
              title="演示模式：模拟数据，不连 Telegram，无需任何凭据">
              <PlayCircle className="h-3 w-3" /> 体验演示
            </Button>
          </>
        )}
      </div>

      {/* 状态胶囊 + 连接明细（note 文案较长，胶囊旁独立展示，hover 看全文） */}
      <span className={cn(
        "hidden sm:inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors",
        meta.pill,
      )} title={`连接状态：${meta.text}${statusExtra}`}>
        <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
        {meta.text}{statusExtra}
      </span>
      {noteText && (
        <span className="hidden max-w-[280px] truncate text-xs text-muted-foreground lg:inline"
          title={noteText}>
          {noteText}
        </span>
      )}

      {props.stage !== "logged_in" && (
        <Button size="sm" variant="primary" onClick={props.onConnect}
          disabled={props.stage === "connecting"}
          title="连接 / 登录当前账号（登录态会保存，下次不用再收验证码）">
          连接 / 登录
        </Button>
      )}

      {/* 搜索：输入即筛，居中占主宽 */}
      <div className="relative mx-auto flex h-9 w-full max-w-md items-center lg:max-w-xl">
        <Search className="absolute left-3 h-4 w-4 text-muted-foreground" />
        <input
          ref={props.searchRef}
          value={props.search}
          onChange={(e) => props.onSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") props.onSearch(""); }}
          placeholder="搜索名称 / 用户名…"
          className="h-9 w-full rounded-full border border-input bg-muted/40 pl-9 pr-16 text-sm transition-colors placeholder:text-muted-foreground focus:border-ring focus:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {props.search ? (
          <>
            <span className="absolute right-8 text-xs tabular-nums text-muted-foreground">{props.searchHit}</span>
            <button type="button" onClick={() => props.onSearch("")}
              className="absolute right-2.5 rounded-full p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground">✕</button>
          </>
        ) : (
          <kbd className="absolute right-3 hidden rounded border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground md:block">Ctrl F</kbd>
        )}
      </div>

      {/* 操作区：功能动作 | 分隔线 | 视图开关（面板 / 日志）——对齐主流软件惯例 */}
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" className="h-8 w-8" onClick={props.onRefresh}
          disabled={!props.loggedIn || props.refreshing}
          title="重新拉取全部会话（分页加载，几百上千个也不会卡）">
          <RefreshCw className={cn("h-4 w-4", props.refreshing && "animate-spin")} />
        </Button>

        <div className="mx-1.5 h-5 w-px bg-border" />

        <Button size="icon" variant="ghost"
          className={cn("h-8 w-8", props.leftVisible && "bg-accent text-accent-foreground")}
          onClick={props.onToggleLeft} title="展开 / 收起设置面板">
          <PanelRight className="h-4 w-4" />
        </Button>

        <Button size="icon" variant="ghost"
          className={cn("h-8 w-8", props.logVisible && "bg-accent text-accent-foreground")}
          onClick={props.onToggleLog} title="展开 / 收起运行日志（右侧栏）">
          <ScrollText className="h-4 w-4" />
        </Button>

        {/* 暗色快速切换（v3.19.7）：亮/暗一键互切，选择持久化到本机 */}
        <Button size="icon" variant="ghost" className="h-8 w-8"
          onClick={toggleTheme}
          title={theme === "dark" ? "切换到亮色模式" : "切换到暗色模式"}>
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}
