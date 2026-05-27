# =============================================================================
# Hermes-Pi Factory Guardian — Architecture Document
# =============================================================================

## 1. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HERMES-PI FACTORY GUARDIAN                           │
│                    (Raspberry Pi 5 — 24/7 Operation)                        │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         DATA ACQUISITION LAYER                       │   │
│  │                                                                      │   │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐   │   │
│  │  │  Pi Camera    │    │  USB Camera  │    │  GPIO Sensors        │   │   │
│  │  │  Module v3    │    │  (USB 2.0)   │    │                      │   │   │
│  │  └──────┬───────┘    └──────┬───────┘    │  ┌────────────────┐   │   │
│  │         │                   │            │  │ MPU6050 (I2C)  │   │   │
│  │         └───────┬───────────┘            │  ├────────────────┤   │   │
│  │                 │                        │  │ DS18B20 (1W)   │   │   │
│  │                 ▼                        │  ├────────────────┤   │   │
│  │  ┌──────────────────────┐               │  │ ACS712 (SPI)   │   │   │
│  │  │   Vision Monitor     │               │  ├────────────────┤   │   │
│  │  │   (OpenCV)           │               │  │ HC-SR501 (GPIO) │   │   │
│  │  │                      │               │  └───────┬────────┘   │   │
│  │  │  • Motion detection  │               └──────────┼────────────┘   │
│  │  │  • Machine stoppage  │                          │                │
│  │  │  • Zone intrusion    │                          ▼                │
│  │  │  • Spill detection   │               ┌──────────────────────┐   │
│  │  └──────────┬───────────┘               │   Sensor Poller      │   │
│  │             │                           │   (RPi.GPIO/smbus2)  │   │
│  └─────────────┼───────────────────────────┴──────────┬─────────────┘   │
│                │                                              │            │
│                ▼                                              ▼            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                       INTELLIGENCE LAYER                           │   │
│  │                                                                      │   │
│  │  ┌──────────────────────────────────────────────────────────────┐   │   │
│  │  │                    HERMES AGENT                              │   │   │
│  │  │                                                              │   │   │
│  │  │  ┌──────────────────┐  ┌────────────────────────────────┐   │   │   │
│  │  │  │  LLM Backend     │  │  Anomaly Detector               │   │   │   │
│  │  │  │  (Ollama/Groq)   │  │  • Z-score + EMA baselines     │   │   │   │
│  │  │  │                  │  │  • Per-machine profiles         │   │   │   │
│  │  │  │  llama3 /        │  │  • Adaptive thresholds          │   │   │   │
│  │  │  │  mistral /       │  └──────────────┬─────────────────┘   │   │   │
│  │  │  │  gemma           │                 │                     │   │   │
│  │  │  └──────────────────┘                 ▼                     │   │   │
│  │  │                              ┌─────────────────────┐         │   │   │
│  │  │                              │  Alert Router       │         │   │   │
│  │  │  ┌──────────────────┐       │  • Severity routing  │         │   │   │
│  │  │  │ ⭐ LEARNING LOOP │       │  • Shift awareness   │         │   │   │
│  │  │  │                  │       │  • Deduplication     │         │   │   │
│  │  │  │  Patterns→Skills │       │  • Escalation        │         │   │   │
│  │  │  │  Feedback→Tune   │       └──────────┬──────────┘         │   │   │
│  │  │  │  History→Profile │                  │                     │   │   │
│  │  │  │                  │                  │                     │   │   │
│  │  │  └──────────────────┘       ┌──────────▼──────────┐         │   │   │
│  │  │                              │  Shift Reporter      │         │   │   │
│  │  │                              │  • Auto-generated    │         │   │   │
│  │  │                              │  • Markdown format   │         │   │   │
│  │  │                              └──────────────────────┘         │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      NOTIFICATION LAYER                            │   │
│  │                                                                      │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │   │
│  │  │  System  │  │ Telegram │  │  Slack   │  │  Email           │   │   │
│  │  │  Log     │  │  Bot     │  │  Webhook │  │  (SMTP)          │   │   │
│  │  │          │  │          │  │          │  │                  │   │   │
│  │  │ journald │  │ @BotFather│  │ #factory │  │ factory-ops@     │   │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      PERSISTENCE LAYER                             │   │
│  │                                                                      │   │
│  │  /data/baselines.json    ← Anomaly baselines (EMA profiles)        │   │
│  │  /data/anomalies/        ← Annotated camera frames (JPEG)          │   │
│  │  /data/reports/          ← Shift reports (Markdown)                 │   │
│  │  /data/learning/         ← Patterns, skills, event history (JSON)   │   │
│  │  /var/log/guardian/      └ Application logs                         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. Data Flows

### 2.1 Sensor Data Flow
```
GPIO Pin → SensorPoller.read_*()
         → SensorReading(machine_id, sensor_type, value, unit)
         → Buffer (max 100 readings)
         → feed_hermes() callback
         → AnomalyDetector.check_anomaly()
         → AnomalyResult(machine_id, score, z_score, severity)
         → AlertRouter.route_alert()
         → Telegram / Slack / Email / Log
```

### 2.2 Camera Data Flow
```
Camera → VisionMonitor.capture_frame()
       → Frame (numpy array)
       → detect_motion(frame, roi) → motion_score (0-1)
       → detect_machine_status(frame, machine_roi)
       → detect_safety_zone(frame, zone_roi)
       → DetectionResult(camera_id, detections)
       → [if anomaly] save_anomaly_frame()
       → [if anomaly] anomaly_callback()
       → AlertRouter.route_alert()
       → Telegram (with image) / Slack / Email
```

### 2.3 Learning Loop Flow
```
Event (alert, feedback, reading)
  → LearningManager.record_event()
  → events.json (persisted)
  → [periodic] analyze_patterns(days=7)
     → _detect_temporal_patterns()     → "Monday warmup"
     → _detect_correlation_patterns()  → "CNC1 temp → CNC2 temp"
     → _detect_failure_precursors()    → "Vibration ↑ before failure"
     → _detect_behavioral_patterns()   → "Night shift low alert rate"
  → Pattern(confidence, machines, conditions)
  → [if confidence > 80%] create_skill_from_pattern()
  → SkillDefinition (Hermes YAML)
  → Registered with Hermes Agent
  → [on feedback] refine_skill()
  → Improved thresholds, new suppressions
```

### 2.4 Operator Feedback Flow
```
Operator receives alert (Telegram)
  → Response: "This is normal" / "Real issue"
  → Hermes Agent parses feedback
  → hooks/on_alert.sh processes
  → LearningManager.record_event("feedback_received", was_real=...)
  → AnomalyDetector.learn_from_feedback(alert_id, was_real)
     → [false alarm] widen baseline, increase threshold
     → [real incident] tighten threshold, record pattern
  → [periodic] analyze_patterns()
  → New/updated skills from accumulated feedback
```

## 3. Hardware Layout

```
┌─────────────────────────────────────────────────────┐
│               RASPBERRY PI 5                         │
│                                                      │
│  ┌─────────┐  GPIO Header                            │
│  │         │  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐       │
│  │  CPU    │  │3V│  │SD│5V│  │  │  │  │  │GND│       │
│  │  BCM    │  ├──┼──┼──┼──┼──┼──┼──┼──┼──┼──┤       │
│  │  2712   │  │  │  │  │  │G │  │18│  │23│  │       │
│  │         │  ├──┼──┼──┼──┼──┼──┼──┼──┼──┼──┤       │
│  │  8GB    │  │  │  │  │  │24│G │  │  │  │  │       │
│  │  RAM    │  ├──┼──┼──┼──┼──┼──┼──┼──┼──┼──┤       │
│  │         │  │  │SC│SD│5V│  │  │CE│SI│SO│  │       │
│  │  ┌───┐  │  └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘       │
│  │  │CSI│──┼──→ Pi Camera Module v3                    │
│  │  └───┘  │                                          │
│  │         │  I2C: GPIO 2 (SDA), GPIO 3 (SCL)         │
│  │  ┌───┐  │    → MPU6050 #1 (0x68) Accelerometer     │
│  │  │USB│──┼──→ USB Webcam                             │
│  │  └───┘  │    → MPU6050 #2 (0x69) Accelerometer     │
│  │         │                                          │
│  │  ┌───┐  │  1-Wire: GPIO 4                          │
│  │  │ETH│  │    → DS18B20 #1 Temperature Probe         │
│  │  └───┘  │    → DS18B20 #2 Temperature Probe         │
│  │         │    → DS18B20 #3 Temperature Probe         │
│  │  ┌───┐  │    → DS18B20 #4 Temperature Probe         │
│  │  │PWR│  │                                          │
│  │  └───┘  │  SPI: GPIO 11 (SCLK), 10 (MOSI),         │
│  └─────────┘        9 (MISO), 8 (CE0)                │
│                      → MCP3008 ADC                      │
│                        → ACS712 #1 Current Sensor       │
│                        → ACS712 #2 Current Sensor       │
│                                                      │
│  GPIO 17 → HC-SR501 PIR Motion Sensor                │
│                                                      │
└─────────────────────────────────────────────────────┘

Factory Floor Layout:
┌─────────────────────────────────────────────────────────┐
│                    FACTORY FLOOR                         │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐    │
│  │  CNC #1     │  │  CNC #2     │  │  CNC #3      │    │
│  │  Camera 1   │  │  Camera 1   │  │  Camera 1    │    │
│  │  ┌────────┐ │  │  ┌────────┐ │  │  ┌─────────┐ │    │
│  │  │  Cam   │ │  │  │  Cam   │ │  │  │  Cam    │ │    │
│  │  └────────┘ │  │  └────────┘ │  │  └─────────┘ │    │
│  │  Temp/Vib  │  │  Temp/Vib  │  │  Temp/Vib   │    │
│  │  Current   │  │  Current   │  │  Current    │    │
│  └─────────────┘  └─────────────┘  └──────────────┘    │
│                                                          │
│  ╔══════════════════════════════════════════════════╗    │
│  ║         RESTRICTED ZONE (Camera 1)               ║    │
│  ║         PIR Motion Sensor: GPIO 17               ║    │
│  ╚══════════════════════════════════════════════════╝    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Conveyor Belt #1                                 │    │
│  │  Camera 2                                        │    │
│  │  ┌────────┐                                      │    │
│  │  │  Cam   │  ←→←→←→←←←← Motion Direction        │    │
│  │  └────────┘                                      │    │
│  │  Temp / Current                                  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  📷 Camera 1 (Pi Camera): Covers CNC machines + zone    │
│  📷 Camera 2 (USB): Covers conveyor belt                 │
│  🌡️ DS18B20 probes attached to motor housings           │
│  📳 MPU6050 mounted on machine frames                    │
│  ⚡ ACS712 in-line with motor power                      │
│  👁️ PIR covering restricted zone entrance               │
└─────────────────────────────────────────────────────────┘
```

## 4. Network Topology

```
┌──────────────────┐     ┌──────────────┐     ┌──────────────┐
│   Raspberry Pi   │     │   Ollama     │     │   Internet   │
│   (Factory)      │     │   (Local)    │     │              │
│                  │     │              │     │              │
│  Hermes Agent    │────▶│  LLM Server  │     │              │
│  Vision Monitor  │     │  Port 11434  │     │              │
│  Sensor Poller   │     │              │     │              │
│  Alert Router    │     └──────────────┘     │              │
│                  │                          │              │
│  ┌────────────┐ │     ┌──────────────┐      │              │
│  │   Ethernet │─┼────▶│  Router      │─────▶│  Telegram    │
│  │   / WiFi   │ │     │  Switch      │      │  API         │
│  └────────────┘ │     └──────────────┘      │              │
│                  │                          │  Slack       │
│                  │                          │  Webhook     │
│                  │                          │              │
│                  │                          │  SMTP        │
│                  │                          │  (Email)     │
└──────────────────┘                          └──────────────┘

Network Requirements:
  - Pi needs internet access for Telegram/Slack APIs (HTTPS outbound)
  - Ollama runs locally on Pi (no external network needed for LLM)
  - Optional: WiFi if Ethernet not available (wired preferred for stability)
  - Firewall: Allow outbound HTTPS (443), Ollama (11434) on localhost only
```

## 5. Software Stack

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| OS | Raspberry Pi OS | 64-bit Bookworm | Base operating system |
| Runtime | Python | 3.11+ | Application language |
| AI Agent | Hermes Agent | latest | Self-improving agent framework |
| LLM | Ollama | latest | Local LLM inference |
| Model | Llama 3 / Mistral / Gemma | latest | Language model |
| Vision | OpenCV | 4.x | Computer vision |
| Array | NumPy | latest | Numerical computing |
| Config | PyYAML | latest | Configuration parsing |
| GPIO | RPi.GPIO | latest | GPIO pin control |
| I2C | smbus2 | latest | I2C communication |
| SPI | spidev | latest | SPI communication |
| Telegram | python-telegram-bot | latest | Telegram notifications |
| Slack | slack-sdk | latest | Slack notifications |
| Service | systemd | latest | Process management |
| Logs | journald | latest | System logging |

## 6. Security Considerations

| Aspect | Mitigation |
|--------|-----------|
| Bot tokens | Environment variables, never in config files |
| Camera feeds | Local-only processing, no cloud upload |
| LLM inference | Fully local via Ollama (no data leaves Pi) |
| SSH access | Key-based auth, disable password login |
| Network | Firewall: only outbound HTTPS allowed |
| Systemd | NoNewPrivileges, ReadOnlyPaths for containment |
| Data | All data stored locally on Pi SD card |
| Updates | Regular apt updates + pip security updates |

## 7. Performance Characteristics

| Metric | Expected Value | Notes |
|--------|---------------|-------|
| CPU usage (idle) | 5-10% | Background polling only |
| CPU usage (active) | 40-60% | Camera + all sensors |
| RAM usage | 150-300 MB | Python + OpenCV + Ollama client |
| Power draw | 8-12 W | Pi 5 + cameras + sensors |
| Camera latency | < 100 ms | Frame capture + analysis |
| Sensor latency | < 50 ms | I2C/SPI read + buffer |
| Alert latency | < 5 s | Detection → Telegram delivery |
| Storage growth | ~500 MB/day | Anomaly frames + logs |
| Uptime target | 99.5% | systemd auto-restart on crash |
