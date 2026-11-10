FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY core/ ./core/
COPY server/ ./server/
COPY dashboard/ ./dashboard/
COPY bench/ ./bench/
COPY .env.example .env

ENV PORT=8000
ENV BIFROST_UPSTREAM_URL=http://bifrost:8080

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
