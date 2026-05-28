"""
Tests for Hermes Factory Guardian skills and modules.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSensorReader(unittest.TestCase):
    """Test GPIO sensor reader functionality."""

    def test_sensor_reading_dataclass(self):
        """Test SensorReading creation and serialization."""
        from src.sensors.gpio_reader import SensorReading, SensorType

        reading = SensorReading(
            machine_id="cnc_01",
            sensor_type=SensorType.VIBRATION,
            value=1.85,
            unit="g",
            timestamp=datetime.utcnow(),
        )

        self.assertEqual(reading.machine_id, "cnc_01")
        self.assertEqual(reading.sensor_type, SensorType.VIBRATION)
        self.assertAlmostEqual(reading.value, 1.85)

        d = reading.to_dict()
        self.assertEqual(d["machine_id"], "cnc_01")
        self.assertEqual(d["sensor_type"], "vibration")
        self.assertEqual(d["unit"], "g")

    def test_vibration_sensor_simulate(self):
        """Test vibration sensor simulation mode."""
        from src.sensors.gpio_reader import VibrationSensor

        sensor = VibrationSensor()
        # Should work without initialization (simulation mode)
        xyz = sensor.read_xyz()
        self.assertIsNotNone(xyz)
        self.assertEqual(len(xyz), 3)
        self.assertTrue(all(isinstance(v, float) for v in xyz))

        rms = sensor.read_rms(samples=10)
        self.assertGreater(rms, 0)

    def test_temperature_sensor_simulate(self):
        """Test temperature sensor simulation mode."""
        from src.sensors.gpio_reader import TemperatureSensor

        sensor = TemperatureSensor(gpio_pin=4)
        temp = sensor.read_temperature()
        self.assertIsInstance(temp, float)
        self.assertGreater(temp, 0)
        self.assertLess(temp, 100)

    def test_current_sensor_simulate(self):
        """Test current sensor simulation mode."""
        from src.sensors.gpio_reader import CurrentSensor

        sensor = CurrentSensor(mcp_channel=0)
        current = sensor.read_current()
        self.assertIsInstance(current, float)
        self.assertGreater(current, 0)

    def test_sensor_manager_read_all(self):
        """Test SensorManager reading all sensors."""
        from src.sensors.gpio_reader import SensorManager

        manager = SensorManager(config_path="/nonexistent/config.yaml")
        readings = manager.read_all()
        # Should return empty list with no config
        self.assertIsInstance(readings, list)


class TestVisionPipeline(unittest.TestCase):
    """Test vision detection pipeline."""

    def test_detection_event_creation(self):
        """Test DetectionEvent creation."""
        from src.vision.detector import DetectionEvent, DetectionSeverity

        event = DetectionEvent(
            camera_id="cam_01",
            timestamp=datetime.utcnow(),
            severity=DetectionSeverity.HIGH,
            motion_score=0.15,
            description="Person detected in work area",
        )

        self.assertEqual(event.camera_id, "cam_01")
        self.assertEqual(event.severity, DetectionSeverity.HIGH)
        self.assertAlmostEqual(event.motion_score, 0.15)

        d = event.to_dict()
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["camera_id"], "cam_01")

    def test_bounding_box_creation(self):
        """Test BoundingBox creation."""
        from src.vision.detector import BoundingBox

        bbox = BoundingBox(
            x1=100, y1=50, x2=200, y2=300,
            confidence=0.92, class_name="person", class_id=0,
        )

        self.assertEqual(bbox.x1, 100)
        self.assertEqual(bbox.class_name, "person")
        self.assertAlmostEqual(bbox.confidence, 0.92)

    def test_zone_creation(self):
        """Test Zone creation."""
        from src.vision.detector import Zone

        zone = Zone(
            name="restricted", x1=0, y1=0, x2=320, y2=240,
            sensitivity=0.85, restricted=True, min_contour_area=200,
        )

        self.assertTrue(zone.restricted)
        self.assertAlmostEqual(zone.sensitivity, 0.85)


class TestAlertDispatcher(unittest.TestCase):
    """Test alert dispatch system."""

    def test_alert_event_creation(self):
        """Test AlertEvent creation."""
        from src.alert.dispatcher import AlertEvent, AlertSeverity

        event = AlertEvent(
            alert_id="alert_001",
            severity=AlertSeverity.CRITICAL,
            machine_id="cnc_mill_01",
            description="Bearing failure imminent",
            confidence=0.95,
            source="anomaly_detection",
            sensor_data={"vibration_rms": "3.8g", "temperature": "71°C"},
        )

        self.assertEqual(event.alert_id, "alert_001")
        self.assertEqual(event.severity, AlertSeverity.CRITICAL)
        self.assertFalse(event.resolved)

    def test_severity_channel_mapping(self):
        """Test that severity levels map to correct channels."""
        from src.alert.dispatcher import AlertDispatcher, AlertChannel, AlertSeverity, AlertEvent

        dispatcher = AlertDispatcher()

        # LOW → LOG only
        self.assertEqual(
            dispatcher.severity_channels[AlertSeverity.LOW],
            [AlertChannel.LOG]
        )

        # MEDIUM → LOG + TELEGRAM
        self.assertIn(AlertChannel.TELEGRAM,
                       dispatcher.severity_channels[AlertSeverity.MEDIUM])

        # HIGH → LOG + TELEGRAM + SLACK + EMAIL
        high_channels = dispatcher.severity_channels[AlertSeverity.HIGH]
        self.assertIn(AlertChannel.SLACK, high_channels)
        self.assertIn(AlertChannel.EMAIL, high_channels)

        # CRITICAL → all channels including BUZZER
        critical_channels = dispatcher.severity_channels[AlertSeverity.CRITICAL]
        self.assertIn(AlertChannel.BUZZER, critical_channels)

    def test_alert_dispatch_log(self):
        """Test alert dispatch with log-only channel."""
        from src.alert.dispatcher import AlertDispatcher, AlertEvent, AlertSeverity

        dispatcher = AlertDispatcher()
        dispatcher.initialize()

        event = AlertEvent(
            alert_id="test_001",
            severity=AlertSeverity.LOW,
            machine_id="conveyor_a",
            description="Minor vibration spike",
            confidence=0.65,
            source="anomaly_detection",
        )

        dispatched = dispatcher.dispatch(event)
        self.assertEqual(dispatched.alert_id, "test_001")
        self.assertEqual(dispatched.machine_id, "conveyor_a")


class TestSkillFiles(unittest.TestCase):
    """Test that Hermes skill files are well-formed."""

    def test_skill_files_exist(self):
        """Test that all skill files exist."""
        skills_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "hermes_skills"
        )
        expected_skills = [
            "anomaly_detection.skill.md",
            "alert_routing.skill.md",
            "camera_monitor.skill.md",
            "vibration_baseline.skill.md",
            "shift_report.skill.md",
        ]

        for skill_file in expected_skills:
            path = os.path.join(skills_dir, skill_file)
            self.assertTrue(
                os.path.exists(path),
                f"Missing skill file: {skill_file}"
            )

    def test_skill_frontmatter(self):
        """Test that skill files have YAML frontmatter."""
        skills_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "hermes_skills"
        )

        for filename in os.listdir(skills_dir):
            if filename.endswith(".skill.md"):
                with open(os.path.join(skills_dir, filename)) as f:
                    content = f.read()
                self.assertTrue(
                    content.startswith("---"),
                    f"{filename} missing YAML frontmatter"
                )
                self.assertTrue(
                    content.find("---", 3) > 0,
                    f"{filename} frontmatter not closed properly"
                )


if __name__ == "__main__":
    unittest.main()
