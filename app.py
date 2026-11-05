"""FixFlow AI Streamlit entry point.

Run from the project root:  streamlit run app.py
The FastAPI backend (uvicorn backend.main:app --port 8000) and the worker
(python -m backend.worker.main) must be running; see README.md.
This file only launches the existing UI in frontend/app.py.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "frontend" / "app.py"), run_name="__main__")
