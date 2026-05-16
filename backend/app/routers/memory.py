"""Digital memory upload endpoints."""

import logging
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import user as user_crud
from app.database import get_db
from app import models
from app.schemas.memory import (
    AgentWorkingMemoryOut,
    MemoryConsolidationAcceptedOut,
    MemorySearchCreate,
    MemorySearchOut,
    PersonalizedPostPreviewOut,
    RelationshipOut,
    MemoryUploadCreate,
    MemoryUploadOut,
)
from app.schemas.chat_import import ImportedChatBatchCreate, ImportedChatBatchOut
from app.security import get_current_user, require_same_user
from app.services.consolidation_service import (
    clear_graph_working_memory,
    consolidate_daily_memory,
    inspect_graph_working_memory,
)
from app.services.core_memory_service import normalize_core_memory
from app.services.rag_service import (
    add_agent_chat_memories,
    add_memory,
    retrieve_memory,
    sync_group_chat_memory_access,
)
from app.services.time_machine import TimeMachine


router = APIRouter(tags=["memory"])
logger = logging.getLogger(__name__)

SLEEP_ACCEPTED_MESSAGE = (
    "睡眠巩固已在后台启动，大概需要 1 分钟，请稍后刷新诊断面板查看结果。"
)


def _normalize_branch_id(branch_id: str) -> str:
    return (branch_id or "").strip() or "main"


def _format_core_memory_fields(core_memory: dict[str, str]) -> str:
    lines = [
        f"{key}: {value.strip()}"
        for key, value in core_memory.items()
        if value.strip()
    ]
    return "\n".join(lines)


def _branch_memory_state_payload(
    agent_id: int,
    branch_id: str,
    db: Session,
    current_user: models.User,
) -> dict:
    normalized_branch_id = _normalize_branch_id(branch_id)
    state = inspect_graph_working_memory(
        agent_id=agent_id,
        user_id=current_user.id,
        branch_id=normalized_branch_id,
    )
    reconstructed_state = TimeMachine(db).reconstruct_state(
        agent_id=agent_id,
        target_timestamp=models.utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    core_memory = normalize_core_memory(reconstructed_state.get("core_memory"))
    current_core_memory = str(
        reconstructed_state.get("current_core_memory") or "",
    ).strip()

    if normalized_branch_id == "main" and not current_core_memory:
        core_memory = normalize_core_memory(current_user.core_memory)
        current_core_memory = _format_core_memory_fields(core_memory)

    return {
        **state,
        "branch_id": normalized_branch_id,
        "core_memory": core_memory,
        "current_core_memory": current_core_memory,
    }


def _require_current_agent(db: Session, current_user: models.User) -> models.Agent:
    db_agent = agent_crud.get_agent_by_user_id(db, current_user.id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this user.",
        )
    return db_agent


async def _run_sleep_consolidation_background(user_id: int, agent_id: int) -> None:
    """Run sleep consolidation with its own DB session after the response returns."""
    try:
        await consolidate_daily_memory(user_id=user_id)
    except Exception:
        logger.exception(
            "Background sleep consolidation failed for user_id=%s agent_id=%s.",
            user_id,
            agent_id,
        )


@router.post(
    "/api/users/me/memory/upload",
    response_model=MemoryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_my_memory(
    memory_in: MemoryUploadCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemoryUploadOut:
    """Store long-form memories for the authenticated user."""
    return await upload_user_memory(current_user.id, memory_in, db, current_user)


@router.post(
    "/api/users/{user_id}/memory/upload",
    response_model=MemoryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_user_memory(
    user_id: int,
    memory_in: MemoryUploadCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemoryUploadOut:
    """Store long-form user memories as pgvector chunks."""
    require_same_user(user_id, current_user)
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
        chunks_added = await add_memory(
            user_id=user_id,
            text_value=memory_in.content.strip(),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store memory in the vector database.",
        ) from exc

    return MemoryUploadOut(message="Memory uploaded.", chunks_added=chunks_added)


@router.post("/api/users/me/memory/search", response_model=MemorySearchOut)
async def search_my_memory(
    search_in: MemorySearchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemorySearchOut:
    """Retrieve RAG chunks for the authenticated user."""
    return await search_user_memory(current_user.id, search_in, db, current_user)


@router.post("/api/users/{user_id}/memory/search", response_model=MemorySearchOut)
async def search_user_memory(
    user_id: int,
    search_in: MemorySearchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemorySearchOut:
    """Retrieve user-scoped RAG chunks for instrumentation and debugging."""
    require_same_user(user_id, current_user)
    if user_crud.get_user(db, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    chunks = await retrieve_memory(
        user_id=user_id,
        query=search_in.query.strip(),
        top_k=search_in.top_k,
        agent_id=current_user.agent.id if current_user.agent is not None else None,
    )
    return MemorySearchOut(query=search_in.query.strip(), chunks=chunks)


@router.post(
    "/api/agents/me/sleep",
    response_model=MemoryConsolidationAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sleep_my_agent(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemoryConsolidationAcceptedOut:
    """Manually trigger sleep-like memory consolidation for my Agent."""
    db_agent = _require_current_agent(db, current_user)
    background_tasks.add_task(
        _run_sleep_consolidation_background,
        user_id=current_user.id,
        agent_id=db_agent.id,
    )
    return MemoryConsolidationAcceptedOut(
        status="processing",
        message=SLEEP_ACCEPTED_MESSAGE,
    )


@router.post(
    "/api/agents/{agent_id}/sleep",
    response_model=MemoryConsolidationAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sleep_agent(
    agent_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> MemoryConsolidationAcceptedOut:
    """Manually trigger sleep-like memory consolidation for one agent."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only consolidate your own agent.",
        )

    background_tasks.add_task(
        _run_sleep_consolidation_background,
        user_id=current_user.id,
        agent_id=agent_id,
    )
    return MemoryConsolidationAcceptedOut(
        status="processing",
        message=SLEEP_ACCEPTED_MESSAGE,
    )


def _require_owned_agent(
    agent_id: int,
    db: Session,
    current_user: models.User,
) -> models.Agent:
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only inspect your own agent.",
        )
    return db_agent


@router.post(
    "/api/agents/me/import_chat",
    response_model=ImportedChatBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_my_agent_group_chat(
    chat_import: ImportedChatBatchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ImportedChatBatchOut:
    """Import group-chat history from the authenticated Agent's perspective."""
    db_agent = _require_current_agent(db, current_user)
    return await import_agent_group_chat(db_agent.id, chat_import, db, current_user)


@router.post(
    "/api/agents/{agent_id}/import_chat",
    response_model=ImportedChatBatchOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_agent_group_chat(
    agent_id: int,
    chat_import: ImportedChatBatchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ImportedChatBatchOut:
    """Import group-chat history with target-agent perspective isolation."""
    db_agent = _require_owned_agent(agent_id, db, current_user)
    sender_agent_ids = {
        message.sender_agent_id
        for message in chat_import.messages
    }
    existing_sender_ids = {
        row[0]
        for row in (
            db.query(models.Agent.id)
            .filter(models.Agent.id.in_(sender_agent_ids))
            .all()
        )
    }
    missing_sender_ids = sorted(sender_agent_ids - existing_sender_ids)
    if missing_sender_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unknown sender Agent ID(s): "
                f"{', '.join(str(sender_id) for sender_id in missing_sender_ids)}"
            ),
        )

    me_messages = sum(
        1
        for message in chat_import.messages
        if message.sender_agent_id == db_agent.id
    )
    others_messages = len(chat_import.messages) - me_messages
    records = [message.model_dump() for message in chat_import.messages]
    participant_agent_ids = sorted(sender_agent_ids | {db_agent.id})
    source_id = f"group-chat-import-{uuid4()}"

    try:
        chunks_added = await add_agent_chat_memories(
            user_id=current_user.id,
            target_agent_id=db_agent.id,
            messages=records,
            branch_id="main",
            topic=chat_import.topic,
            participant_agent_ids=participant_agent_ids,
            source_id=source_id,
        )
        await sync_group_chat_memory_access(
            source_id=source_id,
            participant_agent_ids=participant_agent_ids,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to import group chat memory.",
        ) from exc

    return ImportedChatBatchOut(
        message="Group chat imported with target-agent perspective metadata.",
        target_agent_id=db_agent.id,
        records_received=len(chat_import.messages),
        chunks_added=chunks_added,
        me_messages=me_messages,
        others_messages=others_messages,
    )


@router.get(
    "/api/agents/me/memory/state",
    response_model=AgentWorkingMemoryOut,
)
def get_my_agent_working_memory_state(
    branch_id: str = "main",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentWorkingMemoryOut:
    """Inspect my Agent's short-term LangGraph memory checkpoint."""
    db_agent = _require_current_agent(db, current_user)
    return get_agent_working_memory_state(db_agent.id, branch_id, db, current_user)


@router.get(
    "/api/agents/{agent_id}/memory/state",
    response_model=AgentWorkingMemoryOut,
)
def get_agent_working_memory_state(
    agent_id: int,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentWorkingMemoryOut:
    """Inspect the Agent's short-term LangGraph memory checkpoint."""
    _require_owned_agent(agent_id, db, current_user)
    return AgentWorkingMemoryOut(
        **_branch_memory_state_payload(agent_id, branch_id, db, current_user),
    )


@router.post(
    "/api/agents/me/memory/clear",
    response_model=AgentWorkingMemoryOut,
)
def clear_my_agent_working_memory_state(
    branch_id: str = "main",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentWorkingMemoryOut:
    """Clear my Agent's short-term LangGraph working memory."""
    db_agent = _require_current_agent(db, current_user)
    return clear_agent_working_memory_state(db_agent.id, branch_id, db, current_user)


@router.post(
    "/api/agents/{agent_id}/memory/clear",
    response_model=AgentWorkingMemoryOut,
)
def clear_agent_working_memory_state(
    agent_id: int,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentWorkingMemoryOut:
    """Manually clear the Agent's short-term LangGraph working memory."""
    _require_owned_agent(agent_id, db, current_user)
    normalized_branch_id = _normalize_branch_id(branch_id)
    state = clear_graph_working_memory(
        agent_id=agent_id,
        user_id=current_user.id,
        branch_id=normalized_branch_id,
    )
    return AgentWorkingMemoryOut(
        **{
            **_branch_memory_state_payload(
                agent_id,
                normalized_branch_id,
                db,
                current_user,
            ),
            **state,
        },
    )


@router.get(
    "/api/agents/me/relationships",
    response_model=list[RelationshipOut],
)
def get_my_agent_relationships(
    include_candidates: bool = True,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[RelationshipOut]:
    """Return the directed social-affinity graph from my Agent's perspective."""
    db_agent = _require_current_agent(db, current_user)
    return get_agent_relationships(
        db_agent.id,
        include_candidates,
        limit,
        db,
        current_user,
    )


@router.get(
    "/api/agents/{agent_id}/relationships",
    response_model=list[RelationshipOut],
)
def get_agent_relationships(
    agent_id: int,
    include_candidates: bool = True,
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[RelationshipOut]:
    """Return the directed social-affinity graph from this Agent's perspective."""
    _require_owned_agent(agent_id, db, current_user)
    relationships = (
        db.query(models.Relationship)
        .filter(models.Relationship.agent_id_1 == agent_id)
        .all()
    )
    score_by_target = {
        relationship.agent_id_2: float(relationship.affinity_score or 0.0)
        for relationship in relationships
    }

    if include_candidates:
        target_agents = (
            db.query(models.Agent)
            .filter(models.Agent.id != agent_id)
            .order_by(models.Agent.id.asc())
            .all()
        )
    else:
        target_agents = (
            db.query(models.Agent)
            .filter(models.Agent.id.in_(score_by_target.keys()))
            .order_by(models.Agent.id.asc())
            .all()
            if score_by_target
            else []
        )

    rows = [
        RelationshipOut(
            target_agent_id=agent.id,
            target_agent_name=agent.agent_name,
            affinity_score=score_by_target.get(agent.id, 0.0),
        )
        for agent in target_agents
    ]
    return sorted(rows, key=lambda row: row.affinity_score, reverse=True)[:limit]


@router.get(
    "/api/agents/me/feed-preview",
    response_model=list[PersonalizedPostPreviewOut],
)
def get_my_personalized_feed_preview(
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[PersonalizedPostPreviewOut]:
    """Preview my personalized/filter-bubble feed."""
    db_agent = _require_current_agent(db, current_user)
    return get_personalized_feed_preview(db_agent.id, limit, db, current_user)


@router.get(
    "/api/agents/{agent_id}/feed-preview",
    response_model=list[PersonalizedPostPreviewOut],
)
def get_personalized_feed_preview(
    agent_id: int,
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[PersonalizedPostPreviewOut]:
    """Preview how social affinity creates a personalized/filter-bubble feed."""
    _require_owned_agent(agent_id, db, current_user)
    limit = max(1, min(limit, 50))
    relationships = (
        db.query(models.Relationship)
        .filter(models.Relationship.agent_id_1 == agent_id)
        .all()
    )
    score_by_target = {
        relationship.agent_id_2: float(relationship.affinity_score or 0.0)
        for relationship in relationships
    }
    posts = (
        db.query(models.Post)
        .join(models.Agent)
        .filter(models.Post.agent_id != agent_id)
        .order_by(models.Post.timestamp.desc(), models.Post.id.desc())
        .limit(100)
        .all()
    )
    sorted_posts = sorted(
        posts,
        key=lambda post: (
            score_by_target.get(post.agent_id, 0.0),
            post.timestamp,
            post.id,
        ),
        reverse=True,
    )[:limit]

    return [
        PersonalizedPostPreviewOut(
            id=post.id,
            agent_id=post.agent_id,
            agent_name=post.agent.agent_name,
            affinity_score=score_by_target.get(post.agent_id, 0.0),
            content=post.content,
            timestamp=post.timestamp.isoformat(sep=" ", timespec="seconds"),
        )
        for post in sorted_posts
    ]
