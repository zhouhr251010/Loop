"""Local RAG service for user-scoped digital memories."""

from functools import lru_cache
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CHROMA_PATH = PROJECT_ROOT / "chroma_db"
COLLECTION_NAME = "loop_user_memories"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
MAX_CHUNK_CHARS = 300
FALLBACK_SCAN_LIMIT = 50


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

    try:
        return SentenceTransformer(MODEL_NAME)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load local embedding model '{MODEL_NAME}'.",
        ) from exc


@lru_cache(maxsize=1)
def _get_collection() -> Any:
    """Create or load the persistent Chroma collection."""
    try:
        import chromadb
    except Exception as exc:
        raise RuntimeError("chromadb is not installed or could not be imported.") from exc

    try:
        client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return client.get_or_create_collection(name=COLLECTION_NAME)
    except Exception as exc:
        raise RuntimeError("Failed to initialize the local ChromaDB store.") from exc


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Return plain Python-list embeddings for ChromaDB."""
    model = _get_embedding_model()
    embeddings = model.encode(texts, normalize_embeddings=True)
    return embeddings.tolist()


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


def retrieve_memory(user_id: int, query: str, top_k: int = 3) -> list[str]:
    """Retrieve relevant memory chunks for a user-scoped query."""
    clean_query = query.strip()
    if not clean_query or top_k <= 0:
        return []

    retrieved: list[str] = []
    try:
        collection = _get_collection()
        query_embedding = _embed_texts([clean_query])[0]
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"user_id": user_id},
        )
        documents = results.get("documents") or []
        if documents:
            retrieved.extend(document for document in documents[0] if document)
    except Exception:
        retrieved = []

    fallback_documents = _read_user_documents_from_sqlite(
        user_id,
        limit=max(FALLBACK_SCAN_LIMIT, top_k),
    )
    if fallback_documents:
        retrieved.extend(_rank_documents(clean_query, fallback_documents, top_k))

    unique_documents: list[str] = []
    seen: set[str] = set()
    for document in retrieved:
        if document in seen:
            continue
        seen.add(document)
        unique_documents.append(document)
        if len(unique_documents) >= top_k:
            break

    return unique_documents
