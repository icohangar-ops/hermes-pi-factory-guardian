---
name: camera_monitor
version: 1.0.0
description: >
  Controls camera scheduling, manages motion detection sensitivity per zone,
  triggers YOLO classification on motion events, archives incident footage,
  and adapts detection parameters based on time-of-day and lighting conditions.
triggers:
  - motion_detected
  - yolo_classification_complete
  - scheduled_patrol
  - incident_footage_requested
inputs:
  - camera_config: list of cameras with zones, sensitivity, schedules
  - motion_frame: numpy array from OpenCV background subtractor
  - yolo_detections: list of bounding boxes, classes, confidences
outputs:
  - camera_event: motion detected with classification results
  - archived_footage: path to saved video clip of incident
  - patrol_report: scheduled camera sweep summary
---

# Camera Monitor Skill

## Purpose

This skill manages the entire camera pipeline — from capture scheduling through motion detection to YOLO-based object classification and incident archiving. It optimizes camera performance based on environmental conditions and adapts sensitivity to reduce false triggers.

## How It Works

### 1. Camera Management

Supports multiple cameras with independent configurations:

```yaml
cameras:
  - id: "cam_01"
    name: "CNC Area North"
    type: "rpi_camera_v3"
    resolution: [1920, 1080]
    fps: 15
    night_mode: true
    recording: true
    retention_days: 14
  - id: "cam_02"
    name: "Loading Dock"
    type: "usb_webcam"
    resolution: [1280, 720]
    fps: 10
    night_mode: false
    recording: false
    retention_days: 7
```

### 2. Motion Detection Pipeline

Uses OpenCV background subtraction with adaptive learning rate:

```python
# Background subtractor adapts to lighting changes
bg_subtractor = cv2.createBackgroundSubtractorMOG2(
    history=500,
    varThreshold=50,
    detectShadows=True
)

# Per-zone sensitivity (adapted based on time of day)
zones = {
    "restricted_zone": {"sensitivity": 0.85, "min_contour_area": 200},
    "work_area": {"sensitivity": 0.6, "min_contour_area": 500},
    "entrance": {"sensitivity": 0.7, "min_contour_area": 300}
}
```

**Adaptive Sensitivity**: The skill adjusts motion thresholds based on:
- **Time of day**: Higher sensitivity at night, lower during active shifts
- **Recent false positive rate**: Temporarily lowers sensitivity after 3+ false positives
- **Lighting changes**: When luminance delta exceeds threshold, triggers background model refresh
- **Scheduled activities**: Reduces sensitivity during known activity windows (e.g., shift changes)

### 3. YOLO Classification

On motion detection, the skill triggers YOLOv8-nano classification:

```python
# Load YOLO model (optimized for Coral TPU)
model = YOLO("yolov8n_edgetpu.tflite")

# Classes of interest for factory monitoring
TARGET_CLASSES = [
    "person",        # Unauthorized access detection
    "forklift",      # Vehicle proximity to machinery
    "fire",          # Safety hazard (if available in model)
    "smoke",         # Safety hazard
    "sparks",        # Equipment malfunction indicator
]
```

**Processing flow**:
1. Motion detected in zone → extract bounding box region
2. Run YOLO inference on region (full frame if motion is large)
3. Filter results for target classes
4. If restricted zone + person detected → IMMEDIATE CRITICAL alert
5. If work area + vehicle detected near machine → HIGH alert for collision risk
6. If no target class matched → log as "unclassified motion"

### 4. Incident Archiving

When an alert is triggered, the skill saves a video clip:

```python
# Save 30 seconds before and 2 minutes after the event
clip_duration_before = 30  # seconds
clip_duration_after = 120   # seconds

# Stored with metadata for easy retrieval
archive_path = f"/data/footage/{date}/{camera_id}/{event_id}.mp4"
# Sidecar JSON with detection results, timestamps, bounding boxes
metadata_path = f"/data/footage/{date}/{camera_id}/{event_id}.json"
```

### 5. Scheduled Patrols

The skill performs periodic camera sweeps even when no motion is detected:

- Every 15 minutes during night shift
- Every 30 minutes during day shift
- Captures a frame from each camera and runs YOLO classification
- Generates a patrol report with any unexpected findings

### 6. Self-Improvement via Hermes Loop

The skill learns from operator feedback:

```
Experience: "Motion detected at loading dock during rainstorm — 12 false positives"
  → Creates rule: {condition: "high_luminance_variance + rain_detected",
                   action: "reduce_loading_dock_sensitivity_by_30pct",
                   auto_revert_after: "2h"}
```

## Configuration

```yaml
camera_monitor:
  motion_detection:
    background_history: 500
    variance_threshold: 50
    min_contour_area: 200
    shadow_detection: true

  yolo:
    model: "yolov8n_edgetpu.tflite"
    confidence_threshold: 0.5
    target_classes: ["person", "forklift", "fire", "smoke"]

  archival:
    clip_before_seconds: 30
    clip_after_seconds: 120
    retention_days: 14
    storage_path: "/data/footage/"

  patrols:
    night_shift_interval_minutes: 15
    day_shift_interval_minutes: 30
```
