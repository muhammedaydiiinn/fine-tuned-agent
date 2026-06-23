"""Session-centered review and training workflow."""
from __future__ import annotations
import logging

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.csrf import require_csrf
from app.db import get_db
from app.models import (
    Correction,
    Session as SessionModel,
    SessionReview,
    TrainingCandidate,
    TrainingJob,
    Turn,
)
from app.routes.corrections import _build_candidate
from app.ui_feedback import toast_redirect

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    return {"X-API-Key": settings.api_key} if settings.api_key else {}


@router.get("/review", response_class=HTMLResponse)
def review_queue(request: Request, db: DBSession = Depends(get_db)):
    pending_count = (
        db.query(func.count(SessionModel.id))
        .outerjoin(SessionReview, SessionReview.session_id == SessionModel.id)
        .filter(
            SessionModel.status.in_(("closed", "reviewed")),
            or_(
                SessionModel.external_session_id.is_(None),
                ~SessionModel.external_session_id.like("eval-%"),
            ),
            SessionReview.id.is_(None),
        )
        .scalar() or 0
    )
    reviewed_count = (
        db.query(func.count(SessionReview.id)).scalar() or 0
    )
    jobs_count = db.query(func.count(TrainingJob.id)).scalar() or 0
    return templates.TemplateResponse(
        "review_queue.html",
        {
            "request": request,
            "pending_count": pending_count,
            "reviewed_count": reviewed_count,
            "jobs_count": jobs_count,
        },
    )


@router.get("/review/queue/data")
def review_queue_data(db: DBSession = Depends(get_db)):
    """DataTables AJAX source — review queue."""
    sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.status.in_(("closed", "reviewed")),
            or_(
                SessionModel.external_session_id.is_(None),
                ~SessionModel.external_session_id.like("eval-%"),
            ),
        )
        .order_by(SessionModel.updated_at.desc())
        .limit(200)
        .all()
    )
    reviews = {
        r.session_id: r
        for r in db.query(SessionReview).all()
    }
    turn_counts = dict(
        db.query(Turn.session_id, func.count(Turn.id))
        .group_by(Turn.session_id)
        .all()
    )
    rating_cls = {"good": "badge-approved", "bad": "badge-error"}
    rows = []
    for s in sessions:
        review = reviews.get(s.id)
        if review:
            rcls = rating_cls.get(review.rating, "badge-pending")
            review_badge = f'<span class="badge {rcls}">{review.rating}</span>'
        else:
            review_badge = '<span class="badge badge-running">waiting</span>'

        if review and review.training_job_id:
            training = f'<a href="/training-jobs/{review.training_job_id}">Job #{review.training_job_id}</a>'
        elif review and review.candidate_ids_json:
            training = f'{len(review.candidate_ids_json)} approved'
        else:
            training = "—"

        rows.append({
            "id": s.id,
            "session": s.external_session_id or f"#{s.id}",
            "turns": turn_counts.get(s.id, 0),
            "status": s.status,
            "review_badge": review_badge,
            "training": training,
            "updated_at": s.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if s.updated_at else None,
            "action": s.id,
        })
    return {"data": rows}


@router.get("/review/{session_id}", response_class=HTMLResponse)
def review_session(
    session_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return HTMLResponse("Session not found", status_code=404)
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    review = (
        db.query(SessionReview)
        .filter(SessionReview.session_id == session_id)
        .first()
    )
    correction_counts = dict(
        db.query(Correction.turn_id, func.count(Correction.id))
        .filter(Correction.session_id == session_id)
        .group_by(Correction.turn_id)
        .all()
    )
    return templates.TemplateResponse(
        "review_session.html",
        {
            "request": request,
            "session": session,
            "turns": turns,
            "review": review,
            "correction_counts": correction_counts,
        },
    )


@router.post("/review/{session_id}")
def save_review(
    session_id: int,
    rating: str = Form(...),
    notes: str = Form(""),
    add_to_training: bool = Form(False),
    start_training: bool = Form(False),
    db: DBSession = Depends(get_db),
    _csrf: None = Depends(require_csrf),
):
    if rating not in {"good", "mixed", "bad"}:
        return toast_redirect(
            f"/review/{session_id}",
            "Choose a valid review rating before saving.",
            kind="warning",
            title="Review not saved",
        )
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session:
        return toast_redirect(
            "/review",
            "The requested session could not be found.",
            kind="error",
            title="Review unavailable",
        )
    turns = (
        db.query(Turn)
        .filter(Turn.session_id == session_id)
        .order_by(Turn.turn_index.asc())
        .all()
    )
    candidate_ids: list[int] = []
    if add_to_training:
        candidate_ids = _collect_review_candidates(db, session_id, turns, rating)

    review = (
        db.query(SessionReview)
        .filter(SessionReview.session_id == session_id)
        .first()
    )
    if not review:
        review = SessionReview(session_id=session_id)
        db.add(review)
    review.rating = rating
    review.notes = notes.strip() or None
    review.candidate_ids_json = candidate_ids
    review.reviewed_by = settings.admin_user
    session.status = "reviewed"
    db.commit()
    db.refresh(review)

    if start_training:
        if not candidate_ids:
            return toast_redirect(
                f"/review/{session_id}",
                "Review saved, but no eligible turns were available for training.",
                kind="warning",
                title="Nothing to train",
            )
        try:
            response = httpx.post(
                f"{settings.agent_backend_url}/training-jobs",
                json={
                    "session_id": session_id,
                    "candidate_ids": candidate_ids,
                    "dataset_version": f"session-{session_id}-review",
                },
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            review.training_job_id = int(response.json()["id"])
            db.commit()
        except Exception as exc:
            logger.exception("Could not start session review training")
            return toast_redirect(
                f"/review/{session_id}",
                f"Review saved, but training could not start: {exc}",
                kind="error",
                title="Training start failed",
            )
    if start_training and review.training_job_id:
        return toast_redirect(
            "/review",
            f"Review saved and training job #{review.training_job_id} started.",
            title="Training started",
        )
    return toast_redirect(
        "/review",
        "Review saved. Eligible turns are ready for the next training batch.",
        title="Review saved",
    )


def _collect_review_candidates(
    db: DBSession,
    session_id: int,
    turns: list[Turn],
    rating: str,
) -> list[int]:
    candidate_ids: list[int] = []
    for turn in turns:
        corrections = (
            db.query(Correction)
            .filter(Correction.turn_id == turn.id)
            .order_by(Correction.created_at.desc())
            .all()
        )
        latest = corrections[0] if corrections else None
        eligible = rating == "good" or (
            latest is not None
            and latest.correction_type != "mark_bad"
            and bool(latest.corrected_agent_response)
        )
        if not eligible:
            continue

        existing = (
            db.query(TrainingCandidate)
            .filter(
                TrainingCandidate.source_id == turn.id,
                TrainingCandidate.source_type.in_(("correction", "session_review")),
            )
            .order_by(TrainingCandidate.created_at.desc())
            .first()
        )
        if existing:
            existing.approved = True
            metadata = dict(existing.metadata_json or {})
            metadata.update({"session_id": session_id, "review_rating": rating})
            existing.metadata_json = metadata
            candidate_ids.append(existing.id)
            continue

        corrected_response = (
            latest.corrected_agent_response
            if latest and latest.corrected_agent_response
            else turn.agent_response or ""
        )
        corrected_action = (
            latest.corrected_next_action
            if latest and latest.corrected_next_action
            else turn.next_action or ""
        )
        candidate = _build_candidate(
            turn,
            corrected_response,
            corrected_action,
            "session_review",
        )
        candidate.source_type = "session_review"
        metadata = dict(candidate.metadata_json or {})
        metadata.update({"session_id": session_id, "review_rating": rating})
        candidate.metadata_json = metadata
        candidate.approved = True
        db.add(candidate)
        db.flush()
        candidate_ids.append(candidate.id)
    db.commit()
    return candidate_ids
