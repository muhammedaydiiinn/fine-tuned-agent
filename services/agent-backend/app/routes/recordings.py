"""Recording upload + transcription lifecycle API.

Upload → transcribe (worker callback or mock) → speaker fix-up → import as
Session → judge/review. Protected by the global X-API-Key middleware.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.core import recording_pipeline
from app.db import get_db, SessionLocal
from app.models import Recording, RecordingSegment, TurnEvaluation, Turn
from app.schemas import (
    RecordingResponse,
    RecordingSegmentResponse,
    SegmentPatchRequest,
    TranscriptCallbackRequest,
)
from app.workers.queue import enqueue_transcribe_job

router = APIRouter()
logger = logging.getLogger(__name__)

_KINDS = ("good_example", "to_correct")

# GPU-free development/CI: TRANSCRIBE_MODE=mock skips the worker queue and
# writes this canned German dialogue as the transcript.
_MOCK_SEGMENTS: list[dict] = [
    {"speaker": "agent", "text": "Guten Tag, mein Name ist Anna Weber von CallShield. Spreche ich mit Herrn Müller?"},
    {"speaker": "customer", "text": "Ja, am Apparat. Worum geht es denn?"},
    {"speaker": "agent", "text": "Es geht um Ihren Schutz vor Betrugsanrufen. Unser Gold Paket blockiert über 7.000 Risikonummern automatisch."},
    {"speaker": "customer", "text": "Und was kostet das?"},
    {"speaker": "agent", "text": "Die ersten 14 Tage kostenlos, danach 29,99 Euro monatlich, jederzeit kündbar."},
    {"speaker": "customer", "text": "Gut, das klingt interessant."},
]


def _allowed_exts() -> set[str]:
    return {e.strip().lower() for e in settings.recording_allowed_exts.split(",") if e.strip()}


def _recording_or_404(db: DBSession, recording_id: int) -> Recording:
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


def _segments_payload(db: DBSession, recording_id: int) -> list[RecordingSegmentResponse]:
    rows = (
        db.query(RecordingSegment)
        .filter(RecordingSegment.recording_id == recording_id)
        .order_by(RecordingSegment.idx)
        .all()
    )
    return [RecordingSegmentResponse.model_validate(row, from_attributes=True) for row in rows]


def _response(db: DBSession, recording: Recording, with_segments: bool = False) -> RecordingResponse:
    payload = RecordingResponse.model_validate(recording, from_attributes=True)
    if with_segments:
        payload.segments = _segments_payload(db, recording.id)
    return payload


@router.post("/recordings", response_model=RecordingResponse, status_code=201)
def upload_recording(
    file: UploadFile = File(...),
    kind: str = Form(...),
    notes: str = Form(""),
    uploaded_by: str = Form(""),
    db: DBSession = Depends(get_db),
):
    if kind not in _KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {_KINDS}")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _allowed_exts():
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}' — allowed: {settings.recording_allowed_exts}",
        )

    recording = Recording(
        filename=(file.filename or f"recording{ext}")[:256],
        kind=kind,
        status="uploaded",
        notes=notes or None,
        uploaded_by=uploaded_by or None,
    )
    db.add(recording)
    db.flush()

    target_dir = Path(settings.recordings_dir) / str(recording.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"original{ext}"
    written = 0
    with target_path.open("wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > settings.recording_max_bytes:
                out.close()
                target_path.unlink(missing_ok=True)
                db.rollback()
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds the {settings.recording_max_bytes // (1024 * 1024)} MB limit",
                )
            out.write(chunk)
    if written == 0:
        target_path.unlink(missing_ok=True)
        db.rollback()
        raise HTTPException(status_code=422, detail="Uploaded file is empty")
    recording.stored_path = str(target_path)

    if settings.transcribe_mode == "mock":
        for idx, seg in enumerate(_MOCK_SEGMENTS):
            db.add(RecordingSegment(
                recording_id=recording.id,
                idx=idx,
                start_ms=idx * 4000,
                end_ms=idx * 4000 + 3500,
                speaker=seg["speaker"],
                text=seg["text"],
                confidence=0.95,
            ))
        recording.status = "transcribed"
        recording.attribution_method = "llm"
        recording.duration_seconds = len(_MOCK_SEGMENTS) * 4.0
        recording.channels = 1
        db.commit()
        db.refresh(recording)
        return _response(db, recording, with_segments=True)

    db.commit()
    db.refresh(recording)
    try:
        enqueue_transcribe_job(recording.id, str(target_path))
        recording.status = "transcribing"
    except Exception as exc:
        recording.status = "failed"
        recording.error_message = f"Failed to enqueue transcription: {exc}"[:1000]
        db.commit()
        logger.exception("Failed to enqueue transcribe job for recording %d", recording.id)
        raise HTTPException(status_code=503, detail="Transcription queue is unavailable") from exc
    db.commit()
    db.refresh(recording)
    return _response(db, recording)


@router.get("/recordings", response_model=list[RecordingResponse])
def list_recordings(db: DBSession = Depends(get_db)):
    rows = db.query(Recording).order_by(Recording.created_at.desc()).limit(200).all()
    return [_response(db, row) for row in rows]


@router.get("/recordings/{recording_id}", response_model=RecordingResponse)
def get_recording(recording_id: int, db: DBSession = Depends(get_db)):
    recording = _recording_or_404(db, recording_id)
    return _response(db, recording, with_segments=True)


@router.post("/recordings/{recording_id}/transcript", response_model=RecordingResponse)
def transcript_callback(
    recording_id: int,
    body: TranscriptCallbackRequest,
    db: DBSession = Depends(get_db),
):
    """Worker callback with the finished transcript (or a failure)."""
    recording = _recording_or_404(db, recording_id)

    if body.error:
        recording.status = "failed"
        recording.error_message = body.error[:1000]
        db.commit()
        return _response(db, recording)

    db.query(RecordingSegment).filter(RecordingSegment.recording_id == recording.id).delete()
    speakers_known = True
    for seg in body.segments:
        speaker = (seg.speaker or "unknown").lower()
        if speaker not in ("agent", "customer"):
            speaker = "unknown"
            speakers_known = False
        db.add(RecordingSegment(
            recording_id=recording.id,
            idx=seg.idx,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            speaker=speaker,
            text=seg.text,
            confidence=seg.confidence,
        ))
    recording.duration_seconds = body.duration_seconds
    recording.channels = body.channels
    recording.error_message = None
    db.flush()

    if speakers_known and body.segments:
        recording.attribution_method = "stereo"
    else:
        recording.attribution_method = "llm"
        _run_llm_attribution(db, recording)
    recording.status = "transcribed"
    db.commit()
    return _response(db, recording, with_segments=True)


def _run_llm_attribution(db: DBSession, recording: Recording) -> None:
    """Best-effort LLM speaker attribution — failures leave 'unknown'."""
    rows = (
        db.query(RecordingSegment)
        .filter(RecordingSegment.recording_id == recording.id)
        .order_by(RecordingSegment.idx)
        .all()
    )
    unknown = [r for r in rows if r.speaker == "unknown"]
    if not unknown:
        return
    try:
        mapping = recording_pipeline.attribute_speakers(
            [{"idx": r.idx, "text": r.corrected_text or r.text} for r in rows]
        )
    except Exception:
        logger.exception("LLM speaker attribution failed for recording %d", recording.id)
        return
    for row in unknown:
        speaker = mapping.get(row.idx)
        if speaker:
            row.speaker = speaker


@router.patch("/recordings/{recording_id}/segments/{segment_id}", response_model=RecordingSegmentResponse)
def patch_segment(
    recording_id: int,
    segment_id: int,
    body: SegmentPatchRequest,
    db: DBSession = Depends(get_db),
):
    segment = (
        db.query(RecordingSegment)
        .filter(RecordingSegment.id == segment_id, RecordingSegment.recording_id == recording_id)
        .first()
    )
    if segment is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    if body.speaker is not None:
        if body.speaker not in ("agent", "customer", "unknown"):
            raise HTTPException(status_code=422, detail="speaker must be agent|customer|unknown")
        segment.speaker = body.speaker
    if body.text is not None:
        segment.text = body.text
    if body.corrected_text is not None:
        segment.corrected_text = body.corrected_text.strip() or None
    db.commit()
    db.refresh(segment)
    return RecordingSegmentResponse.model_validate(segment, from_attributes=True)


@router.post("/recordings/{recording_id}/reattribute", response_model=RecordingResponse)
def reattribute(recording_id: int, db: DBSession = Depends(get_db)):
    recording = _recording_or_404(db, recording_id)
    if recording.status not in ("transcribed", "imported"):
        raise HTTPException(status_code=409, detail="Recording has no transcript yet")
    db.query(RecordingSegment).filter(
        RecordingSegment.recording_id == recording.id
    ).update({"speaker": "unknown"})
    _run_llm_attribution(db, recording)
    recording.attribution_method = "llm"
    db.commit()
    return _response(db, recording, with_segments=True)


@router.post("/recordings/{recording_id}/import")
def import_recording(recording_id: int, db: DBSession = Depends(get_db)):
    recording = _recording_or_404(db, recording_id)
    if recording.session_id is not None:
        raise HTTPException(status_code=409, detail="Recording is already imported")
    if recording.status != "transcribed":
        raise HTTPException(status_code=409, detail="Recording is not transcribed yet")
    try:
        result = recording_pipeline.import_recording_as_session(db, recording)
    except recording_pipeline.UnattributedSegmentsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


def _judge_in_background(recording_id: int) -> None:
    db = SessionLocal()
    try:
        recording_pipeline.judge_recording(db, recording_id)
    finally:
        db.close()


@router.post("/recordings/{recording_id}/judge", status_code=202)
def judge_recording_endpoint(
    recording_id: int,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db),
):
    recording = _recording_or_404(db, recording_id)
    if recording.status != "imported" or recording.session_id is None:
        raise HTTPException(status_code=409, detail="Recording must be imported before judging")
    analysis = dict(recording.analysis_json or {})
    if analysis.get("judge_status") == "running":
        raise HTTPException(status_code=409, detail="Judge analysis is already running")
    analysis["judge_status"] = "running"
    recording.analysis_json = analysis
    db.commit()
    background_tasks.add_task(_judge_in_background, recording_id)
    return {"recording_id": recording_id, "judge_status": "running"}


@router.get("/recordings/{recording_id}/evaluations")
def recording_evaluations(recording_id: int, db: DBSession = Depends(get_db)):
    """Per-turn judge verdicts for an imported recording (panel display)."""
    recording = _recording_or_404(db, recording_id)
    if recording.session_id is None:
        return {"recording_id": recording_id, "analysis": recording.analysis_json or {}, "turns": []}
    rows = (
        db.query(TurnEvaluation, Turn)
        .join(Turn, Turn.id == TurnEvaluation.turn_id)
        .filter(Turn.session_id == recording.session_id, TurnEvaluation.source == "recording")
        .order_by(Turn.turn_index)
        .all()
    )
    return {
        "recording_id": recording_id,
        "analysis": recording.analysis_json or {},
        "turns": [
            {
                "turn_evaluation_id": te.id,
                "turn_id": turn.id,
                "turn_index": turn.turn_index,
                "customer_text": turn.customer_text,
                "agent_response": turn.agent_response,
                "overall": te.overall,
                "scores": te.scores_json,
                "suggestion": te.suggestion,
                "rationale": te.rationale,
                "passed": te.passed,
                "status": te.status,
            }
            for te, turn in rows
        ],
    }
