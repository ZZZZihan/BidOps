FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    BIDOPS_ENTERPRISE_ENABLED=true BIDOPS_NETWORK_ENABLED=false
COPY pyproject.toml uv.lock README.md ./
COPY backend/ ./backend/
COPY config/ ./config/
RUN uv sync --frozen --no-dev --no-editable && \
    useradd --uid 10001 --create-home bidops && \
    mkdir -p data private && chown -R bidops:bidops data private
COPY --from=frontend /build/dist ./frontend/dist
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request,urllib.parse; host=urllib.parse.urlsplit(os.environ['BIDOPS_ENTERPRISE_PUBLIC_URL']).netloc; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/health',headers={'Host':host}),timeout=3)"
CMD ["bidops-enterprise", "serve", "--host", "0.0.0.0", "--port", "8765"]
