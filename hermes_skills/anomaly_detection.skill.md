---
name: anomaly_detection
version: 1.0.0
description: >
  Learns normal operational patterns across all sensor streams (vibration,
  temperature, current draw, camera motion) and flags statistical deviations.
  Creates new detection rules from confirmed incidents via Hermes' closed loop.
triggers:
  - sensor_reading_received
  - camera_motion_detected
  - scheduled_health_check
  - incident_confirmed
inputs:
  - sensor_readings: dict with machine_id, sensor_type, value, timestamp
  - motion_data: dict with camera_id, motion_score, bounding_boxes
  - historical_baseline: dict with rolling averages, std deviations
outputs:
  - anomaly_event: dict with severity, machine_id, description, confidence
  - detection_rule: new skill update when incident is confirmed
---

# Anomaly Detection Skill

## Purpose

This skill monitors all sensor streams and camera feeds in real-time to detect deviations from learned normal behavior. It uses statistical process control (SPC) methods combined with pattern recognition to identify anomalies before they become critical failures.

## How It Works

### 1. Baseline Establishment

When first deployed, the skill collects sensor data during a **learning period** (default: 72 hours). During this time it builds per-machine baselines:

- **Vibration**: FFT frequency signatures, RMS velocity, peak-to-peak displacement
- **Temperature**: Operating range with time-of-day adjustments
- **Current Draw**: Duty cycle patterns, idle vs active power profiles
- **Camera Motion**: Background subtraction model, normal activity zones

```python
# Internal baseline structure stored in Hermes memory
baseline = {
    "machine_id": {
        "vibration": {
            "mean_rms": 1.2,
            "std_rms": 0.3,
            "fft_peaks": [(45.0, 0.8), (90.0, 0.3)],
            "learning_samples": 10000,
            "last_updated": "2026-05-28T10:00:00Z"
        },
        "temperature": {
            "mean": 52.3,
            "std": 4.1,
            "hourly_adjustments": {0: -2.1, 6: -1.0, 12: +3.2},
        },
        "current": {
            "idle_mean": 2.1,
            "active_mean": 8.5,
            "duty_cycle": 0.72
        }
    }
}
```

### 2. Real-Time Detection

For each incoming sensor reading, the skill computes a Z-score against the baseline:

```
z_score = |reading - baseline_mean| / baseline_std

Severity Classification:
  z < 2.0    → NORMAL     (log only)
  2.0 ≤ z < 3.0 → WARNING    (log + notify shift lead)
  3.0 ≤ z < 4.0 → CRITICAL   (alert + page on-call)
  z ≥ 4.0     → EMERGENCY  (immediate alert + local buzzer)
```

### 3. Multi-Modal Correlation

Single-sensor anomalies trigger a **correlation check** across all sensors on the same machine:

- High vibration + rising temperature → Bearing failure precursor
- High current + low vibration → Motor stall risk
- Camera motion in restricted zone + no scheduled activity → Unauthorized access
- All sensors normal but motion detected → Personnel safety event

### 4. Closed Learning Loop

When an incident is confirmed by a human operator:

1. **Positive cases**: The skill extracts features from the pre-incident sensor window (typically 30 minutes before) and creates a new detection rule
2. **False positives**: The skill adjusts the baseline for the offending sensor, widening thresholds where appropriate
3. **Environmental adaptation**: If multiple false positives occur due to environmental changes (e.g., ambient temperature shift), the skill re-baselines automatically

### 5. Skill Self-Improvement

The skill writes new detection capabilities back to Hermes memory:

```
Experience: "CNC Mill 01 bearing failure detected 2 hours before breakdown"
  → Creates detection rule: {pattern: "rising_vibration_fft_peak_45hz + temp_+8deg_in_1hr",
                            machine_type: "cnc_mill",
                            lead_time: "2h",
                            confidence: 0.87}
```

## Configuration

```yaml
anomaly_detection:
  learning_period_hours: 72
  z_score_thresholds:
    warning: 2.0
    critical: 3.0
    emergency: 4.0
  correlation_window_minutes: 30
  rebase_trigger_false_positives: 5
  minimum_samples: 1000
```

## Integration Points

- **Input**: Receives data from `gpio_reader` (sensors) and `camera_capture` (vision)
- **Output**: Sends `anomaly_event` to `alert_routing` skill
- **Memory**: Stores baselines and detection rules in Hermes persistent memory
- **Feedback**: Accepts operator confirmations via Telegram to close the learning loop
