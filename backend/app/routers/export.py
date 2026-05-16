"""Research data export endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.database import get_db
from app.security import require_admin
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
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin: object = Depends(require_admin),
) -> Response:
    """Download one user's private chat logs as SFT JSONL."""
    content = export_chatlogs_to_jsonl(db=db, user_id=user_id, branch_id=branch_id)
    return _jsonl_response(
        content,
        f"loop_user_{user_id}_{branch_id}_chatlogs.jsonl",
    )


@router.get("/api/export/by-username/{username}/chatlogs")
def export_username_chatlogs(
    username: str,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin: object = Depends(require_admin),
) -> Response:
    """Download one user's private chat logs by username."""
    db_user = user_crud.get_user_by_username(db, username.strip())
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    content = export_chatlogs_to_jsonl(
        db=db,
        user_id=db_user.id,
        branch_id=branch_id,
    )
    return _jsonl_response(
        content,
        f"loop_user_{db_user.username}_{branch_id}_chatlogs.jsonl",
    )


@router.get("/api/export/{user_id}/feedbacks")
def export_user_feedbacks(
    user_id: int,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin: object = Depends(require_admin),
) -> Response:
    """Download one user's correction feedback as SFT JSONL."""
    content = export_feedback_to_jsonl(db=db, user_id=user_id, branch_id=branch_id)
    return _jsonl_response(
        content,
        f"loop_user_{user_id}_{branch_id}_feedbacks.jsonl",
    )


@router.get("/api/export/by-username/{username}/feedbacks")
def export_username_feedbacks(
    username: str,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin: object = Depends(require_admin),
) -> Response:
    """Download one user's correction feedback by username."""
    db_user = user_crud.get_user_by_username(db, username.strip())
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    content = export_feedback_to_jsonl(
        db=db,
        user_id=db_user.id,
        branch_id=branch_id,
    )
    return _jsonl_response(
        content,
        f"loop_user_{db_user.username}_{branch_id}_feedbacks.jsonl",
    )
