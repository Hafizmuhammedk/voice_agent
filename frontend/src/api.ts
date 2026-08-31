import type {
  Agent,
  AgentSettings,
  OutboundCallCreated,
  PhoneVerificationPolicy,
  PhoneVerificationStarted,
  PhoneVerificationStatus,
  SessionCreated,
  SettingsFormValue,
  Voice,
} from "./types";

const TOKEN_KEY = "voice-agent-access-token";
let tokenPromise: Promise<string> | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

function describeError(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join(" · ");
    }
  }
  return fallback;
}

async function request<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new ApiError(describeError(payload, response.statusText), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function validateOrCreateToken(): Promise<string> {
  const stored = localStorage.getItem(TOKEN_KEY);
  if (stored) {
    try {
      await request("/api/v1/users/me", stored);
      return stored;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
      localStorage.removeItem(TOKEN_KEY);
    }
  }

  const user = await request<{ api_token: string }>("/api/v1/users", null, {
    method: "POST",
    body: JSON.stringify({ display_name: "Voice user" }),
  });
  localStorage.setItem(TOKEN_KEY, user.api_token);
  return user.api_token;
}

export function ensureApiToken(): Promise<string> {
  tokenPromise ??= validateOrCreateToken().catch((error) => {
    tokenPromise = null;
    throw error;
  });
  return tokenPromise;
}

async function authorized<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await ensureApiToken();
  try {
    return await request<T>(path, token, init);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      tokenPromise = null;
    }
    throw error;
  }
}

export const api = {
  listVoices: () => authorized<Voice[]>("/api/v1/voices"),
  listAgents: () => authorized<Agent[]>("/api/v1/agents"),
  createAgent: (value: SettingsFormValue) =>
    authorized<Agent>("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify({
        name: value.name,
        settings: {
          company_name: value.company_name,
          voice_id: value.voice_id || null,
          language: value.language,
          personality: value.personality,
          speaking_speed: value.speaking_speed,
          custom_instructions: value.custom_instructions,
        },
      }),
    }),
  updateAgentName: (agentId: string, name: string) =>
    authorized<Agent>(`/api/v1/agents/${agentId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  updateAgentSettings: (agentId: string, value: SettingsFormValue) =>
    authorized<AgentSettings>(`/api/v1/agents/${agentId}/settings`, {
      method: "PATCH",
      body: JSON.stringify({
        company_name: value.company_name,
        voice_id: value.voice_id || null,
        language: value.language,
        personality: value.personality,
        speaking_speed: value.speaking_speed,
        custom_instructions: value.custom_instructions,
      }),
    }),
  createSession: (agentId: string) =>
    authorized<SessionCreated>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ agent_id: agentId }),
    }),
  createOutboundCall: (agentId: string, phoneNumber: string, customerName: string) =>
    authorized<OutboundCallCreated>("/api/v1/outbound-calls", {
      method: "POST",
      body: JSON.stringify({
        agent_id: agentId,
        phone_number: phoneNumber,
        customer_name: customerName || "there",
      }),
    }),
  getPhoneVerificationPolicy: () =>
    authorized<PhoneVerificationPolicy>("/api/v1/phone-verifications/policy"),
  startPhoneVerification: (phoneNumber: string) =>
    authorized<PhoneVerificationStarted>("/api/v1/phone-verifications", {
      method: "POST",
      body: JSON.stringify({ phone_number: phoneNumber }),
    }),
  checkPhoneVerification: (phoneNumber: string) =>
    authorized<PhoneVerificationStatus>("/api/v1/phone-verifications/status", {
      method: "POST",
      body: JSON.stringify({ phone_number: phoneNumber }),
    }),
  endSession: (sessionId: string) =>
    authorized(`/api/v1/sessions/${sessionId}/end`, { method: "POST" }),
};
