"""Async Postgres/pgvector RAG service for user-scoped digital memories."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import text

from app.database import IS_POSTGRES, SessionLocal
from app.security import RedisError, get_async_redis_client
from app.services.infinity_client import get_infinity_client


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

EMBEDDING_MODEL_ENV = "LOOP_EMBEDDING_MODEL"
RERANKER_MODEL_ENV = "LOOP_RERANKER_MODEL"
EMBEDDING_BASE_URL_ENV = "LOOP_EMBEDDING_BASE_URL"
RERANKER_BASE_URL_ENV = "LOOP_RERANKER_BASE_URL"
LEGACY_EMBEDDING_URL_ENV = "INFINITY_EMBEDDING_URL"
LEGACY_RERANKER_URL_ENV = "INFINITY_RERANKER_URL"
DEFAULT_EMBEDDING_BASE_URL = "http://127.0.0.1:7997"
DEFAULT_RERANKER_BASE_URL = "http://127.0.0.1:7998"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-large-zh-v1.5"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-large"
VECTOR_RAG_ENABLED_ENV = "LOOP_VECTOR_RAG_ENABLED"
RERANKER_ENABLED_ENV = "LOOP_RERANKER_ENABLED"
RAG_STRICT_ENV = "LOOP_RAG_STRICT"
RAG_PRELOAD_ENV = "LOOP_RAG_PRELOAD"
RAG_WARMUP_DONE_TTL_ENV = "LOOP_RAG_WARMUP_TTL_SECONDS"
RAG_WARMUP_LOCK_TTL_ENV = "LOOP_RAG_WARMUP_LOCK_TTL_SECONDS"
MAX_CHUNK_CHARS = 300
RECALL_TOP_K = 15
FALLBACK_SCAN_LIMIT = 50
MIN_MEMORY_RESULTS = 2
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
DEFAULT_RAG_WARMUP_DONE_TTL_SECONDS = 300
DEFAULT_RAG_WARMUP_LOCK_TTL_SECONDS = 60
RAG_WARMUP_DONE_KEY = "loop:rag:warmup_done"
RAG_WARMUP_LOCK_KEY = "loop:rag:warmup_lock"

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strict_rag_enabled() -> bool:
    return _env_flag(RAG_STRICT_ENV, default=True)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _embedding_model() -> str:
    return os.getenv(EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL).strip()


def _reranker_model() -> str:
    return os.getenv(RERANKER_MODEL_ENV, DEFAULT_RERANKER_MODEL).strip()


def _endpoint_from_env(
    legacy_url_env: str,
    base_url_env: str,
    default_base_url: str,
    path: str,
) -> str:
    legacy_url = os.getenv(legacy_url_env, "").strip()
    if legacy_url:
        return legacy_url.rstrip("/") if legacy_url.endswith(path) else f"{legacy_url.rstrip('/')}{path}"
    base_url = os.getenv(base_url_env, default_base_url).strip().rstrip("/")
    return f"{base_url}{path}"


def _embedding_url() -> str:
    return _endpoint_from_env(
        LEGACY_EMBEDDING_URL_ENV,
        EMBEDDING_BASE_URL_ENV,
        DEFAULT_EMBEDDING_BASE_URL,
        "/embeddings",
    )


def _reranker_url() -> str:
    return _endpoint_from_env(
        LEGACY_RERANKER_URL_ENV,
        RERANKER_BASE_URL_ENV,
        DEFAULT_RERANKER_BASE_URL,
        "/rerank",
    )


def _chunk_text(text_value: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split memory text into compact chunks suitable for vector search."""
    paragraphs = [part.strip() for part in text_value.splitlines() if part.strip()]
    if not paragraphs:
        paragraphs = [text_value.strip()]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > max_chars:
            head = remaining[:max_chars].strip()
            if head:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(head)
            remaining = remaining[max_chars:].strip()

        if not remaining:
            continue

        candidate = f"{current}\n{remaining}".strip() if current else remaining
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = remaining

    if current:
        chunks.append(current)
    return chunks


def _parse_embedding_response(data: dict[str, Any]) -> list[float]:
    """Extract one embedding from an Infinity-compatible response."""
    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Infinity embedding response had unexpected shape.") from exc
    if not isinstance(embedding, list):
        raise RuntimeError("Infinity embedding response did not contain a list.")
    try:
        return [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Infinity embedding response contained invalid values.") from exc


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Return Infinity embeddings as plain Python lists."""
    embeddings: list[list[float]] = []
    url = _embedding_url()
    client = get_infinity_client()
    for text_value in texts:
        data = await client.post(
            url,
            {"input": text_value, "model": _embedding_model()},
        )
        embeddings.append(_parse_embedding_response(data))
    return embeddings


async def _embed_query(query: str) -> list[float] | None:
    """Embed a retrieval query with the BGE Chinese retrieval instruction."""
    embeddings = await _embed_texts([f"{BGE_QUERY_INSTRUCTION}{query}"])
    return embeddings[0] if embeddings else None


def _parse_reranker_scores(data: dict[str, Any], documents: list[str]) -> list[float]:
    """Extract per-document rerank scores from common Infinity response shapes."""
    raw_items = data.get("results")
    if raw_items is None:
        raw_items = data.get("data")
    if raw_items is None:
        raw_items = data.get("scores")
    if not isinstance(raw_items, list):
        raise RuntimeError("Infinity reranker response had unexpected shape.")

    scores: list[float | None] = [None] * len(documents)
    try:
        if raw_items and isinstance(raw_items[0], dict):
            for fallback_index, item in enumerate(raw_items):
                index = int(item.get("index", fallback_index))
                score_value = item.get("relevance_score", item.get("score"))
                if score_value is None:
                    score_value = item.get("rerank_score")
                if 0 <= index < len(scores) and score_value is not None:
                    scores[index] = float(score_value)
        else:
            for index, score_value in enumerate(raw_items[: len(scores)]):
                scores[index] = float(score_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Infinity reranker response contained invalid scores.") from exc

    if any(score is None for score in scores):
        raise RuntimeError("Infinity reranker response did not score every document.")
    return [float(score) for score in scores]


async def _rerank_documents(query: str, documents: list[str], top_k: int) -> list[str]:
    """Rerank recalled chunks through the Infinity reranker service."""
    if not _env_flag(RERANKER_ENABLED_ENV, default=True):
        return documents[:top_k]

    data = await get_infinity_client().post(
        _reranker_url(),
        {
            "query": query,
            "documents": documents,
            "model": _reranker_model(),
        },
    )
    scores = _parse_reranker_scores(data, documents)
    scored_documents = [
        (float(score), index, document)
        for index, (score, document) in enumerate(zip(scores, documents))
    ]
    scored_documents.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return [document for _, _, document in scored_documents[:top_k]]


def _vector_literal(embedding: list[float]) -> str:
    """Render a pgvector literal without relying on a driver-specific adapter."""
    return "[" + ",".join(f"{float(value):.12g}" for value in embedding) + "]"


def _require_postgres_vector_store() -> None:
    if not IS_POSTGRES:
        raise RuntimeError(
            "Postgres with pgvector is required for stateless RAG storage.",
        )


def _insert_documents_sync(
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
) -> int:
    _require_postgres_vector_store()
    with SessionLocal() as db:
        for document_id, document, embedding, metadata in zip(
            ids,
            documents,
            embeddings,
            metadatas,
        ):
            db.execute(
                text(
                    """
                    INSERT INTO rag_documents (id, content, metadata, embedding)
                    VALUES (
                        :id,
                        :content,
                        CAST(:metadata AS jsonb),
                        CAST(:embedding AS vector)
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding,
                        updated_at = now()
                    """,
                ),
                {
                    "id": document_id,
                    "content": document,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                    "embedding": _vector_literal(embedding),
                },
            )
        db.commit()
    return len(documents)


def _recall_documents_sync(
    user_id: int,
    branch_id: str,
    query_embedding: list[float],
    limit: int,
    agent_id: int | None = None,
) -> tuple[list[str], float | str]:
    _require_postgres_vector_store()
    participant_agent_ids = json.dumps([agent_id], ensure_ascii=False)
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT
                    content,
                    embedding <=> CAST(:query_embedding AS vector) AS distance
                FROM rag_documents
                WHERE embedding IS NOT NULL
                  AND metadata ->> 'branch_id' = :branch_id
                  AND (
                    (
                      metadata ? 'user_id'
                      AND (metadata ->> 'user_id')::integer = :user_id
                    )
                    OR (
                      :agent_id IS NOT NULL
                      AND metadata ? 'agent_id'
                      AND metadata ->> 'agent_id' ~ '^\\d+$'
                      AND (metadata ->> 'agent_id')::integer = :agent_id
                    )
                    OR (
                      :agent_id IS NOT NULL
                      AND metadata ? 'participant_agent_ids'
                      AND metadata -> 'participant_agent_ids' @> CAST(:participant_agent_ids AS jsonb)
                    )
                  )
                ORDER BY embedding <=> CAST(:query_embedding AS vector)
                LIMIT :limit
                """,
            ),
            {
                "query_embedding": _vector_literal(query_embedding),
                "branch_id": branch_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "participant_agent_ids": participant_agent_ids,
                "limit": limit,
            },
        ).all()
    documents = [str(row[0]) for row in rows if row[0]]
    top_score: float | str = float(rows[0][1]) if rows else "N/A"
    return documents, top_score


def _read_recent_documents_sync(
    user_id: int,
    branch_id: str,
    limit: int,
    agent_id: int | None = None,
) -> list[str]:
    _require_postgres_vector_store()
    participant_agent_ids = json.dumps([agent_id], ensure_ascii=False)
    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT content
                FROM rag_documents
                WHERE metadata ->> 'branch_id' = :branch_id
                  AND (
                    (
                      metadata ? 'user_id'
                      AND (metadata ->> 'user_id')::integer = :user_id
                    )
                    OR (
                      :agent_id IS NOT NULL
                      AND metadata ? 'agent_id'
                      AND metadata ->> 'agent_id' ~ '^\\d+$'
                      AND (metadata ->> 'agent_id')::integer = :agent_id
                    )
                    OR (
                      :agent_id IS NOT NULL
                      AND metadata ? 'participant_agent_ids'
                      AND metadata -> 'participant_agent_ids' @> CAST(:participant_agent_ids AS jsonb)
                    )
                  )
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                """,
            ),
            {
                "branch_id": branch_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "participant_agent_ids": participant_agent_ids,
                "limit": limit,
            },
        ).all()
    return [str(row[0]) for row in rows if row[0]]


async def _insert_documents(
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict[str, Any]],
) -> int:
    return await asyncio.to_thread(
        _insert_documents_sync,
        ids,
        documents,
        embeddings,
        metadatas,
    )


async def _recall_documents(
    user_id: int,
    branch_id: str,
    query_embedding: list[float],
    limit: int,
    agent_id: int | None = None,
) -> tuple[list[str], float | str]:
    return await asyncio.to_thread(
        _recall_documents_sync,
        user_id,
        branch_id,
        query_embedding,
        limit,
        agent_id,
    )


async def _read_recent_documents(
    user_id: int,
    branch_id: str,
    limit: int,
    agent_id: int | None = None,
) -> list[str]:
    return await asyncio.to_thread(
        _read_recent_documents_sync,
        user_id,
        branch_id,
        limit,
        agent_id,
    )


def _tokens_for_ranking(text_value: str) -> set[str]:
    """Create lightweight Chinese/English tokens for fallback reranking."""
    normalized = text_value.lower()
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    cjk_bigrams = {
        "".join(cjk_chars[index : index + 2])
        for index in range(max(len(cjk_chars) - 1, 0))
    }
    words = {
        word
        for word in re.findall(r"[a-z0-9_]+", normalized)
        if len(word) >= 2
    }
    return set(cjk_chars) | cjk_bigrams | words


def _rank_documents(query: str, documents: list[str], top_k: int) -> list[str]:
    """Rank fallback documents by rough lexical overlap while preserving recency."""
    query_tokens = _tokens_for_ranking(query)
    ranked: list[tuple[int, int, str]] = []
    normalized_query = query.lower()
    for index, document in enumerate(documents):
        document_tokens = _tokens_for_ranking(document)
        score = len(query_tokens & document_tokens)
        if normalized_query and normalized_query in document.lower():
            score += 20
        ranked.append((score, -index, document))
    ranked.sort(reverse=True)
    return [document for _, _, document in ranked[:top_k]]


def _dedupe_documents(documents: list[str]) -> list[str]:
    """Keep first occurrences while removing empty or duplicate chunks."""
    unique_documents: list[str] = []
    seen: set[str] = set()
    for document in documents:
        normalized_document = document.strip()
        if not normalized_document or normalized_document in seen:
            continue
        seen.add(normalized_document)
        unique_documents.append(normalized_document)
    return unique_documents


async def warm_up_rag_models() -> None:
    """Warm external RAG services without loading models in FastAPI workers."""
    if not _env_flag(RAG_PRELOAD_ENV, default=True):
        return

    redis_client = get_async_redis_client()
    done_ttl_seconds = _int_env(
        RAG_WARMUP_DONE_TTL_ENV,
        DEFAULT_RAG_WARMUP_DONE_TTL_SECONDS,
    )
    lock_ttl_seconds = _int_env(
        RAG_WARMUP_LOCK_TTL_ENV,
        DEFAULT_RAG_WARMUP_LOCK_TTL_SECONDS,
    )
    if redis_client is not None:
        try:
            if done_ttl_seconds > 0 and await redis_client.exists(RAG_WARMUP_DONE_KEY):
                logger.debug("Skipping RAG warmup because a recent warmup already ran.")
                return
            claimed = await redis_client.set(
                RAG_WARMUP_LOCK_KEY,
                "1",
                nx=True,
                ex=max(1, lock_ttl_seconds),
            )
            if not claimed:
                logger.debug(
                    "Skipping RAG warmup because another worker is warming models.",
                )
                return
        except (RedisError, OSError, ValueError) as exc:
            logger.warning(
                "Redis warmup coordination failed; warming this worker without "
                "a distributed lock: %s",
                exc,
            )
    else:
        logger.warning(
            "Redis warmup coordination is unavailable; warming this worker "
            "without a distributed lock.",
        )

    try:
        if _env_flag(VECTOR_RAG_ENABLED_ENV, default=True):
            await _embed_texts(["Loop RAG warmup"])
        if _env_flag(RERANKER_ENABLED_ENV, default=True):
            await _rerank_documents(
                "Loop RAG warmup",
                ["Loop RAG warmup memory"],
                top_k=1,
            )
    finally:
        if redis_client is None or done_ttl_seconds <= 0:
            return
        try:
            await redis_client.set(
                RAG_WARMUP_DONE_KEY,
                "1",
                ex=max(1, done_ttl_seconds),
            )
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("Failed to record Redis RAG warmup marker: %s", exc)


async def add_memory(user_id: int, text_value: str, branch_id: str = "main") -> int:
    """Chunk and store user memory text in Postgres/pgvector."""
    chunks = _chunk_text(text_value)
    if not chunks:
        return 0

    embeddings = await _embed_texts(chunks)
    if len(embeddings) != len(chunks):
        raise RuntimeError("Infinity embedding returned an incomplete result set.")
    ids = [f"user-{user_id}-{uuid4()}" for _ in chunks]
    metadatas = [
        {
            "user_id": user_id,
            "branch_id": (branch_id or "main").strip() or "main",
            "embedding_model": _embedding_model(),
        }
        for _ in chunks
    ]
    return await _insert_documents(ids, chunks, embeddings, metadatas)


async def add_scored_memories(
    user_id: int,
    agent_id: int,
    memories: list[dict[str, Any]],
    branch_id: str = "main",
) -> int:
    """Store scored episodic memories while preserving ranking metadata."""
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for memory in memories:
        text_value = str(memory.get("text") or "").strip()
        if not text_value:
            continue

        similarity = max(0.0, min(1.0, float(memory.get("similarity", 0.5))))
        importance = max(0.0, min(1.0, float(memory.get("importance", 0.5))))
        time_decay = max(0.0, min(1.0, float(memory.get("time_decay", 0.0))))
        score = similarity * 0.5 + importance * 0.3 - time_decay * 0.2
        if score <= 0:
            continue

        chunks = _chunk_text(text_value)
        for chunk_index, chunk in enumerate(chunks):
            documents.append(chunk)
            metadatas.append(
                {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "branch_id": (branch_id or "main").strip() or "main",
                    "source": "sleep_consolidation",
                    "memory_layer": "episodic",
                    "similarity": similarity,
                    "importance": importance,
                    "time_decay": time_decay,
                    "score": score,
                    "chunk_index": chunk_index,
                    "embedding_model": _embedding_model(),
                },
            )

    if not documents:
        return 0

    embeddings = await _embed_texts(documents)
    if len(embeddings) != len(documents):
        raise RuntimeError("Infinity embedding returned an incomplete result set.")
    ids = [f"user-{user_id}-episodic-{uuid4()}" for _ in documents]
    return await _insert_documents(ids, documents, embeddings, metadatas)


async def add_agent_chat_memories(
    user_id: int,
    target_agent_id: int,
    messages: list[dict[str, Any]],
    branch_id: str = "main",
    topic: str | None = None,
    participant_agent_ids: list[int] | None = None,
    source_id: str | None = None,
) -> int:
    """Store group-chat memory with shared access for all mapped participants."""
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    normalized_topic = str(topic or "").strip()
    normalized_participant_agent_ids = sorted(
        {
            int(agent_id)
            for agent_id in (participant_agent_ids or [])
            if int(agent_id) > 0
        }
        | {int(target_agent_id)}
        | {
            int(message["sender_agent_id"])
            for message in messages
            if int(message["sender_agent_id"]) > 0
        },
    )
    normalized_source_id = (
        str(source_id or "").strip()
        or f"group-chat-import-{uuid4()}"
    )

    for message in messages:
        sender_agent_id = int(message["sender_agent_id"])
        content = str(message["content"]).strip()
        if not content:
            continue

        timestamp = str(message.get("timestamp") or "").strip()
        speaker_label = f"Agent #{sender_agent_id}"
        timestamp_prefix = f"[{timestamp}] " if timestamp else ""
        memory_text = f"{timestamp_prefix}{speaker_label}: {content}"

        chunks = _chunk_text(memory_text)
        for chunk_index, chunk in enumerate(chunks):
            metadata: dict[str, Any] = {
                "user_id": user_id,
                "branch_id": (branch_id or "main").strip() or "main",
                "agent_id": target_agent_id,
                "target_agent_id": target_agent_id,
                "participant_agent_ids": normalized_participant_agent_ids,
                "source_id": normalized_source_id,
                "source": "group_chat_import",
                "speaker": "self" if sender_agent_id == target_agent_id else "participant",
                "sender_agent_id": sender_agent_id,
                "embedding_model": _embedding_model(),
                "chunk_index": chunk_index,
            }
            if timestamp:
                metadata["timestamp"] = timestamp
            if normalized_topic:
                metadata["topic"] = normalized_topic
            if sender_agent_id != target_agent_id:
                metadata["original_speaker_id"] = sender_agent_id
            documents.append(chunk)
            metadatas.append(metadata)

    if not documents:
        return 0

    embeddings = await _embed_texts(documents)
    if len(embeddings) != len(documents):
        raise RuntimeError("Infinity embedding returned an incomplete result set.")
    ids = [f"agent-{target_agent_id}-group-chat-{uuid4()}" for _ in documents]
    return await _insert_documents(ids, documents, embeddings, metadatas)


def _sync_group_chat_memory_access_sync(
    source_id: str,
    participant_agent_ids: list[int],
) -> int:
    """Grant this group-chat batch to every mapped participant Agent."""
    _require_postgres_vector_store()
    normalized_source_id = source_id.strip()
    normalized_participant_agent_ids = sorted(
        {int(agent_id) for agent_id in participant_agent_ids if int(agent_id) > 0},
    )
    if not normalized_source_id or not normalized_participant_agent_ids:
        return 0

    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                UPDATE rag_documents
                SET metadata = jsonb_set(
                    metadata,
                    '{participant_agent_ids}',
                    CAST(:participant_agent_ids AS jsonb),
                    true
                ),
                    updated_at = now()
                WHERE metadata ->> 'source_id' = :source_id
                  AND metadata ->> 'source' = 'group_chat_import'
                """,
            ),
            {
                "source_id": normalized_source_id,
                "participant_agent_ids": json.dumps(
                    normalized_participant_agent_ids,
                    ensure_ascii=False,
                ),
            },
        )
        db.commit()
        return int(rows.rowcount or 0)


async def sync_group_chat_memory_access(
    source_id: str,
    participant_agent_ids: list[int],
) -> int:
    """Synchronize shared access for an already-written group-chat batch."""
    return await asyncio.to_thread(
        _sync_group_chat_memory_access_sync,
        source_id,
        participant_agent_ids,
    )


async def retrieve_memory(
    user_id: int,
    query: str,
    top_k: int = 3,
    branch_id: str = "main",
    source: str = "general",
    agent_id: int | None = None,
) -> list[str]:
    """Retrieve relevant memory chunks with two-stage Advanced RAG."""
    clean_query = query.strip()
    if top_k <= 0:
        return []

    normalized_branch_id = (branch_id or "main").strip() or "main"
    logger.info(
        "[RAG Engine] Querying Memory Vault source=%s branch_id=%s query=%r",
        source,
        normalized_branch_id,
        clean_query,
    )
    top_score: float | str = "N/A"
    recalled_documents: list[str] = []
    if clean_query and _env_flag(VECTOR_RAG_ENABLED_ENV, default=True):
        try:
            query_embedding = await _embed_query(clean_query)
            if query_embedding is not None:
                recalled_documents, top_score = await _recall_documents(
                    user_id=user_id,
                    branch_id=normalized_branch_id,
                    query_embedding=query_embedding,
                    limit=max(RECALL_TOP_K, top_k),
                    agent_id=agent_id,
                )
        except Exception as exc:
            logger.warning("Vector recall failed: %s", exc)
            if _strict_rag_enabled():
                raise
            recalled_documents = []

    recalled_documents = _dedupe_documents(recalled_documents)
    if clean_query and recalled_documents:
        try:
            retrieved = await _rerank_documents(clean_query, recalled_documents, top_k)
        except Exception as exc:
            logger.warning("Rerank failed; using vector recall order: %s", exc)
            if _strict_rag_enabled():
                raise
            retrieved = recalled_documents[:top_k]
    else:
        retrieved = recalled_documents[:top_k]

    fallback_limit = max(FALLBACK_SCAN_LIMIT, top_k, MIN_MEMORY_RESULTS)
    try:
        fallback_documents = await _read_recent_documents(
            user_id,
            normalized_branch_id,
            fallback_limit,
            agent_id=agent_id,
        )
    except Exception as exc:
        logger.warning("Postgres memory fallback read failed: %s", exc)
        if _strict_rag_enabled() and not retrieved:
            raise
        fallback_documents = []

    if fallback_documents:
        ranked_documents = (
            _rank_documents(clean_query, fallback_documents, top_k)
            if clean_query
            else fallback_documents[:top_k]
        )
        retrieved.extend(ranked_documents)

    unique_documents: list[str] = []
    seen: set[str] = set()
    target_count = min(top_k, max(MIN_MEMORY_RESULTS, 1))
    for document in retrieved:
        if document in seen:
            continue
        seen.add(document)
        unique_documents.append(document)
        if len(unique_documents) >= top_k:
            break

    if len(unique_documents) < target_count:
        for document in fallback_documents:
            if document in seen:
                continue
            seen.add(document)
            unique_documents.append(document)
            if len(unique_documents) >= target_count or len(unique_documents) >= top_k:
                break

    logger.info(
        "[RAG Engine] Retrieved %s fragments. Top match score: %s",
        len(unique_documents),
        top_score,
    )
    return unique_documents


def _relationship_context_sync(user_id: int) -> str | None:
    from app import models

    with SessionLocal() as db:
        source_agent = (
            db.query(models.Agent).filter(models.Agent.user_id == user_id).first()
        )
        if source_agent is None:
            return None
        relationships = (
            db.query(models.Relationship)
            .filter(models.Relationship.agent_id_1 == source_agent.id)
            .order_by(models.Relationship.affinity_score.desc())
            .limit(8)
            .all()
        )
        if not relationships:
            return None

        graph_lines: list[str] = []
        for relationship in relationships:
            target_agent = (
                db.query(models.Agent)
                .filter(models.Agent.id == relationship.agent_id_2)
                .first()
            )
            target_name = (
                target_agent.agent_name
                if target_agent is not None
                else f"Agent #{relationship.agent_id_2}"
            )
            graph_lines.append(
                f"{target_name}: affinity_score={relationship.affinity_score:.2f}",
            )
    return "【GraphRAG 社交图谱上下文】\n" + "\n".join(graph_lines)


async def retrieve_hybrid_memory(
    user_id: int,
    query: str,
    top_k: int = 3,
    branch_id: str = "main",
    source: str = "general",
    agent_id: int | None = None,
) -> list[str]:
    """Retrieve vector memories plus graph social context from SQL."""
    normalized_branch_id = (branch_id or "main").strip() or "main"
    memories = await retrieve_memory(
        user_id=user_id,
        query=query,
        top_k=top_k,
        branch_id=normalized_branch_id,
        source=source,
        agent_id=agent_id,
    )
    if normalized_branch_id != "main":
        return memories
    try:
        graph_context = await asyncio.to_thread(_relationship_context_sync, user_id)
        return [*memories, graph_context] if graph_context else memories
    except Exception as exc:
        logger.warning("Hybrid graph retrieval failed: %s", exc)
        return memories
