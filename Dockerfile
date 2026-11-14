FROM node:22-alpine AS dashboard-build

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.11.9-slim

# Create non-root system user
RUN groupadd -r freshgate && useradd -r -g freshgate -d /app freshgate

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code (excluding secrets via .dockerignore)
COPY core/ ./core/
COPY server/ ./server/
COPY --from=dashboard-build /dashboard/dist/ ./dashboard/dist/
COPY bench/ ./bench/
COPY cli/ ./cli/

# Set ownership to non-root user and create persistent data volume
RUN mkdir -p /data && chown -R freshgate:freshgate /app /data
VOLUME ["/data"]
USER freshgate

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
