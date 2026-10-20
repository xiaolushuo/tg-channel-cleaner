import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface MenuItem {
  label: React.ReactNode;
  onClick?: () => void;
  checked?: boolean;
  disabled?: boolean;
}

/**
 * 轻量下拉菜单（对齐 v1.7.7 的 tk.Menubutton + Menu）：
 * 点按钮弹出菜单，点外部 / Esc 关闭。
 */
export function MenuButton({
  label, items, disabled, className, onOpen, title,
}: {
  label: React.ReactNode;
  items: (MenuItem | "-")[];
  disabled?: boolean;
  className?: string;
  onOpen?: () => void;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);   // 下方空间不足时向上弹（底部悬浮条场景必命中）
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

  const toggle = () => {
    if (!open) {
      onOpen?.();
      // 展开前测量：按钮底边到视口底部不足 240px（菜单约 5 项高度）就向上弹
      const rect = ref.current?.getBoundingClientRect();
      setUp(!!rect && window.innerHeight - rect.bottom < 240);
      setOpen(true);
    } else {
      setOpen(false);
    }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        title={title}
        onClick={toggle}
        className={cn(
          "inline-flex h-8 items-center gap-1 rounded-md border border-input bg-background px-2.5 text-sm",
          "hover:bg-accent hover:text-accent-foreground",
          "disabled:pointer-events-none disabled:opacity-50",
          open && "bg-accent text-accent-foreground",
          className,
        )}
      >
        {label}
        <ChevronDown className="h-3.5 w-3.5 opacity-60" />
      </button>
      {open && (
        <div className={cn(
          "absolute left-0 z-50 min-w-56 rounded-md border border-border bg-popover p-1 shadow-md animate-slide-up",
          up ? "bottom-full mb-1" : "top-full mt-1",
        )}>
          {items.map((it, i) =>
            it === "-" ? (
              <div key={i} className="my-1 h-px bg-border" />
            ) : (
              <button
                key={i}
                type="button"
                disabled={it.disabled}
                onClick={() => { setOpen(false); it.onClick?.(); }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm",
                  "hover:bg-accent hover:text-accent-foreground",
                  "disabled:pointer-events-none disabled:opacity-50",
                )}
              >
                <span className="w-4 shrink-0 text-center">{it.checked ? "✓" : ""}</span>
                <span className="min-w-0 flex-1">{it.label}</span>
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
