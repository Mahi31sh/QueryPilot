
"""
Builds a FAISS schema index and retrieves the schema chunks most relevant
to a question at runtime.

Retrieval modes (settings.retrieval_mode):
  - "vector":  embed the question and search the FAISS index (cosine
               similarity via normalized inner product). Falls back to
               keyword matching if the index or embedding model fails.
  - "keyword": term-overlap scoring only; never loads the embedding model.
"""
import json
import logging
import os
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import faiss
import numpy as np

from app.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: Any | None = None


def get_embedding_model() -> "SentenceTransformer":
    """Load the embedding model only when building the index."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Create normalized embeddings for schema-index construction."""
    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return embeddings.astype("float32")


def build_schema_index(schema_chunks: list[dict]) -> None:
    """Build and save the FAISS index and its schema metadata."""
    texts = [chunk["text"] for chunk in schema_chunks]
    vectors = embed_texts(texts)

    dimension = vectors.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(vectors)

    directory = os.path.dirname(settings.faiss_index_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    faiss.write_index(index, f"{settings.faiss_index_path}.index")

    with open(f"{settings.faiss_index_path}.meta.json", "w", encoding="utf-8") as f:
        json.dump(schema_chunks, f)

    logger.info("Built FAISS index with %s schema chunks.", len(schema_chunks))


@lru_cache(maxsize=1)
def load_schema_index() -> tuple[faiss.Index, list[dict]]:
    """Load the existing FAISS index and metadata, caching them per process."""
    index_path = f"{settings.faiss_index_path}.index"
    meta_path = f"{settings.faiss_index_path}.meta.json"

    if not os.path.exists(index_path) or not os.path.exists(meta_path):
        raise FileNotFoundError(
            "Schema index not found. Run the schema index build script first."
        )

    index = faiss.read_index(index_path)
    with open(meta_path, encoding="utf-8") as f:
        metadata = json.load(f)

    return index, metadata


@lru_cache(maxsize=1)
def load_schema_metadata() -> list[dict]:
    """Load only schema metadata, without loading the embedding model."""
    meta_path = f"{settings.faiss_index_path}.meta.json"

    if not os.path.exists(meta_path):
        raise FileNotFoundError(
            "Schema metadata not found. Build the schema index first."
        )

    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=256)
def _embed_query(query: str) -> np.ndarray:
    """Embed a question once; repeated lookups for the same question are free."""
    return embed_texts([query])


def _vector_search(query: str, top_k: int) -> list[dict]:
    index, metadata = load_schema_index()
    k = min(max(top_k, 0), index.ntotal)
    if k == 0:
        return []

    scores, ids = index.search(_embed_query(query), k)

    results = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0 or idx >= len(metadata):
            continue
        chunk = metadata[idx].copy()
        chunk["similarity"] = float(score)
        results.append(chunk)
    return results


def _keyword_search(query: str, top_k: int) -> list[dict]:
    """Scores schema chunks by matching question terms against their text."""
    metadata = load_schema_metadata()
    terms = set(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", query.lower()))

    scored = []
    for position, chunk in enumerate(metadata):
        table_name = chunk.get("table_name", "")
        text = chunk.get("text", "")
        searchable = f"{table_name} {text}".lower()
        words = set(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", searchable))

        overlap = terms & words
        score = len(overlap) / max(len(terms), 1)

        # Give an explicit table-name match extra weight.
        if table_name.lower() in terms:
            score += 1.0

        result = chunk.copy()
        result["similarity"] = float(score)
        scored.append((score, position, result))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[:max(0, top_k)]]


def search_schema(query: str, top_k: int = 4) -> list[dict]:
    """Return the top_k schema chunks most relevant to the question."""
    if settings.retrieval_mode == "vector":
        try:
            return _vector_search(query, top_k)
        except Exception as exc:  # missing index, model download failure, etc.
            logger.warning(
                "Vector retrieval failed (%s); falling back to keyword retrieval.", exc
            )
    return _keyword_search(query, top_k)
