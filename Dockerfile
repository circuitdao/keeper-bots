# Multi-stage build for keeper bots
FROM python:3.13-slim as base

# Install system dependencies in a separate cached layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

FROM base as builder

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VENV_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Set work directory
WORKDIR /app

# Install Poetry with specific version for reproducible builds
RUN pip install poetry==1.8.3

# Copy poetry files first for better caching
COPY pyproject.toml poetry.lock ./

# Configure poetry and install dependencies
RUN poetry config virtualenvs.create false \
    && poetry sync --only=main --no-root --no-interaction --no-ansi \
    && rm -rf $POETRY_CACHE_DIR

# Production stage
FROM python:3.13-slim

# Accept build arguments
ARG CHIA_NETWORK=mainnet

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    CHIA_ROOT=/app/.chia \
    CHIA_NETWORK=${CHIA_NETWORK}

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

# Initialize Chia configuration based on CHIA_NETWORK variable
RUN if [ "$CHIA_NETWORK" = "testnet" ]; then chia init --testnet; else chia init; fi

# Expose port (though these are background services, not HTTP servers)
EXPOSE 8080

# Health check script
COPY --chown=keeper:keeper <<EOF /app/healthcheck.py
#!/usr/bin/env python3
import sys
import os

def main():
    """Simple health check that verifies the environment and dependencies"""
    try:
        # Check if we can import the keeper_bots module
        import keeper_bots
        
        # Check if required environment variables are set
        required_vars = ['PRIVATE_KEY', 'CHIA_ROOT', 'RPC_URL', 'CHIA_NETWORK', 'ADD_SIG_DATA']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        
        if missing_vars:
            print(f"Missing required environment variables: {missing_vars}", file=sys.stderr)
            sys.exit(1)
            
        print("Health check passed")
        sys.exit(0)
        
    except ImportError as e:
        print(f"Import error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Health check failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
EOF

RUN chmod +x /app/healthcheck.py

# Default command (will be overridden by Cloud Run)
CMD ["python", "-m", "keeper_bots.announcer_configure_bot"]