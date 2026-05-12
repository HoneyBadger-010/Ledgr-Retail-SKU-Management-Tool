# Ledgr — Demand-forecasting SaaS for FMCG distributors
# Production-ready Docker image with security hardening

FROM python:3.12-slim AS runtime

# Security and performance environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=5000 \
    PYTHONHASHSEED=random

# Install system dependencies and security updates
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

WORKDIR /app

# Install Python dependencies first (layer caching optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user with fixed UID/GID for security
RUN groupadd --system --gid 1001 ledgr && \
    useradd --system --uid 1001 --gid ledgr --create-home --shell /bin/false ledgr && \
    mkdir -p /app/logs /app/data/processed /app/data/uploads && \
    chown -R ledgr:ledgr /app && \
    chmod -R 755 /app && \
    chmod -R 700 /app/logs /app/data

# Switch to non-root user
USER ledgr

# Expose port (Railway sets PORT env variable dynamically)
EXPOSE ${PORT:-5000}

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-5000}/login" >/dev/null || exit 1

# Production-optimized Gunicorn configuration
# - Workers: 4 (adjust based on CPU cores: 2-4 x cores)
# - Preload: Load app before forking workers (memory efficient)
# - Max requests: Restart workers after 1000 requests (prevent memory leaks)
# - Max requests jitter: Add randomness to prevent thundering herd
# - Timeout: 120s for long-running pipeline operations
# - Access log: Stream to stdout for container logging
# - Error log: Stream to stderr for container logging
# Railway sets PORT dynamically, so we use it instead of hardcoding 5000
CMD gunicorn \
     --workers 4 \
     --bind 0.0.0.0:${PORT:-5000} \
     --preload \
     --max-requests 1000 \
     --max-requests-jitter 50 \
     --timeout 120 \
     --access-logfile - \
     --error-logfile - \
     --log-level info \
     --worker-class sync \
     --worker-tmp-dir /dev/shm \
     app:app
