# Deploy the voice agent to a Google Cloud VM

This deployment runs two containers on one Compute Engine VM:

```text
Internet ──HTTP :8000───────────────────────────► FastAPI
                                                    │
LiveKit Cloud ◄──────── outbound TLS/WebSocket ── Voice worker
                                                    │
                                                    └──► private speech and voice services
```

The API and agent use the same backend-only image but run as separate processes. The React frontend is not copied into or built by this image. FastAPI is published directly on VM port 8000, and SQLite data is stored in a named Docker volume.

## 1. Prerequisites

- A Google Cloud project with billing and Compute Engine enabled.
- Google Cloud CLI authenticated to that project.
- Working LiveKit Cloud, private speech/voice credentials, and optional Twilio/SIP configuration.
- The application source available from a private Git repository or another secure transfer method.

This setup intentionally has no Caddy or Nginx reverse proxy and therefore does not provide HTTPS. It is suitable for the backend API and phone/SIP calls. A separately hosted browser frontend must use HTTPS and cannot safely call this HTTP API from an HTTPS page because browsers block mixed content.

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
  --rules=tcp:8000 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=voice-agent-web
```

Port 8000 is public in this configuration. Restrict `--source-ranges` to trusted client IPs instead of `0.0.0.0/0` whenever public access is unnecessary.

## 3. Install Docker on the VM

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

## 4. Copy the application and configure secrets

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
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-livekit-api-key
LIVEKIT_API_SECRET=your-livekit-api-secret
LIVEKIT_AGENT_NAME=general-assistant

BACKEND_API_TOKEN=generate-a-long-random-value

# Add the private speech, reasoning, and voice variables required by your worker.
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

## 5. Validate, build, and start

`config --quiet` validates the Compose file without printing expanded secrets:

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
```

Expected results:

- `api` becomes `healthy`.
- `agent` logs `registered worker` and remains running while waiting for calls.
- `http://VM_EXTERNAL_IP:8000/docs` opens the FastAPI documentation.
- `http://VM_EXTERNAL_IP:8000/health` returns an `ok` service response.

## 6. Update the deployment

```bash
cd ~/voice_agent
git pull --ff-only

sudo docker compose --env-file backend/.env -f compose.gcp.yml build --pull
sudo docker compose --env-file backend/.env -f compose.gcp.yml up -d --remove-orphans
sudo docker image prune -f
```

Compose recreates only services whose image or configuration changed. The `app_data` volume remains intact.

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

Never add `--volumes` to that command unless you intentionally want to delete the SQLite database.

## Production limitations

- This Compose deployment intentionally runs one API instance because SQLite is a single-host database. Move to Cloud SQL/PostgreSQL before horizontally scaling the API.
- Deploy the React frontend separately if browser access is required, and configure its API URL and the backend's allowed CORS origin for that deployment.
- Add rate limiting and authorization before exposing outbound calling to untrusted users.
- Store production credentials in a controlled secret-management workflow rather than a broadly readable file.
- Configure VM monitoring, disk snapshots, log retention, and an application database backup policy.
- For a public production browser application, put the API behind a managed HTTPS load balancer or another TLS endpoint before connecting the frontend.

## Official references

- [Create a Compute Engine VM](https://cloud.google.com/compute/docs/instances/create-start-instance)
- [Google Cloud firewall rules](https://cloud.google.com/firewall/docs/using-firewalls)
- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Install Docker Compose](https://docs.docker.com/compose/install/linux/)
