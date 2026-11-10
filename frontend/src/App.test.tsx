// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Bootstrap, Session, ToolEvent } from "./types";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  chat: vi.fn(),
  followRun: vi.fn((_id: string, _callback: (event: ToolEvent) => void, _signal: AbortSignal) => new Promise<void>(() => {})),
  setToken: vi.fn(),
}));

vi.mock("./api", () => mocks);

const session = (id: string, title: string): Session => ({
  id,
  title,
  updated_at: 1,
  model: "test-model",
  busy: false,
  message_count: 0,
  messages: [],
  timeline: [],
  tool_outcomes: [],
  cwd: "D:/workspace",
  plan_mode: false,
  tasks: [],
  pending_permission: null,
  allowed_tools: [],
  changes: [],
  undo_count: 0,
  usage: {
    prompt: 0,
    completion: 0,
    cost: null,
    context: 0,
    max_context: 128000,
    cost_summary: {
      known_cost: null,
      unknown_prompt_tokens: 0,
      unknown_completion_tokens: 0,
      unknown_requests: 0,
      missing_usage_requests: 0,
    },
  },
});

const sessions = { a: session("a", "对话甲"), b: session("b", "对话乙") };
const bootstrap: Bootstrap = {
  token: "token",
  version: "test",
  workspace: "D:/workspace",
  project: "项目",
  model: "test-model",
  provider: "openai",
  base_url: null,
  key_configured: true,
  tools: [],
  mcp_tools: [],
  hooks: { pre: 0, post: 0 },
  sessions: Object.values(sessions),
};

beforeEach(() => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    clear: () => values.clear(),
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() {
      return values.size;
    },
  };
  vi.stubGlobal("localStorage", storage);
  mocks.api.mockReset();
  mocks.chat.mockReset();
  mocks.setToken.mockReset();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 1280,
  });
  Element.prototype.scrollIntoView = vi.fn();
  mocks.api.mockImplementation(async (path: string, body?: { id: string; prompt: string }) => {
    if (path === "runs") return mocks.chat(body!.id, body!.prompt);
    if (path === "bootstrap") return bootstrap;
    if (path === "sessions")
      return Object.values(sessions).map(
        ({
          messages: _messages,
          timeline: _timeline,
          tool_outcomes: _outcomes,
          cwd: _cwd,
          plan_mode: _plan,
          tasks: _tasks,
          pending_permission: _permission,
          allowed_tools: _allowed,
          changes: _changes,
          undo_count: _undo,
          usage: _usage,
          ...summary
        }) => summary,
      );
    if (path.includes("id=b")) return sessions.b;
    return sessions.a;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("workbench composer behavior", () => {
  it("keeps per-session drafts while switching conversations", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.type(composer, "甲的草稿");
    await user.click(screen.getByRole("button", { name: /对话乙/ }));
    await waitFor(() =>
      expect((composer as HTMLTextAreaElement).value).toBe(""),
    );
    await user.type(composer, "乙的草稿");
    await user.click(screen.getByRole("button", { name: /对话甲/ }));
    await waitFor(() =>
      expect((composer as HTMLTextAreaElement).value).toBe("甲的草稿"),
    );
  });

  it("does not overwrite a new draft when an unaccepted send fails", async () => {
    let rejectChat!: (error: Error) => void;
    mocks.chat.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectChat = reject;
        }),
    );
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.type(composer, "将被拒绝");
    await user.click(screen.getByRole("button", { name: "发送任务" }));
    await user.clear(composer);
    await user.type(composer, "发送期间的新草稿");
    await act(async () => rejectChat(Object.assign(new Error("会话正在运行"), { name: "ApiError", status: 409 })));
    await waitFor(() =>
      expect((composer as HTMLTextAreaElement).value).toBe("发送期间的新草稿"),
    );
    expect(screen.getByText("会话正在运行")).toBeTruthy();
  });

  it("uses suggestion text and synchronously blocks duplicate submits", async () => {
    let finish!: () => void;
    mocks.chat.mockImplementation(() => new Promise((resolve) => {
      finish = () => resolve({ id: "run-1" });
    }));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByLabelText("输入任务");
    await user.click(await screen.findByRole("button", { name: /读懂这个项目/ }));
    const send = screen.getByRole("button", { name: "发送任务" });
    await user.click(send);
    fireEvent.submit(send.closest("form")!);
    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(mocks.chat.mock.calls[0][1]).toContain("梳理项目结构");
    await act(async () => finish());
  });

  it("does not submit Enter while IME composition is active", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.type(composer, "中文输入");
    fireEvent.keyDown(composer, {
      key: "Enter",
      shiftKey: false,
      isComposing: true,
    });
    expect(mocks.chat).not.toHaveBeenCalled();
  });

  it("shows a persisted failure after reloading an interrupted run", async () => {
    sessions.a.messages = [{ role: "user", content: "触发失败" }];
    sessions.a.message_count = 1;
    sessions.a.timeline = [
      { type: "error", message: "provider failed", at: 2 },
    ];
    render(<App />);
    expect(await screen.findByText("任务运行失败")).toBeTruthy();
    expect(screen.getByText("provider failed")).toBeTruthy();
    sessions.a.timeline = [];
    sessions.a.messages = [];
    sessions.a.message_count = 0;
  });

  it("keeps the composer disabled until a new conversation is loaded", async () => {
    let resolveCreate!: (value: Session) => void;
    mocks.api.mockImplementation(async (path: string, body?: unknown) => {
      if (path === "bootstrap") return bootstrap;
      if (path === "sessions" && body !== undefined)
        return new Promise<Session>((resolve) => {
          resolveCreate = resolve;
        });
      if (path === "sessions")
        return Object.values(sessions).map(
          ({
            messages: _messages,
            timeline: _timeline,
            tool_outcomes: _outcomes,
            cwd: _cwd,
            plan_mode: _plan,
            tasks: _tasks,
            pending_permission: _permission,
            allowed_tools: _allowed,
            changes: _changes,
            undo_count: _undo,
            usage: _usage,
            ...summary
          }) => summary,
        );
      return sessions.a;
    });
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.click(screen.getByRole("button", { name: /新建对话/ }));
    expect((composer as HTMLTextAreaElement).disabled).toBe(true);
    await user.type(composer, "不应写入旧会话");
    expect((composer as HTMLTextAreaElement).value).toBe("");
    await act(async () => resolveCreate(sessions.b));
    await waitFor(() =>
      expect((composer as HTMLTextAreaElement).disabled).toBe(false),
    );
  });

  it("shows and disables the stop control after a stop request", async () => {
    sessions.a.busy = true;
    render(<App />);
    const stop = await screen.findByRole("button", { name: "停止后续步骤" });
    await userEvent.setup().click(stop);
    const stopping = await screen.findByRole("button", { name: "正在停止" });
    expect((stopping as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("正在停止，等待当前步骤安全结束")).toBeTruthy();
    sessions.a.busy = false;
  });
});

describe("run recovery", () => {
  it("does not replay completed runs over current session", async () => {
    const current = { ...session("a", "已重命名"), run_id: "old-run", messages: [] };
    mocks.followRun.mockClear();
    mocks.api.mockImplementation(async (path: string) => path === "bootstrap" ? { ...bootstrap, sessions: [current] } : path === "sessions" ? [current] : current);
    render(<App />);
    expect(await screen.findByRole("button", { name: /已重命名/ })).toBeTruthy();
    expect(mocks.followRun).not.toHaveBeenCalled();
  });
  it("terminal reset displays only the current final answer", async () => {
    const running = { ...session("a", "活动"), busy: true, run_id: "run-1" };
    const finished = { ...running, busy: false, messages: [{ role: "assistant" as const, content: "唯一答案" }] };
    let reads = 0;
    mocks.api.mockImplementation(async (path: string) => path === "bootstrap" ? { ...bootstrap, sessions: [running] } : path === "sessions" ? [running] : reads++ === 0 ? running : finished);
    mocks.followRun.mockImplementation((_id, callback) => { callback({ type: "reset", status: "completed", seq: 10, text: "唯一答案", session: finished }); return Promise.resolve(); });
    render(<App />);
    await waitFor(() => expect(screen.getAllByText("唯一答案")).toHaveLength(1));
  });
  it("does not restore an already resolved permission", async () => {
    const running = { ...session("a", "活动"), busy: true, run_id: "run-2", pending_permission: null };
    mocks.api.mockImplementation(async (path: string) => path === "bootstrap" ? { ...bootstrap, sessions: [running] } : path === "sessions" ? [running] : running);
    mocks.followRun.mockClear();
    mocks.followRun.mockImplementation((_id, callback) => { callback({ type: "permission", id: "old", name: "bash", arguments: { command: "old command" } }); return Promise.resolve(); });
    render(<App />);
    await waitFor(() => expect(mocks.followRun).toHaveBeenCalled());
    expect(screen.queryByText("old command")).toBeNull();
  });
});

describe("run budget controls", () => {
  it("omits empty budget and sends zero explicitly", async () => {
    const user = userEvent.setup();
    mocks.chat.mockResolvedValue({ id: "r", session_id: "a", status: "running", last_seq: 0 });
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.type(composer, "hello");
    await user.click(screen.getByRole("button", { name: "发送任务" }));
    await waitFor(() => expect(mocks.api.mock.calls.find(([p]) => p === "runs")?.[1]).toEqual({ id: "a", prompt: "hello" }));
  });

  it("sends a zero request limit", async () => {
    const user = userEvent.setup();
    mocks.chat.mockResolvedValue({ id: "r", session_id: "a", status: "running", last_seq: 0 });
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.click(screen.getByText("本次运行预算（可选）"));
    await user.type(screen.getByLabelText("请求上限"), "0");
    await user.type(composer, "hello");
    await user.click(screen.getByRole("button", { name: "发送任务" }));
    await waitFor(() => expect(mocks.api.mock.calls.find(([p]) => p === "runs")?.[1]).toMatchObject({ budget: { requests: 0 } }));
  });

  it("rejects an invalid cost without sending", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText("输入任务");
    await user.click(screen.getByText("本次运行预算（可选）"));
    await user.type(screen.getByLabelText("费用上限"), "NaN");
    await user.type(composer, "hello");
    await user.click(screen.getByRole("button", { name: "发送任务" }));
    expect(mocks.api.mock.calls.some(([p]) => p === "runs")).toBe(false);
    expect(await screen.findByText(/预算必须是非负整数/)).toBeTruthy();
  });
});

describe("budget report", () => {
  it("shows unknown cost and distinguishes old runs", async () => {
    const previous = mocks.api.getMockImplementation()!;
    sessions.a.run_id = "budget-run";
    mocks.api.mockImplementation((path: string, body?: { id: string; prompt: string }) => {
      if (path.startsWith("run?id=")) return Promise.resolve({ budget: {
        groups: { actual: { requests: 1, tokens: 5, known_cost: "0.003", unknown_cost_requests: 0 },
          in_flight: { requests: 0, tokens: 0, known_cost: "0", unknown_cost_requests: 0 },
          unconfirmed: { requests: 1, tokens: 8, known_cost: "0", unknown_cost_requests: 1 } },
        limits: { requests: 5, total_tokens: 100, cost: "1" },
        report: { requests_remaining: 3, tokens_remaining: 87, cost_remaining: null, cost_overage_known: "0", tokens_overage: 0, unknown_cost_requests: 1 },
      } });
      return previous(path, body);
    });
    render(<App />);
    expect(await screen.findByText(/剩余费用：未知/)).toBeTruthy();
    expect(screen.getByText(/未知费用笔数：1/)).toBeTruthy();
    delete sessions.a.run_id;
  });

  it("shows unavailable for an old run", async () => {
    render(<App />);
    expect(await screen.findByText("预算数据不可用")).toBeTruthy();
  });
});

it("ignores a delayed budget report after switching sessions", async () => {
  let resolveOld!: (value: unknown) => void;
  const previous = mocks.api.getMockImplementation()!;
  sessions.a.run_id = "old";
  mocks.api.mockImplementation((path: string, body?: { id: string; prompt: string }) => {
    if (path === "run?id=old") return new Promise((resolve) => { resolveOld = resolve; });
    return previous(path, body);
  });
  const user = userEvent.setup();
  render(<App />);
  await screen.findByLabelText("输入任务");
  await waitFor(() => expect(resolveOld).toBeDefined());
  await user.click(screen.getByRole("button", { name: /对话乙/ }));
  await act(async () => resolveOld({ budget: { groups: { actual: { requests: 99, tokens: 0, known_cost: "0", unknown_cost_requests: 0 }, in_flight: { requests: 0, tokens: 0, known_cost: "0", unknown_cost_requests: 0 }, unconfirmed: { requests: 0, tokens: 0, known_cost: "0", unknown_cost_requests: 0 } }, limits: { requests: null, total_tokens: null, cost: null }, report: { requests_remaining: null, tokens_remaining: null, cost_remaining: null, cost_overage_known: null, tokens_overage: 0, unknown_cost_requests: 0 } } }));
  expect(screen.getByText("预算数据不可用")).toBeTruthy();
  expect(screen.queryByText("99")).toBeNull();
  delete sessions.a.run_id;
});

it("freezes the submitted budget while a run is pending", async () => {
  let finish!: (value: unknown) => void;
  mocks.chat.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const user = userEvent.setup();
  render(<App />);
  const composer = await screen.findByLabelText("输入任务");
  await user.click(screen.getByText("本次运行预算（可选）"));
  const requests = screen.getByLabelText("请求上限");
  await user.type(requests, "2");
  await user.type(composer, "hello");
  await user.click(screen.getByRole("button", { name: "发送任务" }));
  await user.clear(requests); await user.type(requests, "5");
  expect(mocks.api.mock.calls.find(([p]) => p === "runs")?.[1]).toMatchObject({ budget: { requests: 2 } });
  await act(async () => finish({ id: "r", session_id: "a", status: "running", last_seq: 0 }));
  expect((requests as HTMLInputElement).value).toBe("5");
});

it("waits for a slow poll and stops after terminal response", async () => {
  let resolve!: (value: unknown) => void;
  const previous = mocks.api.getMockImplementation()!;
  sessions.a.run_id = "poll";
  const group = { requests: 1, tokens: 1, known_cost: "0", unknown_cost_requests: 0 };
  mocks.api.mockImplementation((path: string, body?: { id: string; prompt: string }) => {
    if (path === "run?id=poll") return new Promise((done) => { resolve = done; });
    return previous(path, body);
  });
  render(<App />);
  await waitFor(() => expect(resolve).toBeDefined());
  await new Promise((done) => setTimeout(done, 1700));
  expect(mocks.api.mock.calls.filter(([path]) => path === "run?id=poll")).toHaveLength(1);
  await act(async () => resolve({ status: "completed", budget: { groups: { actual: group, in_flight: group, unconfirmed: group }, limits: { requests: 10, total_tokens: null, cost: null }, report: { requests_remaining: 4, tokens_remaining: null, cost_remaining: null, cost_overage_known: null, tokens_overage: 0, unknown_cost_requests: 0 } } }));
  expect(await screen.findByText(/剩余请求：4/)).toBeTruthy();
  await new Promise((done) => setTimeout(done, 1700));
  expect(mocks.api.mock.calls.filter(([path]) => path === "run?id=poll")).toHaveLength(1);
  delete sessions.a.run_id;
});

it("does not switch plan mode for invalid approval budget", async () => {
  sessions.a.plan_mode = true;
  const user = userEvent.setup();
  render(<App />);
  const composer = await screen.findByLabelText("输入任务");
  await user.click(screen.getByText("本次运行预算（可选）"));
  await user.type(screen.getByLabelText("费用上限"), "NaN");
  await user.type(composer, "/approve");
  await user.click(screen.getByRole("button", { name: "发送任务" }));
  expect(mocks.api.mock.calls.some(([path]) => path === "action" || path === "runs")).toBe(false);
  expect((composer as HTMLTextAreaElement).value).toBe("/approve");
  sessions.a.plan_mode = false;
});
