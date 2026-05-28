---
name: shift_report
version: 1.0.0
description: >
  Generates comprehensive shift summaries including sensor statistics, anomalies
  detected, alerts dispatched, operator responses, and trend analysis with
  actionable recommendations. Reports are delivered at shift handover.
triggers:
  - shift_end
  - report_requested
  - daily_summary_scheduled
  - weekly_digest_scheduled
inputs:
  - shift_data: aggregated sensor readings, anomaly events, alerts, responses
  - trend_data: vibration/temperature/current trends over shift duration
  - operator_feedback: confirmations, dismissals, notes from Telegram
outputs:
  - shift_report: formatted report with statistics, events, trends, recommendations
  - trend_charts: embedded charts showing sensor trends
  - summary_slack: condensed summary posted to Slack channel
---

# Shift Report Skill

## Purpose

This skill generates detailed, actionable shift reports at the end of each shift and daily/weekly digests. Reports include statistical summaries of all sensor data, a timeline of anomalies and alerts, trend analysis with projections, and specific recommendations for the incoming shift.

## How It Works

### 1. Data Aggregation

At shift end, the skill pulls data from multiple sources:

```python
report_data = {
    "shift_info": {
        "date": "2026-05-28",
        "shift": "day_shift",
        "duration_hours": 8,
        "operator": "Maria Chen",
        "machines_active": 12
    },
    "sensor_summary": {
        "total_readings": 288000,
        "anomalies_detected": 7,
        "anomalies_confirmed": 2,
        "anomalies_false_positive": 5,
        "max_severity": "HIGH"
    },
    "alerts": {
        "telegram_sent": 5,
        "slack_sent": 2,
        "email_sent": 1,
        "buzzer_triggered": 0,
        "avg_response_time_minutes": 8.3
    },
    "top_concerns": [
        {
            "machine": "CNC Mill 01",
            "issue": "Vibration RMS trending upward — 15% increase over shift",
            "status": "Monitoring",
            "recommended_action": "Inspect bearings before next shift"
        }
    ]
}
```

### 2. Report Sections

Each shift report includes:

**A. Executive Summary** (2-3 sentences)
- Overall factory health status
- Number and severity of incidents
- Top concern for incoming shift

**B. Machine Health Grid**

```
Machine         | Status | Vibration | Temp  | Current | Trend
----------------|--------|-----------|-------|---------|------
CNC Mill 01     | WATCH  | 2.1g ⬆    | 58°C  | 8.2A    | ⚠ Rising
CNC Mill 02     | OK     | 1.0g      | 52°C  | 7.9A    | Stable
Conveyor A      | OK     | 0.3g      | 28°C  | 3.1A    | Stable
Press Brake 01  | ALERT  | 3.8g ⬆    | 71°C  | 11.5A   | ⚠⚠ Rising
Welding Bay 01  | OK     | 0.5g      | 45°C  | 15.2A   | Normal
```

**C. Anomaly Timeline**

```
06:15  [LOW]    Conveyor A — minor vibration spike (dismissed by operator)
08:42  [MED]    CNC Mill 01 — temperature 8°C above baseline
09:15  [MED]    Camera 02 — motion in restricted zone (false positive — cleaning crew)
11:30  [HIGH]   Press Brake 01 — vibration RMS 3.8g, emergency stop recommended
11:35  [HIGH]   Press Brake 01 — operator confirmed, maintenance dispatched
13:00  [LOW]    CNC Mill 02 — brief current spike (normal duty cycle)
```

**D. Trend Analysis**

Includes mini-charts showing:
- Vibration RMS per machine over the shift
- Temperature trends with baseline overlay
- Alert frequency by severity (bar chart)
- False positive rate trend

**E. Recommendations for Incoming Shift**

```
1. [PRIORITY] Press Brake 01 — vibration critical, inspect hydraulic system
   before resuming operations. Maintenance ticket #2026-0528-04 open.

2. [MONITOR] CNC Mill 01 — vibration trending up for 3 consecutive shifts.
   Schedule bearing inspection within 48 hours.

3. [NOTE] Camera 02 restricted zone — adjust sensitivity for shift change
   times to reduce false positives from cleaning crew movement.
```

### 3. Delivery Channels

| Report Type | Frequency | Channels |
|-------------|-----------|----------|
| Shift Handover | Every shift change | Telegram + Slack |
| Daily Summary | Once per day (22:00) | Email (PDF attachment) |
| Weekly Digest | Monday morning | Email (PDF) + Slack post |
| Incident Report | On-demand after incident | Email (PDF) + Telegram |

### 4. Hermes Learning Integration

The skill feeds back into the anomaly detection system:

```
Experience: "Day shift had 5 false positives from Camera 02 during cleaning hours (05:30-06:30)"
  → Updates alert_routing: reduce Camera 02 sensitivity between 05:00-07:00
  → Updates camera_monitor: add "cleaning_crew" time window to schedule

Experience: "Press Brake 01 high vibration detected, confirmed, maintenance found
  worn hydraulic pump — vibration 2 weeks before visible fluid leak"
  → Updates vibration_baseline: add hydraulic pump frequency monitoring
  → Updates anomaly_detection: create hydraulic_system degradation pattern rule
```

## Configuration

```yaml
shift_report:
  schedule:
    shift_change_report: true
    daily_summary_hour: 22
    weekly_digest_day: "monday"

  format:
    include_charts: true
    chart_resolution: 150
    machine_health_grid: true
    anomaly_timeline: true
    recommendations: true
    max_recommendations: 5

  delivery:
    shift_handover:
      - telegram
      - slack
    daily_summary:
      - email
    weekly_digest:
      - email
      - slack

  retention:
    shift_reports_days: 30
    daily_summaries_days: 365
    weekly_digests_days: 1825
```
