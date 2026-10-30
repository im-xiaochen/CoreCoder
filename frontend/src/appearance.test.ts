// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  APPEARANCE_PRESETS,
  DEFAULT_APPEARANCE,
  STORAGE_KEY,
  applyAppearance,
  contrastRatio,
  getAppearanceColors,
  normalizeAppearance,
  readAppearance,
  saveAppearance,
  type Appearance,
} from "./appearance";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.theme;
});

describe("appearance preference validation", () => {
  it.each([null, undefined, true, [], "tech", {}, { version: 0 }, { version: 2, preset: "tech" }])(
    "falls back safely for unsupported preferences %j",
    (value) => expect(normalizeAppearance(value)).toEqual(DEFAULT_APPEARANCE),
  );

  it("validates each field without losing valid preferences", () => {
    expect(normalizeAppearance({
      version: 1,
      preset: "chinese",
      fontFamily: "url(https://untrusted.example/font)",
      fontScale: 115,
      colors: { accent: "#Aa33CC", background: "red", text: "#112233; color:red" },
    })).toEqual({
      version: 1,
      preset: "chinese",
      fontFamily: "serif",
      fontScale: 115,
      colors: { accent: "#aa33cc", background: null, text: null },
    });
  });

  it.each([0, 84, 126, Infinity, NaN, "115", null])(
    "rejects an invalid font scale %j",
    (fontScale) => expect(normalizeAppearance({ ...DEFAULT_APPEARANCE, fontScale }).fontScale).toBe(100),
  );

  it("accepts both size limits and rounds in-range values to an available step", () => {
    expect(normalizeAppearance({ ...DEFAULT_APPEARANCE, fontScale: 85 }).fontScale).toBe(85);
    expect(normalizeAppearance({ ...DEFAULT_APPEARANCE, fontScale: 125 }).fontScale).toBe(125);
    expect(normalizeAppearance({ ...DEFAULT_APPEARANCE, fontScale: 112 }).fontScale).toBe(110);
  });

  it("rejects invalid preset and color structures", () => {
    const value = normalizeAppearance({ version: 1, preset: "unknown", colors: ["#ffffff"] });
    expect(value.preset).toBe("classic");
    expect(value.colors).toEqual({ accent: null, background: null, text: null });
  });

  it.each(["{invalid json", "null", "[]", JSON.stringify({ version: 0, preset: "tech" })])(
    "recovers from malformed or obsolete stored data %s",
    (stored) => {
      localStorage.setItem(STORAGE_KEY, stored);
      expect(readAppearance()).toEqual(DEFAULT_APPEARANCE);
    },
  );

  it("round-trips independent custom preferences through browser storage", () => {
    const value: Appearance = {
      ...DEFAULT_APPEARANCE,
      preset: "fresh",
      fontFamily: "mono",
      fontScale: 120,
      colors: { accent: "#123456", background: "#223344", text: "#eefeff" },
    };
    expect(saveAppearance(value)).toBe(true);
    expect(readAppearance()).toEqual(value);
    expect(localStorage.setItem).toHaveBeenCalledWith(STORAGE_KEY, expect.any(String));
  });

  it("keeps rendering with defaults when browser storage cannot be read", () => {
    vi.stubGlobal("localStorage", {
      getItem() { throw new DOMException("Storage blocked", "SecurityError"); },
      setItem() { throw new DOMException("Storage blocked", "SecurityError"); },
    });
    expect(readAppearance()).toEqual(DEFAULT_APPEARANCE);
    expect(saveAppearance(DEFAULT_APPEARANCE)).toBe(false);
  });

  it("reports a failed save when browser storage is full", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem() { throw new DOMException("Storage full", "QuotaExceededError"); },
    });
    expect(saveAppearance(DEFAULT_APPEARANCE)).toBe(false);
    expect(() => applyAppearance(DEFAULT_APPEARANCE)).not.toThrow();
  });
});

describe("global appearance application", () => {
  it.each(APPEARANCE_PRESETS)("applies the $name palette to shared CSS tokens", (preset) => {
    const value: Appearance = {
      ...DEFAULT_APPEARANCE,
      preset: preset.id,
      fontFamily: preset.fontFamily,
    };
    applyAppearance(value);
    const root = document.documentElement;
    expect(root.dataset.theme).toBe(preset.id);
    expect(root.style.getPropertyValue("--background")).toBe(preset.colors.background);
    expect(root.style.getPropertyValue("--text")).toBe(preset.colors.text);
    expect(root.style.getPropertyValue("--accent")).toBe(preset.colors.accent);
    expect(root.style.getPropertyValue("--sidebar")).not.toBe("");
    expect(root.style.getPropertyValue("--line")).not.toBe("");
    expect(root.style.colorScheme).toBe(preset.id === "tech" ? "dark" : "light");
    expect(contrastRatio(preset.colors.text, preset.colors.background)).toBeGreaterThanOrEqual(4.5);
  });

  it("derives controls and surfaces from custom colors, including custom dark backgrounds", () => {
    const value: Appearance = {
      ...DEFAULT_APPEARANCE,
      colors: { accent: "#ffff00", background: "#102030", text: "#f1f2f3" },
    };
    applyAppearance(value);
    const root = document.documentElement;
    expect(root.style.colorScheme).toBe("dark");
    expect(getAppearanceColors(value)).toEqual(value.colors);
    expect(root.style.getPropertyValue("--text")).toBe("#f1f2f3");
    expect(root.style.getPropertyValue("--surface")).not.toBe("#ffffff");
    expect(contrastRatio(root.style.getPropertyValue("--on-accent"), "#ffff00")).toBeGreaterThanOrEqual(4.5);
  });

  it("sets a global type scale and switches typography without persisting incidental page state", () => {
    applyAppearance({ ...DEFAULT_APPEARANCE, fontFamily: "serif", fontScale: 125 });
    expect(document.documentElement.style.fontSize).toBe("18.75px");
    expect(document.documentElement.style.fontFamily).toMatch(/serif/i);
    applyAppearance({ ...DEFAULT_APPEARANCE, fontFamily: "mono", fontScale: 85 });
    expect(document.documentElement.style.fontSize).toBe("12.75px");
    expect(document.documentElement.style.fontFamily).toMatch(/mono/i);
    applyAppearance(DEFAULT_APPEARANCE);
    expect(document.documentElement.style.fontSize).toBe("15px");
    expect(document.documentElement.dataset.theme).toBe("classic");
    expect(localStorage.setItem).not.toHaveBeenCalled();
  });
});
