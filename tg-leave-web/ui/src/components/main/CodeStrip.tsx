import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LoginStage } from "@/store/AppContext";

/** 登录验证条（对齐 v1.7.7：固定在界面上，不用弹窗，避免被遮住时看不到输入框）。 */
export function CodeStrip(props: {
  stage: LoginStage;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const prompt: Record<LoginStage, { text: string; tone: "red" | "green" | "gray" }> = {
    idle: { text: "尚未连接。填好右侧「账号」页的 API ID / HASH / 手机号，点右上「连接 / 登录」；或点顶栏「体验演示」先看功能。", tone: "red" },
    connecting: { text: "正在连接 Telegram…", tone: "gray" },
    need_code: { text: "验证码已发送到你的 Telegram，请在下方输入登录验证码。", tone: "red" },
    need_password: { text: "该账号开启了两步验证，请在下方输入密码。", tone: "red" },
    logged_in: { text: "已连接。点「刷新列表」拉取会话；勾选要处理的会话后点底部「🚪 退出 / 删除选中」。", tone: "green" },
    failed: { text: "连接失败，原因见运行日志。检查 API ID / HASH、代理后再试。", tone: "red" },
  };
  const p = prompt[props.stage];
  const active = props.stage === "need_code" || props.stage === "need_password";

  useEffect(() => {
    if (active) {
      setValue("");
      inputRef.current?.focus();
    }
  }, [active]);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border bg-card px-3 py-1.5">
      <span className={cn(
        "text-sm",
        p.tone === "red" && "text-red-500",
        p.tone === "green" && "text-emerald-500",
        p.tone === "gray" && "text-muted-foreground",
      )}>
        {p.text}
      </span>
      {/* 验证码 / 两步密码输入：只在等待输入时出现（idle/连接中显示禁用输入框只会让人困惑） */}
      {active && (
        <>
          <span className="text-sm text-muted-foreground">验证码 / 两步密码:</span>
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && value.trim()) { props.onSubmit(value.trim()); setValue(""); }
            }}
            className="h-8 w-64 rounded-md border border-input bg-transparent px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Button size="sm" variant="outline" disabled={!value.trim()}
            onClick={() => { props.onSubmit(value.trim()); setValue(""); }}>
            提交
          </Button>
        </>
      )}
    </div>
  );
}
