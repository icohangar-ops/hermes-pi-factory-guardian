FROM python:3.11-slim-bookworm

# System dependencies for Raspberry Pi sensor libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-dev \
    libgpiod2 \
    i2c-tools \
    spi-tools \
    libopencv-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p /data/footage /data/reports

# Environment
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FOOTAGE_PATH=/data/footage \
    REPORTS_PATH=/data/reports

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import src.sensors.gpio_reader; print('OK')" || exit 1

# Default command: run Hermes with Factory Guardian skills
CMD ["hermes", "--skills", "./hermes_skills/", "--config", "./config/factory_config.yaml"]
