"""FixFlow AI local Hybrid RAG: approved PDF SOPs -> FAISS + BM25 + RRF.

Customer-facing retrieval excludes the internal escalations SOP by default.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import faiss
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from backend.rag.customer_policy import approved_passages, is_approved_customer_passage

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "policies"
INDEX = ROOT / "local_index"
MODEL = "text-embedding-3-small"
VERSION = "pdf-sop-v2-safe"

APPROVED_POLICIES = (
    "01_Shipping_and_Delivery_SOP.pdf",
    "02_Returns_and_Refunds_SOP.pdf",
    "03_Product_and_Warranty_SOP.pdf",
    "04_Account_and_Login_SOP.pdf",
    "05_Payments_and_Billing_SOP.pdf",
    "06_Escalations_and_Soft_Skills_SOP.pdf",
)
INTERNAL_ONLY = {"06_Escalations_and_Soft_Skills_SOP.pdf"}
DEPARTMENTS = {
    '01_Shipping_and_Delivery_SOP.pdf': 'Shipping & Delivery',
    '02_Returns_and_Refunds_SOP.pdf': 'Returns & Refunds',
    '03_Product_and_Warranty_SOP.pdf': 'Product & Warranty',
    '04_Account_and_Login_SOP.pdf': 'Accounts & Login',
    '05_Payments_and_Billing_SOP.pdf': 'Payments & Billing',
    '06_Escalations_and_Soft_Skills_SOP.pdf': 'Internal Escalations',
}
def citation_metadata(passage):
    source = passage.get('source', '')
    return {'id': passage.get('id'), 'source': source, 'title': source.removesuffix('.pdf').replace('_', ' '),
            'department': DEPARTMENTS.get(source, 'Unknown'), 'page': passage.get('page'),
            'chunk': passage.get('chunk'), 'visibility': passage.get('visibility')}


load_dotenv(ROOT / ".env")


def tokenize(text):
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def split_text(text, chunk_size=1500, overlap=250):
    """Split at word boundaries, with character-bounded overlapping chunks."""
    if chunk_size <= overlap or overlap < 0:
        raise ValueError("chunk_size must be greater than overlap >= 0")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        next_start = max(0, end - overlap)
        # Move to the next word boundary so overlap does not begin mid-word.
        if next_start > 0 and text[next_start - 1] != " ":
            boundary = text.find(" ", next_start, end)
            if boundary != -1:
                next_start = boundary + 1
        if next_start <= start:
            next_start = end
        start = next_start
    return parts

def normalize_pdf_text(text: str) -> str:
    """Repair line-wrapped technical identifiers in PDF text."""
    import re

    # Join uppercase technical identifiers broken across PDF lines.
    # Examples:
    # CARD_DECLINE\nD_05 -> CARD_DECLINED_05
    # 3DS_FAIL_TIMEO\nUT -> 3DS_FAIL_TIMEOUT
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"([A-Z0-9][A-Z0-9_]*_[A-Z0-9_]*)"
        r"[ \t]*\r?\n[ \t]*"
        r"([A-Z0-9_]+)"
        r"(?=[\s`]|$)"
    )

    return pattern.sub(lambda m: m.group(1) + m.group(2), text)
    
def chunks():
    """Return indexed passages with stable IDs and page-level provenance."""
    documents = []
    for filename in APPROVED_POLICIES:
        path = POLICIES / filename
        if not path.is_file():
            raise FileNotFoundError(f"Required approved SOP missing: {path}")
        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            raise ValueError(f"Cannot read PDF {filename}: {exc}") from exc
        count_before = len(documents)
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                raise ValueError(f"Failed extracting {filename}, page {page_number}: {exc}") from exc
            
            page_text = normalize_pdf_text(page_text)
            for chunk_number, passage in enumerate(split_text(page_text), start=1):
                key = f"{filename}|{page_number}|{chunk_number}|{passage}"
                documents.append({
                    "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
                    "source": filename,
                    "title": filename.removesuffix(".pdf").replace("_", " "),
                    "department": DEPARTMENTS[filename],
                    "page": page_number,
                    "chunk": chunk_number,
                    "text": passage,
                    "version": VERSION,
                    "visibility": "internal",
                })
        if len(documents) == count_before:
            raise ValueError(
                f"No extractable text in {filename}. "
                "This PDF may be scanned and require OCR."
            )
    # Original operations manuals are restricted, regardless of filename.
    # Only explicitly curated excerpts are customer-retrievable.
    for passage in approved_passages():
        passage["title"] = passage["source"].removesuffix(".pdf").replace("_", " ")
        passage["department"] = DEPARTMENTS[passage["source"]]
        documents.append(passage)
    return documents


def _digest(docs):
    return hashlib.sha256(json.dumps(docs, sort_keys=True).encode("utf-8")).hexdigest()


def build():
    """Build a complete FAISS/manifest pair; return number of indexed chunks."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY required. Check your project .env file.")
    docs = chunks()
    sources = {doc["source"] for doc in docs}
    if sources != set(APPROVED_POLICIES):
        raise RuntimeError("Not all six approved SOP PDFs were indexed")
    print(f"Loaded {len(sources)} PDF sources and {len(docs)} chunks", flush=True)
    for source in APPROVED_POLICIES:
        print(f"  - {source}", flush=True)

    client = OpenAI()
    embeddings = []
    # Batch requests to avoid overly large embedding inputs.
    for offset in range(0, len(docs), 64):
        batch = docs[offset:offset + 64]
        response = client.embeddings.create(model=MODEL, input=[d["text"] for d in batch])
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(batch):
            raise RuntimeError("Embedding response count mismatch")
        embeddings.extend(item.embedding for item in ordered)

    vectors = np.asarray(embeddings, dtype="float32")
    if vectors.ndim != 2 or vectors.shape[0] != len(docs):
        raise RuntimeError("Invalid embedding vectors")
    faiss.normalize_L2(vectors)
    dense_index = faiss.IndexFlatIP(vectors.shape[1])
    dense_index.add(vectors)

    manifest = {
        "model": MODEL,
        "dimension": int(vectors.shape[1]),
        "chunks": docs,
        "digest": _digest(docs),
    }
    INDEX.mkdir(parents=True, exist_ok=True)
    dense_tmp = INDEX / "dense.faiss.tmp"
    manifest_tmp = INDEX / "manifest.json.tmp"
    faiss.write_index(dense_index, str(dense_tmp))
    manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # Stop the worker/API before rebuilding: the two-file swap is not a single atomic operation.
    os.replace(dense_tmp, INDEX / "dense.faiss")
    os.replace(manifest_tmp, INDEX / "manifest.json")
    print("FAISS index generated successfully", flush=True)
    print("Manifest saved successfully", flush=True)
    return len(docs)


class HybridRetriever:
    """Hybrid search; defaults to customer-safe policy passages only."""

    def __init__(self):
        manifest_path = INDEX / "manifest.json"
        dense_path = INDEX / "dense.faiss"
        if not manifest_path.is_file() or not dense_path.is_file():
            raise FileNotFoundError("RAG index missing. Run python -m scripts.build_index")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.docs = manifest["chunks"]
        self.model = manifest["model"]
        if _digest(self.docs) != manifest["digest"]:
            raise RuntimeError("RAG manifest integrity check failed")
        self.idx = faiss.read_index(str(dense_path))
        if self.idx.ntotal != len(self.docs) or self.idx.d != manifest["dimension"]:
            raise RuntimeError("FAISS index and manifest mismatch")
        self.bm = BM25Okapi([tokenize(doc["text"]) for doc in self.docs])
        self.client = OpenAI()

    def search(self, query, k=4, include_internal=False):
        """Return up to k results, excluding internal SOPs unless explicitly authorized.

        Callers must enforce admin authorization before passing include_internal=True.
        """
        if include_internal:
            # This customer-facing retriever has no authenticated admin context.
            # Never grant internal access based on a caller-controlled boolean.
            raise PermissionError("Internal SOP retrieval requires a separate authorized service")
        if not isinstance(query, str) or not query.strip() or k <= 0:
            return []
        allowed = {
            i for i, doc in enumerate(self.docs)
            if is_approved_customer_passage(doc)
        }
        if not allowed:
            return []
        response = self.client.embeddings.create(model=self.model, input=query)
        query_vector = np.asarray([response.data[0].embedding], dtype="float32")
        faiss.normalize_L2(query_vector)
        _, dense_ids = self.idx.search(query_vector, len(self.docs))
        dense_ranking = [int(i) for i in dense_ids[0] if int(i) in allowed]
        bm_scores = self.bm.get_scores(tokenize(query))
        lexical_ranking = sorted(allowed, key=lambda i: (-float(bm_scores[i]), i))
        scores = {}
        for ranking in (dense_ranking, lexical_ranking):
            for rank, i in enumerate(ranking, 1):
                scores[i] = scores.get(i, 0.0) + 1.0 / (60 + rank)
        ranked = sorted(scores, key=lambda i: (-scores[i], i))
        return [self.docs[i] for i in ranked[:k]]
