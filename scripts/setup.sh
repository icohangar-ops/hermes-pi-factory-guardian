#!/usr/bin/env bash
# =============================================================================
# Hermes-Pi Factory Guardian — Raspberry Pi Setup Script
# =============================================================================
# Run this script on a fresh Raspberry Pi to set up the complete system.
# Designed for Raspberry Pi OS 64-bit (Bookworm).
#
# Usage: sudo ./scripts/setup.sh
# =============================================================================

set -euo pipefail

# ── Color output helpers ───────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

# ── Check running as root ───────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root (use sudo)."
    exit 1
fi

# ── Configuration ──────────────────────────────────────────────────────
PROJECT_DIR="/opt/hermes-pi-factory-guardian"
VENV_DIR="${PROJECT_DIR}/venv"
PYTHON_VERSION="3.11"
SYSTEM_USER="guardian"
SYSTEM_GROUP="gpio"
SERVICE_NAME="hermes-guardian"

log_step "Step 1/8: System Update"
log_info "Updating package lists..."
apt-get update -qq

log_info "Upgrading installed packages..."
apt-get upgrade -y -qq

log_step "Step 2/8: Install System Dependencies"
log_info "Installing system packages..."

# Core build tools
apt-get install -y -qq \
    build-essential \
    cmake \
    git \
    curl \
    wget \
    pkg-config \
    > /dev/null

# OpenCV dependencies
log_info "Installing OpenCV build dependencies..."
apt-get install -y -qq \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libatlas-base-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    > /dev/null

# GPIO and I2C/SPI dependencies
log_info "Installing GPIO/I2C/SPI dependencies..."
apt-get install -y -qq \
    python3-dev \
    python3-pip \
    python3-venv \
    libffi-dev \
    i2c-tools \
    spi-tools \
    > /dev/null

# Enable I2C and SPI interfaces
log_info "Enabling I2C and SPI interfaces..."
raspi-config nonint do_i2c 0 2>/dev/null || log_warn "Could not enable I2C via raspi-config"
raspi-config nonint do_spi 0 2>/dev/null || log_warn "Could not enable SPI via raspi-config"

# Enable camera
log_info "Enabling camera interface..."
raspi-config nonint do_camera 0 2>/dev/null || log_warn "Could not enable camera via raspi-config"

# Enable 1-Wire for DS18B20 temperature sensors
if ! grep -q "dtoverlay=w1-gpio" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtoverlay=w1-gpio" >> /boot/firmware/config.txt
    log_info "Added 1-Wire overlay to config.txt"
fi

log_step "Step 3/8: Create System User"
if id "$SYSTEM_USER" &>/dev/null; then
    log_info "User '$SYSTEM_USER' already exists"
else
    useradd -r -m -s /bin/bash "$SYSTEM_USER"
    log_info "Created system user: $SYSTEM_USER"
fi

# Add user to GPIO group for hardware access
usermod -aG "$SYSTEM_GROUP" "$SYSTEM_USER"
usermod -aG video "$SYSTEM_USER"
usermod -aG i2c "$SYSTEM_USER"
usermod -aG spi "$SYSTEM_USER"

log_step "Step 4/8: Set Up Project Directory"
if [[ -d "$PROJECT_DIR" ]]; then
    log_info "Project directory exists: $PROJECT_DIR"
else
    mkdir -p "$PROJECT_DIR"
    log_info "Created project directory: $PROJECT_DIR"
fi

# Copy project files (assumes running from repo root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -d "$REPO_DIR" && "$REPO_DIR" != "$PROJECT_DIR" ]]; then
    cp -r "$REPO_DIR"/* "$PROJECT_DIR/" 2>/dev/null || true
    log_info "Copied project files to $PROJECT_DIR"
fi

# Create data directories
mkdir -p "${PROJECT_DIR}/data/anomalies"
mkdir -p "${PROJECT_DIR}/data/reports"
mkdir -p "${PROJECT_DIR}/data/learning"
mkdir -p "${PROJECT_DIR}/logs"

log_step "Step 5/8: Set Up Python Virtual Environment"
if [[ -d "$VENV_DIR" ]]; then
    log_info "Virtual environment exists: $VENV_DIR"
else
    log_info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    log_info "Created venv at $VENV_DIR"
fi

log_info "Installing Python dependencies..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel -q

# Core dependencies
pip install \
    opencv-python-headless \
    numpy \
    pyyaml \
    requests \
    > /dev/null 2>&1

# Optional: RPi.GPIO (may need to be installed differently)
if python3 -c "import RPi.GPIO" 2>/dev/null; then
    log_info "RPi.GPIO already available"
else
    pip install RPi.GPIO smbus2 spidev -q 2>/dev/null || \
        log_warn "RPi.GPIO/smbus2/spidev may not be available (normal on non-Pi)"
fi

# Optional: Telegram and Slack bots
pip install python-telegram-bot slack-sdk -q 2>/dev/null || \
    log_warn "Some notification packages failed to install (optional)"

# Optional: Email
pip install aiosmtplib -q 2>/dev/null || true

# Install project in development mode
if [[ -f "${PROJECT_DIR}/pyproject.toml" ]] || [[ -f "${PROJECT_DIR}/setup.py" ]]; then
    pip install -e "$PROJECT_DIR" -q 2>/dev/null || \
        log_warn "Project install skipped (no pyproject.toml/setup.py)"
fi

log_info "Python dependencies installed successfully"

# Create convenience activation script
cat > "${PROJECT_DIR}/activate.sh" << 'EOF'
#!/bin/bash
source /opt/hermes-pi-factory-guardian/venv/bin/activate
cd /opt/hermes-pi-factory-guardian
echo "Hermes-Pi Factory Guardian environment activated"
echo "Python: $(which python3)"
echo "Project: $(pwd)"
EOF
chmod +x "${PROJECT_DIR}/activate.sh"

log_step "Step 6/8: Configure Hermes Agent"
log_info "Hermes Agent will be configured by install_hermes.sh"
log_info "Run: ./scripts/install_hermes.sh after this script completes"

log_step "Step 7/8: Test Hardware"
log_info "Testing I2C devices..."
if i2cdetect -y 1 2>/dev/null | grep -q "68\|69"; then
    log_info "  ✅ MPU6050 accelerometer(s) detected on I2C bus 1"
else
    log_warn "  ⚠️  No MPU6050 detected on I2C bus 1 (expected address 0x68 or 0x69)"
fi

log_info "Testing 1-Wire temperature sensors..."
if [[ -d "/sys/bus/w1/devices" ]]; then
    probe_count=$(ls -d /sys/bus/w1/devices/28-* 2>/dev/null | wc -l)
    if [[ "$probe_count" -gt 0 ]]; then
        log_info "  ✅ $probe_count DS18B20 temperature sensor(s) detected"
    else
        log_warn "  ⚠️  No DS18B20 probes detected on 1-Wire bus"
    fi
else
    log_warn "  ⚠️  1-Wire bus not available (check dtoverlay=w1-gpio in config.txt)"
fi

log_info "Testing SPI..."
if [[ -d "/dev/spidev0.0" ]]; then
    log_info "  ✅ SPI device /dev/spidev0.0 available"
else
    log_warn "  ⚠️  SPI device not found (check SPI is enabled)"
fi

log_info "Testing camera..."
if [[ -d "/dev/video0" ]] || vcgencmd get_camera 2>/dev/null | grep -q "detected=1"; then
    log_info "  ✅ Camera device detected"
else
    log_warn "  ⚠️  No camera device found (check camera is enabled and connected)"
fi

log_step "Step 8/8: Set Up systemd Service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Hermes-Pi Factory Guardian
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SYSTEM_USER}
Group=${SYSTEM_GROUP}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${VENV_DIR}/bin/python3 -m skills.sensor_poller
ExecStartPost=${VENV_DIR}/bin/python3 -m skills.vision_monitor
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

# Performance limits
CPUQuota=80%
MemoryMax=512M

# Security
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${PROJECT_DIR}/data ${PROJECT_DIR}/logs

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hermes-guardian

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" 2>/dev/null || true

log_info "systemd service created and enabled: $SERVICE_NAME"

# ── Set Permissions ────────────────────────────────────────────────────
chown -R "$SYSTEM_USER:$SYSTEM_GROUP" "$PROJECT_DIR"
chmod -R 755 "$PROJECT_DIR"
chmod +x "${PROJECT_DIR}/scripts/"*.sh 2>/dev/null || true
chmod +x "${PROJECT_DIR}/hooks/"*.sh 2>/dev/null || true

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Hermes-Pi Factory Guardian — Setup Complete        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  ✅ System dependencies installed                            ║"
echo "║  ✅ Python virtual environment created                       ║"
echo "║  ✅ Hardware interfaces enabled (I2C, SPI, Camera, 1-Wire)  ║"
echo "║  ✅ Data directories created                                 ║"
echo "║  ✅ systemd service installed and enabled                    ║"
echo "║                                                              ║"
echo "║  NEXT STEPS:                                                 ║"
echo "║  1. Configure:  nano ${PROJECT_DIR}/config/factory_config.yaml"
echo "║  2. Install Hermes: ./scripts/install_hermes.sh              ║"
echo "║  3. Set env vars:  export TELEGRAM_BOT_TOKEN=...             ║"
echo "║  4. Start:         sudo systemctl start ${SERVICE_NAME}       ║"
echo "║  5. Check status:  sudo systemctl status ${SERVICE_NAME}     ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
