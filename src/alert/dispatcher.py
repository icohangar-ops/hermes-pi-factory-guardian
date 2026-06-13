"""
Alert dispatcher for Factory Guardian.

Sends notifications through Telegram, Slack, email, and GPIO buzzer
based on severity and shift context.
"""

import logging
import json
import time
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable
from datetime import datetime, timedelta
from enum import Enum

from cubiczan_resilience import resilient, atomic_write

logger = logging.getLogger(__name__)

# Local dead-letter file for CRITICAL alerts that could not be delivered.
DEAD_LETTER_PATH = os.environ.get(
    "ALERT_DEAD_LETTER_PATH",
    os.path.join("data", "alert_dead_letter.jsonl"),
)


def _persist_dead_letter(channel: str, event: "AlertEvent") -> None:
    """Append an un-deliverable CRITICAL alert to the local dead-letter file.

    Existing entries are preserved; the file is rewritten atomically so a
    crash mid-write can never corrupt prior dead letters.
    """
    record = {
        "channel": channel,
        "alert_id": event.alert_id,
        "severity": event.severity.value,
        "machine_id": event.machine_id,
        "description": event.description,
        "source": event.source,
        "timestamp": event.timestamp.isoformat(),
        "failed_at": datetime.utcnow().isoformat(),
    }
    try:
        existing = ""
        if os.path.exists(DEAD_LETTER_PATH):
            with open(DEAD_LETTER_PATH, "r") as f:
                existing = f.read()
        parent = os.path.dirname(DEAD_LETTER_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        atomic_write(DEAD_LETTER_PATH, existing + json.dumps(record) + "\n")
        logger.error(
            "CRITICAL alert %s persisted to dead-letter (%s undeliverable)",
            event.alert_id, channel,
        )
    except Exception as e:
        logger.error("Failed to persist dead-letter for %s: %s",
                     event.alert_id, e)


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertChannel(Enum):
    LOG = "log"
    TELEGRAM = "telegram"
    SLACK = "slack"
    EMAIL = "email"
    BUZZER = "buzzer"


@dataclass
class AlertEvent:
    alert_id: str
    severity: AlertSeverity
    machine_id: str
    description: str
    confidence: float
    source: str  # "anomaly_detection", "camera_monitor", etc.
    camera_id: Optional[str] = None
    sensor_data: Dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    dispatched_channels: List[AlertChannel] = field(default_factory=list)
    operator_response: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None


class TelegramNotifier:
    """Sends alerts via Telegram Bot API."""

    def __init__(self, bot_token: str = None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send(self, chat_id: str, event: AlertEvent, config: dict = None) -> bool:
        """Send alert to Telegram chat."""
        if not self.bot_token:
            logger.warning("Telegram bot token not configured")
            return False

        config = config or {}
        dashboard_url = config.get("dashboard_url", "http://pi.local:8080")

        severity_emoji = {
            AlertSeverity.LOW: "ℹ️",
            AlertSeverity.MEDIUM: "⚠️",
            AlertSeverity.HIGH: "🔴",
            AlertSeverity.CRITICAL: "🚨",
        }

        emoji = severity_emoji.get(event.severity, "📢")
        message = (
            f"{emoji} [{event.severity.value.upper()}] {event.machine_id} — "
            f"{event.description}\n"
            f"📊 Confidence: {event.confidence:.0%} | Source: {event.source}\n"
        )

        if event.sensor_data:
            for key, value in event.sensor_data.items():
                message += f"  {key}: {value}\n"

        if event.camera_id:
            message += f"📷 Camera: {event.camera_id}\n"
            message += f"🎥 Feed: {dashboard_url}/cam/{event.camera_id}\n"

        message += (
            f"\n🔗 Dashboard: {dashboard_url}\n\n"
            f"Reply CONFIRM | DISMISS | ESCALATE | STATUS"
        )

        try:
            self._post(chat_id, message)
            logger.info("Telegram alert sent to %s: %s", chat_id,
                        event.description[:50])
            return True
        except Exception as e:
            logger.error("Telegram send failed after retries: %s", e)
            if event.severity == AlertSeverity.CRITICAL:
                _persist_dead_letter("telegram", event)
            return False

    @resilient(timeout=10, max_attempts=3, base_delay=1.0)
    def _post(self, chat_id: str, message: str) -> None:
        """POST a message to the Telegram Bot API.

        Wrapped with @resilient so transient network failures are retried
        with exponential backoff (1s/2s/4s) before propagating.
        """
        import urllib.request
        url = f"{self.base_url}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)


class SlackNotifier:
    """Sends alerts via Slack webhook."""

    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url or os.environ.get(
            "SLACK_WEBHOOK_URL", ""
        )

    def send(self, event: AlertEvent, config: dict = None) -> bool:
        """Send alert to Slack channel."""
        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        config = config or {}
        dashboard_url = config.get("dashboard_url", "http://pi.local:8080")

        severity_color = {
            AlertSeverity.LOW: "#36a64f",
            AlertSeverity.MEDIUM: "#f2c744",
            AlertSeverity.HIGH: "#e74c3c",
            AlertSeverity.CRITICAL: "#ff0000",
        }

        payload = {
            "attachments": [
                {
                    "color": severity_color.get(event.severity, "#cccccc"),
                    "title": f"[{event.severity.value.upper()}] {event.machine_id}",
                    "text": event.description,
                    "fields": [
                        {"title": "Confidence", "value": f"{event.confidence:.0%}",
                         "short": True},
                        {"title": "Source", "value": event.source, "short": True},
                    ],
                    "actions": [
                        {
                            "type": "button",
                            "text": "Dashboard",
                            "url": dashboard_url,
                        },
                        {
                            "type": "button",
                            "text": "Camera Feed",
                            "url": f"{dashboard_url}/cam/{event.camera_id or 'cam_01'}",
                        },
                    ] if event.camera_id else [],
                    "footer": "Factory Guardian",
                    "ts": int(event.timestamp.timestamp()),
                }
            ]
        }

        try:
            self._post(payload)
            logger.info("Slack alert sent: %s", event.description[:50])
            return True
        except Exception as e:
            logger.error("Slack send failed after retries: %s", e)
            if event.severity == AlertSeverity.CRITICAL:
                _persist_dead_letter("slack", event)
            return False

    @resilient(timeout=10, max_attempts=3, base_delay=1.0)
    def _post(self, payload: dict) -> None:
        """POST a payload to the Slack webhook.

        Wrapped with @resilient so transient network failures are retried
        with exponential backoff (1s/2s/4s) before propagating.
        """
        import urllib.request
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.webhook_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)


class EmailNotifier:
    """Sends alerts and reports via email."""

    def __init__(self, smtp_config: dict = None):
        self.smtp_host = (smtp_config or {}).get(
            "smtp_host", os.environ.get("SMTP_HOST", "smtp.gmail.com")
        )
        self.smtp_port = int((smtp_config or {}).get(
            "smtp_port", os.environ.get("SMTP_PORT", "587")
        ))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_pass = os.environ.get("SMTP_PASS", "")

    def send(self, to_email: str, subject: str, body: str,
             html: bool = False, attachments: list = None) -> bool:
        """Send email alert."""
        if not self.smtp_user or not self.smtp_pass:
            logger.warning("SMTP credentials not configured")
            return False

        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.base import MIMEBase
            from email import encoders

            msg = MIMEMultipart()
            msg["From"] = self.smtp_user
            msg["To"] = to_email
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "html" if html else "plain"))

            # Attach files
            if attachments:
                for filepath in attachments:
                    if os.path.exists(filepath):
                        with open(filepath, "rb") as f:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(f.read())
                            encoders.encode_base64(part)
                            part.add_header(
                                "Content-Disposition",
                                f"attachment; filename={os.path.basename(filepath)}",
                            )
                            msg.attach(part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)

            logger.info("Email sent to %s: %s", to_email, subject[:50])
            return True
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return False


class BuzzerNotifier:
    """GPIO buzzer for critical on-site alerts."""

    def __init__(self, gpio_pin: int = 23):
        self.gpio_pin = gpio_pin
        self._initialized = False

    def initialize(self):
        """Initialize GPIO buzzer."""
        try:
            from gpiozero import Buzzer
            self._buzzer = Buzzer(self.gpio_pin)
            self._initialized = True
            logger.info("GPIO buzzer initialized on pin %d", self.gpio_pin)
        except ImportError:
            logger.warning("gpiozero not available — buzzer disabled")
        except Exception as e:
            logger.error("Failed to initialize buzzer: %s", e)

    def alert(self, duration_seconds: int = 10, pattern: str = "emergency"):
        """Trigger buzzer alert pattern."""
        if not self._initialized:
            logger.warning("Buzzer not initialized")
            return

        patterns = {
            "emergency": [(0.1, 0.1), (0.1, 0.1), (0.1, 0.1), (0.5, 0.5)],
            "warning": [(0.2, 0.3)],
            "info": [(0.5, 0.5)],
        }

        beeps = patterns.get(pattern, patterns["info"])
        end_time = time.time() + duration_seconds

        try:
            while time.time() < end_time:
                for on_time, off_time in beeps:
                    if time.time() >= end_time:
                        break
                    self._buzzer.on()
                    time.sleep(on_time)
                    self._buzzer.off()
                    time.sleep(off_time)
        except Exception as e:
            logger.error("Buzzer error: %s", e)


class AlertDispatcher:
    """Central alert routing and dispatch system."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.config = config
        self.telegram = TelegramNotifier()
        self.slack = SlackNotifier()
        self.email = EmailNotifier(config.get("email", {}))
        self.buzzer = BuzzerNotifier(
            config.get("escalation", {}).get("emergency_buzzer_pin", 23)
        )

        self._alert_history: List[AlertEvent] = []
        self._event_callbacks: List[Callable] = []
        self._grouped_alerts: Dict[str, List[AlertEvent]] = {}
        self._merge_window = config.get("grouping", {}).get(
            "merge_window_seconds", 300
        )

        # Severity → channel mapping
        self.severity_channels = {
            AlertSeverity.LOW: [AlertChannel.LOG],
            AlertSeverity.MEDIUM: [AlertChannel.LOG, AlertChannel.TELEGRAM],
            AlertSeverity.HIGH: [
                AlertChannel.LOG, AlertChannel.TELEGRAM,
                AlertChannel.SLACK, AlertChannel.EMAIL,
            ],
            AlertSeverity.CRITICAL: [
                AlertChannel.LOG, AlertChannel.TELEGRAM,
                AlertChannel.SLACK, AlertChannel.EMAIL, AlertChannel.BUZZER,
            ],
        }

    def initialize(self):
        """Initialize all notifiers."""
        self.buzzer.initialize()
        logger.info("Alert dispatcher initialized")

    def dispatch(self, event: AlertEvent) -> AlertEvent:
        """Route and dispatch an alert event."""
        # Check for duplicates / grouping
        grouped = self._check_grouping(event)
        if grouped:
            logger.info("Alert grouped with existing event: %s", event.alert_id)
            return event

        channels = self.severity_channels.get(event.severity, [AlertChannel.LOG])
        event.dispatched_channels = channels

        for channel in channels:
            success = False
            if channel == AlertChannel.LOG:
                self._log_alert(event)
                success = True
            elif channel == AlertChannel.TELEGRAM:
                shift_groups = self.config.get("channels", {}).get(
                    "telegram", {}).get("shift_groups", {})
                for group_id in shift_groups.values():
                    success = self.telegram.send(group_id, event, self.config)
            elif channel == AlertChannel.SLACK:
                success = self.slack.send(event, self.config)
            elif channel == AlertChannel.EMAIL:
                recipients = self._get_email_recipients(event.severity)
                for email in recipients:
                    success = self.email.send(
                        email,
                        f"[Factory Guardian] {event.severity.value.upper()} - "
                        f"{event.machine_id}",
                        self._format_email_body(event),
                        html=True,
                    )
            elif channel == AlertChannel.BUZZER:
                self.buzzer.alert(duration_seconds=10, pattern="emergency")
                success = True

            if success:
                event.dispatched_channels.append(channel)

        self._alert_history.append(event)

        # Notify callbacks
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error("Alert callback error: %s", e)

        logger.info("Alert dispatched: [%s] %s via %s",
                     event.severity.value, event.description[:50],
                     [c.value for c in event.dispatched_channels])
        return event

    def handle_response(self, alert_id: str, response: str):
        """Handle operator response to an alert."""
        for event in self._alert_history:
            if event.alert_id == alert_id:
                event.operator_response = response
                if response.upper() in ("CONFIRM", "REAL"):
                    event.resolved = True
                    event.resolved_at = datetime.utcnow()
                    logger.info("Alert %s confirmed by operator", alert_id)
                elif response.upper() in ("DISMISS", "FALSE"):
                    event.resolved = True
                    event.resolved_at = datetime.utcnow()
                    logger.info("Alert %s dismissed as false positive", alert_id)
                elif response.upper() == "ESCALATE":
                    # Bump severity
                    severity_order = list(AlertSeverity)
                    idx = severity_order.index(event.severity)
                    if idx < len(severity_order) - 1:
                        event.severity = severity_order[idx + 1]
                        self.dispatch(event)
                        logger.info("Alert %s escalated to %s",
                                    alert_id, event.severity.value)
                return

    def _check_grouping(self, event: AlertEvent) -> bool:
        """Check if this event should be merged with a recent one."""
        key = f"{event.machine_id}_{event.source}"
        cutoff = datetime.utcnow() - timedelta(seconds=self._merge_window)

        recent = [
            e for e in self._alert_history
            if f"{e.machine_id}_{e.source}" == key
            and e.timestamp > cutoff
            and not e.resolved
        ]

        if len(recent) >= 3:
            # Auto-escalate: 3 repeats → bump severity
            logger.info("Auto-escalating %s after 3 repeated alerts", key)
            severity_order = list(AlertSeverity)
            idx = severity_order.index(event.severity)
            if idx < len(severity_order) - 1:
                event.severity = severity_order[idx + 1]
            return False  # Send the escalated alert

        if recent:
            # Merge into existing
            recent[0].sensor_data.update(event.sensor_data)
            return True
        return False

    def _log_alert(self, event: AlertEvent):
        """Log alert to structured logger."""
        logger.info(
            "ALERT [%s] machine=%s desc='%s' confidence=%.2f source=%s",
            event.severity.value, event.machine_id, event.description,
            event.confidence, event.source,
        )

    def _get_email_recipients(self, severity: AlertSeverity) -> List[str]:
        """Get email recipients based on severity."""
        recipients = self.config.get("channels", {}).get("email", {}).get(
            "recipients", {}
        )
        if severity in (AlertSeverity.HIGH, AlertSeverity.CRITICAL):
            return recipients.get("engineers", [])
        return recipients.get("shift_leads", [])

    def _format_email_body(self, event: AlertEvent) -> str:
        """Format alert as HTML email body."""
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h2 style="color: #e74c3c;">
                [Factory Guardian] {event.severity.value.upper()} Alert
            </h2>
            <table style="border-collapse: collapse; width: 100%;">
                <tr><td><b>Machine:</b></td><td>{event.machine_id}</td></tr>
                <tr><td><b>Description:</b></td><td>{event.description}</td></tr>
                <tr><td><b>Confidence:</b></td><td>{event.confidence:.0%}</td></tr>
                <tr><td><b>Source:</b></td><td>{event.source}</td></tr>
                <tr><td><b>Time:</b></td><td>{event.timestamp.isoformat()}</td></tr>
            </table>
        </body>
        </html>
        """


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Alert dispatcher module loaded. Import and use AlertDispatcher class.")
