# Milestone 1–6 Audit — 2026-06-19

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

The canonical scope and remaining milestones are defined in `MILESTONES.md`.
