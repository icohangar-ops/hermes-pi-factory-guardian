"""
Hermes-Pi Factory Guardian — Alert Router Skill

Routes alerts to appropriate notification channels (Telegram, Slack, Email)
based on severity, time of day, machine criticality, and shift schedule.

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Channel(Enum):
    """Notification channels."""
    LOG = "log"
    TELEGRAM = "telegram"
    SLACK = "slack"
    EMAIL = "email"


@dataclass
class Shift:
    """A work shift definition."""
    name: str
    start: dt_time
    end: dt_time
    telegram_chat_id: str = ""
    on_call: List[str] = field(default_factory=list)
    is_active: bool = False


@dataclass
class MachineCriticality:
    """Criticality level for a machine."""
    machine_id: str
    level: str  # LOW, MEDIUM, HIGH
    escalation_minutes: int = 30


@dataclass
class Alert:
    """An alert to be routed."""
    alert_id: str
    severity: Severity
    machine_id: str
    anomaly_type: str
    anomaly_score: float
    value: float
    baseline_mean: float
    baseline_std: float
    description: str
    timestamp: float = field(default_factory=time.time)
    image_path: Optional[str] = None
    camera_id: Optional[str] = None
    learning_note: Optional[str] = None
    acknowledged: bool = False
    escalation_set: Optional[float] = None


@dataclass
class RoutingDecision:
    """The result of routing an alert."""
    alert_id: str
    severity: Severity
    channels: List[Channel]
    shift_name: str
    messages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    deduplicated: bool = False
    escalated: bool = False
    timestamp: float = field(default_factory=time.time)


class AlertRouter:
    """
    Routes factory alerts to appropriate channels based on severity,
    shift schedule, and machine criticality.

    Implements intelligent escalation, deduplication, and shift-aware
    routing to minimize alert fatigue while ensuring critical issues
    reach the right people immediately.
    """

    # Message templates per severity
    TEMPLATES = {
        Severity.INFO: (
            "ℹ️ {machine_name} — {anomaly_type}\n"
            "{description}\n"
            "Pattern: {pattern}\n"
            "Logged at: {timestamp}"
        ),
        Severity.WARNING: (
            "⚠️ WARNING — {machine_name} — {anomaly_type}\n"
            "{description}\n"
            "Value: {value} | Normal: {baseline_mean} ± {baseline_std}\n"
            "Score: {anomaly_score}/1.0\n"
            "Time: {timestamp}"
        ),
        Severity.CRITICAL: (
            "🚨 CRITICAL — {machine_name} — {anomaly_type}\n"
            "{description}\n"
            "Value: {value} | Normal: {baseline_mean} ± {baseline_std}\n"
            "Score: {anomaly_score}/1.0\n"
            "Time: {timestamp}\n\n"
            "📸 Anomaly frame: {image_note}\n"
            "📍 Camera: {camera_id}\n"
            "🧠 Learning: {learning_note}\n\n"
            "IMMEDIATE ACTION REQUIRED"
        ),
    }

    def __init__(self, config_path: Optional[str] = None) -> None:
        """
        Initialize the alert router.

        Args:
            config_path: Path to factory_config.yaml or config dict.
        """
        self._config: Dict[str, Any] = {}
        self._shifts: Dict[str, Shift] = {}
        self._machine_criticality: Dict[str, MachineCriticality] = {}
        self._dedup_cache: Dict[str, float] = {}  # key -> timestamp
        self._alert_history: List[Alert] = []
        self._rate_limits: Dict[str, List[float]] = {}  # channel -> timestamps

        # Channel credentials (loaded from env or config)
        self._telegram_bot_token: str = ""
        self._telegram_chat_id: str = ""
        self._slack_webhook_url: str = ""
        self._slack_channel: str = "#factory-alerts"
        self._email_smtp: str = ""
        self._email_recipients: List[str] = []

        self._load_config(config_path)
        self._load_env_credentials()

        logger.info(
            "AlertRouter initialized: %d shifts, %d machine criticalities",
            len(self._shifts), len(self._machine_criticality),
        )

    def _load_config(self, config_path: Optional[str] = None) -> None:
        """Load configuration from YAML file."""
        if config_path and Path(config_path).exists():
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    self._config = yaml.safe_load(f) or {}
            except ImportError:
                logger.warning("PyYAML not installed, using default config")
            except Exception as e:
                logger.error("Failed to load config from %s: %s", config_path, e)

        # Load shifts
        shifts_config = self._config.get("alert_routing", {}).get("shifts", {})
        for name, shift_data in shifts_config.items():
            start_parts = shift_data.get("start", "00:00").split(":")
            end_parts = shift_data.get("end", "23:59").split(":")
            self._shifts[name] = Shift(
                name=name,
                start=dt_time(int(start_parts[0]), int(start_parts[1])),
                end=dt_time(int(end_parts[0]), int(end_parts[1])),
                telegram_chat_id=shift_data.get("telegram_chat_id", ""),
            )

        # Load machine criticality
        machines_config = self._config.get("machines", {})
        for machine_id, machine_data in machines_config.items():
            self._machine_criticality[machine_id] = MachineCriticality(
                machine_id=machine_id,
                level=machine_data.get("criticality", "MEDIUM").upper(),
                escalation_minutes=machine_data.get("escalation_minutes", 30),
            )

        # Deduplication config
        self._dedup_window = self._config.get("alert_routing", {}).get(
            "deduplication", {}
        ).get("window_minutes", 60)

        # Rate limiting config
        self._rate_limit_per_minute = self._config.get("alert_routing", {}).get(
            "rate_limit_per_minute", 10,
        )

    def _load_env_credentials(self) -> None:
        """Load credentials from environment variables."""
        self._telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self._email_smtp = os.getenv("EMAIL_SMTP_SERVER", "")
        email_recipients = os.getenv("EMAIL_RECIPIENTS", "")
        self._email_recipients = (
            email_recipients.split(",") if email_recipients else []
        )

        # Override from config if present
        routing = self._config.get("alert_routing", {})
        channels = routing.get("channels", {})
        if channels:
            self._telegram_bot_token = (
                channels.get("telegram", {}).get("bot_token", "")
                or self._telegram_bot_token
            )
            self._telegram_chat_id = (
                channels.get("telegram", {}).get("chat_id", "")
                or self._telegram_chat_id
            )
            self._slack_webhook_url = (
                channels.get("slack", {}).get("webhook_url", "")
                or self._slack_webhook_url
            )
            self._slack_channel = (
                channels.get("slack", {}).get("channel", "#factory-alerts")
                or self._slack_channel
            )

    def route_alert(self, alert: Alert) -> RoutingDecision:
        """
        Route an alert to the appropriate channels.

        Determines channels based on severity, current shift, machine
        criticality, deduplication, and rate limiting.

        Args:
            alert: The Alert to route.

        Returns:
            RoutingDecision with channel list and delivery status.
        """
        now = datetime.now()
        current_time = now.time()
        current_shift = self._get_current_shift(current_time)

        # Get escalation policy
        policy = self.get_escalation_policy(
            severity=alert.severity,
            time_of_day=current_time,
            machine_id=alert.machine_id,
        )

        # Check deduplication
        dedup_key = f"{alert.machine_id}:{alert.anomaly_type}"
        is_dedup = self._is_duplicate(dedup_key)

        if is_dedup and alert.severity != Severity.CRITICAL:
            logger.info(
                "Alert deduplicated: %s (last sent %d min ago)",
                alert.alert_id, self._dedup_window,
            )
            self._dedup_cache[dedup_key] = time.time()
            return RoutingDecision(
                alert_id=alert.alert_id,
                severity=alert.severity,
                channels=[Channel.LOG],
                shift_name=current_shift.name if current_shift else "unknown",
                deduplicated=True,
            )

        # Determine channels
        channels = policy["channels"]
        if not channels:
            channels = [Channel.LOG]

        # Format messages
        messages: Dict[str, Dict[str, Any]] = {}
        for channel in channels:
            if channel == Channel.TELEGRAM:
                messages["telegram"] = self._format_message(alert, "telegram")
            elif channel == Channel.SLACK:
                messages["slack"] = self._format_message(alert, "slack")
            elif channel == Channel.EMAIL:
                messages["email"] = self._format_message(alert, "email")

        # Send to channels
        send_results: Dict[str, Dict[str, Any]] = {}
        for channel in channels:
            try:
                if channel == Channel.TELEGRAM:
                    send_results["telegram"] = self.send_telegram(
                        messages.get("telegram", {}).get("text", ""),
                        alert.image_path,
                    )
                elif channel == Channel.SLACK:
                    send_results["slack"] = self.send_slack(
                        messages.get("slack", {}).get("text", ""),
                        alert.image_path,
                    )
                elif channel == Channel.EMAIL:
                    send_results["email"] = self._send_email(
                        messages.get("email", {}).get("text", ""),
                    )
                elif channel == Channel.LOG:
                    self._log_alert(alert)
                    send_results["log"] = {"sent": True}
            except Exception as e:
                logger.error("Failed to send to %s: %s", channel.value, e)
                send_results[channel.value] = {"sent": False, "error": str(e)}

        # Update dedup cache
        self._dedup_cache[dedup_key] = time.time()

        # Set escalation timer for WARNING alerts
        is_escalated = False
        if alert.severity == Severity.WARNING:
            escalation_delay = self._config.get("alert_routing", {}).get(
                "escalation", {}
            ).get("warning_to_critical_minutes", 30)
            alert.escalation_set = time.time() + (escalation_delay * 60)
            is_escalated = True

        # Store in history
        self._alert_history.append(alert)

        logger.info(
            "Alert routed: %s, severity=%s, channels=%s, shift=%s, dedup=%s",
            alert.alert_id, alert.severity.value,
            [c.value for c in channels],
            current_shift.name if current_shift else "unknown",
            is_dedup,
        )

        return RoutingDecision(
            alert_id=alert.alert_id,
            severity=alert.severity,
            channels=channels,
            shift_name=current_shift.name if current_shift else "unknown",
            messages=send_results,
            deduplicated=is_dedup,
            escalated=is_escalated,
        )

    def get_escalation_policy(
        self,
        severity: Severity,
        time_of_day: dt_time,
        machine_id: str,
    ) -> Dict[str, Any]:
        """
        Determine the routing policy for an alert.

        Args:
            severity: Alert severity level.
            time_of_day: Current time of day.
            machine_id: Machine that generated the alert.

        Returns:
            Dictionary with channels, shift, and escalation info.
        """
        current_shift = self._get_current_shift(time_of_day)
        criticality = self._machine_criticality.get(
            machine_id,
            MachineCriticality(machine_id=machine_id, level="MEDIUM"),
        )

        channels: List[Channel] = []

        if severity == Severity.INFO:
            # Info alerts always go to log only
            channels = [Channel.LOG]

        elif severity == Severity.WARNING:
            # Warning alerts go to Telegram for on-shift team
            if current_shift and current_shift.is_active:
                channels = [Channel.TELEGRAM]
            else:
                # Off-shift: batch for next shift (log for now)
                channels = [Channel.LOG]
                logger.info(
                    "WARNING alert during off-shift — logging only (batch for next shift)"
                )

            # High criticality machines get immediate Slack too
            if criticality.level == "HIGH":
                channels.append(Channel.SLACK)

        elif severity == Severity.CRITICAL:
            # Critical alerts go everywhere, regardless of shift
            channels = [Channel.TELEGRAM]
            if criticality.level in ("MEDIUM", "HIGH"):
                channels.append(Channel.SLACK)
            if criticality.level == "HIGH":
                channels.append(Channel.EMAIL)
            # Always add log
            channels.append(Channel.LOG)

        return {
            "channels": channels,
            "shift": current_shift.name if current_shift else "unknown",
            "criticality": criticality.level,
            "escalation_minutes": criticality.escalation_minutes,
        }

    def send_telegram(
        self,
        message: str,
        image_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an alert to Telegram.

        Args:
            message: Formatted message text.
            image_path: Optional path to an anomaly image.

        Returns:
            Dictionary with send status and message details.
        """
        if not self._telegram_bot_token or not self._telegram_chat_id:
            logger.warning(
                "Telegram not configured (missing bot_token or chat_id). "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars."
            )
            return {"sent": False, "reason": "not_configured"}

        # Rate limiting
        if not self._check_rate_limit("telegram"):
            logger.warning("Telegram rate limit exceeded — alert dropped")
            return {"sent": False, "reason": "rate_limited"}

        try:
            # In production, use python-telegram-bot or requests
            # For now, simulate the send
            logger.info(
                "TELEGRAM SEND (simulated): chat_id=%s, message_len=%d, image=%s",
                self._telegram_chat_id,
                len(message),
                image_path or "none",
            )

            # Record for rate limiting
            self._record_send("telegram")

            return {
                "sent": True,
                "simulated": True,
                "chat_id": self._telegram_chat_id,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return {"sent": False, "error": str(e)}

    def send_slack(
        self,
        message: str,
        image_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send an alert to Slack.

        Args:
            message: Formatted message text.
            image_path: Optional path to an anomaly image.

        Returns:
            Dictionary with send status.
        """
        if not self._slack_webhook_url:
            logger.warning(
                "Slack not configured (missing webhook_url). "
                "Set SLACK_WEBHOOK_URL env var."
            )
            return {"sent": False, "reason": "not_configured"}

        if not self._check_rate_limit("slack"):
            logger.warning("Slack rate limit exceeded — alert dropped")
            return {"sent": False, "reason": "rate_limited"}

        try:
            # In production, use slack-sdk
            logger.info(
                "SLACK SEND (simulated): channel=%s, message_len=%d, image=%s",
                self._slack_channel,
                len(message),
                image_path or "none",
            )

            self._record_send("slack")

            return {
                "sent": True,
                "simulated": True,
                "channel": self._slack_channel,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Slack send failed: %s", e)
            return {"sent": False, "error": str(e)}

    def _send_email(self, message: str) -> Dict[str, Any]:
        """Send an alert via email."""
        if not self._email_smtp or not self._email_recipients:
            logger.warning(
                "Email not configured (missing SMTP server or recipients)."
            )
            return {"sent": False, "reason": "not_configured"}

        try:
            logger.info(
                "EMAIL SEND (simulated): recipients=%s, message_len=%d",
                self._email_recipients,
                len(message),
            )
            return {
                "sent": True,
                "simulated": True,
                "recipients": self._email_recipients,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return {"sent": False, "error": str(e)}

    def _format_message(
        self,
        alert: Alert,
        channel: str,
    ) -> Dict[str, str]:
        """
        Format an alert message for a specific channel.

        Args:
            alert: The Alert to format.
            channel: Target channel ("telegram", "slack", "email").

        Returns:
            Dictionary with formatted text and optional subject.
        """
        template = self.TEMPLATES.get(alert.severity, self.TEMPLATES[Severity.INFO])
        machine_name = alert.machine_id.replace("_", " ").title()

        text = template.format(
            machine_name=machine_name,
            anomaly_type=alert.anomaly_type.replace("_", " ").title(),
            description=alert.description,
            value=f"{alert.value:.2f}",
            baseline_mean=f"{alert.baseline_mean:.2f}",
            baseline_std=f"{alert.baseline_std:.2f}",
            anomaly_score=f"{alert.anomaly_score:.2f}",
            timestamp=datetime.fromtimestamp(alert.timestamp).isoformat(),
            pattern=alert.learning_note or "None",
            image_note="Attached" if alert.image_path else "Not available",
            camera_id=alert.camera_id or "N/A",
            learning_note=alert.learning_note or "No known pattern",
        )

        # Slack uses blocks for better formatting
        if channel == "slack":
            text = text.replace("⚠️", ":warning:").replace(
                "🚨", ":rotating_light:"
            ).replace("ℹ️", ":information_source:")

        result: Dict[str, str] = {"text": text}

        # Email gets a subject line
        if channel == "email":
            result["subject"] = (
                f"[{alert.severity.value}] {machine_name} — "
                f"{alert.anomaly_type}"
            )

        return result

    def _get_current_shift(self, current_time: dt_time) -> Optional[Shift]:
        """Get the active shift for the current time."""
        now = datetime.now()
        for shift in self._shifts.values():
            # Handle overnight shifts (e.g., 22:00–06:00)
            if shift.start <= shift.end:
                if shift.start <= current_time <= shift.end:
                    shift.is_active = True
                    return shift
            else:
                # Overnight: current >= start OR current <= end
                if current_time >= shift.start or current_time <= shift.end:
                    shift.is_active = True
                    return shift
        return None

    def _is_duplicate(self, dedup_key: str) -> bool:
        """Check if an alert is a duplicate within the dedup window."""
        last_sent = self._dedup_cache.get(dedup_key, 0)
        window_seconds = self._dedup_window * 60
        return (time.time() - last_sent) < window_seconds

    def _check_rate_limit(self, channel: str) -> bool:
        """Check if we're within the rate limit for a channel."""
        now = time.time()
        channel_key = channel

        if channel_key not in self._rate_limits:
            self._rate_limits[channel_key] = []

        # Remove old entries (older than 60 seconds)
        self._rate_limits[channel_key] = [
            t for t in self._rate_limits[channel_key]
            if (now - t) < 60
        ]

        return len(self._rate_limits[channel_key]) < self._rate_limit_per_minute

    def _record_send(self, channel: str) -> None:
        """Record a send for rate limiting."""
        channel_key = channel
        if channel_key not in self._rate_limits:
            self._rate_limits[channel_key] = []
        self._rate_limits[channel_key].append(time.time())

    def _log_alert(self, alert: Alert) -> None:
        """Log an alert to the system log."""
        log_method = (
            logger.warning if alert.severity == Severity.WARNING
            else logger.critical if alert.severity == Severity.CRITICAL
            else logger.info
        )
        log_method(
            "ALERT [%s] %s — %s: %s (score=%.2f)",
            alert.severity.value,
            alert.machine_id,
            alert.anomaly_type,
            alert.description,
            alert.anomaly_score,
        )

    def check_escalations(self) -> List[Alert]:
        """
        Check for unacknowledged alerts that need escalation.

        Returns list of alerts that should be escalated to CRITICAL.
        """
        now = time.time()
        escalated: List[Alert] = []
        for alert in self._alert_history:
            if (
                not alert.acknowledged
                and alert.escalation_set
                and alert.escalation_set <= now
            ):
                alert.severity = Severity.CRITICAL
                escalated.append(alert)
                logger.warning(
                    "Alert escalated: %s (was WARNING, now CRITICAL)",
                    alert.alert_id,
                )
        return escalated

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged."""
        for alert in self._alert_history:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                logger.info("Alert acknowledged: %s", alert_id)
                return True
        logger.warning("Alert not found for acknowledgment: %s", alert_id)
        return False
