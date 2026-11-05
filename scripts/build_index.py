"""Build the local FixFlow AI PDF Hybrid RAG index."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # run as: python scripts/<name>.py
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from backend.rag.hybrid import build

if __name__ == "__main__":
    print("Building FixFlow AI Hybrid RAG index...", flush=True)
    count = build()
    print(f"Indexed policy chunks: {count}", flush=True)
    print("Hybrid RAG index build completed.", flush=True)
