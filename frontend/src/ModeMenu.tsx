import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { Check, ChevronDown, Code2, ListChecks, MessageSquare } from "lucide-react";
import type { Session, SessionMode } from "./types";

const MODES = [
  { id: "chat", label: "聊天模式", description: "只基于对话回答，不调用工具", icon: MessageSquare },
  { id: "plan", label: "计划模式", description: "只读探索项目，给出执行计划", icon: ListChecks },
  { id: "code", label: "编程模式", description: "修改文件、执行命令，按需请求许可", icon: Code2 },
] as const;

export function sessionMode(session: Pick<Session, "mode" | "plan_mode"> | null): SessionMode {
  return session?.mode ?? (session?.plan_mode ? "plan" : "code");
}

interface Props {
  mode: SessionMode;
  open: boolean;
  disabled: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (mode: SessionMode) => void;
}

export default function ModeMenu({ mode, open, disabled, onOpenChange, onSelect }: Props) {
  const id = useId();
  const anchor = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const openingIndex = useRef<number | null>(null);
  const current = MODES.find((item) => item.id === mode)!;
  const Icon = current.icon;
  const visible = open && !disabled;

  useEffect(() => {
    if (!visible) return;
    items.current[openingIndex.current ?? MODES.findIndex((item) => item.id === mode)]?.focus();
    openingIndex.current = null;
    const dismiss = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) onOpenChange(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [visible, mode, onOpenChange]);

  function close(restoreFocus = true) {
    onOpenChange(false);
    if (restoreFocus) trigger.current?.focus();
  }

  function onMenuKey(event: KeyboardEvent, index: number) {
    let next = index;
    if (event.key === "ArrowDown") next = (index + 1) % MODES.length;
    else if (event.key === "ArrowUp") next = (index + MODES.length - 1) % MODES.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = MODES.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    } else return;
    event.preventDefault();
    items.current[next]?.focus();
  }

  return (
    <div className="menu-anchor mode-menu" ref={anchor}
      onBlur={(event) => {
        if (visible && !event.currentTarget.contains(event.relatedTarget as Node | null)) close(false);
      }}>
      <button ref={trigger} type="button" className={`mode-button ${mode}`}
        aria-haspopup="menu" aria-expanded={visible} aria-controls={visible ? id : undefined}
        disabled={disabled}
        onClick={() => onOpenChange(!visible)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            openingIndex.current = event.key === "ArrowDown" ? 0 : MODES.length - 1;
            if (visible) items.current[openingIndex.current]?.focus();
            else onOpenChange(true);
          } else if (event.key === "Escape" && visible) {
            event.preventDefault();
            close();
          }
        }}>
        <Icon size={15} />
        <span>{current.label}</span>
        <ChevronDown size={12} />
      </button>
      {visible && (
        <div id={id} role="menu" aria-label="选择模式" className="dropdown mode-dropdown">
          {MODES.map((item, index) => {
            const ItemIcon = item.icon;
            return (
              <button key={item.id} type="button" role="menuitemradio" aria-checked={mode === item.id}
                tabIndex={-1} ref={(element) => { items.current[index] = element; }}
                onKeyDown={(event) => onMenuKey(event, index)}
                onClick={() => {
                  close();
                  if (item.id !== mode) onSelect(item.id);
                }}>
                <ItemIcon size={16} />
                <span><strong>{item.label}</strong><small>{item.description}</small></span>
                {mode === item.id && <Check size={14} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
