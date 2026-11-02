import os
import requests

BASE = os.getenv("FIXFLOW_API_URL", "http://127.0.0.1:8000")


def call(method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.request(method, BASE + path, headers=headers, timeout=30, **kw)
    if not r.ok:
        raise RuntimeError(f"{r.status_code}: {r.text[:350]}")
    return r.json()
