# ---- frontend build ----
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.13-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ZIGGGY_HOST=0.0.0.0 ZIGGGY_PORT=8000 ZIGGGY_DATA_DIR=/data ZIGGGY_FRONTEND_DIST=/app/frontend/dist
RUN useradd --create-home --uid 10001 zigggy && mkdir -p /data && chown zigggy:zigggy /data
WORKDIR /app
COPY pyproject.toml requirements.lock README.md alembic.ini ./
COPY backend ./backend
RUN pip install -r requirements.lock && pip install --no-deps . 
COPY --from=web /web/dist ./frontend/dist
USER zigggy
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
STOPSIGNAL SIGTERM
CMD ["zigggy", "serve"]
