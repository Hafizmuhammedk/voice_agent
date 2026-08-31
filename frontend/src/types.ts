export type Personality =
  | "friendly"
  | "professional"
  | "casual"
  | "calm"
  | "concise"
  | "energetic";

export interface AgentSettings {
  provider: string;
  model: string;
  company_name: string;
  voice_id: string;
  language: string;
  personality: Personality;
  speaking_speed: number;
  custom_instructions: string;
  updated_at: string;
}

export interface Agent {
  id: string;
  name: string;
  settings: AgentSettings;
  created_at: string;
  updated_at: string;
}

export interface Voice {
  id: string;
  name: string;
  provider: string;
  model: string;
  languages: string[];
  supports_speed: boolean;
}

export interface SessionCreated {
  id: string;
  agent_id: string;
  room_name: string;
  participant_identity: string;
  status: string;
  configuration_snapshot: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  server_url: string;
  participant_token: string;
  token_expires_at: string;
}

export interface OutboundCallCreated {
  session_id: string;
  dispatch_id: string;
  room_name: string;
  status: string;
}

export interface PhoneVerificationPolicy {
  available: boolean;
  required: boolean;
  manual_verification_required: boolean;
}

export interface PhoneVerificationStarted {
  phone_number: string;
  validation_code: string | null;
  status: "pending" | "verified";
}

export interface PhoneVerificationStatus {
  phone_number: string;
  verified: boolean;
  status: "pending" | "verified";
}

export interface SettingsFormValue {
  name: string;
  company_name: string;
  voice_id: string;
  language: string;
  personality: Personality;
  speaking_speed: number;
  custom_instructions: string;
}
