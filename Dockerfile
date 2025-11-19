# Multi-stage build for keeper bots
FROM python:3.13-slim AS base

# Install system dependencies in a separate cached layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

FROM base AS builder

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache \
    POETRY_VIRTUALENVS_CREATE=false

# Set work directory
WORKDIR /app

# Install Poetry in isolated location to prevent it from being removed during sync
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --no-warn-script-location poetry==2.1.4
ENV PATH="/root/.local/bin:$PATH"

# Copy poetry files first for better caching
COPY pyproject.toml poetry.lock ./

# Configure poetry and install dependencies with cache mount
RUN --mount=type=cache,target=/tmp/poetry_cache \
    --mount=type=cache,target=/root/.cache/pip \
    poetry config virtualenvs.create false \
    && poetry sync --only=main --no-root --no-interaction --no-ansi

# Production stage
FROM python:3.13-slim

# Accept build arguments

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    CHIA_ROOT=/app/.chia

# Create non-root user and app directory in one layer
RUN useradd --create-home --shell /bin/bash keeper \
    && mkdir -p /app \
    && chown -R keeper:keeper /app

# Set work directory
WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy logging configuration first (changes less frequently)
COPY --chown=keeper:keeper log_conf.yaml logging_filters.py ./

# Copy application code (changes more frequently, keep this last)
COPY --chown=keeper:keeper keeper_bots/ ./keeper_bots/
COPY --chown=keeper:keeper *.py *.md *.txt *.yaml *.toml *.lock ./

# Switch to non-root user
USER keeper


# Expose port (though these are background services, not HTTP servers)
EXPOSE 8080

# Health check script - created inline for compatibility with older Docker versions
RUN echo '#!/usr/bin/env python3' > /app/healthcheck.py && \
    echo 'import sys' >> /app/healthcheck.py && \
    echo 'import os' >> /app/healthcheck.py && \
    echo '' >> /app/healthcheck.py && \
    echo 'def main():' >> /app/healthcheck.py && \
    echo '    """Simple health check that verifies the environment and dependencies"""' >> /app/healthcheck.py && \
    echo '    try:' >> /app/healthcheck.py && \
    echo '        # Check if we can import the keeper_bots module' >> /app/healthcheck.py && \
    echo '        import keeper_bots' >> /app/healthcheck.py && \
    echo '        ' >> /app/healthcheck.py && \
    echo '        # Check if required environment variables are set' >> /app/healthcheck.py && \
    echo "        required_vars = ['PRIVATE_KEY', 'CHIA_ROOT', 'RPC_URL', 'CHIA_NETWORK', 'ADD_SIG_DATA']" >> /app/healthcheck.py && \
    echo '        missing_vars = [var for var in required_vars if not os.getenv(var)]' >> /app/healthcheck.py && \
    echo '        ' >> /app/healthcheck.py && \
    echo '        if missing_vars:' >> /app/healthcheck.py && \
    echo '            print(f"Missing required environment variables: {missing_vars}", file=sys.stderr)' >> /app/healthcheck.py && \
    echo '            sys.exit(1)' >> /app/healthcheck.py && \
    echo '            ' >> /app/healthcheck.py && \
    echo '        print("Health check passed")' >> /app/healthcheck.py && \
    echo '        sys.exit(0)' >> /app/healthcheck.py && \
    echo '        ' >> /app/healthcheck.py && \
    echo '    except ImportError as e:' >> /app/healthcheck.py && \
    echo '        print(f"Import error: {e}", file=sys.stderr)' >> /app/healthcheck.py && \
    echo '        sys.exit(1)' >> /app/healthcheck.py && \
    echo '    except Exception as e:' >> /app/healthcheck.py && \
    echo '        print(f"Health check failed: {e}", file=sys.stderr)' >> /app/healthcheck.py && \
    echo '        sys.exit(1)' >> /app/healthcheck.py && \
    echo '' >> /app/healthcheck.py && \
    echo 'if __name__ == "__main__":' >> /app/healthcheck.py && \
    echo '    main()' >> /app/healthcheck.py && \
    chmod +x /app/healthcheck.py

# Default command (will be overridden by Cloud Run)
CMD ["python", "-m", "keeper_bots.announcer_configure_bot"]