FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching
COPY llm_experiments/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full project
COPY . .

# Health check: dry-run on 2 incidents
HEALTHCHECK --interval=30s --timeout=60s --start-period=10s --retries=3 \
  CMD python -m llm_experiments.src.run_experiment --dry-run --limit 2 || exit 1

# Default: run dry-run
CMD ["python", "-m", "llm_experiments.src.run_experiment", "--dry-run", "--limit", "2"]
