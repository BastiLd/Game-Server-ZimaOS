# CraftControl Panel - FastAPI + statisches Frontend in einem Image
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CRAFTCONTROL_WEB_DIR=/app/web

WORKDIR /app

# System-Pakete: tini fuer sauberes Signal-Handling
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Python-Abhaengigkeiten zuerst (bessere Layer-Caches)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Quellcode + Frontend
COPY backend /app/backend
COPY web /app/web

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
