---
name: vibration_baseline
version: 1.0.0
description: >
  Builds machine-specific vibration frequency signatures using FFT analysis,
  detects bearing wear, misalignment, and imbalance patterns, and creates
  new baseline models when new equipment is introduced to the production line.
triggers:
  - vibration_sample_collected
  - new_machine_registered
  - baseline_update_requested
  - maintenance_completed
inputs:
  - vibration_data: time-series acceleration samples from ADXL345 sensor
  - machine_profile: machine type, operating speed, expected frequency ranges
  - maintenance_record: recent maintenance that may affect vibration signature
outputs:
  - vibration_baseline: FFT signature, RMS velocity, peak frequencies
  - health_assessment: bearing condition, alignment status, balance rating
  - trending_alert: when vibration trends suggest impending failure
---

# Vibration Baseline Skill

## Purpose

This skill specializes in vibration analysis for predictive maintenance. It builds unique frequency signatures for each machine, continuously monitors for deviations that indicate mechanical degradation, and creates baseline models for new equipment automatically.

## How It Works

### 1. Signal Processing Pipeline

Raw accelerometer data from the ADXL345 sensor goes through a multi-stage pipeline:

```python
# Sampling at 3200 Hz (ADXL345 max for Pi SPI)
sample_rate = 3200
samples_per_window = 6400  # 2-second window

# Processing stages:
# 1. Remove DC offset
# 2. Apply Hanning window function
# 3. Compute FFT (0-1600 Hz Nyquist)
# 4. Convert to velocity spectrum (integration)
# 5. Extract feature bands
```

### 2. Feature Extraction

The skill extracts key vibration features per machine:

| Feature | Description | Failure Mode Indicated |
|---------|-------------|----------------------|
| 1× RPM | Fundamental rotation frequency | Imbalance, eccentricity |
| 2× RPM | Second harmonic | Misalignment, looseness |
| BPFO | Ball Pass Freq Outer race | Outer bearing wear |
| BPFI | Ball Pass Freq Inner race | Inner bearing wear |
| BSF | Ball Spin Frequency | Rolling element wear |
| FTF | Fundamental Train Freq | Cage damage |
| Overall RMS | Root Mean Square velocity | General condition |
| Crest Factor | Peak / RMS ratio | Impulsive events |

### 3. Machine-Specific Baselines

Each machine type has characteristic frequency bands:

```yaml
machine_profiles:
  cnc_mill:
    nominal_rpm: 3000
    expected_frequencies:
      - name: "spindle"
        freq_hz: 50
        tolerance_pct: 5
      - name: "coolant_pump"
        freq_hz: 25
        tolerance_pct: 10
    bearing_spec:
      bpfo: 148.3
      bpfi: 221.7
      bsf: 98.2
      ftf: 11.8
    health_thresholds:
      rms_warning: 2.5     # mm/s
      rms_critical: 4.5    # mm/s
      crest_factor_warning: 4.0
      crest_factor_critical: 6.0

  conveyor:
    nominal_rpm: 60
    expected_frequencies:
      - name: "drive_roller"
        freq_hz: 1.0
        tolerance_pct: 20
```

### 4. Trending and Prediction

The skill maintains rolling windows (7/14/30 days) and computes trend slopes:

```
Vibration RMS trend for CNC Mill 01:
  7-day slope:  +0.12 mm/s/week  (accelerating)
  14-day slope: +0.08 mm/s/week
  30-day slope: +0.05 mm/s/week

Projection: Will exceed warning threshold (2.5 mm/s) in approximately 3 weeks
Recommendation: Schedule bearing inspection during next maintenance window

Confidence: 82% (based on 6 similar historical patterns in Hermes memory)
```

### 5. New Machine Onboarding

When a new machine is registered, the skill automatically:

1. Begins continuous sampling for 24 hours
2. Builds initial FFT baseline from stable operating periods
3. Identifies dominant frequencies and assigns labels based on machine profile
4. Sets initial alert thresholds at ±3σ from mean
5. Refines thresholds after 1 week of stable operation

### 6. Post-Maintenance Rebaseline

After maintenance is logged, the skill:

1. Detects a significant shift in vibration signature (±20% change)
2. Triggers an automatic 4-hour rebase period
3. Gradually transitions to new baseline
4. Archives the old baseline for comparison and trend continuity

### 7. Hermes Learning Loop

```
Experience: "CNC Mill 03 bearing failure — BPFO peak appeared 6 weeks before failure"
  → Creates detection rule: {watch_frequency: "BPFO",
                            watch_threshold: "0.5mm/s above baseline",
                            machine_type: "cnc_mill",
                            lead_time: "~6 weeks",
                            confidence: 0.91}

Experience: "Conveyor belt misalignment detected — 2× RPM harmonic grew 300% over 2 weeks"
  → Creates detection rule: {watch_frequency: "2x_RPM",
                            trend_alert: "weekly_increase > 10%",
                            machine_type: "conveyor",
                            confidence: 0.78}
```

## Configuration

```yaml
vibration_baseline:
  sampling:
    rate_hz: 3200
    window_seconds: 2
    windows_per_average: 5

  fft:
    max_frequency_hz: 1600
    frequency_resolution_hz: 0.5
    window_function: "hanning"

  trending:
    rolling_windows: [7, 14, 30]
    min_samples_for_trend: 100
    projection_confidence_min: 0.7

  health_scoring:
    weights:
      overall_rms: 0.4
      crest_factor: 0.2
      bearing_frequencies: 0.3
      trend_acceleration: 0.1
```
