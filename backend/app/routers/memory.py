"""Digital memory upload endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import user as user_crud
from app.database import get_db
from app.schemas.memory import MemoryUploadCreate, MemoryUploadOut
from app.services.rag_service import add_memory


router = APIRouter(prefix="/api/users", tags=["memory"])


@router.post(
    "/{user_id}/memory/upload",
    response_model=MemoryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_user_memory(
    user_id: int,
    memory_in: MemoryUploadCreate,
    db: Session = Depends(get_db),
) -> MemoryUploadOut:
    """Store long-form user memories as local vector chunks."""
    db_user = user_crud.get_user(db, user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    if not memory_in.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Memory content cannot be empty.",
        )

    try:
        chunks_added = add_memory(user_id=user_id, text=memory_in.content.strip())
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store memory in the local vector database.",
        ) from exc

    return MemoryUploadOut(message="Memory uploaded.", chunks_added=chunks_added)
