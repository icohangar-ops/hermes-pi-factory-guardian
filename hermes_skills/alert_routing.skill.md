---
name: alert_routing
version: 1.0.0
description: >
  Context-aware alert dispatch that considers severity, time of day, shift
  schedules, and responder availability. Routes alerts through Telegram, Slack,
  email, and local buzzer with intelligent grouping to reduce fatigue.
triggers:
  - anomaly_detected
  - sensor_threshold_exceeded
  - camera_intrusion_detected
  - system_health_warning
inputs:
  - alert_event: dict with severity, machine_id, description, confidence, source
  - shift_context: current shift, on-call personnel, active incidents
outputs:
  - dispatched_alerts: list of sent notifications per channel
  - escalation_log: tracking of alert progression
---

# Alert Routing Skill

## Purpose

This skill manages the entire alert lifecycle — from receiving an anomaly event to dispatching notifications through the right channels at the right time. It prevents alert fatigue through intelligent grouping, deduplication, and context-aware escalation.

## How It Works

### 1. Severity Assessment

Alerts are classified and routed based on severity:

| Severity | Description | Channels | Response Time |
|----------|-------------|----------|---------------|
| LOW | Minor deviation, likely false positive | Log only | Next shift review |
| MEDIUM | Confirmed anomaly, monitoring required | Telegram (shift group) | 30 minutes |
| HIGH | Multiple correlated anomalies or single critical | Telegram + Slack + Email | 10 minutes |
| CRITICAL | Imminent failure risk or security breach | All channels + Buzzer | Immediate |

### 2. Shift-Aware Routing

The skill maintains shift schedules and routes to appropriate personnel:

```python
# Shift context example
shift_context = {
    "current_shift": "night_shift",
    "shift_lead": "@maria_chen",
    "on_call_engineer": "+1234567890",
    "shift_group_telegram": "-1002003004005",
    "escalation_group": "-1006007008009",
    "factory_manager": "manager@factory.com"
}

# Night shift: only page on-call for HIGH+, reduce noise for MEDIUM
# Day shift: notify shift lead for MEDIUM+, page engineering for HIGH+
```

### 3. Alert Grouping and Deduplication

To prevent alert storms (e.g., cascading sensor failures), the skill groups related alerts:

- **Same machine within 5 minutes**: Merge into single alert with updated severity
- **Correlated machines**: Group as "Area Event" (e.g., multiple CNC stations vibrating)
- **Repeated alerts**: After 3 identical alerts in 10 minutes, escalate severity by one level
- **Resolved alerts**: Auto-dismiss grouped alerts when the root cause is resolved

### 4. Notification Templates

Each channel receives formatted, actionable notifications:

**Telegram**:
```
⚠️ [HIGH] CNC Mill 01 — Bearing Wear Detected
📊 Confidence: 87% | Lead time: ~2h
📈 Vibration RMS: 2.8g (baseline: 1.2g)
🌡️ Temperature: 61°C (rising +8°C/hr)

Actions:
  👤 @shift_lead please inspect Station 1
  📸 Camera feed: http://pi.local:8080/cam_01

[Reply CONFIRM to mark as real | DISMISS to mark as false positive]
```

**Slack**:
```
:rotating_light: *HIGH SEVERITY* — CNC Mill 01
Vibration anomaly detected with 87% confidence.
Estimated 2 hours to potential failure.
<http://pi.local:8080/dashboard|Live Dashboard> | <http://pi.local:8080/cam_01|Camera>
```

**Email** (shift report attachment):
```
Subject: [Factory Guardian] Daily Shift Report - Day Shift - 2026-05-28

Summary: 3 anomalies detected, 1 confirmed, 2 false positives
Top Concern: CNC Mill 01 vibration trending upward
Full report attached as PDF.
```

### 5. Local Buzzer (Hardware)

For CRITICAL alerts, the skill triggers the Pi's GPIO-connected buzzer:

```python
# Emergency buzzer pattern
def trigger_emergency_buzzer():
    # Pattern: 3 short beeps, 1 long, repeat for 30 seconds
    GPIO.output(BUZZER_PIN, HIGH)
    time.sleep(0.1)
    GPIO.output(BUZZER_PIN, LOW)
    # ... (pattern repeats)
```

### 6. Operator Feedback Loop

Operators can respond to alerts via Telegram commands:
- `CONFIRM` / `REAL` — Marks alert as genuine, feeds back to anomaly_detection skill
- `DISMISS` / `FALSE` — Marks as false positive, adjusts detection thresholds
- `ESCALATE` — Manually bumps severity and re-routes to next tier
- `STATUS` — Requests current status of all machines

## Configuration

```yaml
alert_routing:
  channels:
    telegram:
      bot_token: "${TELEGRAM_BOT_TOKEN}"
      shift_groups:
        day_shift: "-1002003004005"
        swing_shift: "-1002003004006"
        night_shift: "-1002003004007"
    slack:
      webhook_url: "${SLACK_WEBHOOK_URL}"
    email:
      smtp_host: "smtp.gmail.com"
      smtp_port: 587

  grouping:
    merge_window_seconds: 300
    max_repeats_before_escalation: 3
    auto_dismiss_after_resolved: true

  escalation:
    level1_wait_minutes: 10
    level2_wait_minutes: 30
    emergency_buzzer_pin: 23
```
