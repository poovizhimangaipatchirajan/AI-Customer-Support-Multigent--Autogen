"""Read-only source metadata resolver; no customer text or internal passage exposed."""
import json
from pathlib import Path
from functools import lru_cache
from backend.rag.hybrid import INDEX, citation_metadata, INTERNAL_ONLY
from backend.rag.customer_runtime import customer_mode
from backend.rag.customer_index import DEFAULT_INDEX, verify_index

@lru_cache(maxsize=2)
def _load_manifest(path, mtime_ns):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {d['id']: citation_metadata(d) for d in data.get('chunks', [])}

def resolve(ids):
    if customer_mode() == "approved_only":
        try:
            manifest, _ = verify_index(DEFAULT_INDEX)
        except (OSError, ValueError, KeyError, RuntimeError):
            return []
        catalog = {d["id"]: citation_metadata(d) for d in manifest["chunks"]}
        return [catalog[cid] for cid in ids if cid in catalog]
    path = INDEX / 'manifest.json' 
    if not path.is_file():
        return []
    catalog = _load_manifest(str(path), path.stat().st_mtime_ns)
    result = []
    for cid in ids:
        meta = catalog.get(cid)
        if meta and meta['visibility'] == 'customer' and meta['source'] not in INTERNAL_ONLY:
            result.append(meta)
    return result
