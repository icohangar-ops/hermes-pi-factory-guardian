# Hermes Skill: sensor_polling

## Metadata
- **Name:** sensor_polling
- **Version:** 1.0.0
- **Category:** data_collection
- **Depends on:** (none — base data provider)

## Description

Polls GPIO-connected sensors at configurable intervals and feeds readings to the anomaly detection skill for analysis. Supports four sensor types commonly used in factory monitoring, with a mock mode for development on non-Raspberry Pi hardware.

This is the "nervous system" of the factory guardian.

## Supported Sensors

### 1. Vibration — MPU6050 (6-Axis Accelerometer/Gyroscope)
- **Interface:** I2C (SMBus)
- **Address:** 0x68
- **Readings:** 3-axis acceleration (g), 3-axis angular velocity (°/s)
- **Used for:** Detecting machine stoppage, bearing wear, imbalance
- **Typical polling:** 1–10 Hz

### 2. Temperature — DS18B20 (1-Wire Waterproof Probe)
- **Interface:** 1-Wire (w1-gpio kernel module)
- **Address:** Auto-detected (28-XXXXXXXXXXXX)
- **Readings:** Temperature (°C), ±0.5°C accuracy
- **Used for:** Motor overheating, coolant temperature, ambient monitoring
- **Typical polling:** 0.1–1 Hz

### 3. Current Draw — ACS712 (Hall Effect Current Sensor)
- **Interface:** Analog (MCP3008 ADC via SPI)
- **Range:** ±30A (ACS712-30A model)
- **Readings:** Current (A), derived power (W)
- **Used for:** Detecting motor stall, overload, idle machines
- **Typical polling:** 1–5 Hz

### 4. Motion / Presence — HC-SR501 (PIR Sensor)
- **Interface:** GPIO digital input
- **Output:** HIGH (motion detected) / LOW (no motion)
- **Readings:** Boolean presence detection
- **Used for:** Restricted zone monitoring, area activity detection
- **Typical polling:** 1 Hz

## Input Configuration

```yaml
sensors:
  vibration:
    enabled: true
    i2c_address: 0x68
    poll_interval_ms: 500
    gpio_sda: 2
    gpio_scl: 3
  
  temperature:
    enabled: true
    poll_interval_ms: 5000
    probe_ids: ["28-00000abcdef", "28-00000fedcba"]
  
  current:
    enabled: true
    spi_device: "/dev/spidev0.0"
    adc_channel: 0
    poll_interval_ms: 1000
    calibration_offset: 2.5  # ACS712 zero-current voltage
    sensitivity: 0.066  # V/A for 30A model
  
  motion:
    enabled: true
    gpio_pin: 17
    poll_interval_ms: 1000
```

## Output Schema

```json
{
  "timestamp": "2025-06-15T14:32:17.123Z",
  "readings": {
    "cnc_machine_1": {
      "vibration": {
        "accel_x": 0.12,
        "accel_y": -0.03,
        "accel_z": 9.78,
        "magnitude": 9.78,
        "unit": "g"
      },
      "temperature": {
        "value": 67.2,
        "unit": "celsius"
      },
      "current": {
        "value": 11.8,
        "unit": "amps"
      },
      "motion": {
        "detected": false
      }
    }
  }
}
```

## Mock Mode

For development on non-Pi hardware (laptop, CI/CD, testing), all sensor reads return simulated data:

```python
# Auto-detected when RPi.GPIO is unavailable
# Returns realistic-looking data with configurable noise
# Simulates machine behavior: normal operation, warmup, stoppage
```

Enable explicitly:
```yaml
sensors:
  mock_mode: true  # Forces mock mode even on Pi
  mock_scenario: "normal"  # "normal", "anomaly", "stoppage"
```

## Data Buffering

Readings are buffered in memory and flushed to the anomaly detector in batches:

```python
buffer_size: 100  # Readings per sensor before batch flush
flush_interval_ms: 5000  # Maximum time between flushes
```

This prevents overwhelming the anomaly detector with individual readings while ensuring timely analysis.

## Error Handling

- Sensor timeout: Log warning, use last known reading, retry
- Sensor disconnect: Alert via anomaly detector (reading = NaN)
- I2C/SPI bus error: Reset bus, log error, continue
- Value out of range: Clamp to sensor limits, flag for review

## Wiring Reference

```
Raspberry Pi 5        MPU6050
─────────────        ───────
GPIO 2 (SDA)  ────── SDA
GPIO 3 (SCL)  ────── SCL
3.3V          ────── VCC
GND           ────── GND

Raspberry Pi 5        DS18B20
─────────────        ───────
GPIO 4        ────── Data (with 4.7kΩ pull-up)
3.3V          ────── VDD
GND           ────── GND

Raspberry Pi 5        ACS712 → MCP3008
─────────────        ────────────────
GPIO 11 (SCLK) ───── CLK
GPIO 10 (MOSI) ───── DIN
GPIO 9  (MISO) ───── DOUT
GPIO 8  (CE0)  ───── CS
5V            ────── VDD
GND           ────── GND

Raspberry Pi 5        HC-SR501 (PIR)
─────────────        ──────────────
GPIO 17       ────── OUT
5V            ────── VCC
GND           ────── GND
```
