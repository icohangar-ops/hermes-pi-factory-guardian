# Hermes Skill: alert_router

## Metadata
- **Name:** alert_router
- **Version:** 1.0.0
- **Category:** notification
- **Depends on:** anomaly_detection

## Description

Routes alerts to the appropriate notification channels based on severity level, time of day, machine criticality, and current shift schedule. Implements intelligent escalation to ensure the right people are notified without causing alert fatigue.

## Triggers

- **On anomaly detection:** Any anomaly flagged by the anomaly_detection skill
- **On shift change:** Re-routes active alerts to incoming shift
- **Manual trigger:** Operator requests alert re-routing

## Escalation Rules

| Severity | Condition | Channels | Example |
|----------|-----------|----------|---------|
| `INFO` | Low deviation, auto-suppressed patterns | System log only | Learned normal vibration spike |
| `WARNING` | Moderate deviation, approaching threshold | Telegram (on-shift team) | Temperature 5°C above normal |
| `CRITICAL` | Severe deviation, safety concern | Telegram + Slack + Email | Machine stopped, person in danger zone |

## Shift Schedule Awareness

The alert router respects shift schedules to prevent alert fatigue:

```
06:00–14:00  → Morning shift → Alerts to #morning-shift channel
14:00–22:00  → Afternoon shift → Alerts to #afternoon-shift channel
22:00–06:00  → Night shift → Alerts to #night-shift channel
```

**Smart behaviors:**
- CRITICAL alerts ALWAYS notify all channels regardless of shift
- WARNING alerts during off-shift hours are batched (sent at next shift start)
- Non-critical repeated alerts are deduplicated (max 1 per hour per machine)
- Escalation timer: If WARNING not acknowledged in 30 min, escalate to CRITICAL

## Machine Criticality

Machine criticality affects alert routing:

| Criticality | WARNING Behavior | CRITICAL Behavior |
|-------------|-----------------|-------------------|
| `LOW` | Log + batched Telegram | Telegram + Slack |
| `MEDIUM` | Immediate Telegram | Telegram + Slack + Email |
| `HIGH` | Immediate Telegram + Slack | All channels + phone call |

## Input Schema

```json
{
  "alert_id": "alert_20250615_143217_001",
  "severity": "WARNING",
  "machine_id": "cnc_machine_1",
  "anomaly_type": "temperature_high",
  "anomaly_score": 0.65,
  "value": 71.3,
  "baseline_mean": 63.5,
  "description": "Temperature 7.8°C above normal baseline",
  "timestamp": "2025-06-15T14:32:17Z",
  "image_path": "/data/anomalies/cam01/temp_high_20250615_143217.jpg"
}
```

## Output Schema

```json
{
  "alert_id": "alert_20250615_143217_001",
  "routed_to": ["telegram"],
  "messages": {
    "telegram": {
      "sent": true,
      "chat_id": "-1001234567890",
      "message_id": 42,
      "timestamp": "2025-06-15T14:32:18Z"
    }
  },
  "deduplicated": false,
  "escalation_timer_set": "2025-06-15T15:02:18Z"
}
```

## Message Templates

### INFO Template
```
ℹ️ {machine_name} — {anomaly_type}
{description}
Pattern: {pattern_match or "None"}
Logged at: {timestamp}
```

### WARNING Template
```
⚠️ WARNING — {machine_name} — {anomaly_type}
{description}
Value: {value} | Normal: {baseline_mean} ± {baseline_std}
Score: {anomaly_score}/1.0
Time: {timestamp}
📸 Anomaly frame attached
```

### CRITICAL Template
```
🚨 CRITICAL — {machine_name} — {anomaly_type}
{description}
Value: {value} | Normal: {baseline_mean} ± {baseline_std}
Score: {anomaly_score}/1.0
Time: {timestamp}

📸 Anomaly frame attached
📍 Camera: {camera_id}
⚡ Current readings: {sensor_snapshot}
🧠 Learning context: {pattern_note or "No known pattern"}

IMMEDIATE ACTION REQUIRED
```

## Configuration

Alert routing is configured in `config/factory_config.yaml`:

```yaml
alert_routing:
  channels:
    telegram:
      bot_token: "${TELEGRAM_BOT_TOKEN}"
      chat_id: "${TELEGRAM_CHAT_ID}"
    slack:
      webhook_url: "${SLACK_WEBHOOK_URL}"
      channel: "#factory-alerts"
    email:
      smtp_server: "smtp.company.com"
      recipients: ["factory-ops@company.com"]

  shifts:
    morning:
      start: "06:00"
      end: "14:00"
      telegram_chat: "-1001111111111"
    afternoon:
      start: "14:00"
      end: "22:00"
      telegram_chat: "-1002222222222"
    night:
      start: "22:00"
      end: "06:00"
      telegram_chat: "-1003333333333"

  deduplication:
    window_minutes: 60
    max_per_window: 1

  escalation:
    warning_to_critical_minutes: 30
```

## Error Handling

- If Telegram send fails, retry 3 times with exponential backoff
- If Slack send fails, log and continue (non-blocking)
- If all channels fail, write alert to local file as fallback
- Rate limiting: Max 10 alerts per minute per channel to prevent spam
