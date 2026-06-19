# CallShield Agent Platform — Implementation Prompt

## Implementation Approach

**Milestone bazlı, adım adım ilerleme:**

- **Milestone 1 (Çekirdek):** Docker Compose + Postgres + Redis + FastAPI backend (/health, /sessions, /agent-turn) + guardrails + product-facts + correction-memory. vLLM mock/real geçişli.
- **Milestone 2:** Supervisor panel (FastAPI+Jinja2) — Sessions, Turn detail, Correction editor.
- **Milestone 3:** Correction flow + training candidate pipeline (JSONL export).
- **Milestone 4:** Training worker (Redis job queue, build_dataset, *_dry_run, gerçek LoRA train).
- **Milestone 5:** Eval worker + scenarios.jsonl + metrikler.
- **Milestone 6:** Model registry ekranı + deploy/rollback.
- **Milestone 7:** Voice-runtime adapter docs, observability polish.

**Development language:** All code, variable names, log messages, comments, docstrings, and UI text must be in English. README files may be in Turkish.

---

## Role

You are a senior AI platform engineer and MLOps architect. Build a production-oriented server platform for the CallShield voice sales agent. The system must move beyond Colab/notebook experimentation and become a deployable service stack on a dedicated GPU server.

The first goal is not a public telephone deployment. The first goal is a robust server-side platform with:

- vLLM model serving
- FastAPI agent backend
- PostgreSQL persistence
- Redis job queue
- supervisor/control panel
- correction memory / hotfix layer
- training candidate generation
- retraining worker skeleton
- eval worker skeleton
- model registry
- deployment/versioning flow
- future-ready voice runtime adapter for LiveKit/Pipecat

Use clean architecture, Docker where appropriate, and keep GPU driver/runtime concerns explicit.

---

## Current Model Context

The latest model direction is:

```text
Base model family: Qwen3.5-9B
Training method: LoRA / QLoRA with Unsloth
Model output: JSON policy object
Expected serving: merged model through vLLM
```

The model produces JSON like:

```json
{
  "intent": "price_question",
  "emotion": "neutral",
  "risk": "low",
  "next_action": "explain_price",
  "behavior_strategy": "answer_briefly_then_advance",
  "allowed_to_continue": true,
  "agent_response": "Das Gold Paket ist 14 Tage kostenlos. Danach kostet es 29,99 Euro monatlich.",
  "voice_style": {
    "tone": "clear",
    "pace": "normal",
    "confidence": "high"
  }
}
```

Critical product facts must not be left only to the model memory. They must be available from runtime configuration and used by guardrails/templates.

Runtime product facts:

```text
- 14 Tage kostenlos
- danach 29,99 Euro monatlich
- Apple App Store oder Google Play Store
- über 7.000 bekannte Risikonummern
- Unterstützung bei Anwalts- und Gerichtskosten bis zu 2.500 Euro
- Support über die App
```

---

## Target Server Assumption

Assume a dedicated GPU server such as:

```text
GPU: NVIDIA RTX A6000 48GB
RAM: 256GB
CPU: dual Xeon or comparable
OS: Ubuntu 22.04 or 24.04
Storage: SSD/NVMe preferred for model files
```

GPU driver and NVIDIA Container Toolkit are installed on the host. Most services should run via Docker Compose. If Unsloth/training inside Docker becomes unstable, keep the training worker able to run as a host-native Python virtual environment as a documented fallback.

---

## Main Architecture

```text
Browser / future voice runtime
        ↓
Agent Backend API
        ↓
State Manager + Guardrails + Correction Memory
        ↓
vLLM Model Server
        ↓
JSON Policy Output
        ↓
Response Repair + Runtime Safety Rules
        ↓
Agent Response

Supervisor Panel
        ↔
Agent Backend
        ↔
Correction Store
        ↔
Training Queue
        ↔
Training Worker
        ↔
Eval Worker
        ↔
Model Registry
        ↔
vLLM Deployment
```

---

## Repository Layout

Create this project structure:

```text
fine-tuned-agent/               ← proje kök dizini (ayrı alt klasör yok)
  docker-compose.yml
  .env.example
  README.md
  PLAN.md
  .gitignore

  infra/
    nginx/
      nginx.conf
    scripts/
      install_host_dependencies.sh
      backup_postgres.sh
      restore_postgres.sh
      check_gpu.sh

  services/
    agent-backend/
      Dockerfile
      requirements.txt
      app/
        main.py
        config.py
        db.py
        schemas.py
        models.py
        routes/
          health.py
          sessions.py
          agent_turn.py
          corrections.py
          training.py
          models.py
          evals.py
        core/
          prompt_builder.py
          state_manager.py
          guardrails.py
          json_repair.py
          product_facts.py
          correction_memory.py
          vllm_client.py
          latency.py
        workers/
          queue.py

    supervisor-panel/
      Dockerfile
      requirements.txt
      app/
        main.py
        routes/
          sessions.py
          turns.py
          corrections.py
          registry.py
          evals.py
        templates/
          base.html
          sessions.html
          session_detail.html
          turn_detail.html
          corrections.html
          training.html
          registry.html
          evals.html
        static/
          style.css

    training-worker/
      Dockerfile
      requirements.txt
      worker.py
      jobs/
        build_dataset.py
        train_lora.py
        merge_model.py
        export_model.py

    eval-worker/
      Dockerfile
      requirements.txt
      worker.py
      evals/
        scenarios.jsonl
        run_eval.py
        metrics.py

    voice-runtime/
      README.md
      adapters/
        livekit_adapter.md
        pipecat_adapter.md

  models/
    base/
    lora/
    merged/
    candidates/
    approved/

  data/
    datasets/
    feedback/
    training_candidates/
    eval_results/
    logs/
    backups/
```

---

## Docker Compose Services

Implement or stub these services:

```text
nginx
postgres
redis
agent-backend
supervisor-panel
vllm-server
training-worker
eval-worker
```

### Ports

```text
80/443    nginx
8010      agent-backend internal
8020      supervisor-panel internal (FastAPI+Jinja2)
8000      vLLM internal
5432      postgres internal
6379      redis internal
```

Only Nginx should be publicly exposed initially. Internal services should communicate over Docker network.

---

## Environment Variables

Create `.env.example` with:

```env
PROJECT_NAME=fine-tuned-agent
ENVIRONMENT=staging

# vLLM mode: "mock" for local dev (GPU'suz Mac), "real" for GPU server
VLLM_MODE=mock

POSTGRES_DB=fine_tuned_agent
POSTGRES_USER=fine_tuned_agent
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0

VLLM_BASE_URL=http://vllm-server:8000/v1
VLLM_MODEL_NAME=fine-tuned-agent-v14

MODEL_ACTIVE_VERSION=fine-tuned-agent-v14
MODEL_DIR=/models
MODEL_MERGED_PATH=/models/merged/fine-tuned-agent-v14

FISH_API_KEY=
FISH_TTS_REFERENCE_ID=446c1dd88d4c4261989e6e000a716337

JWT_SECRET=change_me
ADMIN_USER=admin
ADMIN_PASSWORD=change_me

LOG_LEVEL=INFO
```

---

## Database Schema

Create SQLAlchemy models and migrations or a simple initial SQL schema for:

### `sessions`

```text
id
external_session_id
status
current_stage
current_goal
state_json
created_at
updated_at
```

### `turns`

```text
id
session_id
turn_index
customer_text
agent_response
intent
emotion
risk
next_action
allowed_to_continue
state_before_json
state_after_json
raw_model_json
repaired_model_json
latency_json
model_version
created_at
```

### `corrections`

```text
id
session_id
turn_id
correction_type
old_agent_response
corrected_agent_response
old_next_action
corrected_next_action
notes
apply_immediately
send_to_training
approved
created_by
created_at
```

### `correction_memory`

```text
id
trigger_key
context_json
correct_response
correct_next_action
priority
active
source_correction_id
created_at
updated_at
```

### `training_candidates`

```text
id
source_type
source_id
messages_json
metadata_json
approved
exported
created_at
```

### `training_jobs`

```text
id
job_type
status
input_json
output_json
logs_path
progress_current
progress_total
error_message
created_at
started_at
finished_at
```

### `model_versions`

```text
id
version_name
base_model
lora_path
merged_path
dataset_version
eval_status
deployment_status
metadata_json
created_at
```

### `eval_runs`

```text
id
model_version_id
status
metrics_json
results_path
created_at
finished_at
```

### `deployments`

```text
id
model_version_id
environment
status
deployed_at
rollback_model_version_id
```

### `latency_metrics`

```text
id
session_id
turn_id
metric_name
value_ms
created_at
```

---

## Agent Backend Requirements

Implement `POST /agent-turn`.

Input:

```json
{
  "session_id": "session-123",
  "customer_text": "Was kostet das?"
}
```

Backend flow:

1. Load or create session.
2. Load current state.
3. Check correction memory.
4. Build prompt with:
   - system instruction
   - product facts
   - current state
   - last turns
   - correction hints
   - current customer text
5. Call vLLM OpenAI-compatible endpoint.
6. Extract JSON.
7. Repair missing/invalid JSON.
8. Apply runtime guardrails.
9. Apply product fact templates when needed.
10. Update state.
11. Save turn and latency metrics.
12. Return response.

Response:

```json
{
  "session_id": "session-123",
  "customer_text": "Was kostet das?",
  "agent_response": "Das Gold Paket ist 14 Tage kostenlos. Danach kostet es 29,99 Euro monatlich.",
  "policy": {
    "intent": "price_question",
    "next_action": "explain_price",
    "risk": "low",
    "allowed_to_continue": true
  },
  "state": {},
  "latency": {
    "llm_ms": 650,
    "backend_ms": 80,
    "total_ms": 730
  }
}
```

---

## Runtime Guardrails

Implement deterministic rules:

```text
- If hard decline is detected, move toward close_call.
- If hard_decline_count >= 2, force close_call.
- If identity is not confirmed, block send_activation_link.
- If price question is detected, use approved price template.
- If security/virus link concern is detected, use approved security template.
- If same next_action repeats too many times, force a transition strategy.
- If product facts are mentioned, verify against runtime product facts.
- If model JSON is invalid, repair or fallback to safe response.
```

Approved price response:

```text
Das Gold Paket ist 14 Tage kostenlos. Danach kostet es 29,99 Euro monatlich.
```

Approved security response:

```text
Nein, das ist kein Virus-Link. Der Link führt nur zum offiziellen Apple App Store oder Google Play Store.
```

---

## Supervisor Panel Requirements

**Teknoloji:** FastAPI + Jinja2 templates + HTMX (ayrı Node/npm build yoktur).
Supervisor panel, `services/supervisor-panel/` altında bağımsız bir FastAPI uygulamasıdır;
agent-backend DB'sine doğrudan bağlanır (aynı Postgres). Port: 8020.

Build a usable first version. It can be simple, but it must work.

Views:

```text
- Sessions
- Session detail
- Turn detail
- Model JSON viewer
- State viewer
- Latency viewer
- Correction editor
- Training queue
- Model registry
- Eval results
```

Buttons/actions:

```text
- Mark Good
- Mark Bad
- Correct Response
- Save Correction
- Apply Immediately
- Send to Training
- Create Training Candidate
- Start Training Job
- Run Eval
- Mark Model Approved
- Deploy Model
```

---

## Correction Flow

When a correction is created:

1. Save correction in DB.
2. If `apply_immediately=true`, create or update `correction_memory`.
3. If `send_to_training=true`, create `training_candidate`.
4. Show status in panel.

Correction types:

```text
response_correction
policy_correction
product_fact_correction
style_correction
safety_correction
```

---

## Training Candidate Format

Generate JSONL-compatible samples:

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are an CallShield Gold Paket sales policy agent..."
    },
    {
      "role": "user",
      "content": "{\"customer_message\":\"Was kostet das?\",\"state\":{\"offer_terms_explained\":false}}"
    },
    {
      "role": "assistant",
      "content": "{\"intent\":\"price_question\",\"emotion\":\"neutral\",\"risk\":\"low\",\"next_action\":\"explain_price\",\"behavior_strategy\":\"answer_briefly_then_advance\",\"allowed_to_continue\":true,\"agent_response\":\"Das Gold Paket ist 14 Tage kostenlos. Danach kostet es 29,99 Euro monatlich.\",\"voice_style\":{\"tone\":\"clear\",\"pace\":\"normal\",\"confidence\":\"high\"}}"
    }
  ],
  "metadata": {
    "source": "correction",
    "approved": true,
    "model_version": "fine-tuned-agent-v14"
  }
}
```

---

## Training Worker

Implement as a job worker with Redis.

Initial implementation can support:

```text
build_dataset
train_lora_dry_run
merge_model_dry_run
run_eval_dry_run
```

Then implement real training scripts.

The real training flow:

```text
approved training candidates
+ stable golden examples
+ balanced base dataset
→ new dataset version
→ LoRA training
→ adapter output
→ merged_16bit export
→ model registry candidate
```

Keep logs visible in the panel.

---

## Eval Worker

Implement fixed scenario evaluation.

Metrics:

```text
JSON validity
required key coverage
next_action accuracy
hard decline handling
identity-before-link rule pass
price answer correctness
security objection correctness
loop repetition rate
latency average / p95
```

Create `evals/scenarios.jsonl` with at least these German cases:

```text
Was kostet das?
Ist das ein Virus-Link?
Warum haben Sie meine Nummer?
Ich blockiere schon alles.
Ich habe keine Zeit.
Schicken Sie mir das per SMS.
Ich will nichts kaufen.
Ist das kostenlos?
Was passiert nach 14 Tagen?
Können Sie mir das später erklären?
```

---

## Model Registry and Deployment

Support model versions:

```text
fine-tuned-agent-v14-current
fine-tuned-agent-v15-candidate-001
fine-tuned-agent-v15-approved
```

Deployment levels:

```text
staging
production
```

Initial deployment can restart vLLM with a new model path. Later, support blue/green deployment with two vLLM instances.

---

## Voice Runtime Adapter

Do not implement full phone flow in the first infrastructure pass.

Create a clear adapter layer for future browser voice:

```text
voice-runtime/
  adapters/
    livekit_adapter.md
    pipecat_adapter.md
```

Adapter events:

```json
{
  "event": "transcript_final",
  "session_id": "session-123",
  "text": "Was kostet das?"
}
```

```json
{
  "event": "customer_interruption",
  "session_id": "session-123",
  "partial_text": "Moment..."
}
```

```json
{
  "event": "supervisor_interrupt",
  "session_id": "session-123",
  "turn_id": 7
}
```

---

## Security and Ops

Implement basic security:

```text
- Nginx reverse proxy
- HTTPS ready config
- Admin auth for panel
- .env secrets
- Internal Docker network
- Postgres not public
- Redis not public
- vLLM not public directly
- Backup script for Postgres
- Log rotation
```

---

## Deliverables

Produce:

```text
1. Running Docker Compose stack
2. FastAPI backend with /health and /agent-turn
3. PostgreSQL schema
4. Redis queue
5. vLLM service config
6. Supervisor panel v1
7. Correction memory
8. Training candidate generation
9. Training worker skeleton
10. Eval worker skeleton
11. Model registry
12. Deployment docs
13. Daily plan file
14. README with setup commands
```

---

## Engineering Principles

```text
- Do not leave critical business facts only inside the LLM.
- Keep model serving separate from agent runtime.
- Keep correction memory separate from model training.
- Make every model output auditable.
- Make every correction traceable.
- Make every training job reproducible.
- Prefer safe runtime override over unsafe live weight updates.
- Use Docker Compose for service orchestration.
- Keep training host-native fallback documented.
```

---

## Success Criteria

The first platform version is successful when:

```text
- A merged CallShield model can be called through vLLM.
- /agent-turn returns valid stateful policy output.
- Sessions and turns are saved.
- Corrections can be created from panel.
- Corrections can immediately affect later behavior.
- Corrections can become training candidates.
- A training job can be queued and tracked.
- Eval worker can run fixed scenario tests.
- Model versions are visible in registry.
```

