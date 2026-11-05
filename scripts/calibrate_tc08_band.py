"""One-shot, READ-ONLY calibration of rank_approved's SECONDARY_BAND.

What it does
  * Validates and opens the existing approved-only index (local_customer_index)
    exactly as the application does. It never rebuilds, activates or writes it.
  * Makes EXACTLY ONE paid query-time embedding call (text-embedding-3-small,
    ~60 tokens) for the fixed TC08 question below.
  * Prints cosine scores for every approved passage and which passages the
    ranker returns for several candidate band widths.

What it never does
  * No model/chat calls, no ticket creation, no database or file writes,
    no index changes, no network calls other than the single embedding request.
  * The API key is read from OPENAI_API_KEY (or the project's .env) and is
    never printed.

Usage (from the project root, same Python environment as pytest)
  python scripts\\calibrate_tc08_band.py --selftest   # free, offline, no key needed
  python scripts\\calibrate_tc08_band.py --yes        # the single approved paid call

The annotations printed next to passage IDs are diagnostic labels only. They
are NOT used by retrieval and must never become routing rules.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TC08_QUERY = (
    "I ordered headphones from NovaCart last week, but they arrived in the wrong "
    "size. I want to exchange them for a different colour. If the replacement is "
    "unavailable, can I get a refund instead? Please explain the eligibility "
    "conditions and what I need to do next."
)
BANDS = (0.025, 0.05, 0.075, 0.10, 0.15)

# Diagnostic annotations only (from the project handoff). Not used for routing.
ANNOTATIONS = {
    "e613368ccb42a62f": "electronics 7-day window  [plausibly relevant: headphones]",
    "ca91ca28b962d2b7": "return initiation          [plausibly relevant]",
    "f19af5c766a3e755": "fashion 14-day window      [must NOT be cited for headphones]",
    "c4345462575a7e45": "delivered-not-received    [must NOT be cited here]",
}


def _read_api_key():
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key.strip()
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def analyse(docs, vectors, query_vector, query, bands=BANDS):
    """Pure function: score every approved passage and run the ranker per band."""
    from backend.rag.semantic_rank import (
        MIN_SIMILARITY, meaningful_tokens, normalize_vectors, rank_approved)
    matrix = normalize_vectors(vectors, len(docs))
    qvec = normalize_vectors([query_vector], 1)[0]
    scores = matrix @ qvec
    qtokens = meaningful_tokens(query)
    top = float(scores.max())
    rows = []
    for i, d in enumerate(docs):
        rows.append({
            "id": d["id"], "source": d["source"], "page": d.get("page"),
            "cos": float(scores[i]), "gap": top - float(scores[i]),
            "overlap": len(qtokens & meaningful_tokens(d["text"])),
            "text": " ".join(d["text"].split())[:64],
        })
    rows.sort(key=lambda r: -r["cos"])
    by_band = {}
    for band in bands:
        got = rank_approved(query, docs, query_vector, vectors, k=4,
                            tie_band=0.025, secondary_band=band)
        by_band[band] = [(g["id"], g["source"][:2], g["semantic_score"]) for g in got]
    return rows, by_band, MIN_SIMILARITY


def report(rows, by_band, floor):
    print(f"\nSimilarity floor (MIN_SIMILARITY): {floor}")
    print("\nAll approved passages by cosine similarity to the TC08 question")
    print(f"{'#':>2} {'id':<17}{'src':<4}{'pg':>3} {'cosine':>8} {'gap':>7} {'lex':>4}  note / text")
    for n, r in enumerate(rows, 1):
        note = ANNOTATIONS.get(r["id"], r["text"])
        flag = "  <-- below floor" if r["cos"] < floor else ""
        print(f"{n:>2} {r['id']:<17}{r['source'][:2]:<4}{str(r['page']):>3} "
              f"{r['cos']:>8.4f} {r['gap']:>7.4f} {r['overlap']:>4}  {note}{flag}")
    print("\nRanker output per SECONDARY_BAND (k=4; 0.025 reproduces the ORIGINAL behaviour)")
    for band, got in by_band.items():
        tag = " (original)" if band == 0.025 else ""
        shown = ", ".join(f"{i}[{s}] {sc}" for i, s, sc in got) or "(abstained / none)"
        print(f"  band {band:<6}{tag:<11}-> {shown}")
    print("\nlex = number of meaningful query tokens shared with the passage (0 = no lexical support).")


def selftest():
    """Offline check of this script's own logic using synthetic vectors."""
    import numpy as np
    from backend.rag.customer_policy import approved_passages
    docs = approved_passages()
    vectors = np.eye(len(docs), dtype="float32")
    qvec = np.zeros(len(docs), dtype="float32")
    qvec[1], qvec[5], qvec[9] = 0.70, 0.66, 0.10
    rows, by_band, floor = analyse(docs, vectors, qvec, TC08_QUERY)
    assert len(rows) == len(docs) and rows[0]["cos"] >= rows[-1]["cos"]
    assert set(by_band) == set(BANDS)
    print("SELFTEST: synthetic data, no network, no key used.")
    report(rows, by_band, floor)
    print("\nSELFTEST OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true", help="offline check; no paid call")
    ap.add_argument("--yes", action="store_true",
                    help="confirm the single approved paid embedding call")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.yes:
        sys.exit("Refusing to run: pass --yes to make the single approved embedding call "
                 "(or --selftest for a free offline check).")
    key = _read_api_key()
    if not key:
        sys.exit("OPENAI_API_KEY not found in the environment or .env. No call was made.")

    from backend.rag.customer_index import DEFAULT_INDEX, MODEL, verify_index
    manifest, index = verify_index(DEFAULT_INDEX)          # read-only validation
    docs = manifest["chunks"]
    vectors = [index.reconstruct(i) for i in range(len(docs))]
    print(f"Index OK: kind={manifest['kind']} model={manifest['model']} "
          f"dim={manifest['dimension']} passages={len(docs)}")

    from openai import OpenAI
    client = OpenAI(api_key=key)
    print(f"Making ONE embedding call with {MODEL} ...")
    query_vector = client.embeddings.create(model=MODEL, input=TC08_QUERY).data[0].embedding
    rows, by_band, floor = analyse(docs, vectors, query_vector, TC08_QUERY)
    report(rows, by_band, floor)
    print("\nDone. One embedding call made. Nothing was written.")


if __name__ == "__main__":
    main()
