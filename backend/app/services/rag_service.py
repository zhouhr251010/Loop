"""Local RAG service for user-scoped digital memories."""

from functools import lru_cache
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

CHROMA_PATH = PROJECT_ROOT / "chroma_db"
COLLECTION_NAME = "loop_memories_bge_v1"
MODEL_NAME = "BAAI/bge-large-zh-v1.5"
EMBEDDING_DIMENSIONS = 1024
RERANKER_MODEL_NAME = "BAAI/bge-reranker-large"
EMBEDDING_DEVICE_ENV = "LOOP_EMBEDDING_DEVICE"
RERANKER_DEVICE_ENV = "LOOP_RERANKER_DEVICE"
VECTOR_RAG_ENABLED_ENV = "LOOP_VECTOR_RAG_ENABLED"
RERANKER_ENABLED_ENV = "LOOP_RERANKER_ENABLED"
RAG_STRICT_ENV = "LOOP_RAG_STRICT"
RAG_PRELOAD_ENV = "LOOP_RAG_PRELOAD"
MAX_CHUNK_CHARS = 300
RECALL_TOP_K = 15
FALLBACK_SCAN_LIMIT = 50
MIN_MEMORY_RESULTS = 2
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment flag."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strict_rag_enabled() -> bool:
    return _env_flag(RAG_STRICT_ENV, default=True)


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split memory text into compact chunks suitable for vector search."""
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

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


@lru_cache(maxsize=1)
def _get_embedding_model() -> Any:
    """Load the local sentence-transformer model on first use."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError(
            "sentence-transformers is not installed or could not be imported.",
        ) from exc

    device = _get_embedding_device()
    try:
        return SentenceTransformer(MODEL_NAME, device=device)
    except Exception as exc:
        logger.warning(
            "Failed to load embedding model '%s' on %s: %s",
            MODEL_NAME,
            device,
            exc,
        )
        if device != "cpu" and not os.getenv(EMBEDDING_DEVICE_ENV):
            try:
                logger.warning("Retrying embedding model '%s' on CPU.", MODEL_NAME)
                return SentenceTransformer(MODEL_NAME, device="cpu")
            except Exception as cpu_exc:
                logger.warning(
                    "Failed to load embedding model '%s' on CPU: %s",
                    MODEL_NAME,
                    cpu_exc,
                )
        raise RuntimeError(
            f"Failed to load local embedding model '{MODEL_NAME}'.",
        ) from exc


def _get_embedding_device() -> str:
    """Choose the embedding device, preferring CUDA when available."""
    configured_device = os.getenv(EMBEDDING_DEVICE_ENV)
    if configured_device:
        return configured_device

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass

    return "cpu"


def _get_reranker_device() -> str:
    """Choose the reranker device, spreading large models across GPUs by default."""
    configured_device = os.getenv(RERANKER_DEVICE_ENV)
    if configured_device:
        return configured_device

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:1" if torch.cuda.device_count() >= 2 else "cuda:0"
    except Exception:
        pass

    return "cpu"


@lru_cache(maxsize=1)
def _get_collection() -> Any:
    """Create or load the persistent Chroma collection."""
    try:
        import chromadb
    except Exception as exc:
        raise RuntimeError("chromadb is not installed or could not be imported.") from exc

    try:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={
                "embedding_model": MODEL_NAME,
                "embedding_dimensions": EMBEDDING_DIMENSIONS,
                "reranker_model": RERANKER_MODEL_NAME,
                "retrieval_pipeline": "vector_recall_cross_encoder_rerank",
            },
        )
    except Exception as exc:
        raise RuntimeError("Failed to initialize the local ChromaDB store.") from exc


@lru_cache(maxsize=1)
def _get_reranker() -> Any | None:
    """Load the BGE cross-encoder reranker, falling back cleanly if unavailable."""
    if not _env_flag(RERANKER_ENABLED_ENV, default=True):
        return None

    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:
        if _strict_rag_enabled():
            raise RuntimeError("sentence-transformers CrossEncoder import failed.") from exc
        logger.warning("sentence-transformers CrossEncoder import failed: %s", exc)
        return None

    device = _get_reranker_device()
    try:
        return CrossEncoder(RERANKER_MODEL_NAME, device=device, max_length=512)
    except Exception as exc:
        if _strict_rag_enabled() or os.getenv(RERANKER_DEVICE_ENV):
            raise RuntimeError(
                f"Failed to load reranker '{RERANKER_MODEL_NAME}' on {device}.",
            ) from exc
        logger.warning(
            "Failed to load reranker '%s' on %s: %s",
            RERANKER_MODEL_NAME,
            device,
            exc,
        )
        if device == "cpu" or os.getenv(RERANKER_DEVICE_ENV):
            return None

    try:
        logger.warning("Retrying reranker '%s' on CPU.", RERANKER_MODEL_NAME)
        return CrossEncoder(RERANKER_MODEL_NAME, device="cpu", max_length=512)
    except Exception as exc:
        logger.warning(
            "Failed to load reranker '%s' on CPU: %s",
            RERANKER_MODEL_NAME,
            exc,
        )
        return None


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Return plain Python-list embeddings for ChromaDB."""
    model = _get_embedding_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embeddings.tolist()


def warm_up_rag_models() -> None:
    """Load local RAG models during API startup so chat latency is predictable."""
    if not _env_flag(RAG_PRELOAD_ENV, default=True):
        return

    _get_collection()
    if _env_flag(VECTOR_RAG_ENABLED_ENV, default=True):
        _embed_texts(["Loop RAG warmup"])

    if _env_flag(RERANKER_ENABLED_ENV, default=True):
        _rerank_documents("Loop RAG warmup", ["Loop RAG warmup memory"], top_k=1)


def _embed_query(query: str) -> list[float]:
    """Embed a retrieval query with the BGE Chinese retrieval instruction."""
    return _embed_texts([f"{BGE_QUERY_INSTRUCTION}{query}"])[0]


def _read_user_documents_from_sqlite(user_id: int, limit: int) -> list[str]:
    """Read stored Chroma documents for a user without starting the Chroma client."""
    database_path = CHROMA_PATH / "chroma.sqlite3"
    if not database_path.exists() or limit <= 0:
        return []

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT document_metadata.string_value
            FROM embeddings
            JOIN embedding_metadata AS user_metadata
              ON user_metadata.id = embeddings.id
             AND user_metadata.key = 'user_id'
             AND user_metadata.int_value = ?
            JOIN embedding_metadata AS document_metadata
              ON document_metadata.id = embeddings.id
             AND document_metadata.key = 'chroma:document'
            ORDER BY embeddings.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    finally:
        connection.close()

    return [row[0] for row in rows if row[0]]


def _tokens_for_ranking(text: str) -> set[str]:
    """Create lightweight Chinese/English tokens for fallback reranking."""
    normalized = text.lower()
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


def _rerank_documents(query: str, documents: list[str], top_k: int) -> list[str]:
    """Rerank recalled chunks with a BGE cross-encoder."""
    reranker = _get_reranker()
    if reranker is None:
        return documents[:top_k]

    pairs = [[query, document] for document in documents]
    try:
        scores = reranker.predict(pairs, show_progress_bar=False)
    except Exception as exc:
        if _strict_rag_enabled():
            raise
        logger.warning("BGE reranker prediction failed: %s", exc)
        return documents[:top_k]

    scored_documents = [
        (float(score), index, document)
        for index, (score, document) in enumerate(zip(scores, documents))
    ]
    scored_documents.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return [document for _, _, document in scored_documents[:top_k]]


def add_memory(user_id: int, text: str) -> int:
    """Chunk and store user memory text in the local Chroma vector store."""
    chunks = _chunk_text(text)
    if not chunks:
        return 0

    collection = _get_collection()
    embeddings = _embed_texts(chunks)
    ids = [f"user-{user_id}-{uuid4()}" for _ in chunks]
    metadatas = [
        {"user_id": user_id, "embedding_model": MODEL_NAME}
        for _ in chunks
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(chunks)


def add_scored_memories(
    user_id: int,
    agent_id: int,
    memories: list[dict[str, Any]],
) -> int:
    """Store scored episodic memories while preserving SOTA ranking metadata."""
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for memory in memories:
        text = str(memory.get("text") or "").strip()
        if not text:
            continue

        similarity = max(0.0, min(1.0, float(memory.get("similarity", 0.5))))
        importance = max(0.0, min(1.0, float(memory.get("importance", 0.5))))
        time_decay = max(0.0, min(1.0, float(memory.get("time_decay", 0.0))))
        score = similarity * 0.5 + importance * 0.3 - time_decay * 0.2
        if score <= 0:
            continue

        chunks = _chunk_text(text)
        for chunk_index, chunk in enumerate(chunks):
            documents.append(chunk)
            metadatas.append(
                {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "source": "sleep_consolidation",
                    "memory_layer": "episodic",
                    "similarity": similarity,
                    "importance": importance,
                    "time_decay": time_decay,
                    "score": score,
                    "chunk_index": chunk_index,
                    "embedding_model": MODEL_NAME,
                },
            )

    if not documents:
        return 0

    collection = _get_collection()
    embeddings = _embed_texts(documents)
    ids = [f"user-{user_id}-episodic-{uuid4()}" for _ in documents]
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(documents)


def add_agent_chat_memories(
    user_id: int,
    target_agent_id: int,
    messages: list[dict[str, Any]],
) -> int:
    """Store group-chat memory from one target agent's private perspective."""
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for message in messages:
        sender_agent_id = int(message["sender_agent_id"])
        content = str(message["content"]).strip()
        if not content:
            continue

        speaker = "me" if sender_agent_id == target_agent_id else "others"
        timestamp = str(message.get("timestamp") or "").strip()
        speaker_label = "我" if speaker == "me" else f"Agent #{sender_agent_id}"
        timestamp_prefix = f"[{timestamp}] " if timestamp else ""
        memory_text = f"{timestamp_prefix}{speaker_label}: {content}"

        chunks = _chunk_text(memory_text)
        for chunk_index, chunk in enumerate(chunks):
            metadata: dict[str, Any] = {
                "user_id": user_id,
                "agent_id": target_agent_id,
                "target_agent_id": target_agent_id,
                "source": "group_chat_import",
                "speaker": speaker,
                "sender_agent_id": sender_agent_id,
                "embedding_model": MODEL_NAME,
                "chunk_index": chunk_index,
            }
            if timestamp:
                metadata["timestamp"] = timestamp
            if speaker == "others":
                metadata["original_speaker_id"] = sender_agent_id

            documents.append(chunk)
            metadatas.append(metadata)

    if not documents:
        return 0

    collection = _get_collection()
    embeddings = _embed_texts(documents)
    ids = [f"agent-{target_agent_id}-group-chat-{uuid4()}" for _ in documents]

    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(documents)


def retrieve_memory(user_id: int, query: str, top_k: int = 3) -> list[str]:
    """Retrieve relevant memory chunks with two-stage Advanced RAG."""
    clean_query = query.strip()
    if top_k <= 0:
        return []

    logger.info(f"[RAG Engine] Querying Memory Vault for: '{clean_query}'")
    top_score: float | str = "N/A"
    recalled_documents: list[str] = []
    if clean_query and _env_flag(VECTOR_RAG_ENABLED_ENV, default=True):
        try:
            collection = _get_collection()
            query_embedding = _embed_query(clean_query)
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=max(RECALL_TOP_K, top_k),
                where={"user_id": user_id},
            )
            documents = results.get("documents") or []
            if documents:
                recalled_documents.extend(documents[0])
            distances = results.get("distances") or []
            if distances and distances[0]:
                top_score = float(distances[0][0])
        except Exception as exc:
            if _strict_rag_enabled():
                raise
            logger.warning("BGE vector recall failed: %s", exc)
            recalled_documents = []

    recalled_documents = _dedupe_documents(recalled_documents)
    retrieved = (
        _rerank_documents(clean_query, recalled_documents, top_k)
        if clean_query and recalled_documents
        else recalled_documents[:top_k]
    )

    fallback_limit = max(FALLBACK_SCAN_LIMIT, top_k, MIN_MEMORY_RESULTS)
    try:
        fallback_documents = _read_user_documents_from_sqlite(
            user_id,
            limit=fallback_limit,
        )
    except Exception as exc:
        logger.warning("SQLite memory fallback read failed: %s", exc)
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
        f"[RAG Engine] Retrieved {len(unique_documents)} fragments. "
        f"Top match score: {top_score}",
    )
    return unique_documents


def retrieve_hybrid_memory(
    user_id: int,
    query: str,
    top_k: int = 3,
) -> list[str]:
    """Retrieve vector memories plus graph social context from SQL."""
    memories = retrieve_memory(user_id=user_id, query=query, top_k=top_k)
    try:
        from app import models
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            source_agent = (
                db.query(models.Agent)
                .filter(models.Agent.user_id == user_id)
                .first()
            )
            if source_agent is None:
                return memories
            relationships = (
                db.query(models.Relationship)
                .filter(models.Relationship.agent_id_1 == source_agent.id)
                .order_by(models.Relationship.affinity_score.desc())
                .limit(8)
                .all()
            )
            if not relationships:
                return memories

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
            return [
                *memories,
                "【GraphRAG 社交图谱上下文】\n" + "\n".join(graph_lines),
            ]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Hybrid graph retrieval failed: %s", exc)
        return memories
