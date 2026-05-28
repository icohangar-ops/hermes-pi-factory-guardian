#!/bin/bash
# Raspberry Pi Setup Script for Factory Guardian
# Run as: bash scripts/setup_pi.sh

set -e

echo "=== Factory Guardian Pi Setup ==="
echo "This script configures your Raspberry Pi for factory monitoring."
echo ""

# Check for Raspberry Pi
if [ -f /proc/device-tree/model ]; then
    MODEL=$(cat /proc/device-tree/model | tr -d '\0')
    echo "Detected: $MODEL"
else
    echo "Warning: Not running on a Raspberry Pi. Some steps will be skipped."
fi

# Enable camera interface
echo "[1/7] Enabling camera interface..."
sudo raspi-config nonint do_camera 0 2>/dev/null || echo "  (skipped - not a Pi)"

# Enable SPI for ADXL345 vibration sensor
echo "[2/7] Enabling SPI interface..."
sudo raspi-config nonint do_spi 0 2>/dev/null || echo "  (skipped - not a Pi)"

# Enable I2C for sensors
echo "[3/7] Enabling I2C interface..."
sudo raspi-config nonint do_i2c 0 2>/dev/null || echo "  (skipped - not a Pi)"

# Enable 1-Wire for DS18B20 temperature sensor
echo "[4/7] Enabling 1-Wire interface..."
sudo raspi-config nonint do_onewire 0 2>/dev/null || echo "  (skipped - not a Pi)"

# Load kernel modules
echo "[5/7] Loading kernel modules..."
sudo modprobe w1-gpio 2>/dev/null || true
sudo modprobe w1-therm 2>/dev/null || true
sudo modprobe spidev 2>/dev/null || true

# Add to /etc/modules for persistence
for module in w1-gpio w1-therm spidev; do
    if ! grep -q "^$module" /etc/modules 2>/dev/null; then
        echo "$module" | sudo tee -a /etc/modules > /dev/null
        echo "  Added $module to /etc/modules"
    fi
done

# Create data directories
echo "[6/7] Creating data directories..."
sudo mkdir -p /data/footage /data/reports
sudo chown $USER:$USER /data/footage /data/reports
echo "  /data/footage - camera footage storage"
echo "  /data/reports  - shift reports and logs"

# Install Python dependencies
echo "[7/7] Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "  1. Copy .env.example to .env and fill in your values"
echo "  2. Edit config/factory_config.yaml for your machines"
echo "  3. Connect sensors and camera"
echo "  4. Run: hermes --skills ./hermes_skills/ --config ./config/factory_config.yaml"
echo ""
echo "Or run via Docker: docker compose up -d"
