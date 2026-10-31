// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Bootstrap, Session, SessionMode } from "./types";

const mocks = vi.hoisted(() => ({ api: vi.fn(), chat: vi.fn(), setToken: vi.fn() }));
vi.mock("./api", () => mocks);

function session(id: string, title: string): Session {
  return {
    id, title, updated_at: 1, model: "test-model", busy: false, message_count: 0,
    messages: [], timeline: [], tool_outcomes: [], cwd: "D:/workspace", plan_mode: false,
    tasks: [], pending_permission: null, allowed_tools: [], changes: [], undo_count: 0,
    usage: { prompt: 0, completion: 0, cost: null, context: 0, max_context: 128000,
      cost_summary: { known_cost: null, unknown_prompt_tokens: 0, unknown_completion_tokens: 0,
        unknown_requests: 0, missing_usage_requests: 0 } },
  };
}

let sessions: Record<string, Session>;
let rejectMode: Error | null;
let deferredMode: ((mode: SessionMode) => Promise<void>) | null;

beforeEach(() => {
  sessions = { a: session("a", "对话甲"), b: session("b", "对话乙") };
  rejectMode = null;
  deferredMode = null;
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
  mocks.api.mockReset();
  mocks.chat.mockReset();
  mocks.api.mockImplementation(async (path: string, body?: Record<string, unknown>) => {
    if (path === "bootstrap") return {
      token: "token", version: "test", workspace: "D:/workspace", project: "项目", model: "test-model",
      provider: "openai", base_url: null, key_configured: true, tools: [], mcp_tools: [],
      hooks: { pre: 0, post: 0 }, sessions: Object.values(sessions),
    } satisfies Bootstrap;
    if (path === "sessions") return Object.values(sessions);
    if (path === "action") {
      if (rejectMode) throw rejectMode;
      const next = body!.mode as SessionMode;
      if (deferredMode) await deferredMode(next);
      const id = body!.id as string;
      sessions[id] = { ...sessions[id], mode: next, plan_mode: next === "plan" };
      return { session: sessions[id], sessions: Object.values(sessions), notice: "" };
    }
    if (path.includes("id=b")) return sessions.b;
    return sessions.a;
  });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function modeActions() {
  return mocks.api.mock.calls.filter(([path]) => path === "action");
}

describe("workbench mode selection", () => {
  it("opens without a request, leaves the current mode unchanged, and selects each mode explicitly", async () => {
    const user = userEvent.setup();
    render(<App />);
    const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "编程模式" });
    await waitFor(() => expect(trigger.disabled).toBe(false));
    await user.click(trigger);
    expect(screen.getAllByRole("menuitemradio")).toHaveLength(3);
    expect(screen.getByRole("menuitemradio", { name: /编程模式/ }).getAttribute("aria-checked")).toBe("true");
    expect(modeActions()).toHaveLength(0);
    await user.click(screen.getByRole("menuitemradio", { name: /编程模式/ }));
    expect(modeActions()).toHaveLength(0);
    for (const [mode, label] of [["chat", "聊天模式"], ["plan", "计划模式"], ["code", "编程模式"]] as const) {
      await user.click(trigger);
      await user.click(screen.getByRole("menuitemradio", { name: new RegExp(label) }));
      await waitFor(() => expect(trigger.textContent).toBe(label));
      expect(modeActions().at(-1)?.[1]).toEqual({ id: "a", action: "mode", mode });
      expect(screen.queryByRole("menu")).toBeNull();
    }
    expect(modeActions()).toHaveLength(3);
  });

  it("keeps the old mode on failure and blocks duplicate selections during a pending request", async () => {
    let reject!: (error: Error) => void;
    deferredMode = () => new Promise((_resolve, rejectRequest) => { reject = rejectRequest; });
    const user = userEvent.setup();
    render(<App />);
    const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "编程模式" });
    await waitFor(() => expect(trigger.disabled).toBe(false));
    await user.click(trigger);
    await user.click(screen.getByRole("menuitemradio", { name: /聊天模式/ }));
    expect(trigger.disabled).toBe(true);
    expect(trigger.textContent).toBe("编程模式");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    expect(modeActions()).toHaveLength(1);
    await act(async () => reject(new Error("切换失败，请重试")));
    expect(await screen.findByText("切换失败，请重试")).toBeTruthy();
    expect(trigger.textContent).toBe("编程模式");
    expect(trigger.disabled).toBe(false);
    deferredMode = null;
    await user.click(trigger);
    await user.click(screen.getByRole("menuitemradio", { name: /聊天模式/ }));
    expect(await screen.findByRole("button", { name: "聊天模式" })).toBeTruthy();
  });

  it("uses persisted and legacy modes, closes on session navigation, and keeps model and mode menus exclusive", async () => {
    sessions.a.mode = "chat";
    sessions.b.plan_mode = true;
    const user = userEvent.setup();
    render(<App />);
    const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "聊天模式" });
    await waitFor(() => expect(trigger.disabled).toBe(false));
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "test-model" }));
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByText("此工作区使用的模型")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "关闭模型菜单" }));
    await user.click(trigger);
    expect(screen.queryByText("此工作区使用的模型")).toBeNull();
    await user.click(screen.getByRole("button", { name: /对话乙/ }));
    expect(await screen.findByRole("button", { name: "计划模式" })).toBeTruthy();
    expect(screen.queryByRole("menu")).toBeNull();
    expect(modeActions()).toHaveLength(0);
  });

  it("disables mode selection for a running session", async () => {
    sessions.a.busy = true;
    const user = userEvent.setup();
    render(<App />);
    const trigger = await screen.findByRole<HTMLButtonElement>("button", { name: "编程模式" });
    expect(trigger.disabled).toBe(true);
    await user.click(trigger);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(modeActions()).toHaveLength(0);
  });

  it("sends ordinary chat prompts without changing mode or suggesting project tools", async () => {
    sessions.a.mode = "chat";
    // The explicit mode is authoritative even if an old flag is inconsistent.
    sessions.a.plan_mode = true;
    mocks.chat.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText<HTMLTextAreaElement>("输入任务");
    await waitFor(() => expect(composer.disabled).toBe(false));
    expect(composer.getAttribute("placeholder")).toContain("聊天模式不会调用工具");
    expect(screen.queryByRole("button", { name: /读懂这个项目/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准计划" })).toBeNull();
    await user.type(composer, "approve");
    await user.click(screen.getByRole("button", { name: "发送任务" }));
    await waitFor(() => expect(mocks.chat).toHaveBeenCalledTimes(1));
    expect(mocks.chat.mock.calls[0].slice(0, 2)).toEqual(["a", "approve"]);
    expect(modeActions()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "聊天模式" })).toBeTruthy();
  });

  it("keeps /plan as an explicit shortcut between plan and code", async () => {
    sessions.a.mode = "chat";
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText<HTMLTextAreaElement>("输入任务");
    await waitFor(() => expect(composer.disabled).toBe(false));
    await user.type(composer, "/plan{Enter}");
    expect(await screen.findByRole("button", { name: "计划模式" })).toBeTruthy();
    expect(modeActions().at(-1)?.[1]).toEqual({ id: "a", action: "mode", mode: "plan" });
    await user.type(composer, "/plan{Enter}");
    expect(await screen.findByRole("button", { name: "编程模式" })).toBeTruthy();
    expect(modeActions().at(-1)?.[1]).toEqual({ id: "a", action: "mode", mode: "code" });
    expect(mocks.chat).not.toHaveBeenCalled();
  });

  it("switches to code before sending /approve and preserves that mode while running", async () => {
    sessions.a.mode = "plan";
    sessions.a.plan_mode = true;
    let finish!: () => void;
    mocks.chat.mockImplementation((_id, _prompt, _onEvent, accepted) => {
      accepted?.();
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText<HTMLTextAreaElement>("输入任务");
    await waitFor(() => expect(composer.disabled).toBe(false));
    await user.type(composer, "/approve{Enter}");
    await waitFor(() => expect(mocks.chat).toHaveBeenCalledTimes(1));
    expect(modeActions().at(-1)?.[1]).toEqual({ id: "a", action: "mode", mode: "code" });
    expect(mocks.chat.mock.calls[0].slice(0, 2)).toEqual(["a", "/approve"]);
    expect(screen.getByRole("button", { name: "编程模式" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "批准计划" })).toBeNull();
    await act(async () => finish());
  });

  it("approves the plan button explicitly and prepares the approval draft without auto-sending", async () => {
    sessions.a.mode = "plan";
    sessions.a.plan_mode = true;
    const user = userEvent.setup();
    render(<App />);
    const approve = await screen.findByRole<HTMLButtonElement>("button", { name: "批准计划" });
    await waitFor(() => expect(approve.disabled).toBe(false));
    await user.click(approve);
    expect(await screen.findByRole("button", { name: "编程模式" })).toBeTruthy();
    expect(modeActions().at(-1)?.[1]).toEqual({ id: "a", action: "mode", mode: "code" });
    expect(screen.getByLabelText<HTMLTextAreaElement>("输入任务").value).toBe("approve");
    expect(mocks.chat).not.toHaveBeenCalled();
  });
});
