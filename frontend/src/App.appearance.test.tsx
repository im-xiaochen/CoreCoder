// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { APPEARANCE_PRESETS, DEFAULT_APPEARANCE, STORAGE_KEY, type Appearance } from "./appearance";
import type { Bootstrap, Session } from "./types";

const mocks = vi.hoisted(() => ({ api: vi.fn(), chat: vi.fn(), setToken: vi.fn() }));
vi.mock("./api", () => mocks);

const session: Session = {
  id: "appearance-session", title: "外观测试对话", updated_at: 1, model: "test-model",
  busy: false, message_count: 0, messages: [], timeline: [], tool_outcomes: [],
  cwd: "D:/workspace", plan_mode: false, tasks: [], pending_permission: null,
  allowed_tools: [], changes: [], undo_count: 0,
  usage: {
    prompt: 0, completion: 0, cost: null, context: 0, max_context: 128000,
    cost_summary: {
      known_cost: null, unknown_prompt_tokens: 0, unknown_completion_tokens: 0,
      unknown_requests: 0, missing_usage_requests: 0,
    },
  },
};
const bootstrap: Bootstrap = {
  token: "token", version: "test", workspace: "D:/workspace", project: "外观测试项目",
  model: "test-model", provider: "openai", base_url: null, key_configured: true,
  tools: [], mcp_tools: [], hooks: { pre: 0, post: 0 }, sessions: [session],
};

let values: Map<string, string>;

beforeEach(() => {
  values = new Map();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
    removeItem: vi.fn((key: string) => { values.delete(key); }),
  });
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
  Element.prototype.scrollIntoView = vi.fn();
  mocks.api.mockReset();
  mocks.chat.mockReset();
  mocks.setToken.mockReset();
  mocks.api.mockImplementation(async (path: string) => {
    if (path === "bootstrap") return bootstrap;
    if (path === "sessions") return [session];
    return session;
  });
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.theme;
});

async function openSettings(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByLabelText("输入任务");
  await user.click(screen.getByRole("button", { name: "设置" }));
  return screen.getByRole("dialog", { name: "工作台设置" });
}

function stored(): Appearance {
  return JSON.parse(values.get(STORAGE_KEY)!);
}

describe("workbench appearance settings", () => {
  it("moves between setting tabs with arrow keys, Home and End while preserving keyboard focus", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    const appearanceTab = within(dialog).getByRole("tab", { name: "外观" });
    const modelTab = within(dialog).getByRole("tab", { name: "模型与工具" });
    appearanceTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(modelTab);
    expect(modelTab.getAttribute("aria-selected")).toBe("true");
    expect(appearanceTab.tabIndex).toBe(-1);
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(appearanceTab);
    expect(appearanceTab.getAttribute("aria-selected")).toBe("true");
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(modelTab);
    await user.keyboard("{Home}");
    expect(document.activeElement).toBe(appearanceTab);
  });

  it("applies every preset immediately, persists changes and keeps model settings available", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    expect(within(dialog).getByRole("tab", { name: "外观" }).getAttribute("aria-selected")).toBe("true");
    for (const preset of APPEARANCE_PRESETS) {
      const button = within(dialog).getByRole("button", { name: preset.name });
      await user.click(button);
      expect(button.getAttribute("aria-pressed")).toBe("true");
      expect(document.documentElement.dataset.theme).toBe(preset.id);
      expect(stored().preset).toBe(preset.id);
      expect(stored().fontFamily).toBe(preset.fontFamily);
    }
    await user.click(within(dialog).getByRole("tab", { name: "模型与工具" }));
    expect(within(dialog).getByLabelText("自定义模型名称")).toBeDefined();
    expect(within(dialog).getByText("工具授权", { exact: true })).toBeDefined();
    await user.click(within(dialog).getByRole("tab", { name: "外观" }));
    expect(within(dialog).getByRole("button", { name: "小清新" }).getAttribute("aria-pressed")).toBe("true");
    expect(mocks.api.mock.calls.some(([path]) => path === "action")).toBe(false);
  });

  it("keeps partial hex input local, applies a valid color and restores palette without changing type choices", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    await user.click(within(dialog).getByRole("button", { name: "中国风" }));
    fireEvent.change(within(dialog).getByLabelText("字号", { exact: true }), { target: { value: "120" } });
    await user.selectOptions(within(dialog).getByLabelText("界面字体"), "mono");
    expect(document.documentElement.style.fontSize).toBe("18px");
    const colorInput = within(dialog).getByLabelText("强调色", { exact: true });
    await user.clear(colorInput);
    await user.type(colorInput, "#12");
    expect(stored().colors.accent).toBeNull();
    expect((colorInput as HTMLInputElement).value).toBe("#12");
    await user.clear(colorInput);
    await user.type(colorInput, "#123456");
    expect(stored().colors.accent).toBe("#123456");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#123456");
    await user.click(within(dialog).getByRole("button", { name: "恢复主题配色" }));
    expect(stored()).toMatchObject({ preset: "chinese", fontFamily: "mono", fontScale: 120, colors: DEFAULT_APPEARANCE.colors });
    await user.click(within(dialog).getByRole("button", { name: "恢复全部默认" }));
    expect(stored()).toEqual(DEFAULT_APPEARANCE);
    expect(document.documentElement.style.fontSize).toBe("15px");
  });

  it("restores custom settings after the app is remounted and after the settings dialog is reopened", async () => {
    const user = userEvent.setup();
    const first = render(<App />);
    let dialog = await openSettings(user);
    await user.click(within(dialog).getByRole("button", { name: "科技风" }));
    fireEvent.change(within(dialog).getByLabelText("字号", { exact: true }), { target: { value: "125" } });
    fireEvent.change(within(dialog).getByLabelText("背景色选择器"), { target: { value: "#182838" } });
    const previous = stored();
    await user.click(within(dialog).getByRole("button", { name: "关闭弹窗" }));
    dialog = await openSettings(user);
    expect((within(dialog).getByLabelText("字号", { exact: true }) as HTMLInputElement).value).toBe("125");
    expect((within(dialog).getByLabelText("背景色", { exact: true }) as HTMLInputElement).value).toBe("#182838");
    first.unmount();
    document.documentElement.removeAttribute("style");
    render(<App />);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("tech"));
    expect(document.documentElement.style.fontSize).toBe("18.75px");
    expect(document.documentElement.style.getPropertyValue("--background")).toBe("#182838");
    dialog = await openSettings(user);
    expect((within(dialog).getByLabelText("字号", { exact: true }) as HTMLInputElement).value).toBe("125");
    expect(stored()).toEqual(previous);
  });

  it("clears incomplete or invalid color drafts when restoring an unchanged preset palette", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    const accent = within(dialog).getByLabelText("强调色", { exact: true });
    await user.clear(accent);
    await user.type(accent, "#12");
    await user.tab();
    expect(accent.getAttribute("aria-invalid")).toBe("true");
    expect(stored().colors.accent).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "恢复主题配色" }));
    expect((within(dialog).getByLabelText("强调色", { exact: true }) as HTMLInputElement).value).toBe("#4169c1");
    expect(within(dialog).queryByText(/未应用不完整的色值/)).toBeNull();
    const background = within(dialog).getByLabelText("背景色", { exact: true });
    await user.clear(background);
    await user.type(background, "invalid");
    await user.click(within(dialog).getByRole("button", { name: "恢复全部默认" }));
    expect((within(dialog).getByLabelText("背景色", { exact: true }) as HTMLInputElement).value).toBe("#ffffff");
    expect(stored()).toEqual(DEFAULT_APPEARANCE);
  });

  it("warns when custom text and background have insufficient contrast and can restore readability", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    fireEvent.change(within(dialog).getByLabelText("文字色选择器"), { target: { value: "#ffffff" } });
    expect(within(dialog).getByText(/文字与背景对比不足/)).toBeDefined();
    expect(document.documentElement.style.getPropertyValue("--text")).toBe("#ffffff");
    await user.click(within(dialog).getByRole("button", { name: "恢复主题配色" }));
    expect(within(dialog).queryByText(/文字与背景对比不足/)).toBeNull();
    expect(document.documentElement.style.getPropertyValue("--text")).toBe("#242428");
  });

  it("continues applying appearance when saving fails and tells the user changes are temporary", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
      if (key === STORAGE_KEY) throw new DOMException("Storage full", "QuotaExceededError");
      values.set(key, value);
    });
    await user.click(within(dialog).getByRole("button", { name: "科技风" }));
    expect(document.documentElement.dataset.theme).toBe("tech");
    expect(within(dialog).getByText(/无法|不可用|暂时|临时/)).toBeDefined();
  });

  it("updates the open settings UI for appearance changes made in another browser tab", async () => {
    const user = userEvent.setup();
    render(<App />);
    const dialog = await openSettings(user);
    const value: Appearance = { ...DEFAULT_APPEARANCE, preset: "fresh", fontScale: 110 };
    values.set(STORAGE_KEY, JSON.stringify(value));
    act(() => { window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY, newValue: JSON.stringify(value) })); });
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("fresh"));
    expect(within(dialog).getByRole("button", { name: "小清新" }).getAttribute("aria-pressed")).toBe("true");
    expect((within(dialog).getByLabelText("字号", { exact: true }) as HTMLInputElement).value).toBe("110");
  });
});
