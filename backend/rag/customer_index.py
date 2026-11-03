"""Phase 2E.4: isolated, approved-only customer hybrid index.

Staging and activation are deliberately separate operations. Neither is invoked
by application startup. The embedding provider is injected for offline testing.
"""
import hashlib
import json
import os
import shutil
import re
import uuid
from pathlib import Path

from backend.rag.customer_policy import approved_passages, is_approved_customer_passage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / 'local_customer_index'
MODEL = 'text-embedding-3-small'
RRF_K = 60

def tokenize(value):
    return re.findall(r"\w+", value.lower(), flags=re.UNICODE)


def _digest(documents):
    return hashlib.sha256(json.dumps(documents, sort_keys=True).encode('utf-8')).hexdigest()


def validated_documents():
    docs = approved_passages()
    if len(docs) != 12 or len({d['id'] for d in docs}) != len(docs):
        raise ValueError('Approved customer passage count/identity mismatch')
    if not all(is_approved_customer_passage(d) for d in docs):
        raise ValueError('Unapproved customer passage')
    return docs


def _validate_vectors(vectors, count):
    import numpy as np
    arr = np.asarray(vectors, dtype='float32')
    if arr.ndim != 2 or arr.shape[0] != count or arr.shape[1] < 1:
        raise ValueError('Embedding shape mismatch')
    if not np.isfinite(arr).all() or (np.linalg.norm(arr, axis=1) == 0).any():
        raise ValueError('Invalid embedding vectors')
    return arr


def build_staging(embed_documents, *, staging_dir, model=MODEL):
    """Create isolated staged artifacts. embed_documents(texts) is injected.

    NEVER call this with a paid embedding implementation without user approval.
    """
    import faiss
    docs = validated_documents()
    stage = Path(staging_dir)
    if stage.exists():
        raise FileExistsError(f'Staging directory already exists: {stage}')
    stage.mkdir(parents=True)
    try:
        vectors = _validate_vectors(embed_documents([d['text'] for d in docs]), len(docs))
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        manifest = {'kind': 'approved-customer-only-v1', 'model': model,
                    'dimension': int(vectors.shape[1]), 'chunks': docs,
                    'digest': _digest(docs)}
        faiss.write_index(index, str(stage / 'dense.faiss'))
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        verify_index(stage)
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_index(directory):
    """Fail closed on unapproved content, malformed metadata or vector mismatch."""
    import faiss
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    docs = manifest['chunks']
    if (manifest.get('kind') != 'approved-customer-only-v1' or
        manifest.get('digest') != _digest(docs) or
        len(docs) != 12 or len({d.get('id') for d in docs}) != len(docs) or
        {d['id'] for d in docs} != {d['id'] for d in validated_documents()} or
        not all(is_approved_customer_passage(d) for d in docs)):
        raise ValueError('Customer index approval/integrity validation failed')
    idx = faiss.read_index(str(directory / 'dense.faiss'))
    if idx.ntotal != len(docs) or idx.d != manifest['dimension']:
        raise ValueError('Customer index vector/manifest mismatch')
    return manifest, idx


def activate(staging_dir, *, active_dir=DEFAULT_INDEX, backup_dir=None, approved=False):
    """Explicit, offline atomic directory activation; stop API/workers first.

    Caller must separately verify approval and service shutdown. A rollback
    backup is retained; never silently replace an existing backup.
    """
    if approved is not True:
        raise PermissionError('Explicit human approval required for index activation')
    stage, active = Path(staging_dir), Path(active_dir)
    verify_index(stage)
    if not stage.is_dir() or stage.resolve() == active.resolve():
        raise ValueError('Invalid staging directory')
    backup = Path(backup_dir) if backup_dir else active.with_name(active.name + '.backup-' + uuid.uuid4().hex[:12])
    if backup.exists():
        raise FileExistsError('Backup destination already exists')
    active.parent.mkdir(parents=True, exist_ok=True)
    if stage.stat().st_dev != active.parent.stat().st_dev:
        raise ValueError('Staging and active directory must share a filesystem')
    had_active = active.exists()
    if had_active:
        # Preserve old index before swapping; old index may use legacy format.
        os.replace(active, backup)
    try:
        os.replace(stage, active)
        verify_index(active)
    except BaseException:
        if active.exists():
            failed = active.with_name(active.name + '.failed-' + uuid.uuid4().hex[:12])
            os.replace(active, failed)
        if had_active:
            os.replace(backup, active)
        raise
    return {'active': str(active), 'backup': str(backup) if had_active else None}


def rollback(*, active_dir=DEFAULT_INDEX, backup_dir, approved=False):
    """Explicit rollback. Stop API/workers before calling."""
    if approved is not True:
        raise PermissionError('Explicit human approval required for rollback')
    active, backup = Path(active_dir), Path(backup_dir)
    if not backup.is_dir():
        raise FileNotFoundError('Rollback backup not found')
    # Do not delete the current index; retain it for investigation.
    displaced = active.with_name(active.name + '.rolled-back-' + uuid.uuid4().hex[:12])
    if active.exists():
        os.replace(active, displaced)
    try:
        os.replace(backup, active)
    except BaseException:
        if displaced.exists():
            os.replace(displaced, active)
        raise
    return {'active': str(active), 'displaced': str(displaced) if displaced.exists() else None}


class CustomerHybridRetriever:
    """FAISS + BM25 with RRF, using only verified customer-approved documents."""
    def __init__(self, *, index_dir=DEFAULT_INDEX, embed_query=None):
        from rank_bm25 import BM25Okapi
        self.manifest, self.index = verify_index(index_dir)
        self.docs = self.manifest['chunks']
        self.bm = BM25Okapi([tokenize(d['text']) for d in self.docs])
        self.embed_query = embed_query

    def search(self, query, k=4, include_internal=False):
        import faiss
        import numpy as np
        if include_internal:
            raise PermissionError('Customer retrieval cannot expose internal SOPs')
        if not isinstance(query, str) or not query.strip() or k <= 0:
            return []
        if self.embed_query is None:
            raise RuntimeError('Query embedding provider not configured')
        query_tokens = set(tokenize(query))
        # Stopwords cannot alone establish customer-policy relevance.
        stop = {"the", "and", "for", "with", "what", "how", "does", "can", "are", "you", "your", "please", "tell", "about", "policy", "novacart"}
        meaningful = query_tokens - stop
        if not meaningful:
            return []
        eligible = {i for i, doc in enumerate(self.docs)
                    if meaningful.intersection(tokenize(doc["text"]))}
        if not eligible:
            return []
        vector = _validate_vectors([self.embed_query(query)], 1)
        if vector.shape[1] != self.index.d:
            raise ValueError('Query embedding dimension mismatch')
        faiss.normalize_L2(vector)
        _, ids = self.index.search(vector, len(self.docs))
        dense = [int(i) for i in ids[0] if int(i) in eligible]
        scores = self.bm.get_scores(tokenize(query))
        lexical = sorted(eligible, key=lambda i: (-float(scores[i]), i))
        fused = {}
        for ranking in (dense, lexical):
            for rank, idx in enumerate(ranking, 1):
                fused[idx] = fused.get(idx, 0) + 1 / (RRF_K + rank)
        ranked = sorted(fused, key=lambda i: (-fused[i], i))
        return [self.docs[i] for i in ranked[:k] if is_approved_customer_passage(self.docs[i])]
