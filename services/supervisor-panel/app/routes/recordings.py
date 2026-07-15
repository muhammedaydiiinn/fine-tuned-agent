"""Recordings — upload call audio, fix the transcript, import for training.

Reads recordings/segments from the shared DB; every mutation is proxied to
agent-backend with the X-API-Key header (same split as the other pages).
Audio files are served straight from the read-only /data mount.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import Recording, RecordingSegment, Turn
from app.ui_feedback import toast_fragment, toast_redirect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

_DATA_ROOT = Path("/data")


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


def _backend(path: str) -> str:
    return f"{settings.agent_backend_url}{path}"


@router.get("/recordings", response_class=HTMLResponse)
def recordings_list(request: Request, db: DBSession = Depends(get_db)):
    rows = db.query(Recording).order_by(Recording.created_at.desc()).limit(200).all()
    return templates.TemplateResponse(
        "recordings.html",
        {"request": request, "recordings": rows},
    )


@router.post("/recordings/upload")
async def upload_recording(
    file: UploadFile = File(...),
    kind: str = Form(...),
    notes: str = Form(""),
    _csrf: None = Depends(require_csrf),
):
    try:
        response = httpx.post(
            _backend("/recordings"),
            files={"file": (file.filename or "recording", file.file, file.content_type or "application/octet-stream")},
            data={"kind": kind, "notes": notes, "uploaded_by": "panel"},
            headers=_headers(),
            timeout=180.0,
        )
        response.raise_for_status()
        recording_id = int(response.json()["id"])
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200]
        logger.warning("Recording upload rejected: %s", detail)
        return toast_redirect("/recordings", f"Upload rejected: {detail}", kind="error", title="Upload failed")
    except Exception as exc:
        logger.exception("Recording upload failed")
        return toast_redirect("/recordings", f"Upload failed: {exc}", kind="error", title="Upload failed")
    return toast_redirect(
        f"/recordings/{recording_id}",
        "Recording uploaded. Transcription starts automatically.",
        title="Recording uploaded",
    )


@router.get("/recordings/{recording_id}", response_class=HTMLResponse)
def recording_detail(recording_id: int, request: Request, db: DBSession = Depends(get_db)):
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if recording is None:
        return HTMLResponse("<h2>Recording not found</h2>", status_code=404)
    segments = (
        db.query(RecordingSegment)
        .filter(RecordingSegment.recording_id == recording_id)
        .order_by(RecordingSegment.idx)
        .all()
    )
    turns = []
    if recording.session_id:
        turns = (
            db.query(Turn)
            .filter(Turn.session_id == recording.session_id)
            .order_by(Turn.turn_index)
            .all()
        )
    evaluations: list[dict] = []
    analysis = dict(recording.analysis_json or {})
    if recording.session_id and analysis.get("judge_status") in ("running", "completed", "failed"):
        try:
            response = httpx.get(
                _backend(f"/recordings/{recording_id}/evaluations"),
                headers=_headers(),
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
            evaluations = payload.get("turns", [])
            analysis = payload.get("analysis", analysis)
        except Exception:
            logger.exception("Could not load recording evaluations")
    return templates.TemplateResponse(
        "recording_detail.html",
        {
            "request": request,
            "recording": recording,
            "segments": segments,
            "turns": turns,
            "evaluations": evaluations,
            "analysis": analysis,
            "unknown_count": sum(1 for s in segments if s.speaker == "unknown"),
        },
    )


@router.get("/recordings/{recording_id}/status", response_class=HTMLResponse)
def recording_status_fragment(recording_id: int, db: DBSession = Depends(get_db)):
    """Polled while transcribing/judging — triggers a full refresh when done."""
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if recording is None:
        return HTMLResponse("", status_code=404)
    judge_status = (recording.analysis_json or {}).get("judge_status")
    if recording.status == "transcribing" or judge_status == "running":
        return HTMLResponse(
            '<div class="alert alert-info"><i class="fa-solid fa-spinner fa-spin"></i> '
            f"Processing… (status: {recording.status}"
            + (f", judge: {judge_status}" if judge_status else "")
            + ")</div>"
        )
    response = HTMLResponse("")
    response.headers["HX-Refresh"] = "true"
    return response


@router.get("/recordings/{recording_id}/audio")
def recording_audio(recording_id: int, db: DBSession = Depends(get_db)):
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    if recording is None or not recording.stored_path:
        return HTMLResponse("Not found", status_code=404)
    resolved = Path(recording.stored_path).resolve()
    try:
        resolved.relative_to(_DATA_ROOT.resolve())
    except ValueError:
        logger.warning("Blocked audio path outside /data: %s", recording.stored_path)
        return HTMLResponse("Forbidden", status_code=403)
    if not resolved.is_file():
        return HTMLResponse("Audio file missing", status_code=404)
    media_types = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".ogg": "audio/ogg", ".flac": "audio/flac", ".opus": "audio/opus",
    }
    return FileResponse(
        resolved,
        media_type=media_types.get(resolved.suffix.lower(), "application/octet-stream"),
        filename=recording.filename,
        content_disposition_type="inline",
    )


@router.post("/recordings/{recording_id}/segments/{segment_id}", response_class=HTMLResponse)
def save_segment(
    recording_id: int,
    segment_id: int,
    request: Request,
    speaker: str = Form(...),
    text: str = Form(...),
    corrected_text: str = Form(""),
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    try:
        response = httpx.patch(
            _backend(f"/recordings/{recording_id}/segments/{segment_id}"),
            json={"speaker": speaker, "text": text, "corrected_text": corrected_text},
            headers=_headers(),
            timeout=15.0,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Segment save failed")
        return toast_fragment("Segment could not be saved.", kind="error", status_code=502)
    segment = (
        db.query(RecordingSegment).filter(RecordingSegment.id == segment_id).first()
    )
    db.refresh(segment)
    recording = db.query(Recording).filter(Recording.id == recording_id).first()
    return templates.TemplateResponse(
        "_recording_segment_row.html",
        {"request": request, "segment": segment, "recording": recording, "saved": True},
    )


def _proxy_action(recording_id: int, path: str, ok_message: str, timeout: float = 120.0):
    try:
        response = httpx.post(
            _backend(f"/recordings/{recording_id}/{path}"),
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200]
        return toast_redirect(
            f"/recordings/{recording_id}", detail or "Action failed.", kind="error", title="Action failed"
        )
    except Exception as exc:
        logger.exception("Recording action %s failed", path)
        return toast_redirect(
            f"/recordings/{recording_id}", f"Action failed: {exc}", kind="error", title="Action failed"
        )
    return toast_redirect(f"/recordings/{recording_id}", ok_message, title="Done")


@router.post("/recordings/{recording_id}/reattribute")
def reattribute(recording_id: int, _csrf: None = Depends(require_csrf)):
    return _proxy_action(recording_id, "reattribute", "Speaker attribution re-run completed.")


@router.post("/recordings/{recording_id}/import")
def import_recording(recording_id: int, _csrf: None = Depends(require_csrf)):
    return _proxy_action(
        recording_id,
        "import",
        "Recording imported as a session — it is now in the Review queue.",
        timeout=300.0,
    )


@router.post("/recordings/{recording_id}/judge")
def judge_recording(recording_id: int, _csrf: None = Depends(require_csrf)):
    return _proxy_action(recording_id, "judge", "Judge analysis started.", timeout=30.0)
