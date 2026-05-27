# Hermes Skill: anomaly_detection

## Metadata
- **Name:** anomaly_detection
- **Version:** 1.0.0
- **Category:** monitoring
- **Depends on:** sensor_polling, vision_monitor

## Description

Detects anomalies in camera feeds and sensor data by comparing current readings against learned baseline profiles. Uses Z-score statistical analysis with exponential moving average (EMA) baselines that adapt over time through operator feedback.

This is the core intelligence layer — it decides whether a sensor reading is "normal for this machine at this time" or "something is wrong."

## Triggers

- **On sensor reading:** Every polled sensor value is passed through the anomaly detector
- **On camera frame analysis:** Vision Monitor outputs (motion scores, machine status) are checked
- **On schedule:** Periodic baseline recalibration (configurable interval)
- **On feedback:** Operator confirms/dismisses an alert

## Actions

1. **Compare current readings against learned baselines** using Z-score analysis
2. **Calculate anomaly score** (0.0 = perfectly normal, 1.0 = extreme anomaly)
3. **Flag deviations** above configurable thresholds per sensor type
4. **Update baselines** from confirmed normal readings (EMA smoothing)
5. **Adjust thresholds** based on operator feedback

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ema_alpha` | float | 0.05 | Exponential moving average smoothing factor |
| `zscore_threshold` | float | 2.5 | Z-score above which readings are flagged |
| `min_samples` | int | 20 | Minimum samples before baseline is considered reliable |
| `max_history` | int | 10000 | Maximum baseline history entries per sensor |

## Input Schema

```json
{
  "machine_id": "cnc_machine_1",
  "sensor_type": "temperature",
  "value": 72.5,
  "timestamp": "2025-06-15T14:32:17Z",
  "source": "sensor_polling"
}
```

## Output Schema

```json
{
  "machine_id": "cnc_machine_1",
  "sensor_type": "temperature",
  "value": 72.5,
  "anomaly_score": 0.82,
  "z_score": 3.1,
  "baseline_mean": 63.2,
  "baseline_std": 3.0,
  "is_anomaly": true,
  "confidence": "high",
  "threshold": 2.5,
  "recommended_severity": "WARNING",
  "timestamp": "2025-06-15T14:32:17Z"
}
```

## Example Scenarios

### Scenario 1: Normal Reading
```
Input:  CNC Machine #1, temperature, 64.1°C
Baseline: mean=63.5°C, std=2.1°C
Z-score: 0.29
Output: anomaly_score=0.02, is_anomaly=false
```

### Scenario 2: Mild Deviation (Warning)
```
Input:  CNC Machine #1, temperature, 71.3°C
Baseline: mean=63.5°C, std=2.1°C
Z-score: 3.71
Output: anomaly_score=0.65, is_anomaly=true, severity=WARNING
```

### Scenario 3: Critical Deviation
```
Input:  CNC Machine #1, vibration, 0.01g
Baseline: mean=2.3g, std=0.5g
Z-score: -4.58
Output: anomaly_score=0.95, is_anomaly=true, severity=CRITICAL
Note:   Negative Z-score (too low) also triggers — machine stopped!
```

## Learning Behavior

### After Confirmed False Alarm
```
Trigger: Operator dismisses alert, confirms "this is normal"
Action:
  1. Add current reading to baseline history
  2. Recalculate EMA (wider baseline)
  3. If same pattern 3+ times, increase threshold for this machine+sensor by 10%
  4. Log learning event: "Baseline widened for cnc_machine_1/temperature"
```

### After Confirmed Real Incident
```
Trigger: Operator confirms alert was a real problem
Action:
  1. Record the anomaly as a confirmed incident
  2. Tighten detection threshold for this machine+sensor by 5%
  3. Add incident details to pattern database for future matching
  4. Log learning event: "Threshold tightened for cnc_machine_1/temperature"
```

### Baseline Recalibration
```
Trigger: After N new readings (configurable)
Action:
  1. Recalculate mean and std from recent history
  2. Compare old vs new baseline
  3. If significant drift detected, log "Baseline shift detected"
  4. Optionally trigger alert for significant baseline changes
```

## Configuration

Baselines are persisted to `data/baselines.json` and loaded on startup. Each machine+sensor combination gets its own independent baseline profile.

## Integration

This skill is automatically invoked by:
- `sensor_polling` after each reading cycle
- `vision_monitor` after each frame analysis
- `on_alert.sh` hook for post-alert baseline updates
