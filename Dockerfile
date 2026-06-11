# Restaurant GM — single image for BOTH Cloud Run services:
#   app service (default CMD): FastAPI backend + built dashboard + chat agents
#   worker service:            same image, command override `python -m plumbing.launcher`
#
# Python (ADK agents + plumbing) AND Node (the MongoDB MCP server runs via npx)
# live in one image; the dashboard is built in a separate stage.

# ── stage 1: build the React dashboard ───────────────────────────────────────
FROM node:20-slim AS dashboard
WORKDIR /build
COPY dashboard/package*.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# ── stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# Node 20 for `npx mongodb-mcp-server`; preinstall the server so the first
# agent call doesn't pay an npm download.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g mongodb-mcp-server \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=dashboard /build/dist ./dashboard/dist

ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
