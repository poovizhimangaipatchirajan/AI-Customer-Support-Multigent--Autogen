
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# Allow Python to import frontend modules from /app
ENV PYTHONPATH=/app

COPY frontend/requirements.txt /app/frontend/requirements.txt

RUN pip install --no-cache-dir -r /app/frontend/requirements.txt

COPY frontend /app/frontend
COPY .streamlit /app/.streamlit

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app

USER appuser

EXPOSE 8501

CMD ["streamlit", "run", "frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
