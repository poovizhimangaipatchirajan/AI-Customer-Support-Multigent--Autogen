"""Shared test safety: the default test run is offline and side-effect free.

Loaded by pytest before any test module imports the backend. Unless one of the
explicit live flags is set, real credentials from .env are never used: the
backend's load_dotenv() does not override variables that already exist, so the
values below win. This guarantees a plain `python -m pytest` cannot make paid
OpenAI/Serper calls or send verification email.
"""
import os

LIVE_FLAGS = (
    'FIXFLOW_RUN_BUILDATHON_LIVE', 'FIXFLOW_RUN_LIVE_AI', 'FIXFLOW_RUN_LIVE_MCP',
    'FIXFLOW_RUN_LIVE_SEARCH_BEHAVIOR', 'FIXFLOW_RUN_LIVE_AGENT_TESTS',
)

if not any(os.getenv(flag) == '1' for flag in LIVE_FLAGS):
    os.environ['OPENAI_API_KEY'] = 'sk-offline-test-key'
    os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:9/v1'  # unroutable: fails fast, never billed
    os.environ['SERPER_API_KEY'] = 'offline-test-key'
    os.environ['FIXFLOW_SMTP_APP_PASSWORD'] = ''  # mailer refuses to send without it
