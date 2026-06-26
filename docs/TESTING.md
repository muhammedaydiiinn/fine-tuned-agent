# Testing Guide

Each service keeps its tests next to its own code. The root script is the single entry point.

## Run All Tests

```bash
bash scripts/run_unit_tests.sh
```

Runs six suites in order:

| Suite | Runner | Path |
|-------|--------|------|
| agent-backend | pytest | `services/agent-backend/tests/` |
| voice-runtime | pytest | `services/voice-runtime/tests/` |
| training-worker | pytest | `services/training-worker/tests/` |
| eval-worker | pytest | `services/eval-worker/tests/` |
| supervisor-panel | pytest | `services/supervisor-panel/tests/` |
| supervisor-panel (JS) | node --test | `services/supervisor-panel/tests/node/` |

## Run a Single Service

```bash
# Python
PYTHONPATH=services/<name> python3 -m pytest -q services/<name>/tests

# Node
node --test services/supervisor-panel/tests/node/*.test.js
```

## Test Map

### agent-backend (`services/agent-backend/tests/`)

| File | What it covers |
|------|----------------|
| `test_m6_hardening.py` | Model registry deploy/rollback hardening |
| `test_review_compiler.py` | Deterministic M12 instruction classification and approved templates |
| `test_voice_events.py` | Voice event persistence and retrieval |

### voice-runtime (`services/voice-runtime/tests/`)

| File | What it covers |
|------|----------------|
| `test_pipeline.py` | Turn-taking, barge-in probe, supervisor commands, backchannel classification |
| `test_backend_client.py` | Circuit breaker state machine (open/half-open/reset) |
| `test_stt.py` | STT error wrapping (model load failure, transcription failure) |
| `test_tts.py` | `pace_to_speed` mapping, TTS fallback behaviour |
| `test_segmenter.py` | VAD segmenter logic |
| `test_turn_taking.py` | Turn-taking scenario catalogue |

### training-worker (`services/training-worker/tests/`)

| File | What it covers |
|------|----------------|
| `test_build_dataset.py` | Dataset validation, candidate scoping and manifests |
| `test_artifacts.py` | Atomic candidate publication, checksum, rollback and backup cleanup |
| `test_worker_publication.py` | ModelVersion commit success/failure coordination with candidate publication |

### supervisor-panel (`services/supervisor-panel/tests/`)

| File | What it covers |
|------|----------------|
| `test_ui_feedback.py` | Panel UI feedback and toast notifications |
| `test_review_compiler.py` | Accepted compiler correction types and safe fallback |
| `test_voice_actions.py` | Stop-agent and replace-answer action routing |
| `test_voice_observability.py` | `build_voice_health`, `build_recent_voice_turns`, `build_voice_acceptance` aggregation |

### supervisor-panel JS (`services/supervisor-panel/tests/node/`)

| File | What it covers |
|------|----------------|
| `voice-session-recovery.test.js` | Recovery state machine: connect/disconnect tracking, retry limit, exponential backoff delays |

## Live Acceptance Tests

Manual tests that require a real GPU, real Whisper, and Fish Audio TTS are documented in
`docs/LIVE_ACCEPTANCE.md`. These cannot run in CI — they are performed on the GPU host.
