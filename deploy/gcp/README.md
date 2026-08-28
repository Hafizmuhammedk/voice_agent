# Deploy the voice agent to a Google Cloud VM

This deployment runs three containers on one Compute Engine VM:

```text
Internet ──HTTPS──► Caddy ──HTTP/private network──► FastAPI + React
                                                     │
LiveKit Cloud ◄──────── outbound TLS/WebSocket ── Voice worker
                                                     │
                                                     └──► Deepgram
```

The API and agent use the same application image but run as separate processes. Caddy obtains and renews the public TLS certificate. SQLite data and Caddy certificates are stored in named Docker volumes.

## 1. Prerequisites

- A Google Cloud project with billing and Compute Engine enabled.
- Google Cloud CLI authenticated to that project.
- A domain or subdomain you control, such as `voice.example.com`.
- Working LiveKit Cloud, Deepgram, Cartesia, and optional Twilio/SIP configuration.
- The application source available from a private Git repository or another secure transfer method.

A domain and HTTPS are strongly recommended because public browser microphone access requires a secure context. Caddy needs inbound TCP ports 80 and 443 to obtain certificates; UDP 443 enables HTTP/3.

## 2. Reserve an IP and create the VM

Choose a region and zone close to callers and LiveKit. The example uses Mumbai:

```bash
gcloud config set project YOUR_GCP_PROJECT_ID

gcloud compute addresses create voice-agent-ip \
  --region=asia-south1

STATIC_IP=$(gcloud compute addresses describe voice-agent-ip \
  --region=asia-south1 \
  --format='value(address)')

gcloud compute instances create voice-agent-vm \
  --zone=asia-south1-a \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --address="$STATIC_IP" \
  --tags=voice-agent-web
```

`e2-standard-2` is a reasonable starting point for one API and one worker. Monitor CPU and memory during simultaneous calls and resize the VM if needed.

Allow only public web traffic to the tagged VM:

```bash
gcloud compute firewall-rules create voice-agent-web \
  --network=default \
  --direction=INGRESS \
  --action=ALLOW \
  --rules=tcp:80,tcp:443,udp:443 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=voice-agent-web
```

Do not expose ports 8000 or 5173. FastAPI is reachable only through Caddy, and browser media connects directly to LiveKit Cloud.

## 3. Point DNS to the VM

Create an `A` record with your DNS provider:

```text
voice.example.com  A  STATIC_IP
```

Wait until the record resolves publicly before starting Caddy:

```bash
dig +short voice.example.com
```

The returned address must match the reserved VM address.

## 4. Install Docker on the VM

Connect to the instance:

```bash
gcloud compute ssh voice-agent-vm --zone=asia-south1-a
```

Install Docker Engine and the Compose plugin from Docker's Ubuntu repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

## 5. Copy the application and configure secrets

Clone the repository on the VM. Use your private repository URL:

```bash
git clone YOUR_PRIVATE_REPOSITORY_URL voice_agent
cd voice_agent
cp .env.example backend/.env
chmod 600 backend/.env
nano backend/.env
```

At minimum, replace these values:

```dotenv
APP_DOMAIN=voice.example.com

LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
LIVEKIT_AGENT_NAME=general-assistant
LIVEKIT_LLM_MODEL=google/gemini-2.5-flash-lite

DEEPGRAM_API_KEY=your-deepgram-api-key
BACKEND_API_TOKEN=generate-a-long-random-value

CARTESIA_VOICE_ID=your-cartesia-voice-id
```

Generate `BACKEND_API_TOKEN` on the VM:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

For outbound telephone calls, also configure:

```dotenv
SIP_OUTBOUND_TRUNK_ID=ST_your-current-livekit-project-trunk
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_PHONE_NUMBER=+15105550123
TWILIO_TRIAL_MODE=true
```

The SIP trunk ID must exist in the same LiveKit project as `LIVEKIT_URL` and its API credentials.

## 6. Validate, build, and start

The `--env-file` option supplies `APP_DOMAIN` for Compose interpolation. `config --quiet` validates the Compose model without printing expanded secrets:

```bash
sudo docker compose \
  --env-file backend/.env \
  -f compose.gcp.yml \
  config --quiet

sudo docker compose \
  --env-file backend/.env \
  -f compose.gcp.yml \
  build --pull

sudo docker compose \
  --env-file backend/.env \
  -f compose.gcp.yml \
  up -d
```

Check status and logs:

```bash
sudo docker compose --env-file backend/.env -f compose.gcp.yml ps
sudo docker compose --env-file backend/.env -f compose.gcp.yml logs --tail=100 api
sudo docker compose --env-file backend/.env -f compose.gcp.yml logs --tail=100 agent
sudo docker compose --env-file backend/.env -f compose.gcp.yml logs --tail=100 caddy
```

Expected results:

- `api` becomes `healthy`.
- `agent` logs `registered worker` and remains running while waiting for calls.
- `caddy` obtains a certificate after DNS and firewall propagation.
- `https://voice.example.com/` redirects to `https://voice.example.com/app/`.
- `https://voice.example.com/health` returns an `ok` service response.

## 7. Update the deployment

```bash
cd ~/voice_agent
git pull --ff-only

sudo docker compose --env-file backend/.env -f compose.gcp.yml build --pull
sudo docker compose --env-file backend/.env -f compose.gcp.yml up -d --remove-orphans
sudo docker image prune -f
```

Compose recreates only services whose image or configuration changed. The `app_data` and Caddy volumes remain intact.

## Operations

Follow all logs:

```bash
sudo docker compose --env-file backend/.env -f compose.gcp.yml logs -f
```

Restart only the worker after changing voice-agent settings or provider credentials:

```bash
sudo docker compose --env-file backend/.env -f compose.gcp.yml restart agent
```

Restart both Python services after changing shared LiveKit or backend settings:

```bash
sudo docker compose --env-file backend/.env -f compose.gcp.yml restart api agent
```

Stop the deployment without deleting persistent volumes:

```bash
sudo docker compose --env-file backend/.env -f compose.gcp.yml down
```

Never add `--volumes` to that command unless you intentionally want to delete the SQLite database and Caddy certificate state.

## Production limitations

- This Compose deployment intentionally runs one API instance because SQLite is a single-host database. Move to Cloud SQL/PostgreSQL before horizontally scaling the API.
- The frontend's current local bearer-token bootstrap is suitable for controlled testing, not public multi-user authentication.
- Add rate limiting and authorization before exposing outbound calling to untrusted users.
- Store production credentials in a controlled secret-management workflow rather than a broadly readable file.
- Configure VM monitoring, disk snapshots, log retention, and an application database backup policy.

## Official references

- [Create a Compute Engine VM](https://cloud.google.com/compute/docs/instances/create-start-instance)
- [Google Cloud firewall rules](https://cloud.google.com/firewall/docs/using-firewalls)
- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Install Docker Compose](https://docs.docker.com/compose/install/linux/)
- [Caddy automatic HTTPS](https://caddyserver.com/docs/quick-starts/https)
