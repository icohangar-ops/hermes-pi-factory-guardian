# Hermes Skill: shift_report

## Metadata
- **Name:** shift_report
- **Version:** 1.0.0
- **Category:** reporting
- **Depends on:** anomaly_detection, sensor_polling, alert_router

## Description

Auto-generates comprehensive shift handoff reports summarizing all alerts, sensor readings, machine status, and learning updates during a shift period. Reports are formatted in markdown and can be delivered to Slack, Telegram, email, or saved to file.

Eliminates the "what happened while I was gone?" problem.

## Triggers

- **Shift change:** Automatically triggered at configured shift boundaries
- **Manual request:** Operator requests a report via Hermes Agent command
- **On-demand:** `hermes report --shift morning --date 2025-06-15`
- **Scheduled cron:** Periodic summary reports (e.g., daily digest)

## Report Sections

### 1. Alert Summary
- Total alerts by severity (CRITICAL / WARNING / INFO)
- Alerts auto-suppressed by learned patterns
- Alerts escalated
- Alerts acknowledged vs unacknowledged
- Mean time to acknowledge (MTTA)

### 2. Sensor Summary
- Per-machine min/max/avg for each sensor type
- Trends (improving / stable / degrading)
- Outliers count

### 3. Machine Status
- Current operational status per machine
- Downtime minutes per machine
- Machines with active warnings

### 4. Learning Updates
- New patterns detected
- New skills created
- Baseline adjustments made
- False positive rate trend

### 5. Handoff Notes
- Critical items requiring attention
- Scheduled maintenance reminders
- Operator notes

## Input Schema

```json
{
  "shift_start": "2025-06-15T06:00:00Z",
  "shift_end": "2025-06-15T14:00:00Z",
  "shift_name": "morning",
  "format": "markdown",
  "include_images": true
}
```

## Output Schema

```json
{
  "shift_name": "morning",
  "date": "2025-06-15",
  "generated_at": "2025-06-15T14:00:05Z",
  "summary": {
    "total_alerts": 16,
    "critical": 1,
    "warning": 3,
    "info": 12,
    "auto_suppressed": 7,
    "mtta_minutes": 4.2
  },
  "machines_monitored": 4,
  "learning_updates": 3,
  "report_path": "/data/reports/shift_morning_20250615.md",
  "delivered_to": ["telegram", "slack"]
}
```

## Example Output

```
═══════════════════════════════════════════════════════
  SHIFT REPORT — Morning Shift (06:00–14:00)
  Date: 2025-06-15
═══════════════════════════════════════════════════════

📊 ALERT SUMMARY
  Critical:  1 (Machine stoppage — CNC #3, resolved 14:45)
  Warning:   3 (Elevated temp on CNC #1 x2, oil level low)
  Info:      12 (Routine — 7 auto-suppressed by learned patterns)

🌡️ SENSOR SUMMARY
  Machine          | Avg Temp | Max Temp | Avg Vibration | Status
  ─────────────────┼──────────┼──────────┼───────────────┼────────
  CNC Machine #1   | 67.2°C   | 72.1°C   | 2.3g          | ⚠️ WARM
  CNC Machine #2   | 63.8°C   | 65.4°C   | 1.9g          | ✅ OK
  CNC Machine #3   | 41.0°C   | 41.0°C   | 0.01g         | 🔴 STOPPED
  Conveyor #1      | 45.2°C   | 48.1°C   | 3.8g          | ✅ OK

🧠 LEARNING UPDATES THIS SHIFT
  → Pattern confirmed: Monday AM startup (99.2% confidence)
  → Baseline tightened: CNC #3 vibration (after real incident)
  → New skill generated: "weekly_oil_check_reminder"
  → False positive rate (7-day): 6.1% (↓ from 38.2% at deploy)

📝 HANDOFF NOTES
  - CNC #3 stopped due to jammed workpiece — cleared, resumed 14:45
  - CNC #1 running warm — schedule coolant check for afternoon shift
  - Oil level sensor flagged low on CNC #2 — check during next maintenance
═══════════════════════════════════════════════════════
```

## Configuration

```yaml
shift_report:
  shifts:
    morning: { start: "06:00", end: "14:00" }
    afternoon: { start: "14:00", end: "22:00" }
    night: { start: "22:00", end: "06:00" }
  
  delivery:
    on_shift_change: true
    channels: ["telegram", "slack", "file"]
    file_path: "/data/reports/"
  
  format:
    include_sensor_details: true
    include_learning_updates: true
    include_images: true
    max_images: 3
```

## Integration with Hermes Agent

The shift report skill is registered as a Hermes skill and can be invoked:
- Automatically via the Hermes scheduler at shift boundaries
- By operators asking Hermes: "Generate the morning shift report"
- Via the `on_alert.sh` hook for post-shift automation
