# --- Build Frontend ---
FROM node:20.19 AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ .
RUN npm run build

# --- Build Backend ---
FROM python:3.11-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*


WORKDIR /app/backend
COPY backend/deps/ ./deps/
RUN ls -lah ./deps
RUN pip install --no-cache-dir ./deps/radarclient-*.whl


RUN pip install --no-cache-dir -U apple-interlinked \
    -i https://pypi.apple.com/simple --trusted-host pypi.apple.com

COPY backend/requirements.txt ./requirements.txt
RUN grep -v '^\s*radarclient' requirements.txt > /tmp/reqs.txt && \
    pip install --no-cache-dir -r /tmp/reqs.txt


COPY backend/ /app/backend/


WORKDIR /app
COPY --from=frontend-builder /app/frontend/dist ./frontend-dist

EXPOSE 8001
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8001"]