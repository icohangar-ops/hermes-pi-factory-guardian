# Hermes-Pi Factory Guardian

> AI-powered factory security monitoring using Hermes Agent on Raspberry Pi 5 — self-learning anomaly detection for cameras and sensors.

![Hermes Agent Challenge](https://img.shields.io/badge/Hermes_Agent_Challenge-2025-blue)
![Raspberry Pi](https://img.shields.io/badge/Platform-Raspberry_Pi_5-red)
![Hermes](https://img.shields.io/badge/Powered_By-Hermes_Agent-purple)
![License](https://img.shields.io/badge/License-MIT-green)

## Overview

Hermes-Pi Factory Guardian is an always-on industrial monitoring system that runs [Hermes Agent](https://github.com/nousresearch/hermes) on a Raspberry Pi 5. It monitors factory cameras and GPIO sensors in real-time, using Hermes' closed learning loop to continuously improve its anomaly detection accuracy over time.

The system combines computer vision (OpenCV + YOLOv8-nano) with physical sensor data (vibration, temperature, current draw) to create a unified threat model. Hermes' self-improving capabilities mean the system gets smarter with every shift it monitors — learning new failure patterns, adapting to environmental changes, and refining its alert thresholds without manual reconfiguration.

### Key Features

- **Self-Learning Anomaly Detection**: Hermes creates and refines detection skills from every incident, improving accuracy over time
- **Multi-Modal Monitoring**: Camera feeds + vibration sensors + temperature + current draw analyzed together
- **Real-Time Vision Pipeline**: OpenCV motion detection with YOLOv8-nano object classification
- **Smart Alert Routing**: Context-aware escalation via Telegram, Slack, email, and local buzzer
- **Shift Reports**: Automated daily summaries with trend analysis and anomaly timeline
- **Offline-Capable**: Full operation without internet; syncs skills and reports when connected
- **Edge-Optimized**: Runs entirely on Pi 5 with Coral TPU acceleration

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5                           │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────────────┐ │
│  │ Camera  │→ │ OpenCV + │→ │                            │ │
│  │ Module  │  │ YOLO-nano│  │    Hermes Agent Core       │ │
│  └─────────┘  └──────────┘  │  ┌──────────────────────┐  │ │
│  ┌─────────┐  ┌──────────┐  │  │ Closed Learning Loop  │  │ │
│  │ Vibration│→ │ Sensor   │→ │  │ ──────────────────── │  │ │
│  │ Sensor  │  │ Fusion   │  │  │ Experience → Skill    │  │ │
│  └─────────┘  └──────────┘  │  │ Skill → Improvement   │  │ │
│  ┌─────────┐  ┌──────────┐  │  │ Memory → Context      │  │ │
│  │ Temp    │→ │ Threshold│  │  └──────────────────────┘  │ │
│  │ Sensor  │  │ Engine   │  │                            │ │
│  └─────────┘  └──────────┘  │  Skills:                   │ │
│  ┌─────────┐                │  ├─ anomaly_detection       │ │
│  │ Current │→ ─────────────→│  ├─ alert_routing           │ │
│  │ Sensor  │                │  ├─ camera_monitor          │ │
│  └─────────┘                │  ├─ vibration_baseline      │ │
│                              │  └─ shift_report            │ │
│                              └────────────┬───────────────┘ │
│                                           │                 │
│                              ┌────────────▼───────────────┐ │
│                              │     Alert Dispatcher       │ │
│                              │  Telegram │ Slack │ Email  │ │
│                              └────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Hermes Skills

This project includes 5 custom Hermes Agent skills that form the core intelligence of the system:

### 1. Anomaly Detection (`hermes_skills/anomaly_detection.skill.md`)
Learns normal operational patterns across all sensor streams and flags deviations. Creates new detection rules from confirmed incidents.

### 2. Alert Routing (`hermes_skills/alert_routing.skill.md`)
Context-aware alert dispatch that considers severity, time of day, shift schedules, and responder availability. Reduces alert fatigue through intelligent grouping.

### 3. Camera Monitor (`hermes_skills/camera_monitor.skill.md`)
Controls camera scheduling, manages motion detection sensitivity, triggers YOLO classification on motion events, and archives incident footage.

### 4. Vibration Baseline (`hermes_skills/vibration_baseline.skill.md`)
Builds machine-specific vibration signatures, detects bearing wear and misalignment patterns, and creates new baseline models for new equipment.

### 5. Shift Report (`hermes_skills/shift_report.skill.md`)
Generates comprehensive shift summaries including sensor readings, anomalies detected, alerts sent, and trend analysis with recommendations.

## Project Structure

```
hermes-pi-factory-guardian/
├── hermes_skills/              # Hermes Agent skill definitions
│   ├── anomaly_detection.skill.md
│   ├── alert_routing.skill.md
│   ├── camera_monitor.skill.md
│   ├── vibration_baseline.skill.md
│   └── shift_report.skill.md
├── src/
│   ├── camera/                 # Camera capture and streaming
│   │   └── capture.py
│   ├── sensors/                # GPIO sensor interfaces
│   │   └── gpio_reader.py
│   ├── alert/                  # Alert dispatch system
│   │   └── dispatcher.py
│   └── vision/                 # OpenCV + YOLO pipeline
│       └── detector.py
├── config/
│   └── factory_config.yaml     # Machine profiles and thresholds
├── scripts/
│   ├── setup_pi.sh             # Raspberry Pi setup script
│   └── install_deps.sh         # Dependency installation
├── tests/
│   └── test_skills.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start

### Prerequisites

- Raspberry Pi 5 (8GB recommended)
- Raspberry Pi Camera Module v3 or USB webcam
- Grove vibration sensor, temperature sensor (DS18B20), current sensor (ACS712)
- Coral USB Accelerator (optional, for faster YOLO inference)

### Setup

```bash
# Clone the repository
git clone https://github.com/Cubiczan/hermes-pi-factory-guardian.git
cd hermes-pi-factory-guardian

# Run Pi setup (installs system dependencies, enables camera)
bash scripts/setup_pi.sh

# Install Python dependencies
pip install -r requirements.txt

# Copy environment configuration
cp .env.example .env
# Edit .env with your sensor pins, camera config, and alert endpoints

# Start Hermes Agent with Factory Guardian skills
hermes --skills ./hermes_skills/ --config ./config/factory_config.yaml
```

### Docker Deployment

```bash
docker compose up -d
```

### Verify Installation

```bash
# Check sensor readings
python -m src.sensors.gpio_reader --test

# Test camera capture
python -m src.camera.capture --test

# Verify YOLO detection
python -m src.vision.detector --test
```

## Configuration

Edit `config/factory_config.yaml` to define your factory layout:

```yaml
factory:
  name: "Production Line A"
  shift_schedule:
    day_shift: "06:00-14:00"
    swing_shift: "14:00-22:00"
    night_shift: "22:00-06:00"

machines:
  - id: "cnc_mill_01"
    name: "CNC Mill Station 1"
    type: "cnc_mill"
    sensors:
      vibration: { pin: 17, threshold: 2.5 }
      temperature: { pin: 4, threshold: 75 }
      current: { pin: 27, threshold: 12.0 }
    camera: "cam_01"
    alert_recipients:
      telegram: "-1001234567890"
      email: "shift-lead@factory.com"
```

## Technology Stack

| Component | Technology |
|-----------|-----------|
| AI Agent | Hermes Agent (Nous Research) |
| Platform | Raspberry Pi 5 |
| Computer Vision | OpenCV 4.9, YOLOv8-nano |
| Accelerator | Google Coral TPU |
| Sensors | Grove Pi Hat, DS18B20, ACS712, ADXL345 |
| Language | Python 3.11 |
| Alerts | Telegram Bot API, Slack Webhooks |
| Containerization | Docker, docker-compose |
| Logging | structlog |
| Configuration | YAML |

## Hermes Agent Challenge

This project is a submission for the [Hermes Agent Challenge](https://dev.to/hermes) on dev.to. It demonstrates Hermes' closed learning loop applied to real-world industrial IoT — where the agent genuinely gets better at its job through experience.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Hermes Agent](https://github.com/nousresearch/hermes) by Nous Research
- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics) for edge-optimized object detection
- [OpenCV](https://opencv.org/) for computer vision
- [Raspberry Pi Foundation](https://www.raspberrypi.org/) for the hardware platform
