import type { CSSProperties } from "react";
import { AudioLines, Mic, MicOff, PhoneCall, PhoneOff, Settings2 } from "lucide-react";

import type { VoiceState } from "../hooks/useVoiceSession";

interface VoiceConsoleProps {
  agentName: string;
  ready: boolean;
  active: boolean;
  muted: boolean;
  voiceState: VoiceState;
  audioLevel: number;
  elapsedSeconds: number;
  placingPhoneCall: boolean;
  onStart: () => void;
  onOpenPhoneCall: () => void;
  onStop: () => void;
  onToggleMute: () => void;
  onOpenSettings: () => void;
}

const stateCopy: Record<VoiceState, { label: string; detail: string }> = {
  idle: { label: "Ready when you are", detail: "Start a natural, real-time conversation" },
  connecting: { label: "Connecting", detail: "Preparing a secure voice session" },
  listening: { label: "Listening", detail: "Speak naturally — you can interrupt at any time" },
  speaking: { label: "Speaking", detail: "Your agent is responding" },
  ending: { label: "Ending session", detail: "Closing the room safely" },
};

const waveformProfile = [0.52, 0.76, 0.94, 0.7, 1.14, 0.82, 1, 0.66, 0.48];

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const remaining = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remaining}`;
}

export function VoiceConsole({
  agentName,
  ready,
  active,
  muted,
  voiceState,
  audioLevel,
  elapsedSeconds,
  placingPhoneCall,
  onStart,
  onOpenPhoneCall,
  onStop,
  onToggleMute,
  onOpenSettings,
}: VoiceConsoleProps) {
  const copy = stateCopy[voiceState];
  const reactiveLevel = active ? Math.min(1, Math.max(0, audioLevel)) : 0;
  const mutedWhileListening = muted && active && voiceState !== "speaking";
  const globeStyle = {
    "--voice-level": reactiveLevel.toFixed(3),
    "--globe-scale": (1 + reactiveLevel * 0.11).toFixed(3),
    "--globe-glow": `${24 + reactiveLevel * 72}px`,
    "--halo-outer-opacity": (0.18 + reactiveLevel * 0.55).toFixed(3),
    "--halo-inner-opacity": (0.22 + reactiveLevel * 0.44).toFixed(3),
    "--halo-outer-scale": (0.97 + reactiveLevel * 0.07).toFixed(3),
    "--halo-inner-scale": (0.98 + reactiveLevel * 0.085).toFixed(3),
    "--globe-border-alpha": (0.18 + reactiveLevel * 0.3).toFixed(3),
    "--globe-inner-alpha": (0.05 + reactiveLevel * 0.12).toFixed(3),
    "--globe-outer-alpha": (0.06 + reactiveLevel * 0.12).toFixed(3),
    "--globe-color-opacity": (0.26 + reactiveLevel * 0.28).toFixed(3),
    "--globe-color-rotation": `${-8 + reactiveLevel * 10}deg`,
    "--globe-color-scale": (0.98 + reactiveLevel * 0.07).toFixed(3),
    "--globe-grid-alpha": (0.1 + reactiveLevel * 0.14).toFixed(3),
    "--globe-grid-line-alpha": (0.055 + reactiveLevel * 0.09).toFixed(3),
    "--globe-grid-opacity": (0.45 + reactiveLevel * 0.35).toFixed(3),
    "--globe-grid-rotation": `${reactiveLevel * 3}deg`,
    "--globe-highlight-opacity": (0.15 + reactiveLevel * 0.16).toFixed(3),
  } as CSSProperties;

  return (
    <main className="voice-shell">
      <header className="topbar">
        <a className="brand" href="/app/" aria-label="Ora voice home">
          <span className="brand-mark"><AudioLines size={18} /></span>
          <span className="brand-copy">
            <strong>{agentName || "Ora"}</strong>
            <small>Voice agent</small>
          </span>
        </a>
        <div className="topbar-actions">
          <span className={`connection-chip ${ready ? "online" : ""}`}>
            <i /> {ready ? "Online" : "Preparing"}
          </span>
          <button className="icon-button topbar-settings" type="button" aria-label="Open agent settings" onClick={onOpenSettings}>
            <Settings2 size={19} />
            <span>Settings</span>
          </button>
        </div>
      </header>

      <section className="conversation-stage" aria-live="polite">
        <div className="agent-heading">
          <span className="eyebrow">Full-duplex voice</span>
          <h1>{agentName || "Your agent"}</h1>
          <div className="agent-meta">
            <span className={`live-state state-${voiceState}`}><i /> {active ? copy.label : "Ready"}</span>
            {active && <span className="session-time">{formatDuration(elapsedSeconds)}</span>}
          </div>
        </div>

        <div
          className={`audio-globe-wrap state-${voiceState} ${muted ? "is-muted" : ""}`}
          style={globeStyle}
          role="img"
          aria-label={`${agentName || "Agent"} is ${mutedWhileListening ? "muted" : copy.label.toLowerCase()}`}
        >
          <div className="globe-halo halo-outer" aria-hidden="true" />
          <div className="globe-halo halo-inner" aria-hidden="true" />
          <div className="audio-globe">
            <div className="globe-color" aria-hidden="true" />
            <div className="globe-grid" aria-hidden="true">
              <i /><i /><i /><i />
            </div>
            <div className="globe-highlight" aria-hidden="true" />
            <div className="globe-waveform" aria-hidden="true">
              {waveformProfile.map((gain, index) => {
                const barScale = 0.2 + reactiveLevel * gain;
                return (
                  <i
                    key={index}
                    style={{
                      opacity: 0.55 + reactiveLevel * 0.45,
                      transform: `scaleY(${Math.min(1.2, barScale).toFixed(3)})`,
                    }}
                  />
                );
              })}
            </div>
          </div>
        </div>

        <div className="voice-status">
          <h2>{mutedWhileListening ? "Microphone muted" : copy.label}</h2>
          <p>{mutedWhileListening ? "Unmute when you’re ready to speak" : copy.detail}</p>
        </div>

        {!active ? (
          <div className="start-actions">
            <button className="start-call" type="button" disabled={!ready || placingPhoneCall} onClick={onStart}>
              <Mic size={20} />
              {ready ? "Start conversation" : "Preparing agent…"}
            </button>
            <button className="phone-call-button" type="button" disabled={!ready || placingPhoneCall} onClick={onOpenPhoneCall}>
              <PhoneCall size={19} />
              Call a phone
            </button>
          </div>
        ) : (
          <div className="call-controls">
            <button
              className={`round-control ${muted ? "active" : ""}`}
              type="button"
              aria-label={muted ? "Unmute microphone" : "Mute microphone"}
              onClick={onToggleMute}
              disabled={voiceState === "connecting" || voiceState === "ending"}
            >
              {muted ? <MicOff size={23} /> : <Mic size={23} />}
              <span>{muted ? "Unmute" : "Mute"}</span>
            </button>
            <button
              className="round-control hangup"
              type="button"
              aria-label="End conversation"
              onClick={onStop}
              disabled={voiceState === "ending"}
            >
              <PhoneOff size={23} />
              <span>End</span>
            </button>
          </div>
        )}

      </section>

      <footer className="voice-footer">
        <span><i /> Live full-duplex audio</span>
        <span>Encrypted room · Audio is not stored</span>
      </footer>
    </main>
  );
}
