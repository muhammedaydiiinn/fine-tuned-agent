# Fine-Tuned Agent

An end-to-end platform for a **fine-tuned, German-speaking outbound voice sales agent**:
model training, evaluation, a real-time voice runtime, and a supervisor panel — plus a
continuous fine-tuning loop that turns human corrections into new model versions.

It is designed to run on a dedicated GPU server, but every service also runs on a
GPU-less machine in **mock mode** (`VLLM_MODE=mock`) for local development.

> The demo scenario is built around a **fictional product (CallShield Gold Paket)**.
> Swap the product facts, prompts and persona for your own use case.

---

## Architecture

```text
Browser / Voice UI
      │
Nginx (reverse proxy, 80/443)
      │
Agent Backend API  ←→  Supervisor Panel
      │
State Manager + Guardrails + Correction Memory
      │
vLLM Model Server  (skipped in mock mode)
      │
JSON policy output → Response Repair + Runtime Safety Rules → Agent response

Redis Stack  ←→  Training Worker  ←→  Eval Worker  ←→  Model Registry
```

- **Agent Backend** — FastAPI service that runs the turn pipeline: state, prompt
  building, guardrails, model call, JSON repair and runtime safety rules.
- **Supervisor Panel** — Jinja2/HTMX admin UI to review sessions, submit corrections,
  drive the training/eval/deploy pipeline and run browser voice tests.
- **Voice Runtime** — LiveKit-based real-time pipeline (streaming STT/TTS,
  turn-taking, barge-in).
- **Training / Eval Workers** — build candidate models from corrections, evaluate them
  behind a quality gate, and manage blue/green deploy & rollback.

---

## Quick start (local, no GPU)

```bash
# 1. Configure the environment for local mock mode
cp .env.example .env
# In .env set: VLLM_MODE=mock, TRAINING_MODE=mock, WHISPER_DEVICE=cpu

# 2. Start the core services
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build

# 3. (Optional) start the mock training/eval workers
docker compose -f docker-compose.yml -f docker-compose.local.yml \
  --profile workers up -d training-worker eval-worker

# 4. Health check
curl http://localhost:8010/health
# → {"status":"ok","db":true,"redis":true,"vllm_mode":"mock"}

# 5. Try an agent turn
curl -X POST http://localhost:8010/agent-turn \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-1","customer_text":"Was kostet das?"}'

# API docs
open http://localhost:8010/docs
```

---

## Production (GPU server)

```bash
# Install host dependencies (Docker, NVIDIA Container Toolkit, UFW, fail2ban, dirs)
bash infra/scripts/install_host_dependencies.sh

# Verify the GPU is visible on the host and inside Docker
bash infra/scripts/check_gpu.sh

# Configure production secrets in .env, then start everything (core + vLLM + GPU workers)
docker compose up -d --build
```

Values to change for production in `.env`:

```env
ENVIRONMENT=production
VLLM_MODE=real
POSTGRES_PASSWORD=<strong-password>
ADMIN_PASSWORD=<strong-password>
API_KEY=<openssl rand -hex 32>
EVAL_INTERNAL_TOKEN=<openssl rand -hex 32>
JWT_SECRET=<openssl rand -hex 32>
MODEL_MERGED_PATH=/opt/fine-tuned-agent/models/merged/fine-tuned-agent-v14
```

---

## Models

The platform uses two models. **Neither is included in this repository** — bring your own.

| Model | Source | Target folder | Role |
|-------|--------|---------------|------|
| Fine-tuned LLM (`fine-tuned-agent-v14`) | Your own trained model | `models/merged/fine-tuned-agent-v14/` | Main sales agent (served by vLLM) |
| `whisper-large-v3-turbo-german-ct2` | HuggingFace (CTranslate2) | `models/whisper/whisper-large-v3-turbo-german-ct2/` | German speech-to-text |

`infra/scripts/download_models.sh` fetches both (set `GDRIVE_FOLDER_ID` for the LLM and
it pulls Whisper from HuggingFace). Expected layout:

```
models/
├── merged/
│   └── fine-tuned-agent-v14/                 # LLM loaded by vLLM
│       ├── config.json
│       ├── tokenizer.json
│       └── model-0000X-of-000XX.safetensors
└── whisper/
    └── whisper-large-v3-turbo-german-ct2/    # Whisper STT (CTranslate2)
        ├── config.json
        └── model.bin
```

---

## Services & ports

| Service          | Port                    | Exposure   | Notes                        |
|------------------|-------------------------|------------|------------------------------|
| nginx            | 80 / 443                | Public     | Single entry point           |
| agent-backend    | 8010                    | Internal   | FastAPI                      |
| supervisor-panel | 8020                    | Internal   | Admin UI                     |
| LiveKit          | 7880/7881 TCP, 7882 UDP | WebRTC     | Media server                 |
| vLLM             | 8000                    | Internal   | `real` mode only             |
| PostgreSQL       | 5432                    | Internal   | —                            |
| Redis Stack      | 6379                    | Internal   | —                            |

> In production, expose only nginx. Remove the `ports` lines for `agent-backend` and
> `supervisor-panel` from `docker-compose.yml` — they are published only for convenience
> during development.

Browser voice testing is part of the Supervisor Panel: `Sessions → Start Voice Test`
picks a scenario, starts the microphone, and shows the conversation, latency and steps
on the same session screen.

---

## API

```
GET  /health                 System health
POST /sessions               Create a session
GET  /sessions/{id}          Session details
GET  /sessions/{id}/turns    Turns for a session
POST /agent-turn             Main agent call (full turn pipeline)
GET  /docs                   Swagger UI
```

When `API_KEY` is set, send it on every request:

```bash
curl -H "X-API-Key: <api_key>" http://localhost:8010/health
```

---

## Configuration

All variables are documented inline in [`.env.example`](./.env.example). Key ones:

| Variable | Description |
|----------|-------------|
| `VLLM_MODE` | `mock` (local) or `real` (GPU server) |
| `API_KEY` | If empty, the API-key check is skipped; set it in production |
| `EVAL_INTERNAL_TOKEN` | Inter-service token for candidate-eval routing |
| `POSTGRES_PASSWORD` | Database password — change in production |
| `MODEL_MERGED_PATH` | LLM folder, e.g. `models/merged/fine-tuned-agent-v14` |
| `WHISPER_MODEL_PATH` | Whisper STT model folder |
| `LIVEKIT_PUBLIC_URL` | LiveKit WebSocket URL the browser connects to |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit token signing |
| `FISH_API_KEY` | Streaming TTS access key |
| `JWT_SECRET` | Signs supervisor-panel session cookies |

---

## Backup & restore

```bash
# Manual backup
bash infra/scripts/backup_postgres.sh

# Scheduled nightly backup (cron, 02:00)
bash infra/scripts/setup_cron_backup.sh

# Restore from a backup
bash infra/scripts/restore_postgres.sh \
  /opt/fine-tuned-agent/backups/fine_tuned_agent_YYYYMMDD_HHMMSS.sql.gz
```

Backups are written to `/opt/fine-tuned-agent/backups/`; files older than 30 days are
removed automatically.

---

## Documentation

- [`docs/SETUP.md`](./docs/SETUP.md) — full setup guide
- [`docs/SYSTEM_REFERENCE.md`](./docs/SYSTEM_REFERENCE.md) — technical reference
- [`docs/MILESTONES.md`](./docs/MILESTONES.md) — scope, dependencies and acceptance criteria
- [`docs/TESTING.md`](./docs/TESTING.md) — testing guide
