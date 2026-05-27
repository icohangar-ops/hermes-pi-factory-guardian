"""
Hermes-Pi Factory Guardian — Anomaly Detection Skill

Detects anomalies in camera feeds and sensor data using Z-score analysis
with exponential moving average (EMA) baselines. Adapts thresholds based
on operator feedback through the Hermes learning loop.

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Confidence(Enum):
    """Detection confidence levels based on Z-score magnitude."""
    LOW = "low"          # |z| < 2.0
    MEDIUM = "medium"    # 2.0 <= |z| < 3.0
    HIGH = "high"        # 3.0 <= |z| < 4.0
    EXTREME = "extreme"  # |z| >= 4.0


@dataclass
class BaselineProfile:
    """Statistical baseline for a single machine+sensor combination."""
    machine_id: str
    sensor_type: str
    values: List[float] = field(default_factory=list)
    ema_mean: float = 0.0
    ema_variance: float = 0.0
    sample_count: int = 0
    threshold_adjustment: float = 1.0  # Multiplier: 1.0 = default, >1.0 = wider
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    confirmed_incidents: int = 0
    false_alarms: int = 0

    @property
    def ema_std(self) -> float:
        """Standard deviation from exponential moving variance."""
        return math.sqrt(max(self.ema_variance, 1e-8))

    @property
    def is_reliable(self) -> bool:
        """Whether the baseline has enough data to be trustworthy."""
        return self.sample_count >= 20

    @property
    def false_positive_rate(self) -> float:
        """Running false positive rate for this profile."""
        total = self.confirmed_incidents + self.false_alarms
        if total == 0:
            return 0.0
        return self.false_alarms / total

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "machine_id": self.machine_id,
            "sensor_type": self.sensor_type,
            "values": self.values[-500:],  # Keep last 500 for recalculation
            "ema_mean": self.ema_mean,
            "ema_variance": self.ema_variance,
            "sample_count": self.sample_count,
            "threshold_adjustment": self.threshold_adjustment,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confirmed_incidents": self.confirmed_incidents,
            "false_alarms": self.false_alarms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BaselineProfile:
        """Deserialize from dictionary."""
        profile = cls(
            machine_id=data["machine_id"],
            sensor_type=data["sensor_type"],
            values=data.get("values", []),
            ema_mean=data.get("ema_mean", 0.0),
            ema_variance=data.get("ema_variance", 0.0),
            sample_count=data.get("sample_count", 0),
            threshold_adjustment=data.get("threshold_adjustment", 1.0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            confirmed_incidents=data.get("confirmed_incidents", 0),
            false_alarms=data.get("false_alarms", 0),
        )
        return profile


@dataclass
class AnomalyResult:
    """Result of an anomaly check."""
    machine_id: str
    sensor_type: str
    value: float
    anomaly_score: float
    z_score: float
    baseline_mean: float
    baseline_std: float
    is_anomaly: bool
    confidence: Confidence
    severity: Severity
    timestamp: float = field(default_factory=time.time)
    threshold_used: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "machine_id": self.machine_id,
            "sensor_type": self.sensor_type,
            "value": self.value,
            "anomaly_score": round(self.anomaly_score, 4),
            "z_score": round(self.z_score, 2),
            "baseline_mean": round(self.baseline_mean, 2),
            "baseline_std": round(self.baseline_std, 2),
            "is_anomaly": self.is_anomaly,
            "confidence": self.confidence.value,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "threshold_used": round(self.threshold_used, 2),
        }


class AnomalyDetector:
    """
    Detects anomalies using Z-score analysis with adaptive EMA baselines.

    Maintains per-machine, per-sensor-type statistical baselines and
    continuously adapts them based on new data and operator feedback.

    The anomaly score ranges from 0.0 (perfectly normal) to 1.0 (extreme
    anomaly). It is derived from the Z-score with a sigmoid-like mapping.
    """

    DEFAULT_EMA_ALPHA = 0.05
    DEFAULT_ZSCORE_THRESHOLD = 2.5
    DEFAULT_MIN_SAMPLES = 20
    DEFAULT_MAX_HISTORY = 10000

    def __init__(
        self,
        config_path: Optional[str] = None,
        storage_path: Optional[str] = None,
        ema_alpha: float = DEFAULT_EMA_ALPHA,
        zscore_threshold: float = DEFAULT_ZSCORE_THRESHOLD,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        max_history: int = DEFAULT_MAX_HISTORY,
    ) -> None:
        """
        Initialize the anomaly detector.

        Args:
            config_path: Path to factory_config.yaml (optional).
            storage_path: Path to persist baselines JSON file.
            ema_alpha: Exponential moving average smoothing factor (0-1).
            zscore_threshold: Z-score above which readings are flagged.
            min_samples: Minimum samples before baseline is reliable.
            max_history: Maximum values kept per sensor baseline.
        """
        self.ema_alpha = ema_alpha
        self.zscore_threshold = zscore_threshold
        self.min_samples = min_samples
        self.max_history = max_history
        self.storage_path = Path(storage_path or "data/baselines.json")

        # Baselines keyed by (machine_id, sensor_type) tuples
        self._baselines: Dict[Tuple[str, str], BaselineProfile] = {}

        # Feedback history for learning stats
        self._feedback_history: List[Dict[str, Any]] = []

        # Load persisted baselines
        self._load_baselines()

        logger.info(
            "AnomalyDetector initialized: alpha=%.3f, z_threshold=%.1f, "
            "min_samples=%d, baselines_loaded=%d",
            self.ema_alpha, self.zscore_threshold, self.min_samples,
            len(self._baselines),
        )

    def _get_baseline(self, machine_id: str, sensor_type: str) -> BaselineProfile:
        """Get or create a baseline profile for a machine+sensor pair."""
        key = (machine_id, sensor_type)
        if key not in self._baselines:
            self._baselines[key] = BaselineProfile(
                machine_id=machine_id,
                sensor_type=sensor_type,
            )
            logger.info(
                "Created new baseline profile: machine=%s, sensor=%s",
                machine_id, sensor_type,
            )
        return self._baselines[key]

    def update_baseline(
        self,
        machine_id: str,
        sensor_type: str,
        value: float,
    ) -> BaselineProfile:
        """
        Update the EMA baseline with a new sensor reading.

        Args:
            machine_id: Identifier of the machine.
            sensor_type: Type of sensor (e.g., "temperature", "vibration").
            value: The sensor reading.

        Returns:
            Updated BaselineProfile.
        """
        profile = self._get_baseline(machine_id, sensor_type)

        # Update EMA mean
        if profile.sample_count == 0:
            profile.ema_mean = value
            profile.ema_variance = 0.0
        else:
            # EMA update for mean
            delta = value - profile.ema_mean
            profile.ema_mean = profile.ema_mean + self.ema_alpha * delta

            # EMA update for variance (Welford-like online)
            delta2 = value - profile.ema_mean
            profile.ema_variance = (
                (1 - self.ema_alpha) * profile.ema_variance
                + self.ema_alpha * delta2 * delta
            )

        profile.sample_count += 1
        profile.values.append(value)
        if len(profile.values) > self.max_history:
            profile.values = profile.values[-self.max_history:]
        profile.updated_at = time.time()

        logger.debug(
            "Baseline updated: machine=%s, sensor=%s, value=%.2f, "
            "ema_mean=%.2f, ema_std=%.2f, samples=%d",
            machine_id, sensor_type, value,
            profile.ema_mean, profile.ema_std, profile.sample_count,
        )

        return profile

    def check_anomaly(
        self,
        machine_id: str,
        sensor_type: str,
        value: float,
    ) -> AnomalyResult:
        """
        Check if a sensor reading is anomalous.

        Computes the Z-score of the value against the learned baseline
        and maps it to an anomaly score (0-1). If the baseline is not
        yet reliable (insufficient samples), returns a conservative
        non-anomaly result.

        Args:
            machine_id: Identifier of the machine.
            sensor_type: Type of sensor.
            value: The sensor reading to check.

        Returns:
            AnomalyResult with score, Z-score, severity, and confidence.
        """
        profile = self._get_baseline(machine_id, sensor_type)

        # Update baseline with this reading as part of continuous learning
        self.update_baseline(machine_id, sensor_type, value)

        # If baseline isn't reliable yet, return non-anomaly with low confidence
        if not profile.is_reliable:
            logger.debug(
                "Baseline not yet reliable: machine=%s, sensor=%s, samples=%d/%d",
                machine_id, sensor_type, profile.sample_count, self.min_samples,
            )
            return AnomalyResult(
                machine_id=machine_id,
                sensor_type=sensor_type,
                value=value,
                anomaly_score=0.0,
                z_score=0.0,
                baseline_mean=profile.ema_mean,
                baseline_std=profile.ema_std,
                is_anomaly=False,
                confidence=Confidence.LOW,
                severity=Severity.INFO,
                threshold_used=self._effective_threshold(profile),
            )

        # Compute Z-score
        std = profile.ema_std
        if std < 1e-8:
            z_score = 0.0
        else:
            z_score = (value - profile.ema_mean) / std

        # Effective threshold with per-machine adjustment
        effective_threshold = self._effective_threshold(profile)

        # Map Z-score to anomaly score (0-1) using sigmoid-like function
        anomaly_score = self._zscore_to_score(z_score)

        # Determine confidence level
        abs_z = abs(z_score)
        if abs_z < 2.0:
            confidence = Confidence.LOW
        elif abs_z < 3.0:
            confidence = Confidence.MEDIUM
        elif abs_z < 4.0:
            confidence = Confidence.HIGH
        else:
            confidence = Confidence.EXTREME

        # Determine if anomalous
        is_anomaly = abs_z > effective_threshold

        # Determine severity
        if abs_z > effective_threshold * 2.0:
            severity = Severity.CRITICAL
        elif is_anomaly:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO

        result = AnomalyResult(
            machine_id=machine_id,
            sensor_type=sensor_type,
            value=value,
            anomaly_score=anomaly_score,
            z_score=z_score,
            baseline_mean=profile.ema_mean,
            baseline_std=profile.ema_std,
            is_anomaly=is_anomaly,
            confidence=confidence,
            severity=severity,
            threshold_used=effective_threshold,
        )

        if is_anomaly:
            logger.warning(
                "ANOMALY DETECTED: machine=%s, sensor=%s, value=%.2f, "
                "z_score=%.2f, severity=%s, confidence=%s",
                machine_id, sensor_type, value, z_score,
                severity.value, confidence.value,
            )
        else:
            logger.debug(
                "Normal reading: machine=%s, sensor=%s, value=%.2f, z_score=%.2f",
                machine_id, sensor_type, value, z_score,
            )

        return result

    def learn_from_feedback(
        self,
        alert_id: str,
        was_real: bool,
        machine_id: Optional[str] = None,
        sensor_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adjust baselines and thresholds based on operator feedback.

        When a confirmed false alarm is received, the baseline is widened
        to reduce future false positives. When a real incident is confirmed,
        thresholds are tightened for that machine+sensor.

        Args:
            alert_id: Unique identifier of the alert being evaluated.
            was_real: True if the alert was a real incident, False if false alarm.
            machine_id: Machine identifier (optional, looked up from alert).
            sensor_type: Sensor type (optional, looked up from alert).

        Returns:
            Dictionary describing the adjustments made.
        """
        # Record feedback event
        feedback_event: Dict[str, Any] = {
            "alert_id": alert_id,
            "was_real": was_real,
            "machine_id": machine_id,
            "sensor_type": sensor_type,
            "timestamp": time.time(),
        }
        self._feedback_history.append(feedback_event)

        if machine_id is None or sensor_type is None:
            logger.info(
                "Feedback received for alert %s (was_real=%s), but no "
                "machine_id/sensor_type provided. Recording only.",
                alert_id, was_real,
            )
            return {"action": "recorded_only"}

        profile = self._get_baseline(machine_id, sensor_type)
        adjustments: List[str] = []

        if was_real:
            # Tighten threshold — make detection more sensitive
            old_adjustment = profile.threshold_adjustment
            profile.threshold_adjustment = max(
                0.5,  # Don't tighten below 50% of original
                profile.threshold_adjustment * 0.95,  # Reduce by 5%
            )
            profile.confirmed_incidents += 1
            adjustments.append(
                f"Threshold tightened: {old_adjustment:.3f} → "
                f"{profile.threshold_adjustment:.3f}"
            )
            logger.info(
                "Real incident confirmed: machine=%s, sensor=%s. %s",
                machine_id, sensor_type, adjustments[0],
            )
        else:
            # Widen baseline — reduce false positives
            old_adjustment = profile.threshold_adjustment
            profile.threshold_adjustment = min(
                3.0,  # Don't widen beyond 3x original
                profile.threshold_adjustment * 1.10,  # Increase by 10%
            )
            profile.false_alarms += 1
            adjustments.append(
                f"Threshold widened: {old_adjustment:.3f} → "
                f"{profile.threshold_adjustment:.3f}"
            )

            # After 3+ false alarms for same profile, also widen the baseline
            if profile.false_alarms >= 3 and profile.false_alarms % 3 == 0:
                # Increase EMA alpha temporarily to adapt faster
                old_alpha = self.ema_alpha
                self.ema_alpha = min(0.15, old_alpha * 1.2)
                adjustments.append(
                    f"EMA alpha increased: {old_alpha:.3f} → {self.ema_alpha:.3f} "
                    f"(after {profile.false_alarms} false alarms)"
                )
                logger.info(
                    "Multiple false alarms: machine=%s, sensor=%s. %s",
                    machine_id, sensor_type, adjustments[1],
                )

            logger.info(
                "False alarm confirmed: machine=%s, sensor=%s. %s",
                machine_id, sensor_type, adjustments[0],
            )

        profile.updated_at = time.time()

        # Persist after feedback
        self._save_baselines()

        return {
            "alert_id": alert_id,
            "was_real": was_real,
            "machine_id": machine_id,
            "sensor_type": sensor_type,
            "adjustments": adjustments,
            "profile_false_positive_rate": round(profile.false_positive_rate, 4),
            "total_confirmed_incidents": profile.confirmed_incidents,
            "total_false_alarms": profile.false_alarms,
        }

    def get_machine_profile(self, machine_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get the current baseline profile for all sensors on a machine.

        Args:
            machine_id: Identifier of the machine.

        Returns:
            Dictionary of sensor_type -> profile data.
        """
        profiles: Dict[str, Dict[str, Any]] = {}
        for (mid, sensor_type), profile in self._baselines.items():
            if mid == machine_id:
                profiles[sensor_type] = {
                    "ema_mean": round(profile.ema_mean, 2),
                    "ema_std": round(profile.ema_std, 2),
                    "sample_count": profile.sample_count,
                    "is_reliable": profile.is_reliable,
                    "threshold_adjustment": round(profile.threshold_adjustment, 3),
                    "false_positive_rate": round(profile.false_positive_rate, 4),
                    "confirmed_incidents": profile.confirmed_incidents,
                    "false_alarms": profile.false_alarms,
                    "last_updated": profile.updated_at,
                }
        return profiles

    def get_all_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Get all baseline profiles grouped by machine."""
        result: Dict[str, Dict[str, Any]] = {}
        seen_machines: set = set()
        for (machine_id, _), _ in self._baselines.items():
            if machine_id not in seen_machines:
                result[machine_id] = self.get_machine_profile(machine_id)
                seen_machines.add(machine_id)
        return result

    def get_learning_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the detector's learning progress.

        Returns:
            Dictionary with false positive rates, adjustment counts, etc.
        """
        total_incidents = sum(
            p.confirmed_incidents for p in self._baselines.values()
        )
        total_false_alarms = sum(
            p.false_alarms for p in self._baselines.values()
        )
        total_feedback = total_incidents + total_false_alarms
        overall_fpr = (
            total_false_alarms / total_feedback if total_feedback > 0 else 0.0
        )

        return {
            "total_profiles": len(self._baselines),
            "reliable_profiles": sum(
                1 for p in self._baselines.values() if p.is_reliable
            ),
            "total_samples": sum(
                p.sample_count for p in self._baselines.values()
            ),
            "total_confirmed_incidents": total_incidents,
            "total_false_alarms": total_false_alarms,
            "overall_false_positive_rate": round(overall_fpr, 4),
            "total_feedback_events": len(self._feedback_history),
            "threshold_adjustments": {
                mid: round(p.threshold_adjustment, 3)
                for (mid, st), p in self._baselines.items()
            },
        }

    def _effective_threshold(self, profile: BaselineProfile) -> float:
        """Calculate the effective Z-score threshold for a profile."""
        return self.zscore_threshold * profile.threshold_adjustment

    def _zscore_to_score(self, z_score: float) -> float:
        """
        Map a Z-score to an anomaly score in [0, 1].

        Uses a sigmoid-like function centered at z=0 with steepness
        controlled by the base threshold. Scores above 0.5 indicate
        anomalous readings.

        Args:
            z_score: The computed Z-score.

        Returns:
            Anomaly score between 0.0 and 1.0.
        """
        # Sigmoid: score = 1 / (1 + exp(-k * (|z| - t)))
        # where k = steepness, t = center point
        abs_z = abs(z_score)
        steepness = 1.0
        center = self.zscore_threshold * 0.8  # Slightly below threshold
        try:
            score = 1.0 / (1.0 + math.exp(-steepness * (abs_z - center)))
        except OverflowError:
            score = 1.0 if abs_z > center else 0.0
        return max(0.0, min(1.0, score))

    def _load_baselines(self) -> None:
        """Load persisted baselines from JSON file."""
        if not self.storage_path.exists():
            logger.info(
                "No existing baseline file: %s — starting fresh",
                self.storage_path,
            )
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for profile_data in data.get("baselines", []):
                profile = BaselineProfile.from_dict(profile_data)
                key = (profile.machine_id, profile.sensor_type)
                self._baselines[key] = profile

            logger.info(
                "Loaded %d baseline profiles from %s",
                len(self._baselines), self.storage_path,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(
                "Failed to load baselines from %s: %s — starting fresh",
                self.storage_path, e,
            )

    def _save_baselines(self) -> None:
        """Persist baselines to JSON file."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0.0",
                "saved_at": time.time(),
                "baselines": [
                    profile.to_dict() for profile in self._baselines.values()
                ],
            }
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved %d baselines to %s", len(self._baselines), self.storage_path)
        except OSError as e:
            logger.error("Failed to save baselines to %s: %s", self.storage_path, e)

    def save_baselines(self) -> None:
        """Public method to persist baselines."""
        self._save_baselines()
