"""Single local endpoint configuration for FixFlow browser E2E tests.

Use the same FastAPI and Streamlit processes for both browser test modules.
Old FIXFLOW_TEST_API_URL / FIXFLOW_TEST_UI_URL / FIXFLOW_UI_URL values
are intentionally ignored to prevent accidental port switching.
"""
import os

API_URL = os.getenv("FIXFLOW_E2E_API_URL", "http://127.0.0.1:8000").rstrip("/")
UI_URL = os.getenv("FIXFLOW_E2E_UI_URL", "http://127.0.0.1:8501").rstrip("/")
