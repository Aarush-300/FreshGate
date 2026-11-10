FROM python:3.11-slim

# Create non-root system user
RUN groupadd -r freshgate && useradd -r -g freshgate -d /app freshgate

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code (excluding secrets via .dockerignore)
COPY core/ ./core/
COPY server/ ./server/
COPY dashboard/ ./dashboard/
COPY bench/ ./bench/

# Set ownership to non-root user
RUN chown -R freshgate:freshgate /app
USER freshgate

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
