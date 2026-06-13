---
title: "Hermes-Pi Factory Guardian: Self-Learning AI Surveillance for Industrial Monitoring"
description: "How I built an always-on factory security system using Hermes Agent on Raspberry Pi 5 with cameras, vibration sensors, and a closed learning loop that gets smarter with every shift."
cover_image: https://dev-to-uploads.s3.amazonaws.com/uploads/articles/xxx.jpg
tags: hermesagent, raspberrypi, iot, machinelearning, python
published: false
---

# Hermes-Pi Factory Guardian: Self-Learning AI Surveillance for Industrial Monitoring

![Hermes-Pi Factory Guardian Dashboard](https://github.com/icohangar-ops/hermes-pi-factory-guardian/raw/main/docs/media/hermes-pi-factory-guardian.png)

## The Problem: Factory Monitoring Is Broken

Walk into any manufacturing facility and you'll see the same story: cameras that record footage nobody watches, vibration sensors that only alert *after* something breaks, and maintenance schedules based on calendar dates instead of actual machine condition. The data is there, but the intelligence layer is missing.

Traditional monitoring systems suffer from three fundamental flaws. First, they use static thresholds — a vibration sensor triggers an alert at a fixed value, regardless of whether that value represents normal behavior for a Tuesday afternoon shift or an abnormal spike on a quiet Sunday. Second, they don't learn. Every false positive requires manual threshold adjustment, and every real incident is treated as an isolated event rather than a data point that should improve the system. Third, they don't correlate across modalities. A rising temperature sensor and a vibration anomaly on the same machine are handled by separate systems that never compare notes.

I wanted to build something different: a monitoring system that genuinely gets better at its job over time, using the [Hermes Agent](https://github.com/nousresearch/hermes) from Nous Research as the intelligence core on a Raspberry Pi 5 that sits right on the factory floor.

## Why Hermes Agent?

Hermes Agent's closed learning loop is what makes this project possible. Unlike a traditional ML pipeline where you train a model, deploy it, and hope it still works six months later, Hermes continuously improves through experience. It creates skills from real-world interactions, stores those skills in persistent memory, and applies them in future sessions.

For factory monitoring, this is transformative. Here's a concrete example: when a CNC mill's bearing starts to fail, the vibration signature changes gradually over weeks. A static system won't catch it until the amplitude exceeds the threshold. But Hermes, after seeing one bearing failure, creates a detection skill that watches for the specific FFT frequency pattern (BPFO — Ball Pass Frequency Outer race) that precedes bearing failures. The next time it sees that pattern on *any* CNC mill, it catches it weeks earlier.

This is exactly what the Hermes Agent Challenge asks for: an application that demonstrates the self-improving capabilities of Hermes in a real-world use case.

## Architecture Overview

The system runs on a Raspberry Pi 5 with three categories of input: camera feeds, GPIO sensors, and the Hermes Agent core processing everything through its five specialized skills.

```
Raspberry Pi 5
├── Camera Module v3 → OpenCV + YOLOv8-nano
├── ADXL345 Vibration Sensor (SPI)
├── DS18B20 Temperature Sensor (1-Wire)
├── ACS712 Current Sensor (MCP3008 ADC)
└── Hermes Agent Core
    ├── anomaly_detection skill
    ├── alert_routing skill
    ├── camera_monitor skill
    ├── vibration_baseline skill
    └── shift_report skill
```

### Hardware

I chose the Raspberry Pi 5 (8GB) for three reasons. First, it has enough compute to run YOLOv8-nano at 15 FPS with the Coral TPU accelerator, which is sufficient for factory zone monitoring. Second, it has native SPI and I2C interfaces for direct sensor connectivity — no Arduino intermediate layer needed. Third, it costs under $100, making it practical to deploy multiple units across a factory floor.

The sensor stack connects directly to the Pi's GPIO header:
- **Vibration**: ADXL345 3-axis accelerometer via SPI at 3200 Hz — captures FFT signatures for predictive maintenance
- **Temperature**: DS18B20 via 1-Wire — monitors machine operating temperatures with ±0.5°C accuracy
- **Current**: ACS712 Hall effect sensor via MCP3008 ADC — tracks motor current draw for stall detection
- **Camera**: Pi Camera Module v3 (1080p@30fps) — OpenCV background subtraction + YOLO object classification

### The Five Hermes Skills

The intelligence of the system lives in five Hermes skills that form a cooperative pipeline:

**1. Anomaly Detection** — The core skill. It builds per-machine baselines during a 72-hour learning period, then uses Z-score analysis to detect deviations. It correlates across sensor modalities: if vibration is high *and* temperature is rising on the same machine, the severity is bumped. When an operator confirms an incident, the skill extracts features from the pre-incident window and creates a new detection rule that Hermes remembers permanently.

**2. Vibration Baseline** — Specialized FFT analysis for predictive maintenance. It extracts bearing fault frequencies (BPFO, BPFI, BSF, FTF) from the acceleration signal and tracks them over 7/14/30-day rolling windows. The trending capability projects when a machine will exceed its health threshold — for example, "CNC Mill 01 will exceed warning threshold in approximately 3 weeks at current trend rate."

**3. Camera Monitor** — Manages the full vision pipeline: motion detection with adaptive sensitivity (lower at night, higher during active shifts), YOLO classification on motion events, zone-based monitoring with restricted area alerts, and incident footage archiving. It automatically adjusts sensitivity when environmental conditions change — like reducing detection sensitivity at the loading dock during rainstorms that cause false motion triggers.

**4. Alert Routing** — Context-aware notification dispatch that considers shift schedules, severity levels, and operator availability. It prevents alert fatigue through intelligent grouping (merging repeated alerts from the same machine within 5 minutes) and auto-escalation (bumping severity after 3 repeated alerts). Supports Telegram, Slack, email, and a GPIO-connected buzzer for on-site emergency alerts.

**5. Shift Report** — Generates comprehensive handover reports at each shift change with machine health grids, anomaly timelines, sensor trend charts, and actionable recommendations for the incoming shift. This skill closes the feedback loop by identifying patterns in false positives and adjusting detection parameters accordingly.

## The Closed Learning Loop in Action

Here's how Hermes' self-improvement works in practice with this system:

### Week 1: Learning Period
The system collects sensor data without generating alerts. It builds per-machine vibration FFT signatures, establishes temperature ranges adjusted for time of day, and learns normal current draw duty cycles for each machine.

### Week 2: First Incident
A press brake's hydraulic pump develops a leak. The vibration sensor catches increasing high-frequency content 2 hours before a visible fluid leak. The operator confirms the incident via Telegram reply ("CONFIRM").

**Hermes creates a new skill**: "Hydraulic pump degradation pattern: rising high-frequency vibration FFT content above 0.5mm/s, lead time approximately 2 hours, confidence 87%."

### Week 6: Second Incident Prevention
The same FFT pattern appears on a different press brake. Because Hermes stored the detection rule from Week 2, the system now alerts the maintenance team 2 hours before the pump fails — preventing a costly unplanned shutdown.

### Month 3: Environmental Adaptation
Three consecutive night shifts generate false motion alerts from Camera 02 near the loading dock during rainstorms. Without any manual intervention, the shift report skill identifies the correlation and the camera monitor skill creates a rule: "Reduce loading dock camera sensitivity by 30% during high luminance variance events, auto-revert after 2 hours."

This is the power of the closed loop. The system didn't need a software update, a model retrain, or a configuration change. It learned from experience and improved itself.

## Code Highlights

### Hermes Skill Format

Each skill is a Markdown file with YAML frontmatter that Hermes Agent processes:

```markdown
---
name: anomaly_detection
version: 1.0.0
description: >
  Learns normal operational patterns across all sensor streams and
  flags statistical deviations. Creates new detection rules from
  confirmed incidents via Hermes' closed loop.
triggers:
  - sensor_reading_received
  - camera_motion_detected
  - incident_confirmed
---
```

The frontmatter defines the skill's interface — its triggers, inputs, and outputs. The body contains detailed instructions that Hermes uses to create new skills from experience.

### Multi-Modal Sensor Fusion

The vision pipeline combines motion detection with YOLO classification:

```python
class VisionPipeline:
    def process_frame(self, frame, camera_id="cam_01"):
        # Step 1: Background subtraction for motion detection
        motion_score, motion_boxes = self.motion_detector.detect(frame)
        
        if motion_score < 0.01:
            return None  # No significant motion
        
        # Step 2: Run YOLO on motion regions
        yolo_detections = []
        for mbox in motion_boxes:
            roi = (mbox.x1, mbox.y1, mbox.x2, mbox.y2)
            dets = self.yolo_detector.detect(
                frame, roi=roi, 
                target_classes=["person", "truck", "car"]
            )
            yolo_detections.extend(dets)
        
        # Step 3: Determine severity from zone + classification
        severity = self._assess_severity(yolo_detections, zones)
        return DetectionEvent(severity=severity, ...)
```

### Alert Grouping to Prevent Fatigue

```python
def _check_grouping(self, event):
    recent = [e for e in self._alert_history
              if e.machine_id == event.machine_id
              and not e.resolved
              and e.timestamp > cutoff]
    
    if len(recent) >= 3:
        # Auto-escalate after 3 repeats
        event.severity = bump_severity(event.severity)
        return False  # Send the escalated alert
    elif recent:
        recent[0].sensor_data.update(event.sensor_data)
        return True  # Merge, don't send
    return False
```

## Deployment

The system deploys via Docker on the Pi:

```bash
# Flash Raspberry Pi OS, then:
git clone https://github.com/icohangar-ops/hermes-pi-factory-guardian.git
cd hermes-pi-factory-guardian
bash scripts/setup_pi.sh          # Configure GPIO, load kernel modules
cp .env.example .env             # Add your Telegram bot token, etc.
docker compose up -d              # Start monitoring
```

The setup script handles enabling camera, SPI, I2C, and 1-Wire interfaces on the Pi. Docker maps the GPIO device files into the container so the sensor code works identically inside or outside containers.

## What I Learned

1. **Static thresholds are the enemy of good monitoring**. Every factory is different, every machine has its own personality, and environmental conditions change daily. The only way to build reliable anomaly detection is to learn from actual data — which is exactly what Hermes does.

2. **Multi-modal correlation is underrated**. A vibration anomaly alone might be noise. A temperature spike alone might be normal. But vibration + rising temperature + increasing current draw on the same machine almost always means something is wrong. The correlation check is the highest-value feature in the system.

3. **Alert fatigue kills monitoring systems**. If operators get too many false alarms, they start ignoring all alerts — including the real ones. The grouping, deduplication, and adaptive sensitivity features are essential for long-term deployment.

4. **Hermes' learning loop is genuinely useful** for this domain. Industrial environments have patterns that repeat across machines and shift cycles. When Hermes learns a bearing failure pattern on one CNC mill and applies it to another, that's not a generic ML transfer learning trick — it's the agent reasoning about mechanical similarity and deciding to apply a stored skill.

## Future Work

- **Coral TPU optimization**: YOLOv8-nano on the Coral USB Accelerator can reach 30+ FPS, enabling higher-quality monitoring
- **Multi-Pi mesh networking**: Deploy multiple Pi units across a large factory, sharing learned skills via Hermes memory sync
- **Digital twin integration**: Feed sensor data into a 3D model of the factory for spatial visualization
- **Voice commands**: Add a microphone for operator queries like "What's the status of CNC Mill 03?"

## Repository

All code, skills, configuration, and documentation are available at:

**[github.com/icohangar-ops/hermes-pi-factory-guardian](https://github.com/icohangar-ops/hermes-pi-factory-guardian)**

The repository includes:
- 5 production-ready Hermes skills with detailed documentation
- Complete sensor integration code (vibration, temperature, current)
- OpenCV + YOLOv8-nano vision pipeline
- Alert dispatch system (Telegram, Slack, email, GPIO buzzer)
- Docker deployment configuration
- Pi setup scripts

Built with ❤️ for the [Hermes Agent Challenge](https://dev.to/hermesagent).

---

*This project demonstrates that Hermes Agent's closed learning loop isn't just a research curiosity — it's a practical tool for building monitoring systems that genuinely improve through experience. Every shift it monitors makes it smarter.*
