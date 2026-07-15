"""Uploaded call recordings → transcript segments → Session/Turn import.

The heart of the audio-training feature. A transcribed recording (segments with
speaker attribution) is converted into a regular Session + Turn rows so the
existing review → approve → TrainingCandidate machinery, the corrections flow
and the LLM judge all apply unchanged.

Speaker attribution for mono recordings and per-turn policy inference both use
the production LLM through vllm_client (mock mode works GPU-free).
"""
import json
import logging

from sqlalchemy.orm import Session as DBSession

from app.config import settings

from app.core import json_repair, judge, model_runtime, state_manager, vllm_client
from app.core.product_facts import SYSTEM_OUTPUT_CONTRACT, normalize_next_action
from app.models import Recording, RecordingSegment, Session, Turn, TurnEvaluation

logger = logging.getLogger(__name__)

RECORDING_MODEL_VERSION = "recording_import"

# Chunking for the speaker-attribution prompt: large calls are classified in
# windows; the overlap keeps cross-chunk context, later chunks win on conflict.
_ATTRIBUTION_CHUNK_SIZE = 40
_ATTRIBUTION_CHUNK_OVERLAP = 5

_ATTRIBUTION_SYSTEM = (
    "Du analysierst das Transkript eines Telefon-Verkaufsgesprächs für das "
    "Produkt 'Anrufblocker Gold Paket'. Es gibt genau zwei Sprecher:\n"
    "- 'agent': die Verkäuferin/der Verkäufer (stellt sich vor, z.B. als Anna "
    "Weber von Anrufblocker; erklärt den Anrufschutz, nennt '14 Tage kostenlos' "
    "und '29,99 Euro'; führt durch App-Store/Download-Schritte; stellt Fragen "
    "zur Identität; leitet das Gespräch)\n"
    "- 'customer': die angerufene Person (reagiert, fragt nach Preis/Sicherheit, "
    "äußert Einwände oder Zustimmung)\n\n"
    "Ordne JEDES nummerierte Segment einem Sprecher zu. Kurze Bestätigungen "
    "('ja', 'mhm', 'okay') gehören meist dem Zuhörer des vorherigen Segments.\n"
    "Gib NUR EIN JSON-Objekt zurück, kein Fliesstext:\n"
    '{"segments": [{"idx": 0, "speaker": "agent"}, {"idx": 1, "speaker": "customer"}, ...]}'
)

_INFERENCE_SYSTEM_PREFIX = (
    "Du rekonstruierst die Klassifikation eines BEREITS GESPROCHENEN Turns aus "
    "einem Verkaufsgespräch ('Anna Weber', Anrufblocker Gold Paket). Die "
    "agent_response ist gegeben und darf NICHT umgeschrieben oder neu erfunden "
    "werden — gib sie wortwörtlich zurück. Bestimme nur die Klassifikationsfelder "
    "(intent, emotion, risk, next_action, behavior_strategy, allowed_to_continue) "
    "passend zu Kundennachricht, State und der gegebenen Antwort.\n\n"
)

# Mirror of the price/discount content rules enforced by
# training-worker/jobs/build_dataset.py::_validate_messages. A candidate that
# violates them fails the WHOLE next dataset build, so violations must surface
# as warnings at import time (panel badges) instead of at training time.
_ALLOWED_EURO_AMOUNTS = {29.99, 2500.0}
_PRICE_INTENTS = {"price_question", "free_question", "price_inquiry"}


def validate_training_text(intent: str, response: str) -> list[str]:
    """Return dataset-rule warnings for one agent reply (empty = clean)."""
    import re

    warnings: list[str] = []
    text = (response or "").strip()
    lowered = text.lower()

    for raw in re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:euro|eur|€)", lowered):
        try:
            amount = float(raw.replace(",", "."))
        except ValueError:
            continue
        if amount not in _ALLOWED_EURO_AMOUNTS:
            warnings.append(f"Nicht erlaubter Euro-Betrag: {raw}")

    if (intent or "") in _PRICE_INTENTS:
        if "14 tage kostenlos" not in lowered:
            warnings.append("Preis-Intent ohne '14 Tage kostenlos'")
        if "29,99" not in text and "29.99" not in text:
            warnings.append("Preis-Intent ohne '29,99'")

    if "50% rabatt" in lowered or "50 % rabatt" in lowered:
        warnings.append("Unzulässige Rabatt-Aussage")

    return warnings


# ── Speaker attribution (mono recordings) ────────────────────────────────────

def _attribution_chunk(segments: list[dict]) -> dict[int, str]:
    """One LLM call over a chunk of segments → {idx: speaker}."""
    user_content = json.dumps(
        {"segments": [{"idx": s["idx"], "text": s["text"]} for s in segments]},
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": _ATTRIBUTION_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    target = dict(model_runtime.production_serving_target())
    result: dict[int, str] = {}
    for attempt_messages, temperature in (
        (messages, 0.1),
        (messages + [{"role": "user", "content": "Return ONLY the JSON object."}], 0.0),
    ):
        try:
            raw = vllm_client.chat(
                attempt_messages, target=target, temperature=temperature, max_tokens=2048
            )
            parsed = json_repair.extract_json(raw)
        except Exception:
            logger.warning("speaker attribution LLM call failed", exc_info=True)
            continue
        rows = parsed.get("segments") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            speaker = str(row.get("speaker", "")).strip().lower()
            if speaker in ("agent", "customer") and isinstance(row.get("idx"), int):
                result[row["idx"]] = speaker
        if result:
            return result
    return result


def attribute_speakers(segments: list[dict]) -> dict[int, str]:
    """Classify each segment as agent/customer via the LLM.

    ``segments``: [{"idx": int, "text": str}, ...] in time order. Returns a
    partial mapping — unresolved segments stay 'unknown' (the panel toggle is
    the backstop, per the confirmed design).
    """
    if not segments:
        return {}
    mapping: dict[int, str] = {}
    step = _ATTRIBUTION_CHUNK_SIZE - _ATTRIBUTION_CHUNK_OVERLAP
    for start in range(0, len(segments), step):
        chunk = segments[start:start + _ATTRIBUTION_CHUNK_SIZE]
        # Later chunks win on overlap conflicts: they saw more context.
        mapping.update(_attribution_chunk(chunk))
        if start + _ATTRIBUTION_CHUNK_SIZE >= len(segments):
            break
    return mapping


# ── Per-turn policy inference ────────────────────────────────────────────────

def infer_turn_policy(customer_text: str, agent_response: str, state_before: dict) -> dict:
    """Infer the output-contract classification fields for a spoken turn.

    The spoken agent_response is authoritative: whatever the model echoes is
    overwritten with the verbatim text.
    """
    messages = [
        {"role": "system", "content": _INFERENCE_SYSTEM_PREFIX + SYSTEM_OUTPUT_CONTRACT},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "customer_message": customer_text,
                    "state": state_before,
                    "agent_reply_already_spoken": agent_response,
                },
                ensure_ascii=False,
            ),
        },
    ]
    target = dict(model_runtime.production_serving_target())
    parsed: dict | None = None
    try:
        raw = vllm_client.chat(messages, target=target, temperature=0.1, max_tokens=400)
        candidate = json_repair.extract_json(raw)
        if isinstance(candidate, dict):
            parsed = candidate
    except Exception:
        logger.warning("turn policy inference failed — using conservative defaults", exc_info=True)

    policy = {
        "intent": "unknown",
        "emotion": "neutral",
        "risk": "low",
        "next_action": "continue",
        "behavior_strategy": "recorded",
        "allowed_to_continue": True,
        "voice_style": {"tone": "clear", "pace": "normal", "confidence": "high"},
    }
    if parsed:
        for key in ("intent", "emotion", "behavior_strategy"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                policy[key] = value.strip()
        risk = str(parsed.get("risk", "")).strip().lower()
        if risk in ("low", "medium", "high"):
            policy["risk"] = risk
        next_action = parsed.get("next_action")
        if isinstance(next_action, str) and next_action.strip():
            policy["next_action"] = normalize_next_action(next_action.strip())
        if isinstance(parsed.get("allowed_to_continue"), bool):
            policy["allowed_to_continue"] = parsed["allowed_to_continue"]

    policy["agent_response"] = agent_response
    return policy


# ── Segment pairing ──────────────────────────────────────────────────────────

class UnattributedSegmentsError(ValueError):
    """Raised when segments still have speaker='unknown' at pairing time."""


def _effective_text(segment) -> str:
    corrected = getattr(segment, "corrected_text", None) or (
        segment.get("corrected_text") if isinstance(segment, dict) else None
    )
    text = getattr(segment, "text", None) or (
        segment.get("text") if isinstance(segment, dict) else None
    )
    return (corrected or text or "").strip()


def _speaker(segment) -> str:
    value = getattr(segment, "speaker", None) or (
        segment.get("speaker") if isinstance(segment, dict) else None
    )
    return (value or "unknown").strip().lower()


def pair_segments(segments: list) -> list[tuple[str, str]]:
    """Pair time-ordered segments into (customer_text, agent_response) turns.

    Consecutive same-speaker segments are merged. Customer text accumulates
    until an agent utterance closes the turn; a leading agent utterance yields
    the legitimate empty-customer opening turn. A trailing customer-only tail
    is dropped (there is no reply to learn from). Any remaining 'unknown'
    speaker raises UnattributedSegmentsError.
    """
    merged: list[tuple[str, str]] = []  # (speaker, text)
    for segment in segments:
        text = _effective_text(segment)
        if not text:
            continue
        speaker = _speaker(segment)
        if speaker == "unknown":
            raise UnattributedSegmentsError(
                "Alle Segmente brauchen einen Sprecher (agent/customer), bevor importiert werden kann."
            )
        if merged and merged[-1][0] == speaker:
            merged[-1] = (speaker, f"{merged[-1][1]} {text}")
        else:
            merged.append((speaker, text))

    pairs: list[tuple[str, str]] = []
    customer_buffer = ""
    for speaker, text in merged:
        if speaker == "customer":
            customer_buffer = f"{customer_buffer} {text}".strip() if customer_buffer else text
        else:
            pairs.append((customer_buffer, text))
            customer_buffer = ""
    return pairs


# ── Import as Session + Turns ────────────────────────────────────────────────

def import_recording_as_session(db: DBSession, recording: Recording) -> dict:
    """Convert a transcribed recording into a closed Session with Turn rows.

    status='closed' places the session straight into the existing review queue;
    model_version='recording_import' keeps the turns out of real-log judge
    batches. Dataset-rule violations are recorded per turn as import_warnings.
    """
    segments = (
        db.query(RecordingSegment)
        .filter(RecordingSegment.recording_id == recording.id)
        .order_by(RecordingSegment.idx)
        .all()
    )
    pairs = pair_segments(segments)
    if not pairs:
        raise ValueError("Kayıtta içe aktarılabilir turn bulunamadı (agent segmenti yok).")

    session = Session(
        external_session_id=f"recording-{recording.id}",
        status="closed",
        state_json={},
    )
    db.add(session)
    db.flush()

    all_warnings: list[dict] = []
    state = dict(state_manager.DEFAULT_STATE)
    for index, (customer_text, agent_response) in enumerate(pairs):
        policy = infer_turn_policy(customer_text, agent_response, state)
        warnings = validate_training_text(policy["intent"], agent_response)
        if warnings:
            policy["import_warnings"] = warnings
            all_warnings.append({"turn_index": index, "warnings": warnings})
        new_state = state_manager.update(state, policy, customer_text)
        db.add(Turn(
            session_id=session.id,
            turn_index=index,
            customer_text=customer_text,
            agent_response=agent_response,
            intent=policy["intent"],
            emotion=policy["emotion"],
            risk=policy["risk"],
            next_action=policy["next_action"],
            allowed_to_continue=policy["allowed_to_continue"],
            state_before_json=state,
            state_after_json=new_state,
            final_policy_json=policy,
            model_version=RECORDING_MODEL_VERSION,
        ))
        state = new_state

    session.state_json = state
    session.current_stage = state.get("stage")
    recording.session_id = session.id
    recording.status = "imported"
    db.commit()

    logger.info(
        "Recording %d imported as session %d — %d turns, %d turns with warnings",
        recording.id, session.id, len(pairs), len(all_warnings),
    )
    return {"session_id": session.id, "turns": len(pairs), "warnings": all_warnings}


# ── Judge analysis (mode 1: "learn from a good recording") ───────────────────

def judge_recording(db: DBSession, recording_id: int) -> None:
    """Score every imported turn with the LLM judge; aggregate on the recording.

    Runs in a FastAPI BackgroundTask — owns its own error handling and marks
    progress in recording.analysis_json so the panel can poll.
    """
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if recording is None or recording.session_id is None:
        return
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == recording.session_id)
        .order_by(Turn.turn_index)
        .all()
    )
    recording.analysis_json = {**(recording.analysis_json or {}), "judge_status": "running"}
    db.commit()

    overalls: list[float] = []
    try:
        for turn in turns:
            existing = (
                db.query(TurnEvaluation)
                .filter(TurnEvaluation.turn_id == turn.id, TurnEvaluation.source == "recording")
                .first()
            )
            if existing is not None:
                if existing.overall is not None:
                    overalls.append(existing.overall)
                continue
            verdict = judge.score(
                turn.customer_text,
                turn.state_before_json,
                turn.agent_response,
                turn.final_policy_json,
            )
            db.add(TurnEvaluation(
                turn_id=turn.id,
                source="recording",
                judge_model="production-base",
                scores_json=verdict.get("scores"),
                overall=verdict.get("overall"),
                suggestion=verdict.get("suggestion") or None,
                rationale=verdict.get("rationale") or None,
                passed=verdict.get("passed"),
                status="pending",
                raw_judge_json=verdict.get("raw"),
            ))
            if verdict.get("overall") is not None:
                overalls.append(verdict["overall"])
            db.commit()
    except Exception as exc:
        logger.exception("judge_recording failed — recording=%d", recording_id)
        recording.analysis_json = {
            **(recording.analysis_json or {}),
            "judge_status": "failed",
            "error": str(exc)[:500],
        }
        db.commit()
        return

    mean_overall = round(sum(overalls) / len(overalls), 4) if overalls else None
    passed = sum(1 for value in overalls if value >= settings.judge_pass_threshold)
    recording.analysis_json = {
        **(recording.analysis_json or {}),
        "judge_status": "completed",
        "judged": len(turns),
        "mean_overall": mean_overall,
        "pass_rate": round(passed / len(overalls), 4) if overalls else None,
    }
    db.commit()
