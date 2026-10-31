import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleCheck,
  Code2,
  Command,
  Compass,
  Copy,
  CornerDownLeft,
  CircleAlert,
  CircleX,
  FileCode2,
  FileDiff,
  Folder,
  FolderOpen,
  GitBranch,
  GitPullRequest,
  History,
  Info,
  Layers,
  ListChecks,
  LoaderCircle,
  Menu,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelRight,
  Plus,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api, chat, setToken } from "./api";
import AppearanceSettings from "./AppearanceSettings";
import ModeMenu, { sessionMode } from "./ModeMenu";
import {
  applyAppearance,
  normalizeAppearance,
  readAppearance,
  saveAppearance,
  STORAGE_KEY,
  type Appearance,
} from "./appearance";
import type {
  Bootstrap,
  Change,
  Permission,
  RunResult,
  Session,
  SessionSummary,
  ToolEvent,
  ToolStatus,
} from "./types";
import {
  latestRunResult,
  mergeToolEvents,
  readDraft,
  restoreRejectedPrompt,
  writeDraft,
} from "./workbench-state";

marked.setOptions({ gfm: true, breaks: true });
const format = (n: number) => new Intl.NumberFormat("zh-CN").format(n);
const shortPath = (path: string) =>
  path.replace(/\\/g, "/").split("/").slice(-2).join("/");
const toolNames: Record<string, string> = {
  read_file: "读取文件",
  write_file: "写入文件",
  edit_file: "编辑文件",
  bash: "执行命令",
  glob: "查找文件",
  grep: "搜索代码",
  todo_write: "更新任务",
  agent: "派生子任务",
};
function Markdown({ text }: { text: string }) {
  return (
    <div
      className="markdown"
      dangerouslySetInnerHTML={{
        __html: DOMPurify.sanitize(marked.parse(text, { async: false })),
      }}
    />
  );
}
function Brand({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand-mark ${small ? "small" : ""}`}>
      <Code2 size={small ? 17 : 21} strokeWidth={2} />
    </div>
  );
}
function IconButton({
  label,
  children,
  onClick,
  disabled = false,
  className = "",
}: {
  label: string;
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={`icon-button ${className}`}
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Dialog({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  className?: string;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const elements = dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input, select, textarea, [tabindex="0"]',
        );
        if (!elements?.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        tabIndex={-1}
        className={`dialog ${wide ? "wide" : ""} ${className}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="dialog-heading">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <IconButton label="关闭弹窗" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </div>
        {children}
      </div>
    </div>
  );
}
function OutcomeIcon({ status }: { status?: ToolStatus }) {
  if (status === "failed")
    return <CircleX size={13} className="tool-status failed" />;
  if (status === "refused")
    return <ShieldCheck size={13} className="tool-status refused" />;
  if (status === "cancelled")
    return <Square size={12} className="tool-status cancelled" />;
  if (status === "unknown")
    return <CircleAlert size={13} className="tool-status unknown" />;
  return <Check size={13} className="tool-status succeeded" />;
}
function ToolCard({
  name,
  args,
  result,
  status = "unknown",
  stage,
  warning,
  running = false,
}: {
  name: string;
  args: unknown;
  result?: string;
  status?: ToolStatus;
  stage?: string;
  warning?: string | null;
  running?: boolean;
}) {
  const brief =
    typeof args === "object" && args !== null
      ? String(
          (args as Record<string, unknown>).command ||
            (args as Record<string, unknown>).file_path ||
            (args as Record<string, unknown>).pattern ||
            "",
        )
      : "";
  const statusLabel =
    status === "failed"
      ? "失败"
      : status === "refused"
        ? "已拒绝"
        : status === "cancelled"
          ? "已取消"
          : status === "unknown"
            ? "结果未知"
            : status === "succeeded"
              ? "完成"
              : "";
  return (
    <details
      className={`tool-card ${running ? "running" : ""} ${status ? `status-${status}` : ""}`}
    >
      <summary>
        {running ? (
          <LoaderCircle size={14} className="spin" />
        ) : (
          <Terminal size={14} />
        )}
        <span>{toolNames[name] || name}</span>
        <code>{brief}</code>
        {!running && (
          <>
            {statusLabel && (
              <small className="tool-status-label">
                {statusLabel}
                {stage ? ` · ${stage}` : ""}
              </small>
            )}
            <OutcomeIcon status={status} />
          </>
        )}
        <ChevronRight size={13} className="details-arrow" />
      </summary>
      <pre className="tool-args">{JSON.stringify(args, null, 2)}</pre>
      {result && <pre className="tool-output">{result}</pre>}
      {warning && <p className="tool-warning">{warning}</p>}
    </details>
  );
}

function RunResultBanner({ result }: { result: RunResult }) {
  const copy =
    result.status === "completed"
      ? {
          title: "任务已完成",
          detail: result.rounds
            ? `共完成 ${result.rounds} 轮处理。`
            : "本次运行已正常结束。",
        }
      : result.status === "cancelled"
        ? {
            title: "任务已停止",
            detail: "已完成的操作仍然保留，后续步骤没有继续执行。",
          }
        : result.status === "max_rounds"
          ? {
              title: "已达到工具轮次上限",
              detail: "本次运行已结束，你可以补充要求后继续。",
            }
          : {
              title: "任务运行失败",
              detail:
                result.toolErrors.at(-1)?.message ||
                "本次运行未完成，请检查错误后重试。",
            };
  return (
    <section className={`run-result status-${result.status}`} role="status">
      {result.status === "completed" ? (
        <CircleCheck size={17} />
      ) : result.status === "cancelled" ? (
        <Square size={15} />
      ) : (
        <CircleAlert size={17} />
      )}
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}

export default function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [initializing, setInitializing] = useState(true);
  const [connectionError, setConnectionError] = useState("");
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [sidebar, setSidebar] = useState(() => window.innerWidth >= 700);
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const [inspector, setInspector] = useState(false);
  const [tab, setTab] = useState<"overview" | "changes">("overview");
  const [dialog, setDialog] = useState<
    "settings" | "history" | "help" | "reset" | null
  >(null);
  const [settingsTab, setSettingsTab] = useState<"appearance" | "model">("appearance");
  const [appearance, setAppearance] = useState<Appearance>(readAppearance);
  const [appearanceStorageAvailable, setAppearanceStorageAvailable] = useState(true);
  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);
  const changeAppearance = useCallback((value: Appearance) => {
    const next = normalizeAppearance(value);
    setAppearance(next);
    setAppearanceStorageAvailable(saveAppearance(next));
  }, []);
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== null && event.key !== STORAGE_KEY) return;
      try {
        if (event.storageArea !== null && event.storageArea !== localStorage)
          return;
      } catch {
        return;
      }
      // Events may be queued behind newer writes. Read the current shared value
      // without echoing it back, so tabs converge on the latest saved preference.
      setAppearance(readAppearance());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  const [permission, setPermission] = useState<Permission | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState(false);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [liveTools, setLiveTools] = useState<ToolEvent[]>([]);
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const [notice, setNotice] = useState<{
    message: string;
    error?: boolean;
  } | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [modelMenu, setModelMenu] = useState(false);
  const [modeMenu, setModeMenu] = useState(false);
  const changeModeMenu = useCallback((open: boolean) => {
    setModeMenu(open);
    if (open) setModelMenu(false);
  }, []);
  const [moreMenu, setMoreMenu] = useState(false);
  const [changes, setChanges] = useState<Change[]>([]);
  const [saved, setSaved] = useState<
    { id: string; model: string; preview: string; saved_at: string }[]
  >([]);
  const [copied, setCopied] = useState<number | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const pendingActionRef = useRef(false);
  const sendingRef = useRef(false);
  const draftRef = useRef("");
  const sessionRef = useRef<Session | null>(null);
  const navigationRef = useRef(0);
  const autoScroll = useRef(true);

  const flash = useCallback(
    (message: string, error = false) => setNotice({ message, error }),
    [],
  );
  const refreshList = useCallback(
    async () => setSessions(await api<SessionSummary[]>("sessions")),
    [],
  );
  const applySession = useCallback((value: Session) => {
    setSession(value);
    sessionRef.current = value;
    setModelDraft(value.model);
    setRunResult(latestRunResult(value.timeline));
    setPermission(value.pending_permission);
    setBusy(value.busy);
    busyRef.current = value.busy;
  }, []);
  const loadSession = useCallback(
    async (id: string) => {
      if (busyRef.current || pendingActionRef.current || sendingRef.current)
        return;
      const request = ++navigationRef.current;
      setPendingAction(true);
      pendingActionRef.current = true;
      try {
        const value = await api<Session>(
          `session?id=${encodeURIComponent(id)}`,
        );
        if (request !== navigationRef.current) return;
        applySession(value);
        localStorage.setItem("corecoder-session", id);
        const restored = readDraft(localStorage, boot?.workspace || "", id);
        setDraft(restored);
        draftRef.current = restored;
        setStreamText("");
        setReasoning("");
        setLiveTools([]);
        autoScroll.current = true;
        if (window.innerWidth < 700) setSidebar(false);
      } catch (e) {
        flash((e as Error).message, true);
      } finally {
        if (request === navigationRef.current) {
          setPendingAction(false);
          pendingActionRef.current = false;
        }
      }
    },
    [applySession, boot?.workspace, flash],
  );
  const newSession = useCallback(async () => {
    if (busyRef.current || pendingActionRef.current || sendingRef.current)
      return;
    const request = ++navigationRef.current;
    setPendingAction(true);
    pendingActionRef.current = true;
    try {
      const value = await api<Session>("sessions", {});
      if (request !== navigationRef.current) return;
      applySession(value);
      localStorage.setItem("corecoder-session", value.id);
      const restored = readDraft(localStorage, boot?.workspace || "", value.id);
      setDraft(restored);
      draftRef.current = restored;
      setStreamText("");
      setReasoning("");
      setLiveTools([]);
      await refreshList();
      if (window.innerWidth < 700) setSidebar(false);
      textarea.current?.focus();
    } catch (e) {
      flash((e as Error).message, true);
    } finally {
      if (request === navigationRef.current) {
        setPendingAction(false);
        pendingActionRef.current = false;
      }
    }
  }, [applySession, boot?.workspace, refreshList, flash]);

  useEffect(() => {
    let alive = true;
    async function initialize() {
      try {
        const value = await api<Bootstrap>("bootstrap");
        if (!alive) return;
        setBoot(value);
        setToken(value.token);
        setSessions(value.sessions);
        const previous = localStorage.getItem("corecoder-session");
        const id =
          value.sessions.find((s) => s.id === previous)?.id ||
          value.sessions[0]?.id;
        const current = id
          ? await api<Session>(`session?id=${id}`)
          : await api<Session>("sessions", {});
        if (!alive) return;
        applySession(current);
        localStorage.setItem("corecoder-session", current.id);
        const restored = readDraft(localStorage, value.workspace, current.id);
        setDraft(restored);
        draftRef.current = restored;
        await refreshList();
      } catch (e) {
        if (alive) setConnectionError((e as Error).message);
      } finally {
        if (alive) setInitializing(false);
      }
    }
    void initialize();
    return () => {
      alive = false;
    };
  }, [applySession, refreshList]);
  useEffect(() => {
    if (!boot?.workspace || !session?.id) return;
    writeDraft(localStorage, boot.workspace, session.id, draft);
  }, [boot?.workspace, session?.id, draft]);
  useEffect(() => {
    const onResize = () => {
      setViewportWidth(window.innerWidth);
      if (window.innerWidth >= 700) setSidebar(true);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth < 970) {
        setSidebar(false);
        setInspector(false);
      }
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), notice.error ? 9000 : 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSidebar(true);
        searchInput.current?.focus();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        textarea.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (autoScroll.current)
      bottom.current?.scrollIntoView({ behavior: busy ? "instant" : "smooth" });
  }, [streamText, liveTools.length, session?.messages.length, busy]);
  useEffect(() => {
    if (!session?.busy || streamText || liveTools.length) return;
    const id = session.id;
    const navigation = navigationRef.current;
    let alive = true;
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const value = await api<Session>(`session?id=${id}`);
        if (
          !alive ||
          navigation !== navigationRef.current ||
          sessionRef.current?.id !== id
        )
          return;
        applySession(value);
        if (!value.busy) void refreshList();
      } catch {
        /* preserve the visible session on a transient polling error */
      } finally {
        polling = false;
      }
    }, 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [
    session?.id,
    session?.busy,
    streamText,
    liveTools.length,
    applySession,
    refreshList,
  ]);
  useEffect(() => {
    if (!busy) setStopping(false);
  }, [busy]);
  useEffect(() => {
    if (tab !== "changes" || !session) return;
    api<Change[]>(`changes?id=${session.id}`)
      .then(setChanges)
      .catch((e) => flash(e.message, true));
  }, [tab, session?.id, session?.updated_at, flash]);
  useEffect(() => {
    if (dialog === "history")
      api<typeof saved>("saved-sessions")
        .then(setSaved)
        .catch((e) => flash(e.message, true));
  }, [dialog, flash]);

  const action = async (name: string, values: Record<string, unknown> = {}) => {
    if (
      !session ||
      busyRef.current ||
      pendingActionRef.current ||
      sendingRef.current
    )
      return false;
    setPendingAction(true);
    pendingActionRef.current = true;
    try {
      const result = await api<{
        session: Session;
        notice: string;
        sessions: SessionSummary[];
      }>("action", { id: session.id, action: name, ...values });
      applySession(result.session);
      setSessions(result.sessions);
      if (result.notice) flash(result.notice);
      return true;
    } catch (e) {
      flash((e as Error).message, true);
      return false;
    } finally {
      setPendingAction(false);
      pendingActionRef.current = false;
      setMoreMenu(false);
    }
  };

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const current = sessionRef.current;
    if (
      !current ||
      !draftRef.current.trim() ||
      busyRef.current ||
      pendingActionRef.current ||
      sendingRef.current
    )
      return;
    sendingRef.current = true;
    setSending(true);
    const prompt = draftRef.current.trim();
    const commands: Record<string, () => void> = {
      "/help": () => setDialog("help"),
      "/tokens": () => {
        setInspector(true);
        setTab("overview");
      },
      "/diff": () => {
        setInspector(true);
        setTab("changes");
      },
      "/sessions": () => setDialog("history"),
      "/save": () => {
        void action("save");
      },
      "/compact": () => {
        void action("compact");
      },
      "/undo": () => {
        void action("undo");
      },
      "/reset": () => setDialog("reset"),
      "/plan": () => {
        void action("mode", { mode: sessionMode(current) === "plan" ? "code" : "plan" });
      },
    };
    if (commands[prompt]) {
      setDraft("");
      draftRef.current = "";
      sendingRef.current = false;
      setSending(false);
      commands[prompt]();
      return;
    }
    if (prompt.startsWith("/model ")) {
      setDraft("");
      draftRef.current = "";
      sendingRef.current = false;
      setSending(false);
      void action("model", { model: prompt.slice(7).trim() });
      return;
    }
    if (prompt === "/model") {
      setDraft("");
      draftRef.current = "";
      sendingRef.current = false;
      setSending(false);
      setSettingsTab("model");
      setDialog("settings");
      return;
    }
    if (prompt.startsWith("/") && !["/approve"].includes(prompt)) {
      sendingRef.current = false;
      setSending(false);
      flash("未识别的命令，输入 /help 查看可用功能。", true);
      return;
    }
    if (
      sessionMode(current) === "plan" &&
      ["approve", "/approve"].includes(prompt.toLowerCase())
    ) {
      sendingRef.current = false;
      setSending(false);
      if (!(await action("mode", { mode: "code" }))) return;
      sendingRef.current = true;
      setSending(true);
    }
    const runSession = sessionRef.current ?? current;
    const id = runSession.id;
    let accepted = false;
    try {
      await chat(
        id,
        prompt,
        (event) => {
          if (event.type === "token")
            setStreamText((text) => text + event.text);
          if (event.type === "reasoning")
            setReasoning((text) => text + event.text);
          if (event.type === "tool_start" || event.type === "tool_result")
            setLiveTools((events) => [...events, event]);
          if (event.type === "run_result" && event.runStatus)
            setRunResult({
              status: event.runStatus,
              finishReason: event.finish_reason,
              rounds: event.rounds,
              toolErrors: event.tool_errors || [],
              text: event.text,
              at: event.at,
            });
          if (event.type === "permission")
            setPermission({
              id: event.id!,
              name: event.name!,
              arguments: event.arguments!,
            });
          if (event.type === "error") flash(event.message || "运行失败", true);
          if (event.type === "notice") flash(event.message || "");
          if (event.type === "snapshot" && event.session) {
            applySession(event.session);
            setStreamText("");
            setLiveTools([]);
            setPermission(null);
          }
        },
        () => {
          accepted = true;
          const optimistic = {
            ...runSession,
            busy: true,
            messages: [
              ...runSession.messages,
              { role: "user" as const, content: prompt },
            ],
          };
          setSession(optimistic);
          sessionRef.current = optimistic;
          if (draftRef.current.trim() === prompt) {
            setDraft("");
            draftRef.current = "";
            writeDraft(localStorage, boot?.workspace || "", id, "");
          }
          setBusy(true);
          busyRef.current = true;
          setSending(false);
          autoScroll.current = true;
          setRunResult(null);
          setStreamText("");
          setReasoning("");
          setLiveTools([]);
          setPermission(null);
        },
      );
    } catch (e) {
      if (!accepted) {
        const restored = restoreRejectedPrompt(draftRef.current, prompt);
        setDraft(restored);
        draftRef.current = restored;
      }
      flash((e as Error).message, true);
    } finally {
      try {
        const finalSession = await api<Session>(`session?id=${id}`);
        if (sessionRef.current?.id === id) applySession(finalSession);
        await refreshList();
      } catch {
        if (accepted)
          flash("连接已中断，正在后台确认最终状态；请勿重复提交。", true);
      }
      setStreamText("");
      setLiveTools([]);
      sendingRef.current = false;
      setSending(false);
    }
  };
  const approve = async (answer: string) => {
    if (!session || !permission || pendingActionRef.current) return;
    setPendingAction(true);
    pendingActionRef.current = true;
    try {
      await api("approve", {
        id: session.id,
        permission_id: permission.id,
        answer,
      });
      setPermission(null);
    } catch (e) {
      flash((e as Error).message, true);
    } finally {
      setPendingAction(false);
      pendingActionRef.current = false;
    }
  };
  const stop = async () => {
    const current = sessionRef.current;
    if (!current || stopping) return;
    setStopping(true);
    try {
      await api("stop", { id: current.id });
      setPermission(null);
      flash("正在停止后续步骤，当前工具执行完毕后结束。");
    } catch (e) {
      setStopping(false);
      flash((e as Error).message, true);
    }
  };
  const copy = async (text: string, index: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(index);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      flash("复制失败，请手动选择文本。", true);
    }
  };

  const empty = !session?.messages.length;
  const totalTokens =
    (session?.usage.prompt || 0) + (session?.usage.completion || 0);
  const percent = Math.min(
    100,
    Math.round(
      ((session?.usage.context || 0) / (session?.usage.max_context || 1)) * 100,
    ),
  );
  const filtered = sessions.filter((s) =>
    s.title.toLowerCase().includes(search.toLowerCase()),
  );
  const idleDisabled =
    busy || pendingAction || sending || initializing || !!connectionError;
  const mode = sessionMode(session);
  useEffect(() => {
    setModeMenu(false);
    setModelMenu(false);
  }, [session?.id, idleDisabled]);
  const closeDialog = useCallback(() => setDialog(null), []);

  const liveToolViews = mergeToolEvents(liveTools);
  const mobileOverlay =
    (sidebar && viewportWidth < 700) || (inspector && viewportWidth < 970);

  return (
    <div
      className={`workbench ${sidebar ? "" : "sidebar-closed"} ${inspector ? "with-inspector" : ""}`}
    >
      {mobileOverlay && (
        <button
          type="button"
          className="drawer-backdrop"
          aria-label="关闭侧边抽屉"
          onClick={() => {
            setSidebar(false);
            setInspector(false);
          }}
        />
      )}
      <aside className={`sidebar ${sidebar ? "is-open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <span>CoreCoder</span>
          <span className="version-tag">本地</span>
          <IconButton label="收起侧栏" onClick={() => setSidebar(false)}>
            <PanelLeftClose size={16} />
          </IconButton>
        </div>
        <button
          className="new-chat"
          onClick={newSession}
          disabled={idleDisabled}
        >
          <Plus size={17} />
          <span>新建对话</span>
          <kbd>＋</kbd>
        </button>
        <label className="search-box">
          <Search size={15} />
          <input
            ref={searchInput}
            aria-label="搜索对话"
            placeholder="搜索对话"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <kbd>⌘ K</kbd>
        </label>
        <div className="sidebar-section-label">
          工作区
          <Folder size={13} />
        </div>
        <button
          className="workspace-item"
          onClick={() => {
            setInspector(true);
            setTab("overview");
          }}
        >
          <span className="folder-square">
            <FolderOpen size={17} />
          </span>
          <span>
            <strong>{boot?.project || "CoreCoder"}</strong>
            <small>{boot ? shortPath(boot.workspace) : "本地项目"}</small>
          </span>
          <ChevronDown size={13} />
        </button>
        <div className="sidebar-section-label conversation-label">
          对话<span>{sessions.length}</span>
        </div>
        <nav className="conversation-list" aria-label="对话历史">
          {filtered.map((s) => (
            <button
              className={`conversation ${s.id === session?.id ? "selected" : ""}`}
              key={s.id}
              onClick={() => loadSession(s.id)}
              disabled={idleDisabled}
            >
              {s.busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <MessageSquare size={14} />
              )}
              <span>{s.title}</span>
              {s.id === session?.id && <span className="selected-dot" />}
            </button>
          ))}
          {!filtered.length && (
            <div className="no-conversations">
              {search ? "没有找到相关对话" : "你的对话会显示在这里"}
            </div>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <span className="status-dot" />
            <div>
              <strong>在你的电脑上运行</strong>
              <small>工作区与会话均保存在本机</small>
            </div>
            <ShieldCheck size={16} />
          </div>
          <div className="sidebar-footer">
            <button onClick={() => {
              setSettingsTab("appearance");
              setDialog("settings");
            }}>
              <Settings2 size={16} />
              设置
            </button>
            <IconButton label="使用帮助" onClick={() => setDialog("help")}>
              <BookOpen size={16} />
            </IconButton>
            <span>v{boot?.version || "0.7.0"}</span>
          </div>
        </div>
      </aside>

      <main className={`main-panel ${empty && !initializing ? "is-empty" : ""}`}>
        <header className="topbar">
          <div className="breadcrumbs">
            {!sidebar && (
              <IconButton label="展开侧栏" onClick={() => setSidebar(true)}>
                <Menu size={18} />
              </IconButton>
            )}
            <Folder size={15} />
            <span>{boot?.project || "CoreCoder"}</span>
            <ChevronRight size={13} />
            <strong>{session?.title || "新对话"}</strong>
          </div>
          <div className="topbar-actions">
            <span
              className={`connection-pill ${connectionError ? "offline" : ""}`}
            >
              <span className="status-dot" />
              {initializing
                ? "正在连接"
                : connectionError
                  ? "连接失败"
                  : busy
                    ? "任务进行中"
                    : "服务就绪"}
            </span>
            <IconButton
              label="保存会话"
              disabled={idleDisabled}
              onClick={() => action("save")}
            >
              <Save size={17} />
            </IconButton>
            <IconButton
              label="查看工作区面板"
              className={inspector ? "active" : ""}
              onClick={() => setInspector(!inspector)}
            >
              <PanelRight size={18} />
            </IconButton>
            <div className="menu-anchor">
              <IconButton
                label="更多会话操作"
                onClick={() => setMoreMenu(!moreMenu)}
              >
                <MoreHorizontal size={19} />
              </IconButton>
              {moreMenu && (
                <>
                  <button
                    className="menu-dismiss"
                    tabIndex={-1}
                    aria-label="关闭菜单"
                    onClick={() => setMoreMenu(false)}
                  />
                  <div className="dropdown top-dropdown">
                    <button
                      disabled={idleDisabled}
                      onClick={() => action("compact")}
                    >
                      <Layers size={15} />
                      压缩上下文
                    </button>
                    <button
                      disabled={idleDisabled}
                      onClick={() => action("undo")}
                    >
                      <RotateCcw size={15} />
                      撤销最近修改
                    </button>
                    <button
                      disabled={idleDisabled}
                      onClick={() => {
                        setDialog("history");
                        setMoreMenu(false);
                      }}
                    >
                      <History size={15} />
                      恢复 CLI 会话
                    </button>
                    <div className="menu-divider" />
                    <button
                      disabled={idleDisabled}
                      onClick={() => {
                        setDialog("reset");
                        setMoreMenu(false);
                      }}
                    >
                      <Plus size={15} />
                      清空对话上下文
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </header>

        {connectionError && (
          <div className="connection-error">
            <Info size={18} />
            <div>
              <strong>暂时无法连接本地服务</strong>
              <p>{connectionError}</p>
              <button onClick={() => location.reload()}>重新连接</button>
            </div>
          </div>
        )}
        <div
          className={`conversation-scroll ${empty ? "empty-scroll" : ""}`}
          onScroll={(e) => {
            const el = e.currentTarget;
            autoScroll.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 160;
          }}
        >
          {initializing ? (
            <div className="loading-screen">
              <LoaderCircle size={24} className="spin" />
              <p>正在打开你的工作区…</p>
            </div>
          ) : empty ? (
            <section className="welcome">
              <h1>开始一个任务</h1>
              <p className="welcome-description">在当前项目中提问、规划或修改代码。</p>
              <div className="workspace-chip">
                <FolderOpen size={14} />
                <span>{boot?.project || "CoreCoder"}</span>
                <span className="chip-separator" />
                <GitBranch size={13} />
                <span>本地工作区</span>
              </div>
            </section>
          ) : (
            <div className="message-list">
              <div className="conversation-start">
                <span>对话开始</span>
              </div>
              {session?.messages.map((message, index) => {
                if (message.role === "tool" || message.role === "system")
                  return null;
                return (
                  <article key={index} className={`message ${message.role}`}>
                    <div className="message-avatar">
                      {message.role === "user" ? (
                        <span>你</span>
                      ) : (
                        <Brand small />
                      )}
                    </div>
                    <div className="message-body">
                      <div className="message-author">
                        {message.role === "user" ? "你" : "CoreCoder"}
                        {message.role === "assistant" && (
                          <span>{session.model}</span>
                        )}
                      </div>
                      {message.content && <Markdown text={message.content} />}
                      {message.tool_calls?.map((call) => {
                        const result = session.messages.find(
                          (m) =>
                            m.role === "tool" && m.tool_call_id === call.id,
                        );
                        const callIndex =
                          message.tool_calls?.indexOf(call) || 0;
                        const outcomeIndex =
                          session.messages
                            .slice(0, index)
                            .reduce(
                              (count, item) =>
                                count + (item.tool_calls?.length || 0),
                              0,
                            ) + callIndex;
                        const outcome = session.tool_outcomes?.[outcomeIndex];
                        let args: unknown = call.function.arguments;
                        try {
                          args = JSON.parse(call.function.arguments);
                        } catch {
                          /* show the original arguments */
                        }
                        return (
                          <ToolCard
                            key={`${call.id}-${outcomeIndex}`}
                            name={call.function.name}
                            args={args}
                            result={result?.content || undefined}
                            status={outcome?.status}
                          />
                        );
                      })}
                      {message.role === "assistant" && message.content && (
                        <div className="message-actions">
                          <IconButton
                            label="复制回答"
                            onClick={() => copy(message.content!, index)}
                          >
                            {copied === index ? (
                              <Check size={14} />
                            ) : (
                              <Copy size={14} />
                            )}
                          </IconButton>
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
              {busy && (
                <article className="message assistant live-message">
                  <div className="message-avatar">
                    <Brand small />
                  </div>
                  <div className="message-body">
                    <div className="message-author">
                      CoreCoder
                      <span className="working-label">
                        <LoaderCircle size={11} className="spin" />
                        {permission ? "等待你的许可" : "正在处理"}
                      </span>
                    </div>
                    {reasoning && (
                      <details className="reasoning">
                        <summary>
                          <WandSparkles size={13} />
                          思考过程
                          <ChevronDown size={13} />
                        </summary>
                        <pre>{reasoning}</pre>
                      </details>
                    )}
                    {streamText ? (
                      <Markdown text={streamText} />
                    ) : (
                      !liveTools.length && (
                        <div className="thinking-dots">
                          <i />
                          <i />
                          <i />
                        </div>
                      )
                    )}
                    {liveToolViews.map((tool) => (
                      <ToolCard
                        key={tool.key}
                        name={tool.name}
                        args={tool.arguments}
                        result={tool.result}
                        status={tool.status}
                        stage={tool.stage}
                        warning={tool.warning}
                        running={tool.running}
                      />
                    ))}
                  </div>
                </article>
              )}
              {!busy && runResult && <RunResultBanner result={runResult} />}
              <div ref={bottom} />
            </div>
          )}
        </div>

        <div className="composer-region">
          {permission && (
            <section className="permission-banner" role="alert">
              <div className="permission-heading">
                <ShieldCheck size={17} />
                <strong>CoreCoder 请求操作许可</strong>
                <span>{toolNames[permission.name] || permission.name}</span>
              </div>
              <pre>{JSON.stringify(permission.arguments, null, 2)}</pre>
              <div className="permission-actions">
                <small>确认操作内容后，再允许执行。</small>
                <button
                  disabled={pendingAction}
                  onClick={() => approve("deny")}
                  className="button subtle"
                >
                  拒绝
                </button>
                <button
                  disabled={pendingAction}
                  onClick={() => approve("always")}
                  className="button secondary"
                >
                  此对话持续允许
                </button>
                <button
                  disabled={pendingAction}
                  onClick={() => approve("once")}
                  className="button primary"
                >
                  允许一次
                  <Check size={14} />
                </button>
              </div>
            </section>
          )}
          {mode === "chat" && (
            <div className="chat-banner">
              <MessageSquare size={14} />
              <span>聊天模式 · 只基于对话回答，不读取项目文件或调用工具。</span>
            </div>
          )}
          {mode === "plan" && (
            <div className="plan-banner">
              <ListChecks size={14} />
              <span>计划模式 · 先探索和规划，文件修改与命令执行将被拒绝。</span>
              <button
                disabled={idleDisabled}
                onClick={async () => {
                  if (await action("mode", { mode: "code" })) {
                    setDraft("approve");
                    draftRef.current = "approve";
                    textarea.current?.focus();
                  }
                }}
              >
                批准计划
                <ArrowUpRight size={13} />
              </button>
            </div>
          )}
          {empty && !initializing && mode !== "chat" && (
            <div className="suggestions">
              {[
                {
                  icon: Compass,
                  title: "读懂这个项目",
                  text: "梳理结构，找到关键入口",
                  prompt:
                    "请阅读这个项目，梳理项目结构、核心入口和各模块职责，先不要修改文件。",
                  color: "blue",
                },
                {
                  icon: ListChecks,
                  title: "规划下一步改进",
                  text: "先给出思路，再逐步实现",
                  prompt:
                    "请分析目前项目有哪些值得优先完善的功能，给出具体改进计划，先不要执行修改。",
                  color: "amber",
                },
                {
                  icon: GitPullRequest,
                  title: "检查代码质量",
                  text: "发现问题，补齐遗漏",
                  prompt:
                    "请审查项目核心代码与现有测试，找出可能的 bug 和未覆盖场景，并按优先级列出。",
                  color: "green",
                },
              ].map((item) => (
                <button
                  key={item.title}
                  className="suggestion-card"
                  disabled={idleDisabled}
                  onClick={() => {
                    setDraft(item.prompt);
                    draftRef.current = item.prompt;
                    textarea.current?.focus();
                  }}
                >
                  <span className={`suggestion-icon ${item.color}`}>
                    <item.icon size={19} />
                  </span>
                  <ArrowUpRight size={14} className="suggestion-arrow" />
                  <strong>{item.title}</strong>
                  <p>{item.text}</p>
                </button>
              ))}
            </div>
          )}
          <form
            className={`composer ${busy ? "is-working" : ""}`}
            onSubmit={send}
          >
            <textarea
              ref={textarea}
              aria-label="输入任务"
              placeholder={
                mode === "chat"
                  ? "提出问题或聊聊想法，聊天模式不会调用工具…"
                  : mode === "plan"
                    ? "描述你的目标，让 CoreCoder 先制定计划…"
                    : "描述任务、提出问题，或输入 / 查看命令…"
              }
              value={draft}
              rows={2}
              onChange={(e) => {
                setDraft(e.target.value);
                draftRef.current = e.target.value;
                e.target.style.height = "auto";
                e.target.style.height =
                  Math.min(e.target.scrollHeight, 180) + "px";
              }}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
              disabled={initializing || pendingAction || !!connectionError}
            />
            <div className="composer-toolbar">
              <div className="composer-options">
                <ModeMenu mode={mode} open={modeMenu} disabled={idleDisabled}
                  onOpenChange={changeModeMenu}
                  onSelect={(next) => { void action("mode", { mode: next }); }} />
                <div className="toolbar-divider" />
                <div className="menu-anchor">
                  <button
                    type="button"
                    className="model-button"
                    disabled={idleDisabled}
                    onClick={() => {
                      setModeMenu(false);
                      setModelMenu(!modelMenu);
                    }}
                  >
                    <span className="model-dot" />
                    {session?.model || boot?.model || "选择模型"}
                    <ChevronDown size={12} />
                  </button>
                  {modelMenu && (
                    <>
                      <button
                        type="button"
                        className="menu-dismiss"
                        aria-label="关闭模型菜单"
                        onClick={() => setModelMenu(false)}
                      />
                      <div className="dropdown model-dropdown">
                        <span className="menu-label">此工作区使用的模型</span>
                        {[
                          ...new Set(
                            [session?.model, boot?.model].filter(Boolean),
                          ),
                        ].map((model) => (
                          <button
                            type="button"
                            key={model}
                            onClick={() => {
                              void action("model", { model });
                              setModelMenu(false);
                            }}
                          >
                            <Code2 size={14} />
                            {model}
                            {model === session?.model && <Check size={13} />}
                          </button>
                        ))}
                        <div className="menu-divider" />
                        <button
                          type="button"
                          onClick={() => {
                            setSettingsTab("model");
                            setDialog("settings");
                            setModelMenu(false);
                          }}
                        >
                          <Settings2 size={14} />
                          自定义模型名称
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
              {busy ? (
                <button
                  className="send-button stop"
                  type="button"
                  title={stopping ? "正在停止" : "停止后续步骤"}
                  aria-label={stopping ? "正在停止" : "停止后续步骤"}
                  onClick={stop}
                  disabled={stopping}
                >
                  {stopping ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <Square size={14} fill="currentColor" />
                  )}
                </button>
              ) : (
                <button
                  className="send-button"
                  type="submit"
                  aria-label="发送任务"
                  disabled={!draft.trim() || idleDisabled}
                >
                  <ArrowUp size={20} />
                </button>
              )}
            </div>
          </form>
          <div className="composer-caption">
            <span>
              <ShieldCheck size={12} />
              {stopping
                ? "正在停止，等待当前步骤安全结束"
                : busy
                  ? "任务运行中，可以停止后续步骤"
                  : mode === "chat"
                    ? "只基于对话回答 · 不调用工具"
                    : "修改前询问 · 本地工作区"}
            </span>
            <span>
              <CornerDownLeft size={11} />
              发送<span className="caption-dot">·</span>Shift + Enter 换行
            </span>
          </div>
        </div>
        <footer className="statusbar">
          <span>
            <Terminal size={12} />
            CoreCoder Workbench
            <span className="statusbar-separator" />
            {boot?.project}
          </span>
          <button
            onClick={() => {
              setInspector(true);
              setTab("overview");
            }}
          >
            <Layers size={12} />
            {format(totalTokens)} tokens
            <span className="statusbar-separator" />
            上下文 {percent}%
          </button>
        </footer>
      </main>

      {inspector && (
        <aside className="inspector">
          <div className="inspector-heading">
            <strong>工作区</strong>
            <IconButton
              label="关闭工作区面板"
              onClick={() => setInspector(false)}
            >
              <X size={16} />
            </IconButton>
          </div>
          <div className="inspector-tabs">
            <button
              className={tab === "overview" ? "selected" : ""}
              onClick={() => setTab("overview")}
            >
              概览
            </button>
            <button
              className={tab === "changes" ? "selected" : ""}
              onClick={() => setTab("changes")}
            >
              文件变更
              {session?.changes.length ? (
                <span>{session.changes.length}</span>
              ) : null}
            </button>
          </div>
          <div className="inspector-content">
            {tab === "overview" ? (
              <>
                <section className="project-card">
                  <span className="folder-square">
                    <FolderOpen size={22} />
                  </span>
                  <strong>{boot?.project}</strong>
                  <small>本地工作区</small>
                  <code title={boot?.workspace}>{boot?.workspace}</code>
                  <div className="project-card-footer">
                    <GitBranch size={13} />
                    <span>当前工作目录</span>
                    <ShieldCheck size={14} />
                  </div>
                </section>
                <div className="panel-section-heading">
                  <ListChecks size={14} />
                  任务清单<span>{session?.tasks.length || 0}</span>
                </div>
                {session?.tasks.length ? (
                  <div className="task-list">
                    {session.tasks.map((task, i) => (
                      <div className={`task ${task.status}`} key={i}>
                        {task.status === "done" ? (
                          <CircleCheck size={15} />
                        ) : task.status === "in_progress" ? (
                          <LoaderCircle size={15} className="spin" />
                        ) : (
                          <Circle size={15} />
                        )}
                        <span>{task.content}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="panel-empty">
                    <ListChecks size={24} />
                    <p>让复杂任务井井有条</p>
                    <small>
                      开始多步任务后，Agent 的计划
                      <br />
                      和执行进度会显示在这里。
                    </small>
                  </div>
                )}
                <div className="panel-section-heading">
                  <Zap size={14} />
                  本次会话用量
                </div>
                <div className="usage-grid">
                  <div>
                    <small>输入 tokens</small>
                    <strong>{format(session?.usage.prompt || 0)}</strong>
                  </div>
                  <div>
                    <small>输出 tokens</small>
                    <strong>{format(session?.usage.completion || 0)}</strong>
                  </div>
                </div>
                <div className="context-meter">
                  <div>
                    <span>上下文窗口</span>
                    <span>{percent}%</span>
                  </div>
                  <div className="meter-track">
                    <span style={{ width: `${percent}%` }} />
                  </div>
                  <small>
                    {format(session?.usage.context || 0)} /{" "}
                    {format(session?.usage.max_context || 128000)} 估算 tokens
                  </small>
                </div>
                <div className="cost-row">
                  <span>已知费用小计</span>
                  <strong>
                    {session?.usage.cost_summary?.known_cost == null
                      ? "暂无账本"
                      : `$${Number(session.usage.cost_summary.known_cost).toFixed(4)}`}
                  </strong>
                </div>
                {session?.usage.cost_summary &&
                (session.usage.cost_summary.unknown_prompt_tokens ||
                  session.usage.cost_summary.unknown_completion_tokens ||
                  session.usage.cost_summary.unknown_requests) ? (
                  <div className="cost-row">
                    <span>未知费用</span>
                    <strong>
                      {format(
                        session.usage.cost_summary.unknown_prompt_tokens +
                          session.usage.cost_summary.unknown_completion_tokens,
                      )}{" "}
                      tokens · {session.usage.cost_summary.unknown_requests}{" "}
                      请求
                    </strong>
                  </div>
                ) : null}
                {session?.usage.cost_summary?.missing_usage_requests ? (
                  <div className="cost-row">
                    <span>缺少用量</span>
                    <strong>
                      {session.usage.cost_summary.missing_usage_requests} 请求
                    </strong>
                  </div>
                ) : null}
                <button
                  className="panel-text-button"
                  onClick={() => action("compact")}
                  disabled={idleDisabled}
                >
                  <Layers size={13} />
                  压缩上下文
                  <ArrowUpRight size={12} />
                </button>
                <div className="panel-section-heading">
                  <Code2 size={14} />
                  已启用能力
                  <span>
                    {(boot?.tools.length || 0) + (boot?.mcp_tools.length || 0)}
                  </span>
                </div>
                <div className="capability-tags">
                  {boot?.tools.map((tool) => (
                    <span key={tool}>
                      <Check size={11} />
                      {toolNames[tool]}
                    </span>
                  ))}
                </div>
                {!!boot?.mcp_tools.length && (
                  <div className="extension-note">
                    MCP · {boot.mcp_tools.length} 个外部工具
                  </div>
                )}
                <div className="extension-note">
                  Hooks · {boot?.hooks.pre || 0} 个前置 /{" "}
                  {boot?.hooks.post || 0} 个后置
                </div>
              </>
            ) : (
              <>
                <div className="changes-summary">
                  <FileDiff size={19} />
                  <div>
                    <strong>{session?.changes.length || 0} 个文件已修改</strong>
                    <small>本对话通过文件工具产生的变更</small>
                  </div>
                </div>
                {changes.length ? (
                  changes.map((change) => (
                    <details className="change-card" key={change.path}>
                      <summary>
                        <FileCode2 size={14} />
                        <code>{change.path}</code>
                        <span className="added">+{change.additions}</span>
                        <span className="deleted">−{change.deletions}</span>
                      </summary>
                      <pre className="diff-lines">
                        {change.diff?.split("\n").map((line, i) => (
                          <span
                            key={i}
                            className={
                              line.startsWith("+")
                                ? "diff-add"
                                : line.startsWith("-")
                                  ? "diff-delete"
                                  : line.startsWith("@@")
                                    ? "diff-hunk"
                                    : ""
                            }
                          >
                            {line || " "}
                            <br />
                          </span>
                        ))}
                      </pre>
                    </details>
                  ))
                ) : (
                  <div className="panel-empty changes-empty">
                    <FileDiff size={30} />
                    <p>还没有文件变更</p>
                    <small>
                      文件编辑后，可以在这里
                      <br />
                      查看每一处新增和删除。
                    </small>
                  </div>
                )}
                <button
                  className="button secondary undo-button"
                  onClick={() => action("undo")}
                  disabled={idleDisabled || !session?.undo_count}
                >
                  <RotateCcw size={14} />
                  撤销最近一次文件修改
                </button>
                <p className="panel-hint">
                  Shell 与 MCP 的副作用不在文件撤销范围内。
                </p>
              </>
            )}
          </div>
          <div className="inspector-footer">
            <ShieldCheck size={13} />
            <span>你始终掌控每一次修改</span>
          </div>
        </aside>
      )}

      {notice && (
        <div className={`toast ${notice.error ? "error" : ""}`} role="status">
          {notice.error ? <Info size={17} /> : <CheckCheck size={17} />}
          <span>{notice.message}</span>
          <IconButton label="关闭提示" onClick={() => setNotice(null)}>
            <X size={14} />
          </IconButton>
        </div>
      )}
      {dialog === "settings" && (
        <Dialog
          title="工作台设置"
          subtitle="调整工作台外观，管理模型与工具。"
          onClose={closeDialog}
          className="appearance-dialog"
        >
          <div
            className="settings-tabs"
            role="tablist"
            aria-label="设置分类"
            onKeyDown={(event) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
                return;
              event.preventDefault();
              const next = event.key === "Home"
                ? "appearance"
                : event.key === "End"
                  ? "model"
                  : settingsTab === "appearance" ? "model" : "appearance";
              setSettingsTab(next);
              event.currentTarget
                .querySelector<HTMLButtonElement>(`#settings-tab-${next}`)
                ?.focus();
            }}
          >
            <button
              type="button"
              role="tab"
              id="settings-tab-appearance"
              aria-selected={settingsTab === "appearance"}
              aria-controls="settings-panel-appearance"
              tabIndex={settingsTab === "appearance" ? 0 : -1}
              onClick={() => setSettingsTab("appearance")}
            >外观</button>
            <button
              type="button"
              role="tab"
              id="settings-tab-model"
              aria-selected={settingsTab === "model"}
              aria-controls="settings-panel-model"
              tabIndex={settingsTab === "model" ? 0 : -1}
              onClick={() => setSettingsTab("model")}
            >模型与工具</button>
          </div>
          {settingsTab === "appearance" ? (
            <div
              role="tabpanel"
              id="settings-panel-appearance"
              aria-labelledby="settings-tab-appearance"
            >
              <AppearanceSettings
                value={appearance}
                onChange={changeAppearance}
                storageAvailable={appearanceStorageAvailable}
              />
            </div>
          ) : (
            <div
              role="tabpanel"
              id="settings-panel-model"
              aria-labelledby="settings-tab-model"
            >
              <div className="settings-section">
                <div className="settings-label">
                  <FolderOpen size={16} />
                  工作区
                </div>
                <code className="setting-code">{boot?.workspace}</code>
              </div>
              <div className="settings-section">
                <div className="settings-label">
                  <Code2 size={16} />
                  当前会话模型
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action("model", { model: modelDraft });
                  }}
                  className="model-form"
                >
                  <input
                    aria-label="自定义模型名称"
                    value={modelDraft}
                    onChange={(e) => setModelDraft(e.target.value)}
                    placeholder="输入当前接口支持的模型名称"
                  />
                  <button
                    className="button primary"
                    disabled={idleDisabled || !modelDraft.trim()}
                  >
                    应用
                  </button>
                </form>
                <p className="field-hint">
                  这里只切换模型名称；接口地址和密钥从项目 .env 读取。
                </p>
              </div>
              <div className="settings-facts">
                <div>
                  <span>接口后端</span>
                  <strong>
                    {boot?.provider === "litellm" ? "LiteLLM" : "OpenAI 兼容接口"}
                  </strong>
                </div>
                <div>
                  <span>API 地址</span>
                  <code>{boot?.base_url || "默认接口"}</code>
                </div>
                <div>
                  <span>API Key</span>
                  <strong
                    className={
                      boot?.key_configured ? "success-text" : "warning-text"
                    }
                  >
                    {boot?.key_configured
                      ? "已配置 · 内容不展示"
                      : "未配置，请填写 .env 并重启服务"}
                  </strong>
                </div>
              </div>
              <div className="settings-section">
                <div className="settings-label">
                  <ShieldCheck size={16} />
                  工具授权
                </div>
                <p className="field-hint">
                  读取和搜索直接执行，文件修改、命令与子任务先询问。持续允许只对当前网页会话生效。
                </p>
                <div className="allowed-tools">
                  {session?.allowed_tools.length ? (
                    session.allowed_tools.map((name) => (
                      <span key={name}>{toolNames[name] || name}</span>
                    ))
                  ) : (
                    <span className="muted">尚未持续允许任何工具</span>
                  )}
                </div>
                <button
                  className="button secondary"
                  disabled={idleDisabled || !session?.allowed_tools.length}
                  onClick={() => action("revoke")}
                >
                  清除持续授权
                </button>
              </div>
              <div className="settings-footer">
                <Brand small />
                <span>CoreCoder Workbench</span>
                <small>v{boot?.version}</small>
              </div>
            </div>
          )}
        </Dialog>
      )}
      {dialog === "history" && (
        <Dialog
          title="恢复 CLI 会话"
          subtitle="读取 corecoder /save 保存的对话，在空白网页对话中继续。"
          onClose={closeDialog}
          wide
        >
          <div className="saved-list">
            {saved.length ? (
              saved.map((item) => (
                <button
                  key={item.id}
                  disabled={idleDisabled}
                  onClick={async () => {
                    if (session?.messages.length) {
                      flash("请先新建空白对话，再恢复 CLI 会话。", true);
                      return;
                    }
                    if (await action("import", { saved_id: item.id }))
                      setDialog(null);
                  }}
                >
                  <History size={17} />
                  <div>
                    <strong>{item.preview || item.id}</strong>
                    <span>
                      {item.model} · {item.saved_at}
                    </span>
                    <code>{item.id}</code>
                  </div>
                  <ArrowUpRight size={15} />
                </button>
              ))
            ) : (
              <div className="dialog-empty">
                <History size={30} />
                <p>还没有保存的 CLI 会话</p>
                <small>在命令行使用 /save，或点击网页右上角的保存按钮。</small>
              </div>
            )}
          </div>
        </Dialog>
      )}
      {dialog === "help" && (
        <Dialog
          title="开始使用 CoreCoder"
          subtitle="把已有的编程能力，放进一个更顺手的工作台。"
          onClose={closeDialog}
        >
          <div className="help-grid">
            {[
              [
                "聊天模式",
                "点击输入框下方的模式菜单选择。只基于对话回答，不读取项目文件或调用工具。",
              ],
              [
                "编程模式",
                "直接描述任务。Agent 会读取代码，并在修改或运行命令前请求许可。",
              ],
              [
                "计划模式",
                "切换输入框下方模式，先调查并输出计划。批准后再进入编程模式。",
              ],
              [
                "保存与恢复",
                "对话自动保存到本机。右上角保存按钮还可导出为 CLI 可恢复的会话。",
              ],
              [
                "停止任务",
                "停止后不再发起后续步骤；正在执行的 shell 命令会等待返回。",
              ],
            ].map(([title, text]) => (
              <div key={title}>
                <strong>{title}</strong>
                <p>{text}</p>
              </div>
            ))}
          </div>
          <div className="help-commands">
            <h3>快捷命令</h3>
            {[
              ["/plan", "切换计划模式"],
              ["/tokens", "查看用量"],
              ["/diff", "查看文件变更"],
              ["/compact", "压缩上下文"],
              ["/save", "保存 CLI 会话"],
              ["/sessions", "恢复 CLI 会话"],
              ["/undo", "撤销最近文件修改"],
              ["/reset", "清空上下文"],
              ["/model 名称", "切换模型"],
            ].map(([cmd, text]) => (
              <div key={cmd}>
                <code>{cmd}</code>
                <span>{text}</span>
              </div>
            ))}
          </div>
          <div className="keyboard-note">
            <Command size={14} />
            Ctrl / ⌘ K 搜索对话<span>·</span>Ctrl / ⌘ J 聚焦输入
          </div>
        </Dialog>
      )}
      {dialog === "reset" && (
        <Dialog
          title="清空当前对话上下文？"
          subtitle="消息和任务清单会被清空，已修改的文件会保留。"
          onClose={closeDialog}
        >
          <div className="dialog-actions">
            <button className="button secondary" onClick={closeDialog}>
              取消
            </button>
            <button
              className="button primary"
              disabled={idleDisabled}
              onClick={async () => {
                if (await action("reset")) setDialog(null);
              }}
            >
              清空上下文
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
