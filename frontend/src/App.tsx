import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api, ensureApiToken } from "./api";
import { SettingsPanel } from "./components/SettingsPanel";
import { OutboundCallPanel } from "./components/OutboundCallPanel";
import { SpeakingStream } from "./components/SpeakingStream";
import { VoiceConsole } from "./components/VoiceConsole";
import { useVoiceSession } from "./hooks/useVoiceSession";
import type {
  Agent,
  PhoneVerificationPolicy,
  PhoneVerificationStarted,
  SettingsFormValue,
  Voice,
} from "./types";

const defaultSettings: SettingsFormValue = {
  name: "Ora",
  voice_id: "",
  language: "en-US",
  personality: "friendly",
  speaking_speed: 1,
  custom_instructions: "Keep answers clear, practical, and easy to follow.",
};

function toFormValue(agent: Agent): SettingsFormValue {
  return {
    name: agent.name,
    voice_id: agent.settings.voice_id,
    language: agent.settings.language,
    personality: agent.settings.personality,
    speaking_speed: agent.settings.speaking_speed,
    custom_instructions: agent.settings.custom_instructions,
  };
}

export default function App() {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [formValue, setFormValue] = useState(defaultSettings);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [phonePanelOpen, setPhonePanelOpen] = useState(false);
  const [placingPhoneCall, setPlacingPhoneCall] = useState(false);
  const [phoneVerificationPolicy, setPhoneVerificationPolicy] = useState<PhoneVerificationPolicy>({
    available: false,
    required: false,
    manual_verification_required: false,
  });
  const [callNotice, setCallNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const showError = useCallback((message: string) => setError(message), []);
  const voice = useVoiceSession(showError);

  useEffect(() => {
    let current = true;
    void (async () => {
      try {
        await ensureApiToken();
        const [availableVoices, agents, verificationPolicy] = await Promise.all([
          api.listVoices(),
          api.listAgents(),
          api.getPhoneVerificationPolicy(),
        ]);
        if (!current) return;
        setVoices(availableVoices);
        setPhoneVerificationPolicy(verificationPolicy);
        const existingAgent = agents[0] ?? null;
        if (existingAgent) {
          setAgent(existingAgent);
          setFormValue(toFormValue(existingAgent));
        } else {
          setFormValue((value) => ({ ...value, voice_id: availableVoices[0]?.id ?? "" }));
          setSettingsOpen(true);
        }
      } catch (caught) {
        if (current) showError(caught instanceof Error ? caught.message : "Could not prepare the voice agent.");
      } finally {
        if (current) setLoading(false);
      }
    })();
    return () => {
      current = false;
    };
  }, [showError]);

  const saveSettings = useCallback(async (): Promise<Agent> => {
    if (!formValue.name.trim()) throw new Error("Give your voice agent a name.");
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      let next: Agent;
      if (agent) {
        const renamed = await api.updateAgentName(agent.id, formValue.name.trim());
        const settings = await api.updateAgentSettings(agent.id, formValue);
        next = { ...renamed, settings };
      } else {
        next = await api.createAgent({ ...formValue, name: formValue.name.trim() });
      }
      setAgent(next);
      setFormValue(toFormValue(next));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      return next;
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Could not save agent settings.";
      setError(message);
      throw caught;
    } finally {
      setSaving(false);
    }
  }, [agent, formValue]);

  const startConversation = useCallback(async () => {
    setError(null);
    try {
      const selectedAgent = agent ?? (await saveSettings());
      await voice.start(selectedAgent.id);
    } catch {
      // saveSettings and the voice hook expose a useful error in the UI.
    }
  }, [agent, saveSettings, voice]);

  const startPhoneCall = useCallback(async (phoneNumber: string, customerName: string) => {
    setPlacingPhoneCall(true);
    setCallNotice(null);
    try {
      const selectedAgent = agent ?? (await saveSettings());
      await api.createOutboundCall(selectedAgent.id, phoneNumber, customerName);
      setPhonePanelOpen(false);
      setCallNotice("Call requested. Answer the verified phone when it rings.");
    } finally {
      setPlacingPhoneCall(false);
    }
  }, [agent, saveSettings]);

  const startPhoneVerification = useCallback(
    (phoneNumber: string): Promise<PhoneVerificationStarted> =>
      api.startPhoneVerification(phoneNumber),
    [],
  );

  const checkPhoneVerification = useCallback(async (phoneNumber: string): Promise<boolean> => {
    const result = await api.checkPhoneVerification(phoneNumber);
    return result.verified;
  }, []);

  return (
    <div className="app-frame">
      <VoiceConsole
        agentName={formValue.name}
        ready={!loading && Boolean(formValue.voice_id)}
        active={voice.active}
        muted={voice.muted}
        voiceState={voice.voiceState}
        audioLevel={voice.audioLevel}
        elapsedSeconds={voice.elapsedSeconds}
        placingPhoneCall={placingPhoneCall}
        onStart={() => void startConversation()}
        onOpenPhoneCall={() => setPhonePanelOpen(true)}
        onStop={() => void voice.stop()}
        onToggleMute={() => void voice.toggleMute()}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <SpeakingStream
        active={voice.active}
        speaking={voice.voiceState === "speaking"}
        segments={voice.agentSpeech}
      />

      <OutboundCallPanel
        open={phonePanelOpen}
        submitting={placingPhoneCall}
        verificationAvailable={phoneVerificationPolicy.available}
        verificationRequired={phoneVerificationPolicy.required}
        manualVerificationRequired={phoneVerificationPolicy.manual_verification_required}
        onClose={() => setPhonePanelOpen(false)}
        onSubmit={startPhoneCall}
        onStartVerification={startPhoneVerification}
        onCheckVerification={checkPhoneVerification}
      />

      <SettingsPanel
        open={settingsOpen}
        value={formValue}
        voices={voices}
        saving={saving}
        saved={saved}
        disabled={voice.active}
        onChange={(value) => {
          setSaved(false);
          setFormValue(value);
        }}
        onClose={() => setSettingsOpen(false)}
        onSave={async () => {
          await saveSettings();
          window.setTimeout(() => setSettingsOpen(false), 450);
        }}
      />

      {error && (
        <div className="error-toast" role="alert">
          <AlertCircle size={19} />
          <span>{error}</span>
          <button type="button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={17} /></button>
        </div>
      )}
      {callNotice && (
        <div className="success-toast" role="status">
          <CheckCircle2 size={19} />
          <span>{callNotice}</span>
          <button type="button" aria-label="Dismiss call status" onClick={() => setCallNotice(null)}><X size={17} /></button>
        </div>
      )}
      <div ref={voice.audioHostRef} className="remote-audio" />
    </div>
  );
}
