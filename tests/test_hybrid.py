import pytest
pytest.importorskip('rank_bm25')
from backend.rag.hybrid import chunks,tokenize

def test_policies():
    policy_chunks = chunks()

    assert len(policy_chunks) >= 4

    for chunk in policy_chunks:
        assert chunk.get("id")
        assert chunk.get("source")
        assert chunk.get("text")
        assert chunk.get("version")

    sources = {chunk["source"] for chunk in policy_chunks}

    assert len(sources) >= 4
def test_tokens():assert tokenize('Refund and RETURN')==['refund','and','return']
