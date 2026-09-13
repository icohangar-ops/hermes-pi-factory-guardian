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

# Non-root user (Aikido: container must not run as root). Pre-join conventional
# Raspberry Pi gpio/i2c/spi/dialout groups so bind-mounted devices stay usable.
# If the host uses non-default GIDs, override with `group_add:` in compose.
RUN groupadd --system --gid 1000 hermes \
 && (groupadd --system --gid 997 gpio || true) \
 && (groupadd --system --gid 998 i2c  || true) \
 && (groupadd --system --gid 999 spi  || true) \
 && useradd  --system --uid 1000 --gid hermes --create-home --shell /bin/bash hermes \
 && usermod -aG gpio,i2c,spi,dialout hermes \
 && mkdir -p /data/footage /data/reports \
 && chown -R hermes:hermes /data /app

# Environment
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FOOTAGE_PATH=/data/footage \
    REPORTS_PATH=/data/reports

# Drop privileges before HEALTHCHECK / CMD. Override at runtime with
# `--user root` only if a host-specific setup genuinely needs it.
USER hermes

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import src.sensors.gpio_reader; print('OK')" || exit 1

# Default command: run Hermes with Factory Guardian skills
CMD ["hermes", "--skills", "./hermes_skills/", "--config", "./config/factory_config.yaml"]
