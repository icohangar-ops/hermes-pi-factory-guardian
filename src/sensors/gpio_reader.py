"""
GPIO Sensor Reader for Raspberry Pi Factory Guardian.

Reads vibration (ADXL345), temperature (DS18B20), and current (ACS712) sensors
via GPIO/SPI/I2C and publishes readings to the Hermes event bus.
"""

import time
import struct
import yaml
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class SensorType(Enum):
    VIBRATION = "vibration"
    TEMPERATURE = "temperature"
    CURRENT = "current"


@dataclass
class SensorReading:
    """A single sensor reading with metadata."""
    machine_id: str
    sensor_type: SensorType
    value: float
    unit: str
    timestamp: datetime
    raw_value: int = 0
    pin: int = 0
    extra: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "machine_id": self.machine_id,
            "sensor_type": self.sensor_type.value,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "raw_value": self.raw_value,
            "pin": self.pin,
            **self.extra,
        }


@dataclass
class MachineProfile:
    """Configuration for a single machine's sensors."""
    machine_id: str
    name: str
    machine_type: str
    vibration: Optional[dict] = None
    temperature: Optional[dict] = None
    current: Optional[dict] = None
    camera: Optional[str] = None

    @classmethod
    def from_yaml(cls, data: dict) -> "MachineProfile":
        return cls(
            machine_id=data["id"],
            name=data["name"],
            type=data.get("type", "generic"),
            vibration=data.get("sensors", {}).get("vibration"),
            temperature=data.get("sensors", {}).get("temperature"),
            current=data.get("sensors", {}).get("current"),
            camera=data.get("camera"),
        )


class VibrationSensor:
    """ADXL345 vibration sensor via SPI."""

    def __init__(self, spi_bus: int = 0, spi_cs: int = 0):
        self.spi_bus = spi_bus
        self.spi_cs = spi_cs
        self._spi = None
        self._initialized = False
        self.sample_rate = 3200  # Hz
        self.range_g = 16  # ±16g

    def initialize(self):
        """Initialize the ADXL345 sensor via SPI."""
        try:
            import spidev
            self._spi = spidev.SpiDev()
            self._spi.open(self.spi_bus, self.spi_cs)
            self._spi.max_speed_hz = 5000000
            # Set measurement mode
            self._write_register(0x2D, 0x08)  # POWER_CTL: Measure mode
            # Set full resolution, ±16g
            self._write_register(0x31, 0x0B)  # DATA_FORMAT
            # Set sample rate to 3200 Hz
            self._write_register(0x2C, 0x0F)  # BW_RATE: 3200 Hz
            self._initialized = True
            logger.info("ADXL345 vibration sensor initialized on SPI %d:%d",
                        self.spi_bus, self.spi_cs)
        except ImportError:
            logger.warning("spidev not available — running in simulation mode")
            self._initialized = False
        except Exception as e:
            logger.error("Failed to initialize ADXL345: %s", e)
            self._initialized = False

    def _write_register(self, register: int, value: int):
        """Write a byte to an ADXL345 register."""
        if self._spi:
            self._spi.xfer2([register, value])

    def _read_register(self, register: int, length: int = 1) -> list:
        """Read bytes from an ADXL345 register."""
        if self._spi:
            return self._spi.xfer2([register | 0x80] + [0] * length)
        return [0] * (length + 1)

    def read_xyz(self) -> Optional[tuple]:
        """Read X, Y, Z acceleration values in g."""
        if not self._initialized:
            return self._simulate_xyz()

        try:
            data = self._read_register(0x32, 6)
            if len(data) < 7:
                return None
            x = struct.unpack('<h', bytes(data[1:3]))[0] * self.range_g / 32768.0
            y = struct.unpack('<h', bytes(data[3:5]))[0] * self.range_g / 32768.0
            z = struct.unpack('<h', bytes(data[5:7]))[0] * self.range_g / 32768.0
            return (x, y, z)
        except Exception as e:
            logger.error("Failed to read ADXL345: %s", e)
            return None

    def read_rms(self, samples: int = 100) -> float:
        """Calculate RMS vibration from multiple samples."""
        values = []
        for _ in range(samples):
            xyz = self.read_xyz()
            if xyz:
                magnitude = (xyz[0]**2 + xyz[1]**2 + xyz[2]**2) ** 0.5
                values.append(magnitude)
            time.sleep(1.0 / self.sample_rate * 100)  # Sub-sample

        if not values:
            return 0.0
        rms = (sum(v**2 for v in values) / len(values)) ** 0.5
        return round(rms, 3)

    def read_window(self, duration_ms: int = 500) -> List[tuple]:
        """Read a time-window of acceleration samples for FFT analysis."""
        samples = []
        sample_interval = duration_ms / (self.sample_rate * duration_ms / 1000)
        for _ in range(self.sample_rate * duration_ms // 1000):
            xyz = self.read_xyz()
            if xyz:
                samples.append(xyz)
            time.sleep(sample_interval)
        return samples

    def _simulate_xyz(self) -> tuple:
        """Generate simulated vibration data for testing."""
        import random
        base_vibration = 0.5
        noise = random.gauss(0, 0.1)
        x = base_vibration + noise + random.gauss(0, 0.05)
        y = base_vibration * 0.8 + noise * 0.9
        z = 1.0 + random.gauss(0, 0.02)  # Gravity component
        return (round(x, 4), round(y, 4), round(z, 4))


class TemperatureSensor:
    """DS18B20 temperature sensor via 1-Wire GPIO."""

    def __init__(self, gpio_pin: int = 4):
        self.gpio_pin = gpio_pin
        self._base_path = "/sys/bus/w1/devices/"
        self._initialized = False
        self._device_file = None

    def initialize(self):
        """Initialize 1-Wire interface and find DS18B20 device."""
        try:
            import os
            # Ensure 1-Wire kernel modules are loaded
            os.system("modprobe w1-gpio 2>/dev/null")
            os.system("modprobe w1-therm 2>/dev/null")
            time.sleep(1)

            # Find the device
            import glob
            device_folders = glob.glob(self._base_path + "28-*")
            if device_folders:
                self._device_file = device_folders[0] + "/w1_slave"
                self._initialized = True
                logger.info("DS18B20 found at %s", device_folders[0])
            else:
                logger.warning("No DS18B20 found — running in simulation mode")
        except Exception as e:
            logger.error("Failed to initialize DS18B20: %s", e)

    def read_temperature(self) -> float:
        """Read temperature in Celsius."""
        if not self._initialized:
            return self._simulate_temperature()

        try:
            with open(self._device_file, "r") as f:
                lines = f.readlines()
            if "YES" not in lines[0]:
                logger.warning("DS18B20 CRC check failed")
                return self._simulate_temperature()
            temp_pos = lines[1].find("t=")
            if temp_pos == -1:
                return 0.0
            temp_c = float(lines[1][temp_pos + 2:]) / 1000.0
            return round(temp_c, 2)
        except Exception as e:
            logger.error("Failed to read DS18B20: %s", e)
            return 0.0

    def _simulate_temperature(self) -> float:
        """Generate simulated temperature for testing."""
        import random
        base_temp = 52.0
        # Simulate gradual warming during shift
        hour = datetime.now().hour
        time_offset = (hour - 6) * 0.5 if hour >= 6 else -3.0
        noise = random.gauss(0, 0.5)
        return round(base_temp + time_offset + noise, 2)


class CurrentSensor:
    """ACS712 current sensor via MCP3008 ADC."""

    def __init__(self, mcp_channel: int = 0, max_current: float = 30.0):
        self.mcp_channel = mcp_channel
        self.max_current = max_current
        self._adc = None
        self._initialized = False
        self._zero_offset = 512  # ADC midpoint for 0A

    def initialize(self):
        """Initialize MCP3008 ADC."""
        try:
            from gpiozero import MCP3008
            self._adc = MCP3008(channel=self.mcp_channel)
            self._initialized = True
            logger.info("ACS712 current sensor on MCP3008 ch%d", self.mcp_channel)
        except ImportError:
            logger.warning("gpiozero not available — running in simulation mode")
        except Exception as e:
            logger.error("Failed to initialize ACS712: %s", e)

    def calibrate_zero(self, samples: int = 100):
        """Calibrate the zero-current offset."""
        values = []
        for _ in range(samples):
            values.append(self._read_adc_raw())
            time.sleep(0.01)
        self._zero_offset = sum(values) / len(values)
        logger.info("Current sensor zero offset calibrated: %.1f", self._zero_offset)

    def _read_adc_raw(self) -> float:
        """Read raw ADC value (0-1.0 from gpiozero)."""
        if self._adc:
            return self._adc.value
        return 0.5

    def read_current(self) -> float:
        """Read current in Amperes."""
        if not self._initialized:
            return self._simulate_current()

        adc_value = self._read_adc_raw()
        adc_voltage = adc_value * 3.3  # Convert to voltage
        sensitivity = 0.066  # 66mV/A for 30A ACS712
        current = (adc_voltage - self._zero_offset * 0.066) / sensitivity
        return round(abs(current), 2)

    def _simulate_current(self) -> float:
        """Generate simulated current for testing."""
        import random
        base_current = 8.5
        # Simulate duty cycle variation
        cycle = (time.time() % 10) / 10.0
        duty_factor = 0.7 + 0.3 * abs(cycle - 0.5) * 2
        noise = random.gauss(0, 0.2)
        return round(base_current * duty_factor + noise, 2)


class SensorManager:
    """Manages all sensors across all machines."""

    def __init__(self, config_path: str = "config/factory_config.yaml"):
        self.config = self._load_config(config_path)
        self.machines: Dict[str, MachineProfile] = {}
        self.vibration_sensors: Dict[str, VibrationSensor] = {}
        self.temp_sensors: Dict[str, TemperatureSensor] = {}
        self.current_sensors: Dict[str, CurrentSensor] = {}
        self.callbacks: List[Callable] = []
        self._running = False

    def _load_config(self, path: str) -> dict:
        """Load factory configuration."""
        try:
            with open(path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("Config file %s not found, using defaults", path)
            return {"machines": []}

    def initialize(self):
        """Initialize all sensors based on config."""
        for machine_data in self.config.get("machines", []):
            profile = MachineProfile.from_yaml(machine_data)
            self.machines[profile.machine_id] = profile

            if profile.vibration:
                sensor = VibrationSensor()
                sensor.initialize()
                self.vibration_sensors[profile.machine_id] = sensor

            if profile.temperature:
                sensor = TemperatureSensor(pin=profile.temperature.get("pin", 4))
                sensor.initialize()
                self.temp_sensors[profile.machine_id] = sensor

            if profile.current:
                sensor = CurrentSensor(mcp_channel=profile.current.get("pin", 0))
                sensor.initialize()
                self.current_sensors[profile.machine_id] = sensor

        logger.info("Initialized %d machines with %d vibration, %d temp, %d current sensors",
                     len(self.machines), len(self.vibration_sensors),
                     len(self.temp_sensors), len(self.current_sensors))

    def on_reading(self, callback: Callable):
        """Register a callback for sensor readings."""
        self.callbacks.append(callback)

    def read_all(self) -> List[SensorReading]:
        """Read all sensors and return readings."""
        readings = []
        now = datetime.utcnow()

        for machine_id, sensor in self.vibration_sensors.items():
            rms = sensor.read_rms(samples=50)
            reading = SensorReading(
                machine_id=machine_id,
                sensor_type=SensorType.VIBRATION,
                value=rms,
                unit="g",
                timestamp=now,
                pin=self.machines[machine_id].vibration.get("pin", 0) if self.machines[machine_id].vibration else 0,
            )
            readings.append(reading)

        for machine_id, sensor in self.temp_sensors.items():
            temp = sensor.read_temperature()
            reading = SensorReading(
                machine_id=machine_id,
                sensor_type=SensorType.TEMPERATURE,
                value=temp,
                unit="°C",
                timestamp=now,
                pin=self.machines[machine_id].temperature.get("pin", 4) if self.machines[machine_id].temperature else 4,
            )
            readings.append(reading)

        for machine_id, sensor in self.current_sensors.items():
            current = sensor.read_current()
            reading = SensorReading(
                machine_id=machine_id,
                sensor_type=SensorType.CURRENT,
                value=current,
                unit="A",
                timestamp=now,
                pin=self.machines[machine_id].current.get("pin", 0) if self.machines[machine_id].current else 0,
            )
            readings.append(reading)

        for callback in self.callbacks:
            for reading in readings:
                callback(reading)

        return readings

    def start_polling(self, interval_seconds: float = 5.0):
        """Continuously poll all sensors."""
        self._running = True
        logger.info("Starting sensor polling at %.1f second intervals", interval_seconds)
        while self._running:
            try:
                readings = self.read_all()
                for r in readings:
                    logger.debug("[%s] %s: %.3f %s",
                                r.machine_id, r.sensor_type.value, r.value, r.unit)
            except Exception as e:
                logger.error("Error during polling: %s", e)
            time.sleep(interval_seconds)

    def stop_polling(self):
        """Stop continuous polling."""
        self._running = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import argparse
    parser = argparse.ArgumentParser(description="Factory Guardian GPIO Sensor Reader")
    parser.add_argument("--config", default="config/factory_config.yaml")
    parser.add_argument("--test", action="store_true", help="Run sensor test")
    args = parser.parse_args()

    manager = SensorManager(config_path=args.config)
    manager.initialize()

    if args.test:
        print("=== Sensor Test Mode ===")
        readings = manager.read_all()
        for r in readings:
            print(f"  {r.machine_id} | {r.sensor_type.value:12s} | "
                  f"{r.value:8.3f} {r.unit}")
    else:
        manager.start_polling()
