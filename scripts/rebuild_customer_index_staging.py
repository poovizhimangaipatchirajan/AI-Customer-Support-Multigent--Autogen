"""Build the approved customer index (FAISS) from the approved passage catalog.

Fresh clone / first setup (makes one small embedding call, about one cent):

    python scripts/rebuild_customer_index_staging.py --activate

Without --activate the index is only built and verified in a staging folder
next to the active one. With --activate it is verified again and swapped in
atomically; an existing index is kept as local_customer_index.backup-<id>.
Stop the API and worker (docker compose stop api worker) before activating
an index they are using, then start them again.

The passages come only from backend/rag/customer_policy.py (approved,
customer-visible excerpts); internal SOP text is never embedded.
"""
import argparse
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.rag.customer_index import (  # noqa: E402
    DEFAULT_INDEX, MODEL, activate, build_staging, validated_documents, verify_index)

DIMENSION = 1536  # text-embedding-3-small


def build(embed_documents, *, active_dir=DEFAULT_INDEX, activate_now=False):
    """Build, verify and optionally activate the index. Returns the index path."""
    docs = validated_documents()
    active = Path(active_dir)
    stage = active.with_name(f'{active.name}.staging-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}')
    stage = build_staging(embed_documents, staging_dir=stage, model=MODEL)
    manifest, index = verify_index(stage)
    if manifest['model'] != MODEL or index.d != DIMENSION or index.ntotal != len(docs):
        raise RuntimeError(f'Unexpected index: model={manifest["model"]} dim={index.d} '
                           f'vectors={index.ntotal} passages={len(docs)}')
    print(f'Built and verified: {len(docs)} approved passages, {index.d} dimensions -> {stage}')
    if not activate_now:
        print('Not activated. Re-run with --activate to make it the live index.')
        return stage
    result = activate(stage, active_dir=active, approved=True)
    verify_index(active)
    print(f'Activated: {result["active"]}')
    if result['backup']:
        print(f'Previous index kept at: {result["backup"]}')
    return active


def openai_embedder():
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(ROOT / '.env')
    if not os.getenv('OPENAI_API_KEY'):
        sys.exit('OPENAI_API_KEY is not set (copy .env.example to .env and add your key).')
    client = OpenAI(timeout=60.0, max_retries=2)

    def embed_documents(texts):
        data = sorted(client.embeddings.create(model=MODEL, input=texts).data, key=lambda d: d.index)
        if len(data) != len(texts):
            raise RuntimeError('Embedding count mismatch')
        return [d.embedding for d in data]
    return embed_documents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--activate', action='store_true', help='make the verified index the live one')
    parser.add_argument('--active-dir', default=str(DEFAULT_INDEX), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    build(openai_embedder(), active_dir=args.active_dir, activate_now=args.activate)


if __name__ == '__main__':
    main()
