"""Research data export endpoints."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.security import require_admin_key
from app.services.export_service import (
    export_chatlogs_to_jsonl,
    export_feedback_to_jsonl,
)


router = APIRouter(tags=["export"])


def _jsonl_response(content: str, filename: str) -> Response:
    """Return JSONL text as a downloadable attachment."""
    return Response(
        content=content,
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/api/export/{user_id}/chatlogs")
def export_user_chatlogs(
    user_id: int,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> Response:
    """Download one user's private chat logs as SFT JSONL."""
    content = export_chatlogs_to_jsonl(db=db, user_id=user_id)
    return _jsonl_response(content, f"loop_user_{user_id}_chatlogs.jsonl")


@router.get("/api/export/{user_id}/feedbacks")
def export_user_feedbacks(
    user_id: int,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> Response:
    """Download one user's correction feedback as SFT JSONL."""
    content = export_feedback_to_jsonl(db=db, user_id=user_id)
    return _jsonl_response(content, f"loop_user_{user_id}_feedbacks.jsonl")
