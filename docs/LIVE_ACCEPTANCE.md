# Live Acceptance Tests (GPU Host)

This file lists acceptance criteria that cannot be verified by mock/unit tests — they require
real audio, GPU Whisper, and Fish Audio TTS. Updated after each milestone.

---

## M7 — Browser Voice Foundation

### Prerequisites

1. Configure the acceptance environment in `.env`:

   ```env
   LIVEKIT_PUBLIC_URL=wss://<voice-host>
   LIVEKIT_API_KEY=<generated API key>
   LIVEKIT_API_SECRET=<at least 32 characters>
   WHISPER_DEVICE=cuda
   WHISPER_COMPUTE_TYPE=float16
   TTS_MODE=fish
   FISH_API_KEY=<secret>
   FISH_TTS_REFERENCE_ID=<approved German voice>
   ```

2. Verify Whisper model is present at `WHISPER_MODEL_PATH`.
3. Verify TCP `7880/7881` and UDP `7882` access.
4. Check `voice-runtime-worker` logs for worker registration and model load success.

### Functional Acceptance

- Open a session via `Sessions → Start Voice Test` in the Supervisor Panel.
- Complete at least 10 consecutive turns without typing.
- Verify each customer sentence appears as a final transcript in the conversation flow.
- Verify the heard response matches `agent_response` for the same turn.
- Verify turn indexes advance from 0 to 9 without gaps.
- Use at least three sentences containing German numbers, prices, and product names.
- Browser reconnect / page reload is NOT a criterion here; it is covered in M8.

### Latency Acceptance

For the last 10 voice turns:

```sql
SELECT
  percentile_cont(0.95) WITHIN GROUP (ORDER BY lm.value_ms) AS p95_ms,
  count(*) AS turn_count
FROM latency_metrics lm
JOIN sessions s ON s.id = lm.session_id
WHERE lm.metric_name = 'speech_end_to_first_audio_ms'
  AND s.external_session_id = '<browser-session-id>';
```

Acceptance:

- `turn_count >= 10`
- Speech end → first audio `p95_ms < 2500`
- No voice metrics request rejected due to transcript/response mismatch

### Result Record

Record test date, commit SHA, GPU, Whisper model, Fish model/reference ID,
browser version, session ID, p50/p95 values, and notes on any failed turns here
or in a separate dated operations log.

Current local verification:

- Voice runtime image build: passed
- LiveKit server/worker registration: passed
- Token-based room creation and named-agent dispatch: passed
- Backend turn + voice metrics persistence smoke test: passed
- Real microphone + Whisper + Fish Audio 10-turn acceptance: **pending on GPU host**

---

## M8 — Interruption Hardening

Unit tests: 43/43 passed. The following are verified manually on the GPU host.

### Environment

`.env` values to update:

```bash
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
VLLM_MODE=real
```

Start the required services (GPU profile is not needed for audio testing alone):

```bash
docker compose up -d postgres redis livekit-server agent-backend supervisor-panel voice-runtime-worker
```

Seed:

```bash
POSTGRES_PASSWORD=123456 POSTGRES_USER=anrufblocker POSTGRES_DB=anrufblocker \
  python3 tests/seed_test_data.py
```

### Test 1 — Barge-in Latency Baseline

**Goal:** Verify that `interruption_latency_ms` produces realistic values.

**Steps:**
1. Open a voice session in the panel.
2. Interrupt the agent while it is speaking ("Moment, was kostet das?").
3. After the turn completes, read the `interruption_latency_ms` metric in session detail.

**Acceptance:** `interruption_latency_ms` < 600ms.
If > 1000ms, there is probe lag or STT thread contention — lower `barge_in_min_ms`.

### Test 2 — Multi-token Backchannel

**Goal:** Verify that two-token acknowledgements such as "ja ja" and "mhm okay" do not stop the agent.

**Steps:**
1. Say "ja ja" while the agent is speaking.
2. The agent should continue without interruption.
3. The last event in the panel should be `backchannel_detected`, not `interruption_detected`.

**Phrases to test:**

| Utterance | Expected |
|-----------|----------|
| "ja ja" | backchannel |
| "mhm okay" | backchannel |
| "ja genau" | backchannel |
| "alles klar ja" | backchannel |
| "ja aber nein" | interruption (agent must stop) |
| "okay aber warum" | interruption |

**Risk:** STT may transcribe "ja ja" as "Jaja" or "ja, ja". Punctuation is normalised,
but if an unexpected form appears, add it to `turn_taking_scenarios.jsonl`.

### Test 3 — Adaptive VAD (Optional)

**Goal:** Measure false barge-in rate in the presence of background noise.

Add to `.env`:

```bash
SPEECH_ADAPTIVE_VAD=true
```

**Steps:**
1. Stay silent in an environment with keyboard noise or room noise while the agent speaks.
2. The agent must not be interrupted (no false barge-in).
3. When you actually speak, the agent must stop.

**Acceptance:** No `speech_started` event while silent.
If false positives occur, increase `SPEECH_NOISE_FLOOR_MARGIN` (default 2.5).

### Test 4 — Partial Transcript Early Cancel (Optional)

**Goal:** Measure whether partial-transcript barge-in fires earlier than the probe.

Add to `.env`:

```bash
ENABLE_PARTIAL_TRANSCRIPTS=true
PARTIAL_INTERVAL_MS=300
EARLY_INTERRUPT_MIN_SPEECH_MS=500
```

**Steps:**
1. Speak for more than 500ms while the agent is delivering a long sentence.
2. Compare `interruption_latency_ms` with the Test 1 baseline.
3. Docker logs should contain `barge-in triggered ... source=partial`.

**Risk:** If GPU Whisper cannot decode at 300ms intervals under CPU contention,
set `PARTIAL_INTERVAL_MS=600` or disable the feature entirely. If partial text
flickers across intervals, `EARLY_INTERRUPT_MIN_SPEECH_MS` may need to increase.

### Panel Checklist

After each test, verify in the panel:

- [ ] `interruption_latency_ms` metric cell is populated
- [ ] Barge-in counter visible in the voice events header ("N barge-ins")
- [ ] `backchannel_detected` event shown in accent colour (not green)
- [ ] `interruption_detected` event shown in red

---

## Upcoming Milestones

M9 (live supervisor control) acceptance tests will be added here.
