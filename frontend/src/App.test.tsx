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
import type { Bootstrap, Session } from "./types";

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  chat: vi.fn(),
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
  mocks.api.mockImplementation(async (path: string) => {
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
    await act(async () => rejectChat(new Error("会话正在运行")));
    await waitFor(() =>
      expect((composer as HTMLTextAreaElement).value).toBe("发送期间的新草稿"),
    );
    expect(screen.getByText("会话正在运行")).toBeTruthy();
  });

  it("uses suggestion text and synchronously blocks duplicate submits", async () => {
    let finish!: () => void;
    mocks.chat.mockImplementation((_id, _prompt, _onEvent, accepted) => {
      accepted?.();
      return new Promise<void>((resolve) => {
        finish = resolve;
      });
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findByLabelText("输入任务");
    await user.click(screen.getByRole("button", { name: /读懂这个项目/ }));
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
