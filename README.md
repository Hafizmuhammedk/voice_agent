# PersonaPlex-style LiveKit voice agent

A production-oriented, general-purpose voice assistant built with LiveKit Agents 1.7 and LiveKit Cloud Inference. It aims for the conversational qualities associated with NVIDIA PersonaPlex—continuous listening, quick turn-taking, natural interruption handling, concise speech, and strong role conditioning—without running or claiming to use the PersonaPlex model.

The runtime is a standard STT → LLM → TTS pipeline:

- Deepgram Flux for low-latency English speech recognition;
- Deepgram Nova 3 for supported non-English languages;
- Gemini Flash Lite through the direct Google API for reasoning and tool use;
- Cartesia Sonic 3 for streaming speech;
- LiveKit turn detection, adaptive interruption, and false-interruption recovery; and
- AI-coustics input enhancement.

NVIDIA PersonaPlex itself is currently self-hosted-only in LiveKit and does not support tools or user transcription. This project keeps the cloud pipeline because it supports general assistance, transcripts, SIP actions, and call-control tools.

## Project structure

```text
voice_agent/
├── agent.py                     # compatibility launcher
├── backend/                     # Python backend and LiveKit worker
│   ├── app/                     # FastAPI application package
│   │   ├── api/                 # HTTP routes and dependencies
│   │   ├── core/                # settings and authentication
│   │   ├── db/                  # SQLAlchemy models and lifecycle
│   │   ├── main.py              # FastAPI application factory
│   │   ├── repositories.py      # database query boundaries
│   │   ├── schemas.py           # validated API contracts
│   │   └── services.py          # application business logic
│   ├── voice_agent/             # full-duplex LiveKit runtime
│   ├── agent.py                 # compatibility agent launcher
│   ├── requirements.txt         # runtime Python dependencies
│   └── tests/                   # backend and runtime tests
├── frontend/                    # browser voice-agent client
│   ├── index.html
│   ├── package.json             # React, Vite, and LiveKit JS
│   ├── vite.config.ts
│   └── src/                     # voice console and settings UI
├── .env.example                 # safe configuration template
└── backend/tests/
    └── test_agent.py
```

Files under `test/` are not imported, copied, or used by the runtime.

## Setup

Create a standard Python virtual environment and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

Install the React frontend dependencies and create its production build:

```powershell
cd frontend
npm install
npm run build
cd ..
```

Copy `.env.example` to `backend\.env`, then add credentials from one LiveKit Cloud project:

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-project-api-key
LIVEKIT_API_SECRET=your-project-api-secret
LIVEKIT_AGENT_NAME=general-assistant
GOOGLE_API_KEY=your-private-gemini-api-key
```

`GOOGLE_API_KEY` is used only by the backend worker. Deepgram STT and Cartesia TTS
continue through LiveKit Inference. Do not put participant JWT tokens in `.env`, expose
provider keys to the frontend, or commit `.env`.

## Run

Start the application backend first:

```powershell
python -m backend
```

Equivalent explicit Uvicorn command:

```powershell
python -m uvicorn backend.app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Open the API documentation at `http://127.0.0.1:8000/docs` or the React voice interface at `http://127.0.0.1:8000/app/`.

In another terminal, start the LiveKit worker:

```powershell
python -m backend.agent dev
```

For runtime lifecycle updates, `BACKEND_API_URL` must point to FastAPI and `BACKEND_API_TOKEN` must contain the same private value used by both processes.

### React development mode

For frontend hot reload, keep FastAPI running and start Vite in another terminal:

```powershell
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173/app/`. Vite proxies `/api` and `/health` to FastAPI on port 8000. Run `npm run build` before serving the frontend through FastAPI on port 8000.

### API flow

1. `POST /api/v1/users` creates a test user and returns an opaque bearer token once.
2. Send that token as `Authorization: Bearer <token>`.
3. `POST /api/v1/agents` saves a validated agent and settings.
4. `POST /api/v1/sessions` creates an immutable settings snapshot and returns a short-lived LiveKit participant token.
5. The frontend connects directly to LiveKit; audio never flows through FastAPI.

Important endpoints:

```text
GET    /health
POST   /api/v1/users
GET    /api/v1/users/me
GET    /api/v1/voices
POST   /api/v1/agents
GET    /api/v1/agents
GET    /api/v1/agents/{agent_id}
PATCH  /api/v1/agents/{agent_id}
DELETE /api/v1/agents/{agent_id}
GET    /api/v1/agents/{agent_id}/settings
PATCH  /api/v1/agents/{agent_id}/settings
POST   /api/v1/sessions
GET    /api/v1/sessions/{session_id}
POST   /api/v1/sessions/{session_id}/end
POST   /api/v1/livekit/token
POST   /api/v1/outbound-calls
GET    /api/v1/phone-verifications/policy
POST   /api/v1/phone-verifications
POST   /api/v1/phone-verifications/status
```

The default database is SQLite at `voice_agent.db`. Set `DATABASE_URL` to another SQLAlchemy async URL when the application requires a managed relational database.

### Twilio trial number verification

Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_TRIAL_MODE=true` in
`backend/.env`. Twilio blocks its public caller-ID validation API on trial accounts, so add
test destinations under **Phone Numbers → Manage → Verified Caller IDs** in the Twilio
Console. The frontend links to that page, and FastAPI checks the number before creating the
LiveKit dispatch so an unverified call fails immediately with a useful message. Set
`TWILIO_TRIAL_MODE=false` after upgrading the Twilio account so permitted destinations can
be called without the trial-only precheck.

## Voice-only development commands

Direct microphone and speaker test:

```powershell
python -m backend.agent console --input-device 1 --output-device 3
```

Development worker for browser, app, or phone calls:

```powershell
python -m backend.agent dev
```

Installed command alternatives:

```powershell
python -m backend.agent console
python -m backend.agent dev
```

The worker remaining at `registered worker` is normal. It is waiting for a participant and agent dispatch.

## Full-duplex test

1. Ask the assistant for a detailed explanation.
2. While it is speaking, say: “Stop. Give me only the short answer.”
3. Its current audio should stop and the next answer should follow the new request.
4. Try a short acknowledgement such as “uh-huh”; adaptive interruption should normally avoid treating it as a complete replacement turn.

This is behavioral similarity, not model equivalence. A cascaded cloud pipeline cannot reproduce PersonaPlex's learned non-verbal audio generation or true single-model dual-stream architecture.

## Persona and voice configuration

```dotenv
VOICE_AGENT_NAME=Alex
COMPANY_NAME=your team
AGENT_LANGUAGE=en-US
AGENT_TIMEZONE=Asia/Kolkata
AGENT_INSTRUCTIONS=Help with general questions and practical tasks.
AGENT_PERSONALITY=friendly
SPEAKING_SPEED=1.0
CARTESIA_VOICE_ID=f786b574-daa5-4673-aa0c-cbe3e8534c02
LLM_TEMPERATURE=0.3
AEC_WARMUP_SECONDS=0.5
ENABLE_BACKGROUND_AUDIO=false
```

The stable prompt policy lives in `prompts.py`. `AGENT_INSTRUCTIONS` supplies the changeable role, knowledge, and task. This separation provides PersonaPlex-style role conditioning without mixing business instructions into safety and call-control rules.

Supported language families are Arabic, Danish, Dutch, English, French, German, Portuguese, Spanish, and Swedish. Unsupported languages fall back to English.

## Optional SIP integration

SIP calling and human transfer:

```dotenv
SIP_OUTBOUND_TRUNK_ID=ST_xxxxxxxxxxxx
HUMAN_TRANSFER_NUMBER=+15105550123
```

Outbound and transfer numbers must use E.164 format. Transfers run only after an explicit request and only for an active SIP participant.

## Dispatch metadata

```json
{
  "direction": "outbound",
  "phone_number": "+15105550123",
  "customer_name": "Sam",
  "call_log_id": 42,
  "agent_config": {
    "agent_name": "Alex",
    "company_name": "Example Organization",
    "language": "en-US",
    "timezone": "Asia/Kolkata",
    "temperature": 0.3,
    "instructions": "Help {customer_name} with general questions for {company_name}.",
    "enable_background_audio": false
  }
}
```

Nested `agent_config` values take precedence over top-level metadata, which takes precedence over environment defaults. Prompt placeholders `{customer_name}`, `{agent_name}`, and `{company_name}` are expanded before each session.

Each room is treated as a dedicated call and deleted when the session closes so web and SIP callers are not left connected to silence.

## Verification

```powershell
python -m pip install -r backend\requirements-dev.txt
python -m ruff check . --config backend\ruff.toml
python -m ruff format --check . --config backend\ruff.toml
python -m pyright --project backend\pyrightconfig.json
python -m unittest discover -s backend\tests -v
python -m backend.agent --help
```

References: [LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/), [adaptive interruption](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/), [NVIDIA PersonaPlex research](https://research.nvidia.com/labs/adlr/personaplex/), and [LiveKit PersonaPlex integration](https://docs.livekit.io/agents/models/realtime/plugins/personaplex/).
