import { AudioLines } from "lucide-react";

import type { AgentSpeechSegment } from "../hooks/useVoiceSession";

interface SpeakingStreamProps {
  active: boolean;
  speaking: boolean;
  segments: AgentSpeechSegment[];
}

export function SpeakingStream({ active, speaking, segments }: SpeakingStreamProps) {
  const visible = active && segments.length > 0;

  return (
    <aside
      className={`speaking-stream ${visible ? "is-visible" : ""} ${speaking ? "is-speaking" : ""}`}
      aria-live="polite"
      aria-label="Live agent speech"
      aria-hidden={!visible}
    >
      <div className="speaking-stream-header">
        <span className="speaking-stream-icon"><AudioLines size={14} /></span>
        <span>
          <strong>{speaking ? "Agent speaking" : "Latest response"}</strong>
          <small>{speaking ? "Streaming live" : "Most recent reply"}</small>
        </span>
        <i aria-hidden="true" />
      </div>
      <div className="speaking-stream-copy">
        {segments.map((segment, index) => (
          <p
            key={segment.id}
            className={index === segments.length - 1 ? "is-current" : ""}
          >
            {segment.text}
          </p>
        ))}
      </div>
    </aside>
  );
}
