"""
Hermes-Pi Factory Guardian — Sensor Polling Skill

Polls GPIO-connected sensors at configurable intervals and feeds
readings to the anomaly detection skill. Includes mock mode for
development on non-Raspberry Pi hardware.

Supported sensors:
  - MPU6050 (vibration/accelerometer via I2C)
  - DS18B20 (temperature via 1-Wire)
  - ACS712 (current via SPI ADC)
  - HC-SR501 (motion PIR via GPIO)

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import hardware libraries — fall back to mock if unavailable
try:
    import RPi.GPIO as gpio
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    logger.info("RPi.GPIO not available — using mock sensor mode")

try:
    import smbus2
    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False
    logger.info("smbus2 not available — I2C sensors will use mock data")

try:
    import spidev
    HAS_SPI = True
except ImportError:
    HAS_SPI = False
    logger.info("spidev not available — SPI sensors will use mock data")


@dataclass
class SensorReading:
    """A single sensor reading."""
    machine_id: str
    sensor_type: str
    value: float
    unit: str
    timestamp: float = field(default_factory=time.time)
    raw_value: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "machine_id": self.machine_id,
            "sensor_type": self.sensor_type,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp,
            "raw_value": self.raw_value,
            "metadata": self.metadata,
        }


@dataclass
class SensorConfig:
    """Configuration for a single sensor."""
    sensor_type: str
    machine_id: str
    enabled: bool = True
    gpio_pin: int = 0
    i2c_address: int = 0
    spi_device: str = ""
    adc_channel: int = 0
    poll_interval_ms: int = 1000
    calibration_offset: float = 0.0
    sensitivity: float = 1.0
    probe_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class SensorPoller:
    """
    Polls factory sensors at configurable intervals.

    Supports four sensor types commonly used in factory monitoring.
    Falls back to mock mode when hardware libraries are unavailable,
    making development and testing possible on any machine.

    Readings are buffered and can be flushed to the anomaly detector
    in batches for efficient processing.
    """

    # Mock sensor value ranges for realistic simulation
    MOCK_RANGES = {
        "vibration": (1.5, 3.5),      # g (typical CNC vibration)
        "temperature": (55.0, 75.0),   # °C (typical motor temp)
        "current": (8.0, 16.0),        # A (typical motor current)
        "motion": (0.0, 1.0),          # boolean (0 or 1)
    }

    def __init__(
        self,
        config_path: Optional[str] = None,
        mock_mode: Optional[bool] = None,
        mock_scenario: str = "normal",
        data_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> None:
        """
        Initialize the sensor poller.

        Args:
            config_path: Path to factory_config.yaml.
            mock_mode: Force mock mode (True/False). Auto-detects if None.
            mock_scenario: Mock data scenario — "normal", "anomaly", "stoppage".
            data_callback: Callback function called with batched readings.
        """
        self._mock_mode = mock_mode if mock_mode is not None else not HAS_GPIO
        self._mock_scenario = mock_scenario
        self._data_callback = data_callback

        self._sensors: Dict[Tuple[str, str], SensorConfig] = {}
        self._buffer: List[SensorReading] = []
        self._buffer_size = 100
        self._flush_interval_ms = 5000
        self._last_flush = time.time()
        self._poll_threads: Dict[Tuple[str, str], Any] = {}

        # Mock state tracking
        self._mock_tick = 0
        self._mock_machine_states: Dict[str, str] = {
            "cnc_machine_1": "running",
            "cnc_machine_2": "running",
            "cnc_machine_3": "running",
            "conveyor_1": "running",
        }

        # Hardware initialization
        self._i2c_bus: Any = None
        self._spi: Any = None

        if HAS_GPIO and not self._mock_mode:
            try:
                gpio.setmode(gpio.BCM)
                gpio.setwarnings(False)
                logger.info("GPIO initialized (BCM mode)")
            except Exception as e:
                logger.error("Failed to initialize GPIO: %s — switching to mock", e)
                self._mock_mode = True

        if HAS_SMBUS and not self._mock_mode:
            try:
                self._i2c_bus = smbus2.SMBus(1)  # I2C bus 1 on Pi
                logger.info("I2C bus initialized")
            except Exception as e:
                logger.warning("Failed to initialize I2C: %s", e)

        if HAS_SPI and not self._mock_mode:
            try:
                self._spi = spidev.SpiDev()
                self._spi.open(0, 0)  # SPI bus 0, CE0
                self._spi.max_speed_hz = 1000000  # 1 MHz
                logger.info("SPI bus initialized")
            except Exception as e:
                logger.warning("Failed to initialize SPI: %s", e)

        self._load_config(config_path)

        logger.info(
            "SensorPoller initialized: mock_mode=%s, sensors=%d, scenario=%s",
            self._mock_mode, len(self._sensors), self._mock_scenario,
        )

    def _load_config(self, config_path: Optional[str] = None) -> None:
        """Load sensor configuration from YAML."""
        if config_path and Path(config_path).exists():
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}

                # Check for global mock mode
                if config.get("sensors", {}).get("mock_mode", False):
                    self._mock_mode = True
                    logger.info("Mock mode enabled via config")

                sensors_config = config.get("sensors", {})

                # Parse vibration sensors
                for mid, s_cfg in sensors_config.get("vibration", {}).items():
                    if isinstance(s_cfg, dict):
                        self._sensors[(mid, "vibration")] = SensorConfig(
                            sensor_type="vibration",
                            machine_id=mid,
                            enabled=s_cfg.get("enabled", True),
                            i2c_address=s_cfg.get("i2c_address", 0x68),
                            poll_interval_ms=s_cfg.get("poll_interval_ms", 500),
                        )

                # Parse temperature sensors
                for mid, s_cfg in sensors_config.get("temperature", {}).items():
                    if isinstance(s_cfg, dict):
                        self._sensors[(mid, "temperature")] = SensorConfig(
                            sensor_type="temperature",
                            machine_id=mid,
                            enabled=s_cfg.get("enabled", True),
                            poll_interval_ms=s_cfg.get("poll_interval_ms", 5000),
                            probe_id=s_cfg.get("probe_id", ""),
                        )

                # Parse current sensors
                for mid, s_cfg in sensors_config.get("current", {}).items():
                    if isinstance(s_cfg, dict):
                        self._sensors[(mid, "current")] = SensorConfig(
                            sensor_type="current",
                            machine_id=mid,
                            enabled=s_cfg.get("enabled", True),
                            adc_channel=s_cfg.get("adc_channel", 0),
                            poll_interval_ms=s_cfg.get("poll_interval_ms", 1000),
                            calibration_offset=s_cfg.get("calibration_offset", 2.5),
                            sensitivity=s_cfg.get("sensitivity", 0.066),
                        )

                # Parse motion sensors
                for mid, s_cfg in sensors_config.get("motion", {}).items():
                    if isinstance(s_cfg, dict):
                        self._sensors[(mid, "motion")] = SensorConfig(
                            sensor_type="motion",
                            machine_id=mid,
                            enabled=s_cfg.get("enabled", True),
                            gpio_pin=s_cfg.get("gpio_pin", 17),
                            poll_interval_ms=s_cfg.get("poll_interval_ms", 1000),
                        )

            except ImportError:
                logger.warning("PyYAML not installed, using default sensor config")
            except Exception as e:
                logger.error("Failed to load sensor config: %s", e)

        # Add default sensors if none configured (for easy testing)
        if not self._sensors:
            logger.info("No sensors configured — adding defaults for mock mode")
            for mid in ["cnc_machine_1", "cnc_machine_2", "cnc_machine_3", "conveyor_1"]:
                for stype in ["vibration", "temperature", "current", "motion"]:
                    self._sensors[(mid, stype)] = SensorConfig(
                        sensor_type=stype,
                        machine_id=mid,
                        enabled=True,
                        poll_interval_ms=1000 if stype != "temperature" else 5000,
                    )

    def poll_all(self) -> List[SensorReading]:
        """
        Poll all enabled sensors and return readings.

        Returns:
            List of SensorReading objects from all enabled sensors.
        """
        readings: List[SensorReading] = []

        for (machine_id, sensor_type), config in self._sensors.items():
            if not config.enabled:
                continue

            try:
                reading = self._poll_single(machine_id, sensor_type, config)
                if reading is not None:
                    readings.append(reading)
                    self._buffer.append(reading)
            except Exception as e:
                logger.error(
                    "Failed to poll %s/%s: %s",
                    machine_id, sensor_type, e,
                )

        # Check if buffer should be flushed
        now = time.time()
        if (
            len(self._buffer) >= self._buffer_size
            or (now - self._last_flush) * 1000 >= self._flush_interval_ms
        ):
            self.feed_hermes()

        return readings

    def _poll_single(
        self,
        machine_id: str,
        sensor_type: str,
        config: SensorConfig,
    ) -> Optional[SensorReading]:
        """
        Poll a single sensor.

        Args:
            machine_id: Machine identifier.
            sensor_type: Type of sensor to read.
            config: Sensor configuration.

        Returns:
            SensorReading or None if read failed.
        """
        if sensor_type == "vibration":
            value, raw = self.read_vibration(config)
            return SensorReading(
                machine_id=machine_id,
                sensor_type=sensor_type,
                value=value,
                unit="g",
                raw_value=raw,
                metadata={"i2c_address": config.i2c_address},
            )
        elif sensor_type == "temperature":
            value, raw = self.read_temperature(config)
            return SensorReading(
                machine_id=machine_id,
                sensor_type=sensor_type,
                value=value,
                unit="celsius",
                raw_value=raw,
                metadata={"probe_id": config.probe_id},
            )
        elif sensor_type == "current":
            value, raw = self.read_current(config)
            return SensorReading(
                machine_id=machine_id,
                sensor_type=sensor_type,
                value=value,
                unit="amps",
                raw_value=raw,
                metadata={"adc_channel": config.adc_channel},
            )
        elif sensor_type == "motion":
            value, raw = self.read_motion(config)
            return SensorReading(
                machine_id=machine_id,
                sensor_type=sensor_type,
                value=value,
                unit="boolean",
                raw_value=raw,
                metadata={"gpio_pin": config.gpio_pin},
            )
        else:
            logger.warning("Unknown sensor type: %s", sensor_type)
            return None

    def read_vibration(self, config: SensorConfig) -> Tuple[float, float]:
        """
        Read vibration from MPU6050 accelerometer.

        Returns (magnitude_g, raw_accel_z) tuple.
        """
        if self._mock_mode:
            return self._mock_vibration(config.machine_id)

        if self._i2c_bus is None:
            logger.warning("I2C bus not available for vibration reading")
            return self._mock_vibration(config.machine_id)

        try:
            addr = config.i2c_address
            # Read 6 bytes from accelerometer data register (0x3B)
            data = self._i2c_bus.read_i2c_block_data(addr, 0x3B, 6)

            # Convert to 16-bit signed integers
            accel_x = self._twos_comp(data[0] << 8 | data[1], 16)
            accel_y = self._twos_comp(data[2] << 8 | data[3], 16)
            accel_z = self._twos_comp(data[4] << 8 | data[5], 16)

            # Convert to g (MPU6050 ±2g range: 16384 LSB/g)
            scale = 16384.0
            ax = accel_x / scale
            ay = accel_y / scale
            az = accel_z / scale

            # Calculate magnitude (excluding gravity component approx)
            magnitude = math.sqrt(ax * ax + ay * ay + az * az)

            # Remove gravity (rough approximation for mounted sensor)
            magnitude = abs(magnitude - 1.0)

            return round(magnitude, 4), round(az, 4)

        except Exception as e:
            logger.error("MPU6050 read error: %s", e)
            return self._mock_vibration(config.machine_id)

    def read_temperature(self, config: SensorConfig) -> Tuple[float, float]:
        """
        Read temperature from DS18B20 probe.

        Returns (temperature_c, raw_value) tuple.
        """
        if self._mock_mode:
            return self._mock_temperature(config.machine_id)

        # DS18B20 uses 1-Wire kernel interface
        probe_id = config.probe_id
        w1_path = f"/sys/bus/w1/devices/{probe_id}/w1_slave"

        try:
            with open(w1_path, "r") as f:
                content = f.read()

            if "YES" not in content:
                logger.warning("DS18B20 CRC check failed")
                return self._mock_temperature(config.machine_id)

            # Parse temperature from output: t=25000 = 25.000°C
            for line in content.split("\n"):
                if "t=" in line:
                    raw_temp = int(line.split("t=")[1])
                    temp_c = raw_temp / 1000.0
                    return round(temp_c, 2), float(raw_temp)

            return self._mock_temperature(config.machine_id)

        except FileNotFoundError:
            logger.debug("DS18B20 probe not found at %s", w1_path)
            return self._mock_temperature(config.machine_id)
        except Exception as e:
            logger.error("DS18B20 read error: %s", e)
            return self._mock_temperature(config.machine_id)

    def read_current(self, config: SensorConfig) -> Tuple[float, float]:
        """
        Read current from ACS712 via MCP3008 ADC.

        Returns (current_amps, raw_adc_value) tuple.
        """
        if self._mock_mode:
            return self._mock_current(config.machine_id)

        if self._spi is None:
            logger.warning("SPI not available for current reading")
            return self._mock_current(config.machine_id)

        try:
            channel = config.adc_channel
            # MCP3008: send [start_bit=1, single=1, channel(3 bits), x(5 bits)]
            cmd = [1, (8 + channel) << 4, 0]
            response = self._spi.xfer2(cmd)

            # 10-bit ADC value
            raw_value = ((response[1] & 0x03) << 8) | response[2]

            # Convert to voltage (MCP3008: 3.3V reference)
            voltage = (raw_value / 1023.0) * 3.3

            # ACS712: Vcc/2 = zero current, sensitivity = V/A
            offset = config.calibration_offset  # Typically 2.5V for 5V supply
            sensitivity = config.sensitivity      # 0.066 V/A for 30A model
            current_amps = (voltage - offset) / sensitivity

            return round(abs(current_amps), 2), float(raw_value)

        except Exception as e:
            logger.error("ACS712 read error: %s", e)
            return self._mock_current(config.machine_id)

    def read_motion(self, config: SensorConfig) -> Tuple[float, float]:
        """
        Read motion detection from HC-SR501 PIR sensor.

        Returns (motion_detected, raw_gpio) tuple.
        """
        if self._mock_mode:
            return self._mock_motion(config.machine_id)

        try:
            pin = config.gpio_pin
            value = gpio.input(pin)
            return float(value), float(value)

        except Exception as e:
            logger.error("PIR read error: %s", e)
            return self._mock_motion(config.machine_id)

    def feed_hermes(
        self,
        readings: Optional[List[SensorReading]] = None,
    ) -> Dict[str, Any]:
        """
        Send buffered readings to Hermes anomaly detector.

        Args:
            readings: Optional readings to send (uses buffer if None).

        Returns:
            Dictionary with send status and count.
        """
        if readings is None:
            readings = list(self._buffer)

        if not readings:
            return {"sent": False, "reason": "no_readings"}

        reading_dicts = [r.to_dict() for r in readings]

        if self._data_callback is not None:
            try:
                self._data_callback(reading_dicts)
                logger.info("Fed %d readings to Hermes callback", len(readings))
            except Exception as e:
                logger.error("Hermes callback failed: %s", e)
                return {"sent": False, "error": str(e)}
        else:
            logger.debug(
                "No callback set — %d readings buffered. "
                "Set data_callback to feed Hermes.",
                len(readings),
            )

        # Clear buffer
        self._buffer.clear()
        self._last_flush = time.time()

        return {
            "sent": True,
            "count": len(readings),
            "timestamp": time.time(),
        }

    # ── Mock Sensor Methods ──────────────────────────────────────────

    def _mock_vibration(self, machine_id: str) -> Tuple[float, float]:
        """Generate mock vibration data."""
        state = self._mock_machine_states.get(machine_id, "running")
        self._mock_tick += 1

        if state == "stopped" or self._mock_scenario == "stoppage":
            return round(random.uniform(0.0, 0.05), 4), 0.0
        elif self._mock_scenario == "anomaly":
            base = random.uniform(3.5, 6.0)
            noise = random.gauss(0, 0.1)
            return round(base + noise, 4), round(base + noise, 4)
        else:
            base = random.uniform(1.8, 3.0)
            noise = random.gauss(0, 0.15)
            return round(base + noise, 4), round(base + noise, 4)

    def _mock_temperature(self, machine_id: str) -> Tuple[float, float]:
        """Generate mock temperature data."""
        state = self._mock_machine_states.get(machine_id, "running")
        hour = time.localtime().tm_hour

        if state == "stopped":
            return round(random.uniform(25.0, 35.0), 2), 30000.0
        elif self._mock_scenario == "anomaly":
            base = random.uniform(75.0, 90.0)
            noise = random.gauss(0, 0.5)
            return round(base + noise, 2), round((base + noise) * 1000)
        else:
            # Simulate higher temps during day shift
            base = 62.0 + (5.0 if 6 <= hour <= 18 else 0.0)
            noise = random.gauss(0, 1.5)
            return round(base + noise, 2), round((base + noise) * 1000)

    def _mock_current(self, machine_id: str) -> Tuple[float, float]:
        """Generate mock current draw data."""
        state = self._mock_machine_states.get(machine_id, "running")

        if state == "stopped":
            return round(random.uniform(0.1, 0.5), 2), 100.0
        elif self._mock_scenario == "anomaly":
            base = random.uniform(18.0, 28.0)
            return round(base, 2), round(base * 30)
        else:
            base = random.uniform(9.0, 14.0)
            noise = random.gauss(0, 0.3)
            return round(base + noise, 2), round((base + noise) * 30)

    def _mock_motion(self, machine_id: str) -> Tuple[float, float]:
        """Generate mock motion detection data."""
        # Simulate occasional motion
        if random.random() < 0.05:  # 5% chance of motion detection
            return 1.0, 1.0
        return 0.0, 0.0

    # ── Utility Methods ──────────────────────────────────────────────

    @staticmethod
    def _twos_comp(val: int, bits: int) -> int:
        """Convert two's complement to signed integer."""
        if val & (1 << (bits - 1)):
            return val - (1 << bits)
        return val

    def get_sensor_count(self) -> int:
        """Return the number of configured sensors."""
        return len([s for s in self._sensors.values() if s.enabled])

    def set_machine_state(self, machine_id: str, state: str) -> None:
        """
        Set the state of a machine (affects mock data generation).

        Args:
            machine_id: Machine identifier.
            state: "running", "stopped", "anomaly"
        """
        self._mock_machine_states[machine_id] = state
        logger.info("Machine %s state set to: %s", machine_id, state)

    def cleanup(self) -> None:
        """Clean up GPIO and other hardware resources."""
        if HAS_GPIO and not self._mock_mode:
            try:
                gpio.cleanup()
                logger.info("GPIO cleaned up")
            except Exception as e:
                logger.error("GPIO cleanup failed: %s", e)

        if self._i2c_bus is not None:
            try:
                self._i2c_bus.close()
                logger.info("I2C bus closed")
            except Exception as e:
                logger.error("I2C cleanup failed: %s", e)

        if self._spi is not None:
            try:
                self._spi.close()
                logger.info("SPI bus closed")
            except Exception as e:
                logger.error("SPI cleanup failed: %s", e)
