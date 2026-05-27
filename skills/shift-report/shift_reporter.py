"""
Hermes-Pi Factory Guardian — Shift Reporter Skill

Auto-generates shift handoff reports summarizing alerts, sensor readings,
machine status, and learning updates. Output as formatted markdown.

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MachineStatus(Enum):
    """Machine operational status."""
    RUNNING = "running"
    STOPPED = "stopped"
    WARNING = "warning"
    UNKNOWN = "unknown"


@dataclass
class SensorSummary:
    """Statistical summary for a sensor on a machine."""
    machine_id: str
    sensor_type: str
    min_value: float
    max_value: float
    avg_value: float
    sample_count: int
    unit: str = ""
    trend: str = "stable"  # "improving", "stable", "degrading"


@dataclass
class AlertSummary:
    """Summary of alerts during a shift."""
    total: int = 0
    critical: int = 0
    warning: int = 0
    info: int = 0
    auto_suppressed: int = 0
    escalated: int = 0
    acknowledged: int = 0
    unacknowledged: int = 0
    mean_time_to_acknowledge_minutes: float = 0.0
    by_machine: Dict[str, int] = field(default_factory=dict)
    by_type: Dict[str, int] = field(default_factory=dict)


@dataclass
class LearningUpdate:
    """A learning update that occurred during the shift."""
    update_type: str  # "pattern_confirmed", "skill_created", "baseline_adjusted"
    description: str
    timestamp: float = field(default_factory=time.time)
    confidence: float = 0.0


@dataclass
class ShiftReport:
    """Complete shift report."""
    shift_name: str
    date: str
    start_time: float
    end_time: float
    generated_at: float = field(default_factory=time.time)
    alert_summary: Optional[AlertSummary] = None
    sensor_summaries: List[SensorSummary] = field(default_factory=list)
    machine_statuses: Dict[str, MachineStatus] = field(default_factory=dict)
    learning_updates: List[LearningUpdate] = field(default_factory=list)
    handoff_notes: List[str] = field(default_factory=list)
    machines_monitored: int = 0


class ShiftReporter:
    """
    Generates shift handoff reports for factory monitoring.

    Aggregates alert data, sensor readings, machine statuses, and
    learning updates into a comprehensive markdown report suitable
    for shift handoff between operators.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        """
        Initialize the shift reporter.

        Args:
            config_path: Path to factory configuration (optional).
        """
        self._config: Dict[str, Any] = {}
        self._shift_definitions: Dict[str, Dict[str, str]] = {}
        self._report_history: List[ShiftReport] = []

        self._load_config(config_path)

        logger.info("ShiftReporter initialized: %d shift definitions", len(self._shift_definitions))

    def _load_config(self, config_path: Optional[str] = None) -> None:
        """Load shift definitions from config."""
        if config_path:
            try:
                from pathlib import Path
                import yaml

                if Path(config_path).exists():
                    with open(config_path, "r", encoding="utf-8") as f:
                        self._config = yaml.safe_load(f) or {}
            except (ImportError, FileNotFoundError, Exception) as e:
                logger.warning("Could not load shift config: %s", e)

        # Default shift definitions
        self._shift_definitions = self._config.get("shift_report", {}).get("shifts", {
            "morning": {"start": "06:00", "end": "14:00"},
            "afternoon": {"start": "14:00", "end": "22:00"},
            "night": {"start": "22:00", "end": "06:00"},
        })

    def generate_report(
        self,
        shift_start: Optional[float] = None,
        shift_end: Optional[float] = None,
        shift_name: Optional[str] = None,
        alerts: Optional[List[Dict[str, Any]]] = None,
        sensor_readings: Optional[List[Dict[str, Any]]] = None,
        machine_statuses: Optional[Dict[str, str]] = None,
        learning_updates: Optional[List[Dict[str, Any]]] = None,
        handoff_notes: Optional[List[str]] = None,
    ) -> ShiftReport:
        """
        Generate a full shift report.

        Args:
            shift_start: Unix timestamp of shift start. Defaults to 8 hours ago.
            shift_end: Unix timestamp of shift end. Defaults to now.
            shift_name: Name of the shift (e.g., "morning").
            alerts: List of alert dictionaries from the alert router.
            sensor_readings: List of sensor reading dictionaries.
            machine_statuses: Dict of machine_id -> status string.
            learning_updates: List of learning update dictionaries.
            handoff_notes: Optional list of handoff note strings.

        Returns:
            Complete ShiftReport object.
        """
        now = time.time()

        # Determine shift timing
        if shift_start is None:
            shift_start = now - (8 * 3600)  # Default: 8 hours ago
        if shift_end is None:
            shift_end = now

        # Determine shift name from time
        if shift_name is None:
            shift_name = self._infer_shift_name(shift_start, shift_end)

        # Default data sources
        alerts = alerts or []
        sensor_readings = sensor_readings or []
        learning_updates = learning_updates or []
        handoff_notes = handoff_notes or []

        # Process each section
        alert_summary = self.summarize_alerts(alerts, shift_start, shift_end)
        sensor_summaries = self.summarize_sensors(sensor_readings, shift_start, shift_end)

        # Convert machine status strings to enum
        status_map: Dict[str, MachineStatus] = {}
        if machine_statuses:
            for mid, status_str in machine_statuses.items():
                try:
                    status_map[mid] = MachineStatus(status_str.lower())
                except ValueError:
                    status_map[mid] = MachineStatus.UNKNOWN

        # Convert learning update dicts
        learning_list: List[LearningUpdate] = []
        for lu in learning_updates:
            learning_list.append(LearningUpdate(
                update_type=lu.get("type", "unknown"),
                description=lu.get("description", ""),
                timestamp=lu.get("timestamp", now),
                confidence=lu.get("confidence", 0.0),
            ))

        report = ShiftReport(
            shift_name=shift_name,
            date=datetime.fromtimestamp(shift_start).strftime("%Y-%m-%d"),
            start_time=shift_start,
            end_time=shift_end,
            alert_summary=alert_summary,
            sensor_summaries=sensor_summaries,
            machine_statuses=status_map,
            learning_updates=learning_list,
            handoff_notes=handoff_notes,
            machines_monitored=len(status_map),
        )

        self._report_history.append(report)
        logger.info(
            "Shift report generated: %s shift on %s — "
            "%d alerts, %d sensor summaries, %d machines",
            shift_name, report.date,
            alert_summary.total, len(sensor_summaries), len(status_map),
        )

        return report

    def summarize_alerts(
        self,
        alerts: List[Dict[str, Any]],
        start_time: float,
        end_time: float,
    ) -> AlertSummary:
        """
        Summarize alerts within a time window.

        Args:
            alerts: List of alert dictionaries with 'severity', 'timestamp',
                    'machine_id', 'anomaly_type', 'acknowledged', etc.
            start_time: Start of the time window (Unix timestamp).
            end_time: End of the time window (Unix timestamp).

        Returns:
            AlertSummary with counts by severity, per-machine breakdown, etc.
        """
        summary = AlertSummary()

        filtered = [
            a for a in alerts
            if start_time <= a.get("timestamp", 0) <= end_time
        ]

        for alert in filtered:
            severity = alert.get("severity", "INFO").upper()
            summary.total += 1
            summary.by_machine[alert.get("machine_id", "unknown")] = (
                summary.by_machine.get(alert.get("machine_id", "unknown"), 0) + 1
            )
            summary.by_type[alert.get("anomaly_type", "unknown")] = (
                summary.by_type.get(alert.get("anomaly_type", "unknown"), 0) + 1
            )

            if severity == "CRITICAL":
                summary.critical += 1
            elif severity == "WARNING":
                summary.warning += 1
            else:
                summary.info += 1

            if alert.get("auto_suppressed", False):
                summary.auto_suppressed += 1
            if alert.get("escalated", False):
                summary.escalated += 1
            if alert.get("acknowledged", False):
                summary.acknowledged += 1
                # Track MTTA
                if "acknowledged_at" in alert and "timestamp" in alert:
                    ack_time = alert["acknowledged_at"] - alert["timestamp"]
                    summary.mean_time_to_acknowledge_minutes = (
                        ack_time / 60.0
                    )
            else:
                summary.unacknowledged += 1

        return summary

    def summarize_sensors(
        self,
        readings: List[Dict[str, Any]],
        start_time: float,
        end_time: float,
    ) -> List[SensorSummary]:
        """
        Calculate min/max/avg statistics for sensor readings.

        Args:
            readings: List of reading dicts with 'machine_id', 'sensor_type',
                      'value', 'timestamp', 'unit'.
            start_time: Start of time window.
            end_time: End of time window.

        Returns:
            List of SensorSummary objects, one per machine+sensor combination.
        """
        # Group readings by machine_id + sensor_type
        groups: Dict[tuple, List[float]] = {}
        group_meta: Dict[tuple, Dict[str, str]] = {}

        for r in readings:
            ts = r.get("timestamp", 0)
            if not (start_time <= ts <= end_time):
                continue
            key = (r.get("machine_id", "unknown"), r.get("sensor_type", "unknown"))
            if key not in groups:
                groups[key] = []
                group_meta[key] = {
                    "unit": r.get("unit", ""),
                }
            groups[key].append(float(r.get("value", 0)))

        summaries: List[SensorSummary] = []
        for (machine_id, sensor_type), values in groups.items():
            if not values:
                continue

            sorted_values = sorted(values)
            n = len(sorted_values)
            avg_val = sum(sorted_values) / n

            # Simple trend detection: compare first half avg vs second half avg
            mid = n // 2
            first_half_avg = sum(sorted_values[:mid]) / max(mid, 1)
            second_half_avg = sum(sorted_values[mid:]) / max(n - mid, 1)
            diff_pct = (
                (second_half_avg - first_half_avg) / max(abs(first_half_avg), 0.001)
            ) * 100

            if diff_pct > 5:
                trend = "degrading"
            elif diff_pct < -5:
                trend = "improving"
            else:
                trend = "stable"

            summaries.append(SensorSummary(
                machine_id=machine_id,
                sensor_type=sensor_type,
                min_value=min(sorted_values),
                max_value=max(sorted_values),
                avg_value=avg_val,
                sample_count=n,
                unit=group_meta[key].get("unit", ""),
                trend=trend,
            ))

        return summaries

    def get_machine_status(
        self,
        machine_statuses: Optional[Dict[str, str]] = None,
    ) -> Dict[str, MachineStatus]:
        """
        Get current status of all monitored machines.

        Args:
            machine_statuses: Override dict of machine_id -> status string.

        Returns:
            Dictionary of machine_id -> MachineStatus.
        """
        result: Dict[str, MachineStatus] = {}
        if machine_statuses:
            for mid, status_str in machine_statuses.items():
                try:
                    result[mid] = MachineStatus(status_str.lower())
                except ValueError:
                    result[mid] = MachineStatus.UNKNOWN
        return result

    def format_report(self, report: ShiftReport) -> str:
        """
        Format a ShiftReport as a markdown string.

        Args:
            report: The ShiftReport to format.

        Returns:
            Formatted markdown report string.
        """
        lines: List[str] = []
        sep = "═" * 65
        thin_sep = "─" * 65

        start_str = datetime.fromtimestamp(report.start_time).strftime("%H:%M")
        end_str = datetime.fromtimestamp(report.end_time).strftime("%H:%M")
        gen_str = datetime.fromtimestamp(report.generated_at).strftime("%H:%M:%S")

        # Header
        lines.append(sep)
        lines.append(
            f"  SHIFT REPORT — {report.shift_name.upper()} Shift "
            f"({start_str}–{end_str})"
        )
        lines.append(f"  Date: {report.date}")
        lines.append(f"  Generated: {gen_str}")
        lines.append(sep)
        lines.append("")

        # Alert Summary
        lines.append("📊 ALERT SUMMARY")
        if report.alert_summary:
            a = report.alert_summary
            lines.append(f"  Total:     {a.total}")
            lines.append(f"  Critical:  {a.critical}")
            lines.append(f"  Warning:   {a.warning}")
            lines.append(f"  Info:      {a.info}")
            if a.auto_suppressed > 0:
                lines.append(f"  Auto-suppressed (learned): {a.auto_suppressed}")
            if a.escalated > 0:
                lines.append(f"  Escalated: {a.escalated}")
            if a.unacknowledged > 0:
                lines.append(f"  ⚠️  Unacknowledged: {a.unacknowledged}")
            if a.mean_time_to_acknowledge_minutes > 0:
                lines.append(
                    f"  Mean time to acknowledge: "
                    f"{a.mean_time_to_acknowledge_minutes:.1f} min"
                )
            if a.by_machine:
                lines.append("")
                lines.append("  By machine:")
                for mid, count in sorted(a.by_machine.items()):
                    lines.append(f"    {mid}: {count}")
        else:
            lines.append("  No alerts this shift ✅")
        lines.append("")

        # Sensor Summary
        lines.append("🌡️  SENSOR SUMMARY")
        if report.sensor_summaries:
            # Group by machine
            machines: Dict[str, List[SensorSummary]] = {}
            for s in report.sensor_summaries:
                machines.setdefault(s.machine_id, []).append(s)

            header = f"  {'Machine':<20} | {'Sensor':<12} | {'Min':>8} | {'Max':>8} | {'Avg':>8} | {'Trend':<10}"
            lines.append(header)
            lines.append(f"  {'─'*20}-+-{'─'*12}-+-{'─'*8}-+-{'─'*8}-+-{'─'*8}-+-{'─'*10}")

            for mid in sorted(machines.keys()):
                sensors = machines[mid]
                for i, s in enumerate(sensors):
                    display_name = mid.replace("_", " ").title() if i == 0 else ""
                    trend_arrow = {
                        "improving": "✅ ↓",
                        "stable": "➡️  →",
                        "degrading": "⚠️  ↑",
                    }.get(s.trend, "❓ ?")

                    lines.append(
                        f"  {display_name:<20} | {s.sensor_type:<12} | "
                        f"{s.min_value:>8.1f} | {s.max_value:>8.1f} | "
                        f"{s.avg_value:>8.1f} | {trend_arrow:<10}"
                    )
        else:
            lines.append("  No sensor data available")
        lines.append("")

        # Machine Status
        lines.append("🏭 MACHINE STATUS")
        if report.machine_statuses:
            for mid, status in sorted(report.machine_statuses.items()):
                name = mid.replace("_", " ").title()
                icon = {
                    MachineStatus.RUNNING: "✅ RUNNING",
                    MachineStatus.STOPPED: "🔴 STOPPED",
                    MachineStatus.WARNING: "⚠️  WARNING",
                    MachineStatus.UNKNOWN: "❓ UNKNOWN",
                }.get(status, "❓ UNKNOWN")
                lines.append(f"  {name}: {icon}")
        else:
            lines.append("  No machine status data")
        lines.append("")

        # Learning Updates
        lines.append("🧠 LEARNING UPDATES")
        if report.learning_updates:
            for lu in report.learning_updates:
                icon = {
                    "pattern_confirmed": "✅",
                    "skill_created": "🆕",
                    "baseline_adjusted": "🔧",
                    "threshold_tightened": "🎯",
                    "threshold_widened": "📐",
                }.get(lu.update_type, "ℹ️ ")
                conf_str = (
                    f" ({lu.confidence:.0%} confidence)" if lu.confidence > 0 else ""
                )
                lines.append(f"  {icon} {lu.description}{conf_str}")
        else:
            lines.append("  No learning updates this shift")
        lines.append("")

        # Handoff Notes
        if report.handoff_notes:
            lines.append("📝 HANDOFF NOTES")
            for note in report.handoff_notes:
                lines.append(f"  • {note}")
            lines.append("")

        lines.append(sep)
        return "\n".join(lines)

    def _infer_shift_name(self, start_time: float, end_time: float) -> str:
        """Infer the shift name from timestamps."""
        start_dt = datetime.fromtimestamp(start_time)
        start_hour = start_dt.hour

        for name, times in self._shift_definitions.items():
            start_parts = times.get("start", "00:00").split(":")
            end_parts = times.get("end", "23:59").split(":")
            shift_start = int(start_parts[0])
            shift_end = int(end_parts[0])

            if shift_start <= shift_end:
                if shift_start <= start_hour < shift_end:
                    return name
            else:
                # Overnight shift
                if start_hour >= shift_start or start_hour < shift_end:
                    return name

        return "custom"
