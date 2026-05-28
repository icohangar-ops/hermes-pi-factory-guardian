"""
Computer Vision detector using OpenCV + YOLOv8-nano for Factory Guardian.

Processes camera frames for motion detection, object classification, and
zone monitoring. Supports Coral TPU acceleration.
"""

import logging
import time
import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DetectionSeverity(Enum):
    NORMAL = "normal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    class_name: str
    class_id: int


@dataclass
class DetectionEvent:
    camera_id: str
    timestamp: datetime
    severity: DetectionSeverity
    motion_score: float
    detections: List[BoundingBox] = field(default_factory=list)
    zone_name: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
            "motion_score": round(self.motion_score, 3),
            "detections": [
                {
                    "class": d.class_name,
                    "confidence": round(d.confidence, 3),
                    "bbox": [d.x1, d.y1, d.x2, d.y2],
                }
                for d in self.detections
            ],
            "zone": self.zone_name,
            "description": self.description,
        }


@dataclass
class Zone:
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    sensitivity: float  # 0.0 - 1.0
    restricted: bool = False
    min_contour_area: int = 200


class MotionDetector:
    """OpenCV-based motion detection with background subtraction."""

    def __init__(self, history: int = 500, var_threshold: int = 50,
                 detect_shadows: bool = True):
        self.history = history
        self.var_threshold = var_threshold
        self.detect_shadows = detect_shadows
        self._bg_subtractor = None
        self._zones: List[Zone] = []
        self._initialized = False

    def initialize(self):
        """Initialize the background subtractor."""
        try:
            import cv2
            self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
                history=self.history,
                varThreshold=self.var_threshold,
                detectShadows=self.detect_shadows
            )
            self._initialized = True
            logger.info("Motion detector initialized (history=%d, varThreshold=%d)",
                        self.history, self.var_threshold)
        except ImportError:
            logger.warning("OpenCV not available — motion detection disabled")
        except Exception as e:
            logger.error("Failed to initialize motion detector: %s", e)

    def add_zone(self, zone: Zone):
        """Add a monitoring zone."""
        self._zones.append(zone)

    def detect(self, frame, frame_id: int = 0) -> Tuple[float, List[BoundingBox]]:
        """Detect motion in frame. Returns (motion_score, zone_events)."""
        if not self._initialized or frame is None:
            return 0.0, []

        try:
            import cv2
            import numpy as np

            # Apply background subtraction
            fg_mask = self._bg_subtractor.apply(frame)
            if self.detect_shadows:
                # Remove shadows (value 127 in mask)
                _, fg_mask = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)

            # Find contours
            contours, _ = cv2.findContours(
                fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            total_motion_area = 0
            frame_area = frame.shape[0] * frame.shape[1]
            zone_detections = []

            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 200:
                    continue
                total_motion_area += area

                x, y, w, h = cv2.boundingRect(contour)
                cx, cy = x + w // 2, y + h // 2

                # Check which zone this motion is in
                for zone in self._zones:
                    if zone.x1 <= cx <= zone.x2 and zone.y1 <= cy <= zone.y2:
                        if area >= zone.min_contour_area:
                            score = area / frame_area
                            zone_detections.append(BoundingBox(
                                x1=x, y1=y, x2=x + w, y2=y + h,
                                confidence=score,
                                class_name="motion",
                                class_id=0,
                            ))

            # Overall motion score
            motion_score = min(total_motion_area / frame_area, 1.0)

            return round(motion_score, 4), zone_detections

        except Exception as e:
            logger.error("Motion detection error: %s", e)
            return 0.0, []


class YOLODetector:
    """YOLOv8-nano object detector with optional Coral TPU acceleration."""

    DEFAULT_CLASSES = [
        "person", "bicycle", "car", "motorcycle", "bus", "truck",
        "fire hydrant", "bench",
    ]

    def __init__(self, model_path: str = "yolov8n.pt",
                 confidence_threshold: float = 0.5,
                 use_tpu: bool = False):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.use_tpu = use_tpu
        self._model = None
        self._initialized = False

    def initialize(self):
        """Load the YOLO model."""
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)
            self._initialized = True
            logger.info("YOLO model loaded: %s (TPU: %s)",
                        self.model_path, self.use_tpu)
        except ImportError:
            logger.warning("ultralytics not installed — YOLO detection disabled")
        except Exception as e:
            logger.error("Failed to load YOLO model: %s", e)

    def detect(self, frame, roi: Optional[Tuple[int, int, int, int]] = None,
               target_classes: Optional[List[str]] = None) -> List[BoundingBox]:
        """Run YOLO detection on a frame (or ROI region)."""
        if not self._initialized or frame is None:
            return []

        try:
            import numpy as np

            # Crop to ROI if specified
            if roi is not None:
                x1, y1, x2, y2 = roi
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    return []
            else:
                crop = frame

            # Run inference
            results = self._model(crop, verbose=False, conf=self.confidence_threshold)
            detections = []

            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    cls_id = int(box.cls[0])
                    cls_name = self._model.names[cls_id]
                    conf = float(box.conf[0])

                    # Filter by target classes if specified
                    if target_classes and cls_name not in target_classes:
                        continue

                    # Get bounding box coordinates
                    xyxy = box.xyxy[0].cpu().numpy()
                    bx1, by1, bx2, by2 = int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])

                    # Offset back to full frame if ROI was used
                    if roi is not None:
                        bx1 += roi[0]
                        by1 += roi[1]
                        bx2 += roi[0]
                        by2 += roi[1]

                    detections.append(BoundingBox(
                        x1=bx1, y1=by1, x2=bx2, y2=by2,
                        confidence=conf,
                        class_name=cls_name,
                        class_id=cls_id,
                    ))

            return detections

        except Exception as e:
            logger.error("YOLO detection error: %s", e)
            return []


class VisionPipeline:
    """Combined motion detection + YOLO classification pipeline."""

    def __init__(self, config: dict = None):
        config = config or {}
        motion_config = config.get("motion_detection", {})
        yolo_config = config.get("yolo", {})

        self.motion_detector = MotionDetector(
            history=motion_config.get("background_history", 500),
            var_threshold=motion_config.get("variance_threshold", 50),
            detect_shadows=motion_config.get("shadow_detection", True),
        )

        self.yolo_detector = YOLODetector(
            model_path=yolo_config.get("model", "yolov8n.pt"),
            confidence_threshold=yolo_config.get("confidence_threshold", 0.5),
            use_tpu=yolo_config.get("use_tpu", False),
        )

        self.target_classes = yolo_config.get("target_classes", ["person", "truck"])
        self.zones: List[Zone] = []
        self._event_callbacks = []

    def initialize(self):
        """Initialize all vision components."""
        self.motion_detector.initialize()
        self.yolo_detector.initialize()

        # Default restricted zone (full frame)
        if not self.zones:
            self.zones = [
                Zone("default", 0, 0, 1920, 1080,
                     sensitivity=0.7, min_contour_area=300),
            ]

        for zone in self.zones:
            self.motion_detector.add_zone(zone)

        logger.info("Vision pipeline initialized with %d zones", len(self.zones))

    def add_zone(self, zone: Zone):
        """Add a monitoring zone."""
        self.zones.append(zone)
        self.motion_detector.add_zone(zone)

    def on_event(self, callback):
        """Register an event callback."""
        self._event_callbacks.append(callback)

    def process_frame(self, frame, camera_id: str = "cam_01") -> Optional[DetectionEvent]:
        """Process a single frame through the full pipeline."""
        # Step 1: Motion detection
        motion_score, motion_boxes = self.motion_detector.detect(frame)

        if motion_score < 0.01:
            return None  # No significant motion

        # Step 2: If motion detected, run YOLO on the motion regions
        yolo_detections = []
        for mbox in motion_boxes:
            roi = (mbox.x1, mbox.y1, mbox.x2, mbox.y2)
            dets = self.yolo_detector.detect(frame, roi=roi,
                                             target_classes=self.target_classes)
            yolo_detections.extend(dets)

        # Also run YOLO on the full frame if motion is significant
        if motion_score > 0.05:
            full_dets = self.yolo_detector.detect(frame,
                                                  target_classes=self.target_classes)
            # Deduplicate
            existing = {(d.class_name, d.x1, d.y1) for d in yolo_detections}
            for det in full_dets:
                if (det.class_name, det.x1, det.y1) not in existing:
                    yolo_detections.append(det)

        # Step 3: Determine severity and description
        severity = DetectionSeverity.NORMAL
        description = ""

        # Check for restricted zone violations
        for zone in self.zones:
            for det in yolo_detections:
                cx = (det.x1 + det.x2) // 2
                cy = (det.y1 + det.y2) // 2
                if zone.x1 <= cx <= zone.x2 and zone.y1 <= cy <= zone.y2:
                    if zone.restricted and det.class_name == "person":
                        severity = DetectionSeverity.CRITICAL
                        description = f"Person in restricted zone '{zone.name}'"
                    elif det.class_name in ("truck", "car", "motorcycle"):
                        severity = DetectionSeverity.HIGH
                        description = f"Vehicle detected near machinery in '{zone.name}'"
                    elif det.class_name == "person":
                        severity = DetectionSeverity.MEDIUM
                        description = f"Person detected in '{zone.name}'"

        if severity == DetectionSeverity.NORMAL and yolo_detections:
            severity = DetectionSeverity.LOW
            classes = [d.class_name for d in yolo_detections]
            description = f"Motion detected: {', '.join(set(classes))}"
        elif severity == DetectionSeverity.NORMAL and motion_score > 0.01:
            description = f"Unclassified motion (score: {motion_score:.3f})"

        event = DetectionEvent(
            camera_id=camera_id,
            timestamp=datetime.utcnow(),
            severity=severity,
            motion_score=motion_score,
            detections=yolo_detections,
            description=description,
        )

        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error("Event callback error: %s", e)

        return event


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import argparse
    parser = argparse.ArgumentParser(description="Factory Guardian Vision Pipeline")
    parser.add_argument("--test", action="store_true", help="Run test detection")
    args = parser.parse_args()

    pipeline = VisionPipeline()
    pipeline.initialize()
    pipeline.add_zone(Zone("restricted", 0, 0, 640, 480,
                           sensitivity=0.85, restricted=True, min_contour_area=200))

    if args.test:
        print("=== Vision Pipeline Test Mode ===")
        try:
            import numpy as np
            test_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
            event = pipeline.process_frame(test_frame, camera_id="test_cam")
            if event:
                print(f"  Event: {event.description}")
                print(f"  Severity: {event.severity.value}")
                print(f"  Motion score: {event.motion_score}")
            else:
                print("  No event detected (expected for random frames)")
        except ImportError:
            print("  numpy not available — cannot generate test frame")
    else:
        print("Vision pipeline ready. Use --test for quick test.")
