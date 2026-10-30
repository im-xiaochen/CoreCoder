export type Appearance = {
  version: 1;
  preset: "classic" | "tech" | "chinese" | "fresh";
  fontFamily: "system" | "serif" | "mono";
  /** Percentage of the workbench's default type scale. */
  fontScale: number;
  colors: { accent: string | null; background: string | null; text: string | null };
};

type Colors = { accent: string; background: string; text: string };
type Preset = {
  id: Appearance["preset"];
  name: string;
  description: string;
  colors: Colors;
  fontFamily: Appearance["fontFamily"];
  panel: string;
};

export const STORAGE_KEY = "corecoder-appearance-v1";
export const DEFAULT_APPEARANCE: Appearance = {
  version: 1,
  preset: "classic",
  fontFamily: "system",
  fontScale: 100,
  colors: { accent: null, background: null, text: null },
};

export const APPEARANCE_PRESETS: readonly Preset[] = [
  {
    id: "classic", name: "经典", description: "简洁中性，专注任务",
    colors: { background: "#ffffff", accent: "#4169c1", text: "#242428" },
    panel: "#f7f7f8", fontFamily: "system",
  },
  {
    id: "tech", name: "科技风", description: "深海蓝与青色，沉浸编程",
    colors: { background: "#101c2c", accent: "#58c9e8", text: "#e1ecf5" },
    panel: "#17283c", fontFamily: "system",
  },
  {
    id: "chinese", name: "中国风", description: "宣纸、墨色与朱砂",
    colors: { background: "#f6f2e8", accent: "#a33332", text: "#302f2b" },
    panel: "#eee7d9", fontFamily: "serif",
  },
  {
    id: "fresh", name: "小清新", description: "雾绿底色，清爽舒适",
    colors: { background: "#f2f8f4", accent: "#246b55", text: "#243c32" },
    panel: "#e6f0e9", fontFamily: "system",
  },
];

export const FONT_OPTIONS: readonly { id: Appearance["fontFamily"]; label: string }[] = [
  { id: "system", label: "系统黑体" },
  { id: "serif", label: "宋体 / 衬线" },
  { id: "mono", label: "等宽字体" },
];

const FONTS: Record<Appearance["fontFamily"], string> = {
  system: '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
  serif: '"Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif',
  mono: '"Cascadia Code", "SFMono-Regular", Consolas, "Microsoft YaHei", monospace',
};

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function color(value: unknown): string | null {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value)
    ? value.toLowerCase() : null;
}

export function normalizeAppearance(value: unknown): Appearance {
  const input = record(value);
  if (input.version !== 1) {
    return { ...DEFAULT_APPEARANCE, colors: { ...DEFAULT_APPEARANCE.colors } };
  }
  const preset = APPEARANCE_PRESETS.find((item) => item.id === input.preset)
    ?? APPEARANCE_PRESETS[0];
  const colors = record(input.colors);
  return {
    version: 1,
    preset: preset.id,
    fontFamily: FONT_OPTIONS.some((item) => item.id === input.fontFamily)
      ? input.fontFamily as Appearance["fontFamily"] : preset.fontFamily,
    fontScale: typeof input.fontScale === "number" && Number.isFinite(input.fontScale)
      && input.fontScale >= 85 && input.fontScale <= 125
      ? Math.round(input.fontScale / 5) * 5 : 100,
    colors: { accent: color(colors.accent), background: color(colors.background), text: color(colors.text) },
  };
}

export function readAppearance(): Appearance {
  try {
    return normalizeAppearance(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null"));
  } catch {
    return normalizeAppearance(null);
  }
}

export function saveAppearance(value: Appearance): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(normalizeAppearance(value)));
    return true;
  } catch {
    return false;
  }
}

export function getAppearanceColors(value: Appearance): Colors {
  const appearance = normalizeAppearance(value);
  const preset = APPEARANCE_PRESETS.find((item) => item.id === appearance.preset)!;
  return {
    background: appearance.colors.background ?? preset.colors.background,
    accent: appearance.colors.accent ?? preset.colors.accent,
    text: appearance.colors.text ?? preset.colors.text,
  };
}

function rgb(hex: string): number[] {
  return [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
}

function mix(base: string, tint: string, fraction: number): string {
  const target = rgb(tint);
  return `#${rgb(base).map((channel, index) => Math.round(channel * (1 - fraction)
    + target[index] * fraction).toString(16).padStart(2, "0")).join("")}`;
}

function luminance(hex: string): number {
  const channels = rgb(hex).map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

export function contrastRatio(foreground: string, background: string): number {
  const first = luminance(foreground), second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

/** Move semantic colors toward a readable foreground, preserving the chosen accent. */
function readable(candidate: string, background: string, minimum = 4.5): string {
  const target = contrastRatio("#ffffff", background) > contrastRatio("#000000", background)
    ? "#ffffff" : "#000000";
  for (let step = 0; step <= 20; step++) {
    const adjusted = mix(candidate, target, step / 20);
    if (contrastRatio(adjusted, background) >= minimum) return adjusted;
  }
  return target;
}

export function applyAppearance(value: Appearance): void {
  if (typeof document === "undefined") return;
  const appearance = normalizeAppearance(value);
  const preset = APPEARANCE_PRESETS.find((item) => item.id === appearance.preset)!;
  const { background, accent, text } = getAppearanceColors(appearance);
  const dark = luminance(background) < 0.35;
  const onAccent = contrastRatio("#ffffff", accent) >= contrastRatio("#000000", accent)
    ? "#ffffff" : "#000000";
  const panel = appearance.colors.background || appearance.colors.text
    ? mix(background, text, dark ? 0.065 : 0.035) : preset.panel;
  const tokens: Record<string, string> = {
    "--background": background,
    "--surface": background,
    "--surface-subtle": mix(background, text, 0.025),
    "--surface-raised": dark ? mix(background, "#ffffff", 0.035) : background,
    "--sidebar": panel,
    "--text": text,
    "--muted": readable(readable(mix(text, background, 0.36), background), panel),
    "--line": mix(background, text, dark ? 0.2 : 0.14),
    "--line-strong": mix(background, text, 0.27),
    "--hover": mix(background, text, 0.075),
    "--selected": mix(background, accent, dark ? 0.2 : 0.12),
    "--accent": accent,
    "--accent-soft": mix(background, accent, dark ? 0.16 : 0.08),
    "--accent-line": mix(background, accent, 0.35),
    "--accent-hover": mix(accent, onAccent === "#ffffff" ? "#000000" : "#ffffff", 0.12),
    "--on-accent": onAccent,
    "--link": readable(accent, background),
    "--focus": readable(accent, background, 3),
    "--code-background": mix(background, text, 0.04),
    "--selection": mix(background, accent, 0.25),
    "--shadow": dark ? "#00000055" : "#18181b12",
    "--overlay": dark ? "#00000099" : "#18233066",
  };
  for (const [name, candidate] of Object.entries({ success: "#267854", warning: "#9c6919", danger: "#b23f3f", info: "#4169c1" })) {
    // Keep semantic labels readable on both the canvas and their tinted banners.
    const semantic = readable(candidate, background, 5.5);
    tokens[`--${name}`] = semantic;
    tokens[`--${name}-soft`] = mix(background, semantic, dark ? 0.12 : 0.055);
    tokens[`--${name}-line`] = mix(background, semantic, 0.3);
  }
  const root = document.documentElement;
  root.dataset.theme = appearance.preset;
  root.style.fontSize = `${15 * appearance.fontScale / 100}px`;
  root.style.fontFamily = FONTS[appearance.fontFamily];
  root.style.colorScheme = dark ? "dark" : "light";
  for (const [name, token] of Object.entries(tokens)) root.style.setProperty(name, token);
}
