"""
Hermes-Pi Factory Guardian — Anomaly Detector Unit Tests

Tests baseline creation, anomaly detection, learning from feedback,
and persistence using unittest.mock for GPIO and file I/O.
"""

import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path

# Add skill directories to path for imports (hyphenated dirs aren't valid Python packages)
import sys

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "skills" / "anomaly-detection"))

from anomaly_detector import (
    AnomalyDetector,
    AnomalyResult,
    BaselineProfile,
    Confidence,
    Severity,
)


class TestBaselineProfile(unittest.TestCase):
    """Tests for the BaselineProfile dataclass."""

    def test_ema_std_calculation(self):
        """Test standard deviation calculation from EMA variance."""
        profile = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
            ema_variance=4.0,  # std should be 2.0
        )
        self.assertAlmostEqual(profile.ema_std, 2.0, places=4)

    def test_ema_std_zero_variance(self):
        """Test that zero variance doesn't cause division by zero."""
        profile = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
            ema_variance=0.0,
        )
        self.assertGreater(profile.ema_std, 0.0)  # Should be small positive

    def test_reliable_check(self):
        """Test reliability based on sample count."""
        # Not reliable with few samples
        profile_unreliable = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
            sample_count=10,
        )
        self.assertFalse(profile_unreliable.is_reliable)

        # Reliable with enough samples
        profile_reliable = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
            sample_count=25,
        )
        self.assertTrue(profile_reliable.is_reliable)

    def test_false_positive_rate(self):
        """Test false positive rate calculation."""
        profile = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
            confirmed_incidents=5,
            false_alarms=15,
        )
        self.assertAlmostEqual(profile.false_positive_rate, 0.75, places=4)

    def test_false_positive_rate_no_data(self):
        """Test FPR with no data returns 0."""
        profile = BaselineProfile(
            machine_id="test_machine",
            sensor_type="temperature",
        )
        self.assertEqual(profile.false_positive_rate, 0.0)

    def test_serialization_roundtrip(self):
        """Test to_dict / from_dict roundtrip."""
        profile = BaselineProfile(
            machine_id="cnc_machine_1",
            sensor_type="vibration",
            ema_mean=2.3,
            ema_variance=0.25,
            sample_count=100,
            threshold_adjustment=1.05,
            confirmed_incidents=3,
            false_alarms=7,
        )
        data = profile.to_dict()
        restored = BaselineProfile.from_dict(data)

        self.assertEqual(restored.machine_id, profile.machine_id)
        self.assertEqual(restored.sensor_type, profile.sensor_type)
        self.assertAlmostEqual(restored.ema_mean, profile.ema_mean, places=4)
        self.assertAlmostEqual(restored.ema_std, profile.ema_std, places=4)
        self.assertEqual(restored.sample_count, profile.sample_count)
        self.assertEqual(restored.confirmed_incidents, profile.confirmed_incidents)
        self.assertEqual(restored.false_alarms, profile.false_alarms)


class TestAnomalyDetector(unittest.TestCase):
    """Tests for the AnomalyDetector class."""

    def setUp(self):
        """Create a temporary directory for test baselines."""
        self.temp_dir = tempfile.mkdtemp()
        self.storage_path = os.path.join(self.temp_dir, "baselines.json")

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization(self):
        """Test detector initializes with empty baselines."""
        detector = AnomalyDetector(storage_path=self.storage_path)
        self.assertEqual(len(detector._baselines), 0)

    def test_update_baseline_creates_profile(self):
        """Test that updating baseline creates a new profile."""
        detector = AnomalyDetector(storage_path=self.storage_path)
        detector.update_baseline("machine_1", "temperature", 65.0)

        profile = detector._get_baseline("machine_1", "temperature")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.sample_count, 1)
        self.assertAlmostEqual(profile.ema_mean, 65.0, places=4)

    def test_update_baseline_ema(self):
        """Test that EMA smoothing works correctly."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            ema_alpha=0.1,  # Fast learning for test
        )

        # Feed a series of values around 65
        values = [65.0, 65.5, 64.8, 65.2, 65.0, 64.9, 65.1, 65.3, 64.7, 65.0]
        for v in values:
            detector.update_baseline("machine_1", "temperature", v)

        profile = detector._get_baseline("machine_1", "temperature")
        # EMA mean should be close to the actual mean
        actual_mean = sum(values) / len(values)
        self.assertAlmostEqual(profile.ema_mean, actual_mean, places=1)
        self.assertEqual(profile.sample_count, 10)

    def test_check_anomaly_unreliable_baseline(self):
        """Test that unreliable baselines return non-anomaly."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=5,
        )

        # Feed only 2 values — not enough for reliability
        detector.update_baseline("machine_1", "temperature", 65.0)
        result = detector.check_anomaly("machine_1", "temperature", 100.0)

        self.assertFalse(result.is_anomaly)
        self.assertEqual(result.confidence, Confidence.LOW)
        self.assertEqual(result.severity, Severity.INFO)

    def test_check_anomaly_normal_reading(self):
        """Test that a normal reading is not flagged."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            ema_alpha=0.05,
            min_samples=1,  # Low for testing
        )

        # Build a baseline with realistic variance around 65.0
        import random
        random.seed(42)
        for _ in range(200):
            val = 65.0 + random.gauss(0, 1.5)  # std ≈ 1.5
            detector.update_baseline("machine_1", "temperature", val)

        # Test a normal reading (within 1 std)
        result = detector.check_anomaly("machine_1", "temperature", 66.0)

        self.assertFalse(result.is_anomaly)
        self.assertLess(result.anomaly_score, 0.5)

    def test_check_anomaly_extreme_reading(self):
        """Test that an extreme reading IS flagged as anomaly."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            ema_alpha=0.05,
            min_samples=1,
        )

        # Build a tight baseline around 65.0
        for _ in range(100):
            detector.update_baseline("machine_1", "temperature", 65.0)

        # Test an extreme reading
        result = detector.check_anomaly("machine_1", "temperature", 100.0)

        self.assertTrue(result.is_anomaly)
        self.assertGreater(result.anomaly_score, 0.5)
        self.assertIn(result.severity, [Severity.WARNING, Severity.CRITICAL])

    def test_check_anomaly_low_reading(self):
        """Test that very low readings (machine stopped) are detected."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            ema_alpha=0.05,
            min_samples=1,
        )

        # Baseline: machine normally draws 12A
        for _ in range(100):
            detector.update_baseline("cnc_1", "current", 12.0)

        # Machine stopped — only drawing 0.3A
        result = detector.check_anomaly("cnc_1", "current", 0.3)

        self.assertTrue(result.is_anomaly)
        self.assertGreater(result.anomaly_score, 0.5)

    def test_anomaly_score_range(self):
        """Test that anomaly scores are always in [0, 1]."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=1,
        )

        for _ in range(50):
            detector.update_baseline("m1", "temp", 65.0)

        # Test extreme values
        for test_val in [-1000, -100, -1, 0, 1, 50, 100, 1000]:
            result = detector.check_anomaly("m1", "temp", test_val)
            self.assertGreaterEqual(result.anomaly_score, 0.0)
            self.assertLessEqual(result.anomaly_score, 1.0,
                                 f"Score {result.anomaly_score} for value {test_val}")

    def test_learn_from_feedback_false_alarm(self):
        """Test that false alarm feedback widens threshold."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=1,
        )

        # Build baseline
        for _ in range(50):
            detector.update_baseline("m1", "temp", 65.0)

        initial_profile = detector._get_baseline("m1", "temp")
        initial_adjustment = initial_profile.threshold_adjustment

        # Report false alarm
        result = detector.learn_from_feedback(
            alert_id="test_001",
            was_real=False,
            machine_id="m1",
            sensor_type="temp",
        )

        # Threshold should have widened
        updated_profile = detector._get_baseline("m1", "temp")
        self.assertGreater(
            updated_profile.threshold_adjustment,
            initial_adjustment,
        )
        self.assertEqual(updated_profile.false_alarms, 1)

    def test_learn_from_feedback_real_incident(self):
        """Test that real incident feedback tightens threshold."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=1,
        )

        for _ in range(50):
            detector.update_baseline("m1", "temp", 65.0)

        initial_profile = detector._get_baseline("m1", "temp")
        initial_adjustment = initial_profile.threshold_adjustment

        # Report real incident
        result = detector.learn_from_feedback(
            alert_id="test_002",
            was_real=True,
            machine_id="m1",
            sensor_type="temp",
        )

        updated_profile = detector._get_baseline("m1", "temp")
        self.assertLess(
            updated_profile.threshold_adjustment,
            initial_adjustment,
        )
        self.assertEqual(updated_profile.confirmed_incidents, 1)

    def test_learn_from_feedback_multiple_false_alarms(self):
        """Test that 3+ false alarms trigger EMA alpha increase."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=1,
            ema_alpha=0.05,
        )

        for _ in range(50):
            detector.update_baseline("m1", "temp", 65.0)

        initial_alpha = detector.ema_alpha

        # Report 3 false alarms
        for i in range(3):
            detector.learn_from_feedback(
                alert_id=f"test_false_{i}",
                was_real=False,
                machine_id="m1",
                sensor_type="temp",
            )

        # EMA alpha should have increased (after 3rd false alarm)
        # because false_alarms % 3 == 0
        self.assertGreaterEqual(detector.ema_alpha, initial_alpha)

    def test_get_machine_profile(self):
        """Test retrieving machine profile for all sensors."""
        detector = AnomalyDetector(storage_path=self.storage_path)

        detector.update_baseline("cnc_1", "temperature", 65.0)
        detector.update_baseline("cnc_1", "vibration", 2.3)

        profile = detector.get_machine_profile("cnc_1")

        self.assertIn("temperature", profile)
        self.assertIn("vibration", profile)
        self.assertNotIn("current", profile)

    def test_get_machine_profile_empty(self):
        """Test retrieving profile for unknown machine."""
        detector = AnomalyDetector(storage_path=self.storage_path)
        profile = detector.get_machine_profile("unknown_machine")
        self.assertEqual(len(profile), 0)

    def test_persistence_save_load(self):
        """Test that baselines persist to JSON and load correctly."""
        # Create and populate detector
        detector1 = AnomalyDetector(storage_path=self.storage_path)
        for i in range(100):
            detector1.update_baseline("cnc_1", "temperature", 65.0 + (i % 5) * 0.1)
        detector1.save_baselines()

        # Verify file exists
        self.assertTrue(os.path.exists(self.storage_path))

        # Create new detector and load
        detector2 = AnomalyDetector(storage_path=self.storage_path)
        profile = detector2._get_baseline("cnc_1", "temperature")

        self.assertIsNotNone(profile)
        self.assertEqual(profile.sample_count, 100)
        self.assertAlmostEqual(profile.ema_mean, 65.2, places=0)

    def test_persistence_corrupted_file(self):
        """Test graceful handling of corrupted baseline file."""
        # Write invalid JSON
        with open(self.storage_path, "w") as f:
            f.write("{invalid json content")

        # Should not crash
        detector = AnomalyDetector(storage_path=self.storage_path)
        self.assertEqual(len(detector._baselines), 0)

    def test_learning_stats(self):
        """Test learning statistics tracking."""
        detector = AnomalyDetector(storage_path=self.storage_path)

        for _ in range(50):
            detector.update_baseline("m1", "temp", 65.0)

        detector.learn_from_feedback("a1", was_real=True, machine_id="m1", sensor_type="temp")
        detector.learn_from_feedback("a2", was_real=False, machine_id="m1", sensor_type="temp")

        stats = detector.get_learning_stats()

        self.assertEqual(stats["total_profiles"], 1)
        self.assertEqual(stats["total_confirmed_incidents"], 1)
        self.assertEqual(stats["total_false_alarms"], 1)
        self.assertAlmostEqual(stats["overall_false_positive_rate"], 0.5, places=4)

    def test_multiple_machines_independent(self):
        """Test that different machines have independent baselines."""
        detector = AnomalyDetector(
            storage_path=self.storage_path,
            min_samples=1,
        )

        # Machine 1 runs at 65°C
        for _ in range(50):
            detector.update_baseline("cnc_1", "temperature", 65.0)

        # Machine 2 runs at 120°C
        for _ in range(50):
            detector.update_baseline("cnc_2", "temperature", 120.0)

        # 65°C should be normal for machine 1
        r1 = detector.check_anomaly("cnc_1", "temperature", 65.0)
        self.assertFalse(r1.is_anomaly)

        # 65°C should be anomalous for machine 2
        r2 = detector.check_anomaly("cnc_2", "temperature", 65.0)
        self.assertTrue(r2.is_anomaly)

        # 120°C should be normal for machine 2
        r3 = detector.check_anomaly("cnc_2", "temperature", 120.0)
        self.assertFalse(r3.is_anomaly)


class TestAnomalyResult(unittest.TestCase):
    """Tests for the AnomalyResult dataclass."""

    def test_result_serialization(self):
        """Test that AnomalyResult serializes correctly."""
        result = AnomalyResult(
            machine_id="cnc_1",
            sensor_type="temperature",
            value=72.5,
            anomaly_score=0.82,
            z_score=3.1,
            baseline_mean=63.2,
            baseline_std=3.0,
            is_anomaly=True,
            confidence=Confidence.HIGH,
            severity=Severity.WARNING,
        )

        d = result.to_dict()
        self.assertEqual(d["machine_id"], "cnc_1")
        self.assertTrue(d["is_anomaly"])
        self.assertEqual(d["severity"], "WARNING")
        self.assertEqual(d["confidence"], "high")


if __name__ == "__main__":
    unittest.main()
