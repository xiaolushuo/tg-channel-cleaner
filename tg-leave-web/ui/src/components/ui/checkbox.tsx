import { Check, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

export interface CheckboxProps {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  /** 三态：true = 部分选中（显示横线），点击视为切换为选中 */
  indeterminate?: boolean;
  disabled?: boolean;
  className?: string;
  id?: string;
  title?: string;
}

export function Checkbox({ checked, indeterminate, onCheckedChange, disabled, className, id, title }: CheckboxProps) {
  const on = checked || indeterminate;
  return (
    <button
      type="button"
      id={id}
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      disabled={disabled}
      title={title}
      onClick={() => onCheckedChange(!on)}
      className={cn(
        "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border border-input transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on ? "bg-primary border-primary text-primary-foreground" : "bg-transparent",
        disabled && "opacity-50 cursor-not-allowed",
        className,
      )}
    >
      {indeterminate ? <Minus className="h-3 w-3" strokeWidth={3} />
        : checked ? <Check className="h-3 w-3" strokeWidth={3} /> : null}
    </button>
  );
}
