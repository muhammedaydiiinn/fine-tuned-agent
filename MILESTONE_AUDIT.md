# Milestone 1–7 Audit — 2026-06-20

## Result

Milestones 1–3 are complete. Milestones 4–6 meet their current platform goals
in mock mode and are conditionally complete until their GPU/candidate-serving
acceptance gates are verified. The complete
evaluation flow was verified through Docker:

```text
POST /eval-runs
  -> Redis anruf:eval_jobs
  -> eval-worker
  -> POST /agent-turn for 10 single-turn + 5 multi-turn scenarios
  -> eval_runs metrics/progress/log paths
  -> model_versions.eval_status
  -> supervisor panel evaluation list/detail pages
```

The end-to-end run completed 15 scenario groups, wrote the log and result
artifacts, produced every configured metric, and rendered without browser
console errors or DataTables warnings.

## Milestone 1 — Core

Status: complete after audit fixes.

Verified:

- Backend, PostgreSQL, Redis, health endpoint, sessions, turns and agent turns.
- Stateful guardrails and fixed product fact templates.
- Mock/real vLLM switch.
- Correction memory is applied after policy repair and before guardrails.

Audit fixes:

- Turn indexes no longer repeat after the fifth turn.
- Identity is confirmed only from an explicit customer statement, not when the
  agent merely asks for confirmation.
- Mock `free_question` classification is no longer shadowed by the generic
  price keyword check.
- Intent-keyed correction memory now matches the repaired policy intent.

## Milestone 2 — Supervisor Panel

Status: complete.

Verified:

- Sessions, turn details, corrections, training data and training job pages.
- Authentication, declarative DataTables configuration and AJAX data sources.
- No browser console errors or unknown-column warnings on the checked pages.

Audit fixes:

- Internal backend proxy requests now include `X-API-Key` when configured.
- Correction memory records created from the panel include matching context.

## Milestone 3 — Correction and Training Candidates

Status: complete after correction-memory matching fix.

Verified:

- Corrections are traceable to sessions and turns.
- Immediate corrections can affect later policy output.
- Training candidates are generated and exported as JSONL.

## Milestone 4 — Training Worker

Status: conditionally complete for the implemented mock/real pipeline.

Verified:

- Redis job dispatch, progress, logs, dataset build, LoRA train, merge and model
  registration paths completed end-to-end in Docker mock mode.
- Queue failures now mark the database job failed instead of leaving it pending.
- Mock Docker builds install only worker/runtime dependencies; GPU training
  dependencies are isolated in `requirements-gpu.txt`.

Limitation:

- Real GPU/Unsloth training was not executed on this Mac. It still requires the
  target NVIDIA host and model files.
- Real GPU artifacts must be verified against the same manifest and atomic
  publication rules.

## Milestone 5 — Evaluation Worker

Status: conditionally complete.

Implemented:

- Eval run CRUD/log/result endpoints.
- Idempotent `eval_runs` schema upgrade.
- Redis worker with DB progress, logs, atomic result writes and failure states.
- Ten fixed single-turn and five multi-turn scenario groups through `/agent-turn`.
- JSON validity, required-key coverage, next-action accuracy, hard-decline,
  identity-before-link, price, security, loop repetition and latency metrics.
- Quality score and model pass/fail status.
- Evaluation list/detail pages with scenario results and live logs.

Limitation:

- A real isolated candidate vLLM run remains to be executed on the GPU host.

## Milestone 6 — Model Lifecycle and Deployment

Status: conditionally complete.

Verified in Docker mock mode:

- Candidate-specific eval routing and turn-level model version evidence.
- Versioned `m6-gate-v1` deployment checks.
- Artifact verification, approval lifecycle and deployment audit.
- Two sequential deployments followed by rollback.
- Normal agent traffic switched to the deployed model and back after rollback.
- Production configuration rejects mock-only eval evidence.
- Supervisor UI reduced to Sessions, Review & Train and Models workspaces.
- Session review created a candidate-ID-scoped training batch and automatically
  started its quality check.

Limitation:

- Blue/green vLLM serving and rollback require final verification on the target
  NVIDIA host.

## Milestone 7 — Browser Voice Foundation

Status: conditionally complete.

Verified locally:

- LiveKit 1.9.12 server and LiveKit Agents 1.6.2 worker started in Docker.
- The named worker registered and accepted an explicit room dispatch created by
  a browser token.
- Supervisor Sessions UI now owns scenario selection, microphone start/stop,
  transcript/response events, remote audio and latency display.
- The authenticated panel creates the LiveKit room token. The temporary
  standalone voice UI/API and port 8030 were removed.
- Faster Whisper German STT, `/agent-turn`, Fish Audio streaming PCM TTS and
  LiveKit audio publication are connected in one runtime.
- Voice and backend sessions share one external session ID.
- A mock backend turn persisted `stt_ms`, `backend_ms`, `llm_ms`,
  `tts_first_audio_ms` and `total_voice_turn_ms` against the same turn.
- Metric persistence rejects mismatched final transcripts or heard responses.
- Voice runtime unit tests and Docker imports passed.

Limitation:

- The local Whisper directory contains no model and Fish Audio is not
  configured, so the real microphone-to-audio path was not accepted locally.
- The target GPU host must pass the 10-turn browser test and p95
  speech-end-to-first-audio threshold in
  `services/voice-runtime/LIVE_ACCEPTANCE.md`.

The canonical scope and remaining milestones are defined in `MILESTONES.md`.

## Milestone 8 — Realtime Turn-taking and Interruption

Status: conditionally complete for the local/mock implementation.

Implemented and verified:

- Bounded utterance queue replaces the previous overlap-drop behavior.
- Sustained customer speech cancels active agent playback.
- Conservative German backchannel classification avoids treating short
  acknowledgements as new agent turns.
- Duplicate final transcript and stale response guards.
- Durable, idempotent `voice_events` audit records and a live panel timeline.
- Listening, hearing, processing, speaking and interrupted UI states with a
  real microphone level meter.
- Deterministic backchannel/interruption/reconnect test scenarios.

Remaining live acceptance:

- Browser/Fish Audio interruption latency and false-interrupt thresholds need
  a real voice run.
- Text partial hypotheses are not emitted yet; the runtime currently emits
  speech-boundary and final-transcript events.
