"""
Hermes-Pi Factory Guardian — Alert Router Unit Tests

Tests routing decisions for different severities and times,
message formatting, deduplication, escalation, and rate limiting.
"""

import os
import tempfile
import time
import unittest
from datetime import time as dt_time
from pathlib import Path

import sys

_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root / "skills" / "alert-router"))

from alert_router import (
    Alert,
    AlertRouter,
    Channel,
    MachineCriticality,
    RoutingDecision,
    Severity,
    Shift,
)


class TestRoutingPolicy(unittest.TestCase):
    """Tests for the escalation policy logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.router = AlertRouter()
        self.router._telegram_bot_token = "test_token"
        self.router._telegram_chat_id = "-1001234567890"

    def test_info_routes_to_log_only(self):
        """Test that INFO alerts go to system log only."""
        policy = self.router.get_escalation_policy(
            severity=Severity.INFO,
            time_of_day=dt_time(10, 0),
            machine_id="test_machine",
        )
        self.assertEqual(policy["channels"], [Channel.LOG])

    def test_warning_routes_to_telegram_during_shift(self):
        """Test that WARNING alerts route to Telegram during active shift."""
        # Set up a shift for 06:00-14:00
        self.router._shifts["morning"] = Shift(
            name="morning",
            start=dt_time(6, 0),
            end=dt_time(14, 0),
            telegram_chat_id="-100111",
            is_active=True,
        )

        policy = self.router.get_escalation_policy(
            severity=Severity.WARNING,
            time_of_day=dt_time(10, 0),
            machine_id="test_machine",
        )
        self.assertIn(Channel.TELEGRAM, policy["channels"])

    def test_critical_routes_to_all_channels(self):
        """Test that CRITICAL alerts route to all channels."""
        self.router._shifts["morning"] = Shift(
            name="morning",
            start=dt_time(6, 0),
            end=dt_time(14, 0),
            telegram_chat_id="-100111",
            is_active=True,
        )
        self.router._machine_criticality["critical_machine"] = MachineCriticality(
            machine_id="critical_machine",
            level="HIGH",
        )

        policy = self.router.get_escalation_policy(
            severity=Severity.CRITICAL,
            time_of_day=dt_time(10, 0),
            machine_id="critical_machine",
        )
        self.assertIn(Channel.TELEGRAM, policy["channels"])
        self.assertIn(Channel.SLACK, policy["channels"])
        self.assertIn(Channel.EMAIL, policy["channels"])

    def test_warning_off_shift_logs_only(self):
        """Test that WARNING alerts off-shift are logged only."""
        # Set up morning shift, but test at night
        self.router._shifts["morning"] = Shift(
            name="morning",
            start=dt_time(6, 0),
            end=dt_time(14, 0),
            telegram_chat_id="-100111",
        )

        policy = self.router.get_escalation_policy(
            severity=Severity.WARNING,
            time_of_day=dt_time(3, 0),  # 3 AM
            machine_id="test_machine",
        )
        self.assertEqual(policy["channels"], [Channel.LOG])

    def test_critical_bypasses_shift_schedule(self):
        """Test that CRITICAL always routes regardless of shift."""
        self.router._machine_criticality["m1"] = MachineCriticality(
            machine_id="m1",
            level="MEDIUM",
        )

        policy = self.router.get_escalation_policy(
            severity=Severity.CRITICAL,
            time_of_day=dt_time(3, 0),  # 3 AM, no shift
            machine_id="m1",
        )
        self.assertIn(Channel.TELEGRAM, policy["channels"])

    def test_high_criticality_warning_gets_slack(self):
        """Test that HIGH criticality machines get Slack on WARNING."""
        self.router._shifts["morning"] = Shift(
            name="morning",
            start=dt_time(6, 0),
            end=dt_time(14, 0),
            is_active=True,
        )
        self.router._machine_criticality["important_machine"] = MachineCriticality(
            machine_id="important_machine",
            level="HIGH",
        )

        policy = self.router.get_escalation_policy(
            severity=Severity.WARNING,
            time_of_day=dt_time(10, 0),
            machine_id="important_machine",
        )
        self.assertIn(Channel.TELEGRAM, policy["channels"])
        self.assertIn(Channel.SLACK, policy["channels"])


class TestAlertRouting(unittest.TestCase):
    """Tests for the route_alert method."""

    def setUp(self):
        self.router = AlertRouter()
        self.router._telegram_bot_token = "test_token"
        self.router._telegram_chat_id = "-100123"

    def test_route_info_alert(self):
        """Test routing an INFO alert."""
        alert = Alert(
            alert_id="info_001",
            severity=Severity.INFO,
            machine_id="machine_1",
            anomaly_type="pattern_match",
            anomaly_score=0.1,
            value=65.0,
            baseline_mean=63.0,
            baseline_std=2.0,
            description="Learned normal pattern detected",
        )
        result = self.router.route_alert(alert)

        self.assertIsInstance(result, RoutingDecision)
        self.assertIn(Channel.LOG, result.channels)

    def test_route_critical_alert(self):
        """Test routing a CRITICAL alert."""
        self.router._machine_criticality["m1"] = MachineCriticality(
            machine_id="m1", level="HIGH",
        )

        alert = Alert(
            alert_id="crit_001",
            severity=Severity.CRITICAL,
            machine_id="m1",
            anomaly_type="machine_stoppage",
            anomaly_score=0.95,
            value=0.3,
            baseline_mean=12.0,
            baseline_std=2.0,
            description="Machine stopped — no current draw",
        )
        result = self.router.route_alert(alert)

        self.assertIsInstance(result, RoutingDecision)
        self.assertTrue(len(result.channels) >= 2)

    def test_deduplication(self):
        """Test that duplicate alerts are deduplicated."""
        self.router._dedup_window = 60  # 60 minute window

        for i in range(3):
            alert = Alert(
                alert_id=f"dup_{i}",
                severity=Severity.WARNING,
                machine_id="m1",
                anomaly_type="temp_high",
                anomaly_score=0.6,
                value=72.0,
                baseline_mean=65.0,
                baseline_std=2.0,
                description="Temperature slightly elevated",
            )
            result = self.router.route_alert(alert)

            if i == 0:
                self.assertFalse(result.deduplicated)
            else:
                self.assertTrue(result.deduplicated)


class TestMessageFormatting(unittest.TestCase):
    """Tests for alert message formatting."""

    def setUp(self):
        self.router = AlertRouter()

    def test_format_info_message(self):
        """Test INFO message formatting."""
        alert = Alert(
            alert_id="info_001",
            severity=Severity.INFO,
            machine_id="conveyor_1",
            anomaly_type="vibration_spike",
            anomaly_score=0.2,
            value=4.2,
            baseline_mean=3.0,
            baseline_std=0.5,
            description="Startup vibration spike — learned normal pattern",
            learning_note="Matches conveyor_warmup pattern (94% confidence)",
        )

        msg = self.router._format_message(alert, "telegram")
        self.assertIn("Conveyor 1", msg["text"])  # Title-cased in output
        self.assertIn("Vibration Spike", msg["text"])  # anomaly_type is title-cased
        self.assertIn("conveyor_warmup", msg["text"])

    def test_format_critical_message(self):
        """Test CRITICAL message formatting."""
        alert = Alert(
            alert_id="crit_001",
            severity=Severity.CRITICAL,
            machine_id="cnc_machine_3",
            anomaly_type="machine_stoppage",
            anomaly_score=0.95,
            value=0.3,
            baseline_mean=12.4,
            baseline_std=2.0,
            description="Machine stopped — no current draw",
            camera_id="cam_01",
            image_path="/data/anomalies/stoppage.jpg",
        )

        msg = self.router._format_message(alert, "telegram")
        text = msg["text"]
        self.assertIn("CRITICAL", text)
        self.assertIn("Cnc Machine 3", text)  # Title-cased in output
        self.assertIn("IMMEDIATE ACTION REQUIRED", text)
        self.assertIn("cam_01", text)

    def test_format_slack_message(self):
        """Test Slack message uses emoji codes instead of unicode."""
        alert = Alert(
            alert_id="warn_001",
            severity=Severity.WARNING,
            machine_id="m1",
            anomaly_type="temp_high",
            anomaly_score=0.6,
            value=72.0,
            baseline_mean=65.0,
            baseline_std=2.0,
            description="Temperature elevated",
        )

        msg = self.router._format_message(alert, "slack")
        self.assertIn(":warning:", msg["text"])

    def test_format_email_has_subject(self):
        """Test that email format includes subject line."""
        alert = Alert(
            alert_id="email_001",
            severity=Severity.CRITICAL,
            machine_id="cnc_1",
            anomaly_type="fire_risk",
            anomaly_score=0.99,
            value=95.0,
            baseline_mean=65.0,
            baseline_std=2.0,
            description="Extreme temperature — possible fire risk",
        )

        msg = self.router._format_message(alert, "email")
        self.assertIn("subject", msg)
        self.assertIn("CRITICAL", msg["subject"])
        self.assertIn("Cnc 1", msg["subject"])


class TestDeduplication(unittest.TestCase):
    """Tests for deduplication logic."""

    def test_not_duplicate_first_alert(self):
        """First alert should not be deduplicated."""
        router = AlertRouter()
        router._dedup_window = 60

        self.assertFalse(router._is_duplicate("m1:temp_high"))

    def test_duplicate_within_window(self):
        """Alert within window should be deduplicated."""
        router = AlertRouter()
        router._dedup_window = 60

        router._dedup_cache["m1:temp_high"] = time.time()
        self.assertTrue(router._is_duplicate("m1:temp_high"))

    def test_not_duplicate_after_window(self):
        """Alert after window expires should not be deduplicated."""
        router = AlertRouter()
        router._dedup_window = 60

        router._dedup_cache["m1:temp_high"] = time.time() - (120 * 60)  # 2 hours ago
        self.assertFalse(router._is_duplicate("m1:temp_high"))

    def test_different_alerts_not_deduplicated(self):
        """Different alert types should not deduplicate each other."""
        router = AlertRouter()
        router._dedup_window = 60

        router._dedup_cache["m1:temp_high"] = time.time()
        self.assertFalse(router._is_duplicate("m1:vibration_high"))


class TestRateLimiting(unittest.TestCase):
    """Tests for rate limiting."""

    def test_under_limit(self):
        """Should allow sends under the limit."""
        router = AlertRouter()
        router._rate_limit_per_minute = 5

        self.assertTrue(router._check_rate_limit("telegram"))

    def test_over_limit(self):
        """Should block sends over the limit."""
        router = AlertRouter()
        router._rate_limit_per_minute = 2

        router._record_send("telegram")
        router._record_send("telegram")

        self.assertFalse(router._check_rate_limit("telegram"))

    def test_limit_resets_after_window(self):
        """Rate limit should reset after old sends expire."""
        router = AlertRouter()
        router._rate_limit_per_minute = 2

        # Record old sends
        router._rate_limits["telegram"] = [time.time() - 120, time.time() - 120]

        self.assertTrue(router._check_rate_limit("telegram"))


class TestTelegramSend(unittest.TestCase):
    """Tests for Telegram send functionality."""

    def test_send_not_configured(self):
        """Should gracefully handle missing configuration."""
        router = AlertRouter()
        router._telegram_bot_token = ""
        router._telegram_chat_id = ""

        result = router.send_telegram("Test message")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "not_configured")

    def test_send_with_config(self):
        """Should simulate send when configured."""
        router = AlertRouter()
        router._telegram_bot_token = "test_token"
        router._telegram_chat_id = "-100123"

        result = router.send_telegram("Test alert message")
        self.assertTrue(result["sent"])
        self.assertTrue(result["simulated"])


class TestSlackSend(unittest.TestCase):
    """Tests for Slack send functionality."""

    def test_send_not_configured(self):
        """Should gracefully handle missing webhook URL."""
        router = AlertRouter()
        router._slack_webhook_url = ""

        result = router.send_slack("Test message")
        self.assertFalse(result["sent"])
        self.assertEqual(result["reason"], "not_configured")


class TestEscalation(unittest.TestCase):
    """Tests for alert escalation."""

    def test_escalation_timer_set_on_warning(self):
        """WARNING alerts should have escalation timer."""
        router = AlertRouter()
        router._config = {
            "alert_routing": {
                "escalation": {"warning_to_critical_minutes": 30},
            }
        }

        alert = Alert(
            alert_id="warn_esc_001",
            severity=Severity.WARNING,
            machine_id="m1",
            anomaly_type="temp_high",
            anomaly_score=0.6,
            value=72.0,
            baseline_mean=65.0,
            baseline_std=2.0,
            description="Temperature elevated",
        )

        result = router.route_alert(alert)
        self.assertTrue(result.escalated)

    def test_check_escalations(self):
        """Test that unacknowledged alerts are escalated."""
        router = AlertRouter()

        alert = Alert(
            alert_id="esc_001",
            severity=Severity.WARNING,
            machine_id="m1",
            anomaly_type="temp_high",
            anomaly_score=0.6,
            value=72.0,
            baseline_mean=65.0,
            baseline_std=2.0,
            description="Temperature elevated",
            escalation_set=time.time() - 1,  # Past due
        )
        router._alert_history.append(alert)

        escalated = router.check_escalations()
        self.assertEqual(len(escalated), 1)
        self.assertEqual(escalated[0].severity, Severity.CRITICAL)

    def test_acknowledge_alert(self):
        """Test alert acknowledgment."""
        router = AlertRouter()

        alert = Alert(
            alert_id="ack_001",
            severity=Severity.WARNING,
            machine_id="m1",
            anomaly_type="temp_high",
            anomaly_score=0.6,
            value=72.0,
            baseline_mean=65.0,
            baseline_std=2.0,
            description="Temperature elevated",
        )
        router._alert_history.append(alert)

        result = router.acknowledge_alert("ack_001")
        self.assertTrue(result)
        self.assertTrue(router._alert_history[0].acknowledged)

    def test_acknowledge_unknown_alert(self):
        """Test acknowledging a non-existent alert."""
        router = AlertRouter()
        result = router.acknowledge_alert("nonexistent")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
