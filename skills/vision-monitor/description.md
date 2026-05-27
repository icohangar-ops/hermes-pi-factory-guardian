# Hermes Skill: vision_monitor

## Metadata
- **Name:** vision_monitor
- **Version:** 1.0.0
- **Category:** monitoring
- **Depends on:** (none — base data provider, feeds anomaly_detection)

## Description

Captures frames from Pi Camera or USB cameras pointed at factory machinery and runs lightweight computer vision analysis using OpenCV. Detects machine status, unauthorized personnel in restricted zones, spills/leaks, and missing safety equipment — all on Raspberry Pi hardware.

Optimized for low-power operation: background subtraction instead of heavy DNNs, configurable frame rates, and ROI-based processing to minimize CPU load.

## Detection Capabilities

### 1. Machine Stoppage Detection
- **Method:** Frame differencing + optical flow magnitude analysis
- **Logic:** Running machinery has consistent motion patterns. Sudden motion stop = potential issue.
- **Output:** Motion score (0.0–1.0), normalized against baseline activity for that machine
- **Trigger:** Score drops below threshold for N consecutive seconds

### 2. Restricted Zone Intrusion
- **Method:** Background subtraction (MOG2) within defined Region of Interest (ROI)
- **Logic:** Any foreground pixels detected in restricted zone polygon = intrusion
- **Output:** Boolean detection + bounding box of intruder
- **Trigger:** Person detected in zone for more than 2 seconds

### 3. Spill / Leak Detection
- **Method:** Color-based detection in floor ROIs
- **Logic:** Unusual dark/wet patches on known floor areas
- **Output:** Contour area (pixels), confidence score
- **Trigger:** Detected area exceeds minimum threshold

### 4. Safety Equipment Check
- **Method:** Color detection (high-vis vests = fluorescent yellow/green, hard hats = white/orange)
- **Logic:** Workers in machinery areas must wear safety equipment
- **Output:** Boolean per detected person, missing equipment list
- **Trigger:** Person detected without required equipment

## Camera Configuration

```yaml
cameras:
  cam_01:
    type: "picamera"  # or "usb"
    device_id: 0
    resolution: [1280, 720]  # Reduced for Pi performance
    fps: 10  # Lower than default for CPU headroom
    roi_fps: 5  # Process ROIs every 5th frame
    
    machines:
      - id: "cnc_machine_1"
        roi: [100, 50, 400, 350]  # [x, y, width, height]
        type: "stoppage"
        motion_threshold: 0.15
        stoppage_duration_seconds: 45
        
      - id: "cnc_machine_2"
        roi: [500, 50, 400, 350]
        type: "stoppage"
        motion_threshold: 0.12
        stoppage_duration_seconds: 30

    zones:
      - id: "restricted_bay_3"
        roi: [0, 400, 1280, 320]
        type: "intrusion"
        cooldown_seconds: 60  # Don't re-alert within 60s

    floor_check:
      enabled: true
      roi: [0, 500, 1280, 220]
      type: "spill"
```

## Input Schema

```json
{
  "camera_id": "cam_01",
  "action": "analyze",
  "timestamp": "2025-06-15T14:32:17Z"
}
```

## Output Schema

```json
{
  "camera_id": "cam_01",
  "timestamp": "2025-06-15T14:32:17Z",
  "frame_number": 42891,
  "fps_actual": 9.7,
  "detections": {
    "cnc_machine_1": {
      "type": "stoppage",
      "motion_score": 0.02,
      "threshold": 0.15,
      "duration_seconds": 45,
      "is_anomaly": true,
      "severity": "CRITICAL"
    },
    "cnc_machine_2": {
      "type": "stoppage",
      "motion_score": 0.28,
      "threshold": 0.12,
      "duration_seconds": 0,
      "is_anomaly": false
    },
    "restricted_bay_3": {
      "type": "intrusion",
      "detected": false
    },
    "floor_check": {
      "type": "spill",
      "detected": false
    }
  },
  "annotated_frame": "/data/frames/cam01_annotated_20250615_143217.jpg"
}
```

## Performance Optimization

| Technique | Benefit |
|-----------|---------|
| ROI-only processing | Only analyze relevant regions, skip background |
| Frame skipping | Process every Nth frame for non-critical checks |
| Resolution reduction | Downscale before analysis (640x360 is sufficient) |
| Background model aging | MOG2 adapts to slow lighting changes automatically |
| Async capture | Camera thread separate from analysis thread |
| JPEG quality control | Adjustable quality for anomaly frame saves |

## Anomaly Frame Saving

When an anomaly is detected, the system saves:
1. **Raw frame** — Original camera capture
2. **Annotated frame** — With bounding boxes, ROIs, and labels drawn
3. **Metadata JSON** — Detection details alongside the images

Storage management:
```yaml
storage:
  anomaly_frames_dir: "/data/anomalies/"
  max_disk_usage_mb: 2048  # Auto-cleanup when exceeded
  retention_days: 30
  image_quality: 85  # JPEG quality 1-100
```

## Error Handling

- Camera disconnect: Retry connection every 10 seconds, log error
- Low light: Warn if frame average brightness below threshold
- Analysis timeout: Skip frame if processing takes > 200ms (maintain FPS)
- Storage full: Delete oldest anomaly frames first

## Hardware Notes

- Pi Camera Module v3 supports 1080p@30fps but we use 720p@10fps for headroom
- USB cameras: Tested with Logitech C270, C920 — both work well
- Multiple cameras: Use USB hub, max 2 simultaneous (Pi CPU limit)
- IR cameras: Supported if using Pi NoIR module + IR illuminators for night monitoring
