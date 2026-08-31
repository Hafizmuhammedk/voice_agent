import { AudioLines } from "lucide-react";

import type { SpeechSegment } from "../hooks/useVoiceSession";

interface SpeakingStreamProps {
  active: boolean;
  listening: boolean;
  speaking: boolean;
  segments: SpeechSegment[];
}

export function SpeakingStream({ active, listening, speaking, segments }: SpeakingStreamProps) {
  const visible = active && segments.length > 0;
  const currentSpeaker = segments.at(-1)?.speaker;
  const title = speaking ? "Agent speaking" : listening ? "User speaking" : "Live transcript";
  const subtitle = speaking || listening ? "Streaming live" : "Latest conversation";

  return (
    <aside
      className={`speaking-stream ${visible ? "is-visible" : ""} ${speaking ? "is-speaking" : ""} ${listening ? "is-listening" : ""}`}
      aria-live="polite"
      aria-label="Live voice transcript"
      aria-hidden={!visible}
    >
      <div className="speaking-stream-header">
        <span className="speaking-stream-icon"><AudioLines size={14} /></span>
        <span>
          <strong>{title}</strong>
          <small>{subtitle}</small>
        </span>
        <i className={currentSpeaker === "user" ? "is-user" : ""} aria-hidden="true" />
      </div>
      <div className="speaking-stream-copy">
        {segments.map((segment, index) => (
          <p
            key={segment.id}
            className={`${index === segments.length - 1 ? "is-current" : ""} is-${segment.speaker}`}
          >
            <span>{segment.speaker === "user" ? "You" : "Agent"}</span>
            {segment.text}
          </p>
        ))}
      </div>
    </aside>
  );
}
