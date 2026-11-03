"""Explicit customer-only runtime selection. Never silently fall back on failure."""
import os
from functools import lru_cache
from backend.rag.customer_index import CustomerHybridRetriever


def customer_mode():
    mode = os.getenv("FIXFLOW_CUSTOMER_INDEX_MODE", "legacy")
    if mode not in ("legacy", "approved_only"):
        raise ValueError("Invalid FIXFLOW_CUSTOMER_INDEX_MODE")
    return mode


def create_customer_retriever(*, embed_query=None, index_dir=None):
    if customer_mode() == "legacy":
        from backend.rag.hybrid import HybridRetriever
        return HybridRetriever()
    if embed_query is None:
        from openai import OpenAI
        from backend.rag.customer_index import MODEL
        client = OpenAI()
        def embed_query(text):
            return client.embeddings.create(model=MODEL, input=text).data[0].embedding
    kwargs = {"embed_query": embed_query}
    if index_dir is not None:
        kwargs["index_dir"] = index_dir
    return CustomerHybridRetriever(**kwargs)
