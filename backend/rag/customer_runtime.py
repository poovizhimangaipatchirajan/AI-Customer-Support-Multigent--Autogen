"""Explicit customer-only runtime selection. Never silently fall back on failure."""
import os
from functools import lru_cache
from backend.rag.customer_index import CustomerHybridRetriever


def customer_mode():
    mode = os.getenv("FIXFLOW_CUSTOMER_INDEX_MODE", "legacy")
    if mode not in ("legacy", "approved_only"):
        raise ValueError("Invalid FIXFLOW_CUSTOMER_INDEX_MODE")
    return mode


@lru_cache(maxsize=2)
def _process_retriever(mode):
    """One validated FAISS/BM25 pair per mode per process.

    Rebuild/activate indexes only while services are stopped; restart API and
    worker after activation so no process retains a stale in-memory index.
    """
    if mode == "legacy":
        from backend.rag.hybrid import HybridRetriever
        return HybridRetriever()
    return _new_approved_retriever()


def _new_approved_retriever(*, embed_query=None, index_dir=None):
    if embed_query is None:
        from openai import OpenAI
        from backend.rag.customer_index import MODEL
        # Query embeddings run inside API requests (Help Center): keep them short.
        client = OpenAI(timeout=10.0, max_retries=1)
        def embed_query(text):
            return client.embeddings.create(model=MODEL, input=text).data[0].embedding
    kwargs = {"embed_query": embed_query}
    if index_dir is not None:
        kwargs["index_dir"] = index_dir
    return CustomerHybridRetriever(**kwargs)


def create_customer_retriever(*, embed_query=None, index_dir=None):
    """Reuse production retrievers; injected/test configurations stay isolated."""
    mode = customer_mode()  # Always validate mode before using a cached instance.
    if embed_query is None and index_dir is None:
        return _process_retriever(mode)
    if mode == "legacy":
        # Legacy mode has no injectable embedding/index interface.
        raise ValueError("Custom embedding/index settings require approved_only mode")
    return _new_approved_retriever(embed_query=embed_query, index_dir=index_dir)


def clear_retriever_cache():
    """For tests/controlled shutdown only; not live index hot-swapping."""
    _process_retriever.cache_clear()
