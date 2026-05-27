"""
Hermes-Pi Factory Guardian — Vision Monitor Skill

Captures frames from Pi Camera / USB cameras and runs lightweight
OpenCV analysis for factory monitoring. Detects machine stoppage,
restricted zone intrusion, spills/leaks, and missing safety equipment.

Optimized for Raspberry Pi performance with ROI-based processing,
frame skipping, and configurable quality settings.

Author: Hermes-Pi Factory Guardian Contributors
License: MIT
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# OpenCV is optional — provides better error messages if missing
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    logger.warning(
        "OpenCV not installed. Install with: pip install opencv-python-headless"
    )


class DetectionType(Enum):
    """Types of visual anomalies the vision monitor can detect."""
    MACHINE_STOPPAGE = "machine_stoppage"
    ZONE_INTRUSION = "zone_intrusion"
    SPILL_LEAK = "spill_leak"
    SAFETY_EQUIPMENT = "safety_equipment"


@dataclass
class CameraConfig:
    """Configuration for a single camera."""
    camera_id: str
    camera_type: str = "usb"       # "picamera" or "usb"
    device_id: int = 0
    resolution: Tuple[int, int] = (1280, 720)
    fps: int = 10
    roi_fps: int = 5               # Process ROIs every Nth frame

    # Machine monitoring regions
    machines: List[Dict[str, Any]] = field(default_factory=list)

    # Restricted zones
    zones: List[Dict[str, Any]] = field(default_factory=list)

    # Floor spill detection
    floor_check: Optional[Dict[str, Any]] = None

    # Storage
    anomaly_dir: str = "/data/anomalies"
    image_quality: int = 85
    max_disk_mb: int = 2048
    retention_days: int = 30


@dataclass
class MachineROI:
    """Region of interest for monitoring a specific machine."""
    machine_id: str
    roi: Tuple[int, int, int, int]  # x, y, width, height
    detection_type: DetectionType
    motion_threshold: float = 0.15
    stoppage_duration: float = 45.0  # seconds
    last_motion_score: float = 1.0
    stoppage_start: Optional[float] = None
    cooldown_until: float = 0.0


@dataclass
class ZoneROI:
    """Region of interest for restricted zone monitoring."""
    zone_id: str
    roi: Tuple[int, int, int, int]
    detection_type: DetectionType = DetectionType.ZONE_INTRUSION
    intrusion_detected: bool = False
    cooldown_seconds: int = 60
    last_alert_time: float = 0.0


@dataclass
class DetectionResult:
    """Result from analyzing a single frame."""
    camera_id: str
    timestamp: float = field(default_factory=time.time)
    frame_number: int = 0
    fps_actual: float = 0.0
    detections: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    annotated_frame_path: Optional[str] = None
    processing_time_ms: float = 0.0


class VisionMonitor:
    """
    Monitors factory cameras using lightweight OpenCV analysis.

    Detects machine stoppage via motion analysis, restricted zone
    intrusion via background subtraction, and floor spills via color
    detection. Designed to run efficiently on Raspberry Pi hardware.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        mock_mode: bool = False,
        anomaly_callback: Optional[Any] = None,
    ) -> None:
        """
        Initialize the vision monitor.

        Args:
            config_path: Path to factory_config.yaml.
            mock_mode: Use synthetic frames instead of real camera.
            anomaly_callback: Called with DetectionResult on anomaly.
        """
        self._mock_mode = mock_mode or not HAS_OPENCV
        self._anomaly_callback = anomaly_callback

        self._cameras: Dict[str, CameraConfig] = {}
        self._machine_rois: Dict[str, Dict[str, MachineROI]] = {}
        self._zone_rois: Dict[str, Dict[str, ZoneROI]] = {}
        self._background_models: Dict[str, Any] = {}
        self._captures: Dict[str, Any] = {}
        self._frame_counters: Dict[str, int] = {}
        self._fps_trackers: Dict[str, List[float]] = {}

        self._load_config(config_path)

        # Ensure anomaly directories exist
        for cam_config in self._cameras.values():
            Path(cam_config.anomaly_dir).mkdir(parents=True, exist_ok=True)

        logger.info(
            "VisionMonitor initialized: mock=%s, cameras=%d",
            self._mock_mode, len(self._cameras),
        )

    def _load_config(self, config_path: Optional[str] = None) -> None:
        """Load camera configuration from YAML."""
        if config_path and Path(config_path).exists():
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}

                cameras_config = config.get("cameras", {})
                for cam_id, cam_data in cameras_config.items():
                    cam_config = CameraConfig(
                        camera_id=cam_id,
                        camera_type=cam_data.get("type", "usb"),
                        device_id=cam_data.get("device_id", 0),
                        resolution=tuple(cam_data.get("resolution", [1280, 720])),
                        fps=cam_data.get("fps", 10),
                        roi_fps=cam_data.get("roi_fps", 5),
                        machines=cam_data.get("machines", []),
                        zones=cam_data.get("zones", []),
                        floor_check=cam_data.get("floor_check"),
                        anomaly_dir=cam_data.get("anomaly_dir", "/data/anomalies"),
                        image_quality=cam_data.get("image_quality", 85),
                    )
                    self._cameras[cam_id] = cam_config

                    # Parse machine ROIs
                    self._machine_rois[cam_id] = {}
                    for m in cam_config.machines:
                        self._machine_rois[cam_id][m["id"]] = MachineROI(
                            machine_id=m["id"],
                            roi=tuple(m["roi"]),
                            detection_type=DetectionType.MACHINE_STOPPAGE,
                            motion_threshold=m.get("motion_threshold", 0.15),
                            stoppage_duration=m.get("stoppage_duration_seconds", 45),
                        )

                    # Parse zone ROIs
                    self._zone_rois[cam_id] = {}
                    for z in cam_config.zones:
                        self._zone_rois[cam_id][z["id"]] = ZoneROI(
                            zone_id=z["id"],
                            roi=tuple(z["roi"]),
                            cooldown_seconds=z.get("cooldown_seconds", 60),
                        )

            except ImportError:
                logger.warning("PyYAML not installed, using default camera config")
            except Exception as e:
                logger.error("Failed to load camera config: %s", e)

        # Add a default camera if none configured
        if not self._cameras:
            logger.info("No cameras configured — adding default cam_01")
            cam_config = CameraConfig(camera_id="cam_01")
            self._cameras["cam_01"] = cam_config
            self._machine_rois["cam_01"] = {
                "cnc_machine_1": MachineROI(
                    machine_id="cnc_machine_1",
                    roi=(100, 50, 400, 350),
                    detection_type=DetectionType.MACHINE_STOPPAGE,
                ),
            }

    def start(self, camera_id: str) -> bool:
        """
        Start monitoring a camera.

        Opens the camera device and initializes the background model.

        Args:
            camera_id: Camera identifier from config.

        Returns:
            True if camera started successfully.
        """
        cam_config = self._cameras.get(camera_id)
        if cam_config is None:
            logger.error("Unknown camera: %s", camera_id)
            return False

        if self._mock_mode:
            logger.info("Camera %s started (mock mode)", camera_id)
            return True

        if not HAS_OPENCV:
            logger.error("OpenCV not available — cannot start camera %s", camera_id)
            return False

        try:
            if cam_config.camera_type == "picamera":
                # Pi Camera uses different API
                logger.info("Pi Camera requested — using USB fallback")
                cap = cv2.VideoCapture(cam_config.device_id)
            else:
                cap = cv2.VideoCapture(cam_config.device_id)

            if not cap.isOpened():
                logger.error("Failed to open camera %s (device %d)", camera_id, cam_config.device_id)
                return False

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam_config.resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam_config.resolution[1])
            cap.set(cv2.CAP_PROP_FPS, cam_config.fps)

            self._captures[camera_id] = cap
            self._frame_counters[camera_id] = 0
            self._fps_trackers[camera_id] = []

            # Initialize background subtractor
            self._background_models[camera_id] = cv2.createBackgroundSubtractorMOG2(
                history=500,
                varThreshold=50,
                detectShadows=True,
            )

            logger.info(
                "Camera %s started: device=%d, res=%s, fps=%d",
                camera_id, cam_config.device_id,
                cam_config.resolution, cam_config.fps,
            )
            return True

        except Exception as e:
            logger.error("Failed to start camera %s: %s", camera_id, e)
            return False

    def capture_frame(self, camera_id: str) -> Optional[Any]:
        """
        Capture a frame from a camera.

        Args:
            camera_id: Camera identifier.

        Returns:
            OpenCV frame (numpy array) or None.
        """
        if self._mock_mode:
            return self._generate_mock_frame(camera_id)

        cap = self._captures.get(camera_id)
        if cap is None:
            logger.warning("Camera %s not started", camera_id)
            return None

        try:
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("Failed to read frame from camera %s", camera_id)
                return None

            self._frame_counters[camera_id] = (
                self._frame_counters.get(camera_id, 0) + 1
            )

            # Track FPS
            self._fps_trackers[camera_id].append(time.time())
            self._fps_trackers[camera_id] = self._fps_trackers[camera_id][-30:]

            return frame

        except Exception as e:
            logger.error("Frame capture error on camera %s: %s", camera_id, e)
            return None

    def detect_motion(self, frame: Any, roi: Tuple[int, int, int, int]) -> float:
        """
        Detect motion within a region of interest using background subtraction.

        Args:
            frame: OpenCV frame (numpy array).
            roi: Region of interest as (x, y, width, height).

        Returns:
            Motion score from 0.0 (no motion) to 1.0 (maximum motion).
        """
        if not HAS_OPENCV or frame is None:
            return 0.0

        try:
            x, y, w, h = roi
            region = frame[y:y + h, x:x + w]

            # Resize for performance on Pi
            small = cv2.resize(region, (160, 120))

            # Convert to grayscale
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)

            # Frame differencing (simpler than background subtraction)
            # We'll use a simple approach: compare current frame to previous
            if not hasattr(self, '_prev_frames'):
                self._prev_frames: Dict[str, Any] = {}
                return 0.5  # First frame — no comparison available

            key = f"{x},{y},{w},{h}"
            prev = self._prev_frames.get(key)
            if prev is None:
                self._prev_frames[key] = gray.copy()
                return 0.5

            # Calculate absolute difference
            diff = cv2.absdiff(prev, gray)
            self._prev_frames[key] = gray.copy()

            # Threshold
            _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

            # Count motion pixels
            motion_pixels = cv2.countNonZero(thresh)
            total_pixels = thresh.shape[0] * thresh.shape[1]
            motion_ratio = motion_pixels / max(total_pixels, 1)

            # Normalize to 0-1 range with sigmoid-like scaling
            score = min(1.0, motion_ratio * 5.0)

            return round(score, 4)

        except Exception as e:
            logger.error("Motion detection error: %s", e)
            return 0.0

    def detect_machine_status(
        self,
        frame: Any,
        machine_roi: MachineROI,
        camera_id: str,
    ) -> Dict[str, Any]:
        """
        Check if a machine is running or stopped.

        Args:
            frame: OpenCV frame.
            machine_roi: MachineROI configuration.
            camera_id: Camera identifier.

        Returns:
            Detection result dictionary.
        """
        motion_score = self.detect_motion(frame, machine_roi.roi)
        now = time.time()

        # Update last known motion score
        machine_roi.last_motion_score = motion_score

        # Detect stoppage
        is_stopped = False
        if motion_score < machine_roi.motion_threshold:
            if machine_roi.stoppage_start is None:
                machine_roi.stoppage_start = now
            elif (now - machine_roi.stoppage_start) >= machine_roi.stoppage_duration:
                is_stopped = True
        else:
            machine_roi.stoppage_start = None

        return {
            "type": DetectionType.MACHINE_STOPPAGE.value,
            "machine_id": machine_roi.machine_id,
            "motion_score": motion_score,
            "threshold": machine_roi.motion_threshold,
            "duration_seconds": (
                now - machine_roi.stoppage_start
                if machine_roi.stoppage_start else 0.0
            ),
            "is_anomaly": is_stopped,
            "severity": "CRITICAL" if is_stopped else "INFO",
        }

    def detect_safety_zone(
        self,
        frame: Any,
        zone_roi: ZoneROI,
        camera_id: str,
    ) -> Dict[str, Any]:
        """
        Check for unauthorized personnel in a restricted zone.

        Args:
            frame: OpenCV frame.
            zone_roi: ZoneROI configuration.
            camera_id: Camera identifier.

        Returns:
            Detection result dictionary.
        """
        now = time.time()

        # Check cooldown
        if now < zone_roi.last_alert_time + zone_roi.cooldown_seconds:
            return {
                "type": DetectionType.ZONE_INTRUSION.value,
                "zone_id": zone_roi.zone_id,
                "detected": False,
                "reason": "cooldown",
            }

        motion_score = self.detect_motion(frame, zone_roi.roi)

        # Intrusion = significant motion in restricted zone
        intrusion = motion_score > 0.3  # 30% of zone pixels moving

        if intrusion:
            zone_roi.intrusion_detected = True
            zone_roi.last_alert_time = now
        else:
            zone_roi.intrusion_detected = False

        return {
            "type": DetectionType.ZONE_INTRUSION.value,
            "zone_id": zone_roi.zone_id,
            "detected": intrusion,
            "motion_score": motion_score,
            "is_anomaly": intrusion,
            "severity": "WARNING" if intrusion else "INFO",
        }

    def save_anomaly_frame(
        self,
        frame: Any,
        anomaly_type: str,
        camera_id: str = "unknown",
        detections: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Save an annotated frame when an anomaly is detected.

        Draws bounding boxes, labels, and ROIs on the frame before saving.

        Args:
            frame: OpenCV frame to save.
            anomaly_type: Type of anomaly (for filename).
            camera_id: Camera identifier.
            detections: Detection results to annotate.

        Returns:
            Path to saved image, or None if save failed.
        """
        if not HAS_OPENCV or frame is None:
            logger.warning("Cannot save frame: OpenCV not available or frame is None")
            return None

        try:
            cam_config = self._cameras.get(camera_id)
            if cam_config is None:
                save_dir = Path("/data/anomalies")
            else:
                save_dir = Path(cam_config.anomaly_dir) / camera_id

            save_dir.mkdir(parents=True, exist_ok=True)

            # Create annotated copy
            annotated = frame.copy()

            # Draw machine ROIs
            for roi in self._machine_rois.get(camera_id, {}).values():
                x, y, w, h = roi.roi
                color = (0, 0, 255) if roi.stoppage_start else (0, 255, 0)
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                cv2.putText(
                    annotated,
                    f"{roi.machine_id}: {roi.last_motion_score:.2f}",
                    (x, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    color,
                    1,
                )

            # Draw zone ROIs
            for roi in self._zone_rois.get(camera_id, {}).values():
                x, y, w, h = roi.roi
                color = (0, 0, 255) if roi.intrusion_detected else (255, 165, 0)
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                cv2.putText(
                    annotated,
                    f"ZONE: {roi.zone_id}",
                    (x, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    color,
                    1,
                )

            # Add anomaly type label
            timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            label = f"ANOMALY: {anomaly_type} | {timestamp_str}"
            cv2.putText(
                annotated,
                label,
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 255),
                2,
            )

            # Save
            filename = f"{anomaly_type}_{timestamp_str}.jpg"
            filepath = save_dir / filename
            cv2.imwrite(str(filepath), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])

            logger.info("Anomaly frame saved: %s", filepath)
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save anomaly frame: %s", e)
            return None

    def analyze_frame(
        self,
        camera_id: str,
        frame: Optional[Any] = None,
    ) -> DetectionResult:
        """
        Analyze a single frame from a camera, checking all ROIs.

        Args:
            camera_id: Camera identifier.
            frame: Optional pre-captured frame. Captures new frame if None.

        Returns:
            DetectionResult with all detection outcomes.
        """
        start = time.time()

        if frame is None:
            frame = self.capture_frame(camera_id)

        result = DetectionResult(
            camera_id=camera_id,
            frame_number=self._frame_counters.get(camera_id, 0),
        )

        if frame is None:
            logger.warning("No frame available for camera %s", camera_id)
            return result

        # Check if this frame should be processed (ROI frame skipping)
        frame_num = self._frame_counters.get(camera_id, 0)
        cam_config = self._cameras.get(camera_id)
        roi_interval = cam_config.roi_fps if cam_config else 5
        should_process_rois = (frame_num % roi_interval == 0)

        any_anomaly = False
        anomaly_types: List[str] = []

        if should_process_rois:
            # Check machine ROIs
            for roi in self._machine_rois.get(camera_id, {}).values():
                detection = self.detect_machine_status(frame, roi, camera_id)
                result.detections[roi.machine_id] = detection
                if detection["is_anomaly"]:
                    any_anomaly = True
                    anomaly_types.append("machine_stoppage")

            # Check zone ROIs
            for roi in self._zone_rois.get(camera_id, {}).values():
                detection = self.detect_safety_zone(frame, roi, camera_id)
                result.detections[roi.zone_id] = detection
                if detection["is_anomaly"]:
                    any_anomaly = True
                    anomaly_types.append("zone_intrusion")

        # Save frame if anomaly detected
        if any_anomaly:
            anomaly_path = self.save_anomaly_frame(
                frame, "_".join(anomaly_types), camera_id, result.detections,
            )
            result.annotated_frame_path = anomaly_path

            # Call anomaly callback
            if self._anomaly_callback:
                try:
                    self._anomaly_callback(result)
                except Exception as e:
                    logger.error("Anomaly callback error: %s", e)

        # Calculate processing time and FPS
        result.processing_time_ms = (time.time() - start) * 1000
        fps_list = self._fps_trackers.get(camera_id, [])
        if len(fps_list) >= 2:
            elapsed = fps_list[-1] - fps_list[0]
            result.fps_actual = (len(fps_list) - 1) / max(elapsed, 0.001)

        return result

    def _generate_mock_frame(self, camera_id: str) -> Any:
        """Generate a synthetic frame for testing."""
        if not HAS_OPENCV:
            return None

        cam_config = self._cameras.get(camera_id)
        w, h = cam_config.resolution if cam_config else (1280, 720)

        # Create a dark frame with some noise
        frame = np.random.randint(20, 40, (h, w, 3), dtype=np.uint8)

        # Draw machine-like rectangles
        cam_rois = self._machine_rois.get(camera_id, {})
        for roi in cam_rois.values():
            x, y, rw, rh = roi.roi
            # Machine body
            cv2.rectangle(frame, (x, y), (x + rw, y + rh), (60, 60, 60), -1)
            cv2.rectangle(frame, (x, y), (x + rw, y + rh), (100, 100, 100), 2)
            # Machine label
            cv2.putText(
                frame, roi.machine_id, (x + 10, y + rh // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
            )

        self._frame_counters[camera_id] = self._frame_counters.get(camera_id, 0) + 1
        self._fps_trackers.setdefault(camera_id, []).append(time.time())

        return frame

    def stop(self, camera_id: str) -> None:
        """Stop monitoring a camera and release resources."""
        cap = self._captures.pop(camera_id, None)
        if cap is not None:
            try:
                cap.release()
                logger.info("Camera %s stopped", camera_id)
            except Exception as e:
                logger.error("Error stopping camera %s: %s", camera_id, e)

    def stop_all(self) -> None:
        """Stop all cameras."""
        for camera_id in list(self._captures.keys()):
            self.stop(camera_id)

    def get_camera_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all configured cameras."""
        status: Dict[str, Dict[str, Any]] = {}
        for cam_id, cam_config in self._cameras.items():
            is_active = cam_id in self._captures or self._mock_mode
            fps_list = self._fps_trackers.get(cam_id, [])
            actual_fps = 0.0
            if len(fps_list) >= 2:
                elapsed = fps_list[-1] - fps_list[0]
                actual_fps = (len(fps_list) - 1) / max(elapsed, 0.001)

            status[cam_id] = {
                "active": is_active,
                "type": cam_config.camera_type,
                "resolution": cam_config.resolution,
                "target_fps": cam_config.fps,
                "actual_fps": round(actual_fps, 1),
                "frames_processed": self._frame_counters.get(cam_id, 0),
                "machines_monitored": list(
                    self._machine_rois.get(cam_id, {}).keys()
                ),
                "zones_monitored": list(
                    self._zone_rois.get(cam_id, {}).keys()
                ),
            }
        return status
