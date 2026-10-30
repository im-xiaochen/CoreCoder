import { useEffect, useId, useState, type CSSProperties } from "react";
import { Check, CircleAlert, RotateCcw, Type } from "lucide-react";
import {
  APPEARANCE_PRESETS,
  DEFAULT_APPEARANCE,
  FONT_OPTIONS,
  contrastRatio,
  getAppearanceColors,
  type Appearance,
} from "./appearance";
import "./appearance-settings.css";

const HEX_COLOR = /^#[0-9a-f]{6}$/i;

function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setDraft(value);
    setInvalid(false);
  }, [value]);
  return (
    <div className="appearance-color-field">
      <label htmlFor={id}>{label}</label>
      <div className={`appearance-color-input ${invalid ? "invalid" : ""}`}>
        <input
          type="color"
          aria-label={`${label}选择器`}
          value={value}
          onChange={(event) => {
            setDraft(event.target.value);
            setInvalid(false);
            onChange(event.target.value);
          }}
        />
        <input
          id={id}
          type="text"
          value={draft}
          spellCheck={false}
          autoComplete="off"
          maxLength={7}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${id}-error` : undefined}
          onChange={(event) => {
            const next = event.target.value;
            setDraft(next);
            setInvalid(false);
            if (HEX_COLOR.test(next)) onChange(next.toLowerCase());
          }}
          onBlur={() => setInvalid(!HEX_COLOR.test(draft))}
        />
      </div>
      {invalid && (
        <small id={`${id}-error`} className="appearance-field-error" role="status">
          请输入 #RRGGBB 格式；未应用不完整的色值。
        </small>
      )}
    </div>
  );
}

export default function AppearanceSettings({
  value,
  onChange,
  storageAvailable,
}: {
  value: Appearance;
  onChange: (value: Appearance) => void;
  storageAvailable: boolean;
}) {
  const id = useId();
  const [colorReset, setColorReset] = useState(0);
  const colors = getAppearanceColors(value);
  const textContrast = contrastRatio(colors.text, colors.background);
  const accentContrast = contrastRatio(colors.accent, colors.background);
  const customized = Object.values(value.colors).some((color) => color !== null);
  const setColor = (key: keyof Appearance["colors"], color: string) =>
    onChange({ ...value, colors: { ...value.colors, [key]: color } });

  return (
    <div className="appearance-settings">
      <section className="appearance-section" aria-labelledby={`${id}-themes`}>
        <div className="appearance-section-heading">
          <div>
            <h3 id={`${id}-themes`}>选择主题</h3>
            <p>从一套配色开始，再调整成自己的样子。</p>
          </div>
          {customized && <span className="appearance-custom-label">已自定义配色</span>}
        </div>
        <div className="appearance-theme-grid">
          {APPEARANCE_PRESETS.map((preset) => (
            <button
              type="button"
              key={preset.id}
              className={`appearance-theme-card ${value.preset === preset.id ? "selected" : ""}`}
              aria-label={preset.name}
              aria-pressed={value.preset === preset.id}
              onClick={() => {
                setColorReset((count) => count + 1);
                onChange({
                  ...value,
                  preset: preset.id,
                  fontFamily: preset.fontFamily,
                  colors: { accent: null, background: null, text: null },
                });
              }}
            >
              <span
                className="appearance-theme-thumbnail"
                aria-hidden="true"
                style={
                  {
                    "--preview-bg": preset.colors.background,
                    "--preview-text": preset.colors.text,
                    "--preview-accent": preset.colors.accent,
                  } as CSSProperties
                }
              >
                <span className="appearance-thumbnail-sidebar">
                  <span className="appearance-thumbnail-logo" />
                  <span />
                  <span />
                  <span />
                </span>
                <span className="appearance-thumbnail-main">
                  <span className="appearance-thumbnail-title" />
                  <span className="appearance-thumbnail-line" />
                  <span className="appearance-thumbnail-composer">
                    <span /><span />
                  </span>
                </span>
              </span>
              <span className="appearance-theme-name">
                {preset.name}
                {value.preset === preset.id && <Check size={15} aria-hidden="true" />}
              </span>
              <span className="appearance-theme-description">{preset.description}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="appearance-section appearance-type-section" aria-labelledby={`${id}-type`}>
        <div className="appearance-section-heading">
          <h3 id={`${id}-type`}><Type size={16} aria-hidden="true" /> 字体与阅读</h3>
        </div>
        <div className="appearance-type-controls">
          <div className="appearance-font-field">
            <label htmlFor={`${id}-font`}>界面字体</label>
            <select
              id={`${id}-font`}
              value={value.fontFamily}
              onChange={(event) => onChange({ ...value, fontFamily: event.target.value as Appearance["fontFamily"] })}
            >
              {FONT_OPTIONS.map((font) => <option key={font.id} value={font.id}>{font.label}</option>)}
            </select>
          </div>
          <div className="appearance-scale-field">
            <div className="appearance-scale-label">
              <label htmlFor={`${id}-scale`}>字号</label>
              <output htmlFor={`${id}-scale`}>{value.fontScale}%</output>
            </div>
            <input
              id={`${id}-scale`}
              type="range"
              min={85}
              max={125}
              step={5}
              value={value.fontScale}
              aria-valuetext={`${value.fontScale}%`}
              onChange={(event) => onChange({ ...value, fontScale: Number(event.target.value) })}
            />
            <div className="appearance-scale-marks" aria-hidden="true"><span>85%</span><span>100%</span><span>125%</span></div>
          </div>
        </div>
        <p className="appearance-note">字号作用于整个工作台，代码始终使用等宽字体。</p>
      </section>

      <section className="appearance-section" aria-labelledby={`${id}-colors`}>
        <div className="appearance-section-heading">
          <h3 id={`${id}-colors`}>自定义配色</h3>
          <button type="button" className="appearance-text-button" onClick={() => {
            setColorReset((count) => count + 1);
            onChange({ ...value, colors: { accent: null, background: null, text: null } });
          }}>
            恢复主题配色
          </button>
        </div>
        <div className="appearance-colors">
          <ColorField key={`accent-${value.preset}-${colorReset}`} label="强调色" value={colors.accent} onChange={(color) => setColor("accent", color)} />
          <ColorField key={`background-${value.preset}-${colorReset}`} label="背景色" value={colors.background} onChange={(color) => setColor("background", color)} />
          <ColorField key={`text-${value.preset}-${colorReset}`} label="文字色" value={colors.text} onChange={(color) => setColor("text", color)} />
        </div>
        {(textContrast < 4.5 || accentContrast < 3) && (
          <p className="appearance-contrast-warning" role="status">
            <CircleAlert size={16} aria-hidden="true" />
            <span>{textContrast < 4.5 ? "文字与背景对比不足，阅读可能吃力。" : "强调色与背景接近，链接与选中状态可能不够清晰。"}可以调整颜色，或恢复主题配色。</span>
          </p>
        )}
      </section>

      <section className="appearance-live-preview" aria-label="外观预览">
        <div className="appearance-preview-heading"><span>效果预览</span><span>跟随当前外观</span></div>
        <div className="appearance-preview-message"><span className="appearance-preview-avatar">C</span><p>让工作台更合你的心意。<br /><a href="#appearance-preview" onClick={(event) => event.preventDefault()}>继续探索项目</a></p></div>
        <pre><code>{"const theme = 'CoreCoder';"}</code></pre>
      </section>

      <div className="appearance-footer">
        <p role="status" className={!storageAvailable ? "appearance-storage-warning" : ""}>
          {storageAvailable ? "即时生效，仅保存在当前浏览器。" : "外观已生效，但浏览器无法保存设置；刷新后可能丢失。"}
        </p>
        <button type="button" className="appearance-text-button" onClick={() => {
          setColorReset((count) => count + 1);
          onChange(DEFAULT_APPEARANCE);
        }}>
          <RotateCcw size={14} aria-hidden="true" /> 恢复全部默认
        </button>
      </div>
    </div>
  );
}
