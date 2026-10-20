# --- build the single-page app
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- API + static files
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BENCHTOP_DATA_DIR=/data BENCHTOP_STATIC_DIR=/app/static BENCHTOP_BUNDLED_PLUGINS_DIR=/app/plugins
WORKDIR /app
# RDKit draws structures with X rendering libraries the slim image leaves out; without them Benchtop can't start
RUN apt-get update && apt-get install -y --no-install-recommends libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]>=3.2"
COPY backend/app ./app
COPY plugins ./plugins
COPY --from=web /web/dist ./static
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
