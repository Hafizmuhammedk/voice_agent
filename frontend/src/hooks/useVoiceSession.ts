import type {
  LocalAudioTrack,
  RemoteAudioTrack,
  Room as LiveKitRoom,
} from "livekit-client";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";

export type VoiceState = "idle" | "connecting" | "listening" | "speaking" | "ending";

export type SpeechSpeaker = "user" | "agent";

export interface SpeechSegment {
  id: string;
  speaker: SpeechSpeaker;
  text: string;
  final: boolean;
}

interface AudioAnalyserHandle {
  calculateVolume: () => number;
  cleanup: () => Promise<void>;
}

function closeAnalyser(analyser: AudioAnalyserHandle | null): void {
  if (analyser) void analyser.cleanup().catch(() => undefined);
}

function speechKey(text: string): string {
  return text
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function mergeSpeechSegment(
  current: SpeechSegment[],
  incoming: SpeechSegment,
): SpeechSegment[] {
  const existingIndex = current.findIndex((segment) => segment.id === incoming.id);
  if (existingIndex === -1) {
    const incomingKey = speechKey(incoming.text);
    const duplicateIndex = current.findIndex(
      (segment) => segment.speaker === incoming.speaker && speechKey(segment.text) === incomingKey,
    );
    if (incomingKey && duplicateIndex !== -1) {
      const duplicate = current[duplicateIndex];
      if (duplicate.final || !incoming.final) return current;

      const next = [...current];
      next[duplicateIndex] = { ...duplicate, final: true };
      return next;
    }

    return [...current, incoming].slice(-4);
  }

  const existing = current[existingIndex];
  if (existing.final && !incoming.final) return current;
  if (existing.text === incoming.text && existing.final === incoming.final) return current;

  const next = [...current];
  next[existingIndex] = incoming;
  return next.slice(-4);
}

export function useVoiceSession(onError: (message: string) => void) {
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [muted, setMuted] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [speechSegments, setSpeechSegments] = useState<SpeechSegment[]>([]);
  const [audioLevel, setAudioLevel] = useState(0);
  const roomRef = useRef<LiveKitRoom | null>(null);
  const microphoneRef = useRef<LocalAudioTrack | null>(null);
  const microphoneAnalyserRef = useRef<AudioAnalyserHandle | null>(null);
  const agentAnalyserRef = useRef<AudioAnalyserHandle | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const audioHostRef = useRef<HTMLDivElement>(null);
  const intentionalDisconnectRef = useRef(false);

  const active = voiceState !== "idle";

  const clearAudioAnalysers = useCallback(() => {
    closeAnalyser(microphoneAnalyserRef.current);
    closeAnalyser(agentAnalyserRef.current);
    microphoneAnalyserRef.current = null;
    agentAnalyserRef.current = null;
    setAudioLevel(0);
  }, []);

  useEffect(() => {
    if (!active || voiceState === "connecting") return;
    const timer = window.setInterval(() => setElapsedSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active, voiceState]);

  useEffect(
    () => () => {
      intentionalDisconnectRef.current = true;
      clearAudioAnalysers();
      void roomRef.current?.disconnect(true);
    },
    [clearAudioAnalysers],
  );

  useEffect(() => {
    if (!active) {
      setAudioLevel(0);
      return;
    }

    let animationFrame = 0;
    let displayedLevel = 0;
    let lastRenderTime = 0;

    const sampleAudio = (time: number) => {
      const room = roomRef.current;
      let rawLevel = 0;

      try {
        if (voiceState === "speaking") {
          rawLevel =
            agentAnalyserRef.current?.calculateVolume() ??
            Math.max(0, ...Array.from(room?.remoteParticipants.values() ?? [], (item) => item.audioLevel));
        } else if (voiceState === "listening" && !muted) {
          rawLevel =
            microphoneAnalyserRef.current?.calculateVolume() ??
            room?.localParticipant.audioLevel ??
            0;
        }
      } catch {
        // Audio analysis is only visual. A browser that blocks Web Audio must
        // never interrupt the actual LiveKit voice session.
        rawLevel = 0;
      }

      const normalizedLevel = Math.min(1, Math.max(0, rawLevel * 4.2));
      const smoothing = normalizedLevel > displayedLevel ? 0.42 : 0.16;
      displayedLevel += (normalizedLevel - displayedLevel) * smoothing;
      if (displayedLevel < 0.008) displayedLevel = 0;

      if (time - lastRenderTime >= 45) {
        lastRenderTime = time;
        setAudioLevel(Number(displayedLevel.toFixed(3)));
      }
      animationFrame = window.requestAnimationFrame(sampleAudio);
    };

    animationFrame = window.requestAnimationFrame(sampleAudio);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [active, muted, voiceState]);

  const start = useCallback(
    async (agentId: string) => {
      if (roomRef.current) return;
      setVoiceState("connecting");
      setElapsedSeconds(0);
      setMuted(false);
      setSpeechSegments([]);
      intentionalDisconnectRef.current = false;

      try {
        const {
          createAudioAnalyser,
          createLocalAudioTrack,
          DisconnectReason,
          Room,
          RoomEvent,
          Track,
        } =
          await import("livekit-client");
        const session = await api.createSession(agentId);
        sessionIdRef.current = session.id;
        const room = new Room({ adaptiveStream: true, dynacast: true });
        roomRef.current = room;

        room.registerTextStreamHandler("lk.transcription", async (reader, participantInfo) => {
          const attributes = reader.info.attributes ?? {};
          const isTranscription = Boolean(attributes["lk.transcribed_track_id"]);
          const isLocalSpeaker = participantInfo.identity === room.localParticipant.identity;
          if (!isTranscription) return;

          const id = attributes["lk.segment_id"] || reader.info.id;
          const final = attributes["lk.transcription_final"] === "true";
          const speaker: SpeechSpeaker = isLocalSpeaker ? "user" : "agent";
          let text = "";

          try {
            for await (const chunk of reader) {
              text += chunk;
              const normalized = text.trim();
              if (!normalized || roomRef.current !== room) continue;
              setSpeechSegments((current) =>
                mergeSpeechSegment(current, { id, speaker, text: normalized, final }),
              );
            }
          } catch {
            // A transcription stream can be cut short by barge-in or disconnect;
            // audio remains authoritative and should continue without a UI error.
          }
        });

        room.on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind !== Track.Kind.Audio || !audioHostRef.current) return;
          const element = track.attach();
          element.autoplay = true;
          audioHostRef.current.appendChild(element);
          closeAnalyser(agentAnalyserRef.current);
          try {
            agentAnalyserRef.current = createAudioAnalyser(track as RemoteAudioTrack, {
              fftSize: 256,
              smoothingTimeConstant: 0.48,
              minDecibels: -72,
              maxDecibels: -18,
            });
          } catch {
            agentAnalyserRef.current = null;
          }
        });
        room.on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((node) => node.remove());
          if (track.kind === Track.Kind.Audio) {
            closeAnalyser(agentAnalyserRef.current);
            agentAnalyserRef.current = null;
          }
        });
        room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          const remoteIsSpeaking = speakers.some((speaker) => !speaker.isLocal);
          setVoiceState(remoteIsSpeaking ? "speaking" : "listening");
        });
        room.on(RoomEvent.Reconnecting, () => {
          if (roomRef.current === room) setVoiceState("connecting");
        });
        room.on(RoomEvent.Reconnected, () => {
          if (roomRef.current === room) setVoiceState("listening");
        });
        room.on(RoomEvent.Disconnected, (reason) => {
          if (roomRef.current !== room) return;
          roomRef.current = null;
          microphoneRef.current = null;
          clearAudioAnalysers();
          const sessionId = sessionIdRef.current;
          sessionIdRef.current = null;
          audioHostRef.current?.replaceChildren();
          if (sessionId) void api.endSession(sessionId).catch(() => undefined);
          if (!intentionalDisconnectRef.current) {
            setVoiceState("idle");
            setMuted(false);
            setSpeechSegments([]);
            // The agent's end-call tool deletes the dedicated room after a normal
            // goodbye. That is a successful call ending, not a connection failure.
            if (reason !== DisconnectReason.ROOM_DELETED) {
              const reasonName =
                reason === undefined ? "UNKNOWN_REASON" : DisconnectReason[reason];
              onError(`The voice connection ended unexpectedly (${reasonName}).`);
            }
          }
        });

        await room.connect(session.server_url, session.participant_token);
        const microphone = await createLocalAudioTrack({
          echoCancellation: true,
          // QUAIL performs the primary suppression in the worker. Applying a
          // second browser filter can clip quiet speech and short words.
          noiseSuppression: false,
          autoGainControl: true,
        });
        microphoneRef.current = microphone;
        try {
          microphoneAnalyserRef.current = createAudioAnalyser(microphone, {
            fftSize: 256,
            smoothingTimeConstant: 0.42,
            minDecibels: -72,
            maxDecibels: -18,
          });
        } catch {
          microphoneAnalyserRef.current = null;
        }
        await room.localParticipant.publishTrack(microphone, {
          source: Track.Source.Microphone,
        });
        setVoiceState("listening");
      } catch (error) {
        intentionalDisconnectRef.current = true;
        await roomRef.current?.disconnect(true).catch(() => undefined);
        roomRef.current = null;
        microphoneRef.current = null;
        clearAudioAnalysers();
        const sessionId = sessionIdRef.current;
        sessionIdRef.current = null;
        if (sessionId) await api.endSession(sessionId).catch(() => undefined);
        setVoiceState("idle");
        onError(error instanceof Error ? error.message : "Unable to start the voice session.");
      }
    },
    [clearAudioAnalysers, onError],
  );

  const stop = useCallback(async () => {
    if (!roomRef.current && !sessionIdRef.current) return;
    setVoiceState("ending");
    intentionalDisconnectRef.current = true;
    const room = roomRef.current;
    const sessionId = sessionIdRef.current;
    roomRef.current = null;
    microphoneRef.current = null;
    clearAudioAnalysers();
    sessionIdRef.current = null;

    try {
      await room?.disconnect(true);
      if (sessionId) await api.endSession(sessionId);
    } catch (error) {
      onError(error instanceof Error ? error.message : "The session could not close cleanly.");
    } finally {
      audioHostRef.current?.replaceChildren();
      setMuted(false);
      setSpeechSegments([]);
      setVoiceState("idle");
    }
  }, [clearAudioAnalysers, onError]);

  const toggleMute = useCallback(async () => {
    const microphone = microphoneRef.current;
    if (!microphone) return;
    try {
      if (muted) await microphone.unmute();
      else await microphone.mute();
      setMuted(!muted);
    } catch {
      onError("Could not change the microphone state.");
    }
  }, [muted, onError]);

  return {
    voiceState,
    active,
    muted,
    elapsedSeconds,
    audioLevel,
    speechSegments,
    audioHostRef,
    start,
    stop,
    toggleMute,
  };
}
