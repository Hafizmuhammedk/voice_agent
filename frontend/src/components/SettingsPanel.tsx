import { Check, ChevronDown, SlidersHorizontal, X } from "lucide-react";
import { useEffect, type FormEvent } from "react";

import type { Personality, SettingsFormValue, Voice } from "../types";

const languages = [
  ["en-US", "English (US)"],
  ["en-GB", "English (UK)"],
  ["ar", "Arabic"],
  ["de", "German"],
  ["es", "Spanish"],
  ["fr", "French"],
  ["nl", "Dutch"],
  ["pt", "Portuguese"],
  ["sv", "Swedish"],
] as const;

const personalities: { value: Personality; label: string; description: string }[] = [
  { value: "friendly", label: "Friendly", description: "Warm and approachable" },
  { value: "professional", label: "Professional", description: "Clear and polished" },
  { value: "casual", label: "Casual", description: "Relaxed and natural" },
  { value: "calm", label: "Calm", description: "Measured and reassuring" },
  { value: "concise", label: "Concise", description: "Brief and direct" },
  { value: "energetic", label: "Energetic", description: "Upbeat and expressive" },
];

interface SettingsPanelProps {
  open: boolean;
  value: SettingsFormValue;
  voices: Voice[];
  saving: boolean;
  saved: boolean;
  disabled: boolean;
  onChange: (value: SettingsFormValue) => void;
  onClose: () => void;
  onSave: () => Promise<void>;
}

export function SettingsPanel({
  open,
  value,
  voices,
  saving,
  saved,
  disabled,
  onChange,
  onClose,
  onSave,
}: SettingsPanelProps) {
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  const update = <K extends keyof SettingsFormValue>(key: K, next: SettingsFormValue[K]) =>
    onChange({ ...value, [key]: next });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void onSave().catch(() => undefined);
  };

  return (
    <>
      <button
        className={`settings-scrim ${open ? "is-open" : ""}`}
        aria-label="Close settings"
        tabIndex={open ? 0 : -1}
        onClick={onClose}
      />
      <aside className={`settings-panel ${open ? "is-open" : ""}`} aria-hidden={!open}>
        <header className="panel-header">
          <div>
            <span className="eyebrow">Configuration</span>
            <h2>Agent settings</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close settings" onClick={onClose}>
            <X size={20} />
          </button>
        </header>

        <form className="settings-form" onSubmit={submit}>
          <div className="form-section">
            <div className="section-heading">
              <SlidersHorizontal size={16} />
              <span>Identity & voice</span>
            </div>
            <label className="field">
              <span>Agent name</span>
              <input
                value={value.name}
                maxLength={80}
                required
                disabled={disabled}
                onChange={(event) => update("name", event.target.value)}
              />
            </label>
            <label className="field">
              <span>Hotel name</span>
              <input
                value={value.company_name || ""}
                maxLength={120}
                required
                disabled={disabled}
                onChange={(event) => update("company_name", event.target.value)}
              />
            </label>
            <div className="field-grid">
              <label className="field">
                <span>Voice</span>
                <div className="select-wrap">
                  <select
                    value={value.voice_id}
                    disabled={disabled || voices.length === 0}
                    onChange={(event) => update("voice_id", event.target.value)}
                  >
                    {voices.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.name}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={16} />
                </div>
              </label>
              <label className="field">
                <span>Language</span>
                <div className="select-wrap">
                  <select
                    value={value.language}
                    disabled={disabled}
                    onChange={(event) => update("language", event.target.value)}
                  >
                    {languages.map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <ChevronDown size={16} />
                </div>
              </label>
            </div>
          </div>

          <div className="form-section">
            <div className="section-heading">
              <span className="section-dot" />
              <span>Personality</span>
            </div>
            <div className="personality-grid">
              {personalities.map((item) => (
                <button
                  className={`personality-option ${value.personality === item.value ? "selected" : ""}`}
                  type="button"
                  key={item.value}
                  disabled={disabled}
                  onClick={() => update("personality", item.value)}
                >
                  <span>{item.label}</span>
                  <small>{item.description}</small>
                  {value.personality === item.value && <Check size={15} />}
                </button>
              ))}
            </div>
          </div>

          <div className="form-section">
            <label className="field range-field">
              <span>
                Speaking pace <strong>{value.speaking_speed.toFixed(2)}×</strong>
              </span>
              <input
                type="range"
                min="0.7"
                max="1.3"
                step="0.05"
                value={value.speaking_speed}
                disabled={disabled}
                onChange={(event) => update("speaking_speed", Number(event.target.value))}
              />
              <span className="range-labels"><small>Measured</small><small>Expressive</small></span>
            </label>
            <label className="field">
              <span>Instructions</span>
              <textarea
                value={value.custom_instructions}
                maxLength={4000}
                rows={5}
                disabled={disabled}
                placeholder="Describe how your agent should respond..."
                onChange={(event) => update("custom_instructions", event.target.value)}
              />
              <small className="character-count">{value.custom_instructions.length} / 4000</small>
            </label>
          </div>

          <div className="panel-actions">
            <p>{disabled ? "End the conversation to change settings." : "Changes apply to your next conversation."}</p>
            <button
              className="save-button"
              type="submit"
              disabled={saving || disabled || !value.name.trim() || !(value.company_name || "").trim()}
            >
              {saved ? <Check size={17} /> : null}
              {saving ? "Saving…" : saved ? "Saved" : "Save settings"}
            </button>
          </div>
        </form>
      </aside>
    </>
  );
}
