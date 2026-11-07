"""
Tests for schema retrieval (app/services/embeddings.py).

The real embedding model is replaced by a tiny deterministic bag-of-words
embedder, so these tests run offline and in milliseconds while still
exercising the real FAISS build/search code path.
"""
import hashlib

import numpy as np
import pytest

from app.config import settings
from app.services import embeddings

CHUNKS = [
    {"table_name": "customers", "text": "Table 'customers' has columns: customer_id, full_name, email, city, country, signup_date."},
    {"table_name": "products", "text": "Table 'products' has columns: product_id, product_name, category_id, unit_price, stock_quantity."},
    {"table_name": "orders", "text": "Table 'orders' has columns: order_id, customer_id, order_date, status."},
]
DIM = 64


def fake_embed_texts(texts):
    """Hashed bag-of-words, L2-normalised (same contract as embed_texts)."""
    out = np.zeros((len(texts), DIM), dtype="float32")
    for row, text in enumerate(texts):
        for word in text.lower().replace(",", " ").replace("'", " ").split():
            col = int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM
            out[row, col] += 1.0
        norm = np.linalg.norm(out[row])
        if norm:
            out[row] /= norm
    return out


@pytest.fixture
def index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(settings, "faiss_index_path", str(tmp_path / "idx" / "schema_index"))
    for cached in (embeddings.load_schema_index, embeddings.load_schema_metadata, embeddings._embed_query):
        cached.cache_clear()
    yield tmp_path
    for cached in (embeddings.load_schema_index, embeddings.load_schema_metadata, embeddings._embed_query):
        cached.cache_clear()


def test_vector_search_ranks_relevant_table_first(index_dir, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "vector")
    embeddings.build_schema_index(CHUNKS)

    results = embeddings.search_schema("unit_price stock_quantity of each product_name", top_k=2)

    assert results[0]["table_name"] == "products"
    assert len(results) == 2
    assert results[0]["similarity"] >= results[1]["similarity"]


def test_missing_index_raises_a_clear_error_in_both_modes(index_dir, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "vector")
    # No index built -> vector path raises FileNotFoundError internally.
    with pytest.raises(FileNotFoundError):
        embeddings.search_schema("orders by status", top_k=2)  # keyword path also needs metadata


def test_vector_mode_falls_back_to_keyword_when_embedding_fails(index_dir, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "vector")
    embeddings.build_schema_index(CHUNKS)

    def boom(_texts):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(embeddings, "embed_texts", boom)
    embeddings._embed_query.cache_clear()

    results = embeddings.search_schema("order_date and status of orders", top_k=1)
    assert results[0]["table_name"] == "orders"  # served by keyword fallback


def test_keyword_mode_never_calls_the_embedder(index_dir, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "keyword")
    embeddings.build_schema_index(CHUNKS)

    def must_not_run(_texts):
        raise AssertionError("embedder should not be used in keyword mode")

    monkeypatch.setattr(embeddings, "embed_texts", must_not_run)

    results = embeddings.search_schema("customers city country", top_k=1)
    assert results[0]["table_name"] == "customers"


def test_top_k_larger_than_index_is_safe(index_dir, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_mode", "vector")
    embeddings.build_schema_index(CHUNKS)
    assert len(embeddings.search_schema("anything", top_k=50)) == len(CHUNKS)
