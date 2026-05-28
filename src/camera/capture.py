"""
Camera capture module for Raspberry Pi Factory Guardian.

Supports RPi Camera Module v3 and USB webcams. Provides frame capture,
video recording, and frame streaming for the vision pipeline.
"""

import time
import os
import logging
import json
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class CameraType(Enum):
    RPI_CAMERA_V3 = "rpi_camera_v3"
    USB_WEBCAM = "usb_webcam"


@dataclass
class CameraConfig:
    camera_id: str
    name: str
    camera_type: CameraType
    resolution: Tuple[int, int] = (1920, 1080)
    fps: int = 15
    night_mode: bool = False
    recording: bool = True
    retention_days: int = 14
    storage_path: str = "/data/footage"


@dataclass
class FrameEvent:
    """A captured frame with metadata."""
    camera_id: str
    timestamp: datetime
    frame_data: bytes
    width: int
    height: int
    motion_score: float = 0.0


class CameraCapture:
    """Camera capture interface for Raspberry Pi."""

    def __init__(self, config: CameraConfig):
        self.config = config
        self._camera = None
        self._initialized = False
        self._recording = False
        self._video_writer = None
        self._callbacks: list = []
        self._frame_buffer: list = []
        self._buffer_duration_seconds = 30  # Pre-event buffer
        self._running = False

    def initialize(self) -> bool:
        """Initialize the camera based on type."""
        try:
            if self.config.camera_type == CameraType.RPI_CAMERA_V3:
                self._init_rpi_camera()
            elif self.config.camera_type == CameraType.USB_WEBCAM:
                self._init_usb_camera()

            if self._camera is not None:
                self._initialized = True
                logger.info("Camera '%s' (%s) initialized at %dx%d @ %d fps",
                            self.config.camera_id, self.config.camera_type.value,
                            *self.config.resolution, self.config.fps)
            return self._initialized
        except Exception as e:
            logger.error("Failed to initialize camera '%s': %s",
                         self.config.camera_id, e)
            return False

    def _init_rpi_camera(self):
        """Initialize Raspberry Pi Camera Module v3 using picamera2."""
        try:
            from picamera2 import Picamera2
            self._camera = Picamera2()
            config = self._camera.create_video_configuration(
                main={"size": self.config.resolution, "format": "XRGB8888"},
                encode="main",
                controls={
                    "FrameDurationLimits": (1000000 // self.config.fps,) * 2,
                }
            )
            if self.config.night_mode:
                config["controls"]["AeEnable"] = True
                config["controls"]["AwbEnable"] = True
            self._camera.configure(config)
            self._camera.start()
            logger.info("Pi Camera v3 started for '%s'", self.config.camera_id)
        except ImportError:
            logger.warning("picamera2 not available — using OpenCV fallback")
            self._init_opencv_fallback()
        except Exception as e:
            logger.warning("Pi Camera v3 init failed: %s — using OpenCV fallback", e)
            self._init_opencv_fallback()

    def _init_usb_camera(self):
        """Initialize USB webcam via OpenCV."""
        self._init_opencv_fallback()

    def _init_opencv_fallback(self):
        """Fallback to OpenCV VideoCapture."""
        try:
            import cv2
            self._camera = cv2.VideoCapture(0)
            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.resolution[0])
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.resolution[1])
            self._camera.set(cv2.CAP_PROP_FPS, self.config.fps)
            logger.info("OpenCV VideoCapture initialized for '%s'",
                        self.config.camera_id)
        except Exception as e:
            logger.error("OpenCV fallback also failed: %s", e)

    def capture_frame(self) -> Optional[object]:
        """Capture a single frame (returns OpenCV BGR numpy array or None)."""
        if not self._initialized:
            return self._simulate_frame()

        try:
            # Determine camera backend
            if hasattr(self._camera, 'capture_array'):
                # picamera2
                frame = self._camera.capture_array()
                return frame
            elif hasattr(self._camera, 'read'):
                # OpenCV VideoCapture
                ret, frame = self._camera.read()
                if ret:
                    return frame
            return None
        except Exception as e:
            logger.error("Failed to capture frame from '%s': %s",
                         self.config.camera_id, e)
            return None

    def capture_jpeg(self) -> Optional[bytes]:
        """Capture a single frame as JPEG bytes."""
        frame = self.capture_frame()
        if frame is None:
            return None
        try:
            import cv2
            encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            success, buffer = cv2.imencode('.jpg', frame, encode_params)
            if success:
                return buffer.tobytes()
        except Exception as e:
            logger.error("Failed to encode JPEG: %s", e)
        return None

    def on_motion(self, callback: Callable):
        """Register a callback for motion-detected frames."""
        self._callbacks.append(callback)

    def start_recording(self, filepath: str):
        """Start recording video to file."""
        if not self._initialized:
            logger.warning("Camera not initialized, cannot record")
            return

        try:
            import cv2
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self._video_writer = cv2.VideoWriter(
                filepath, fourcc, self.config.fps, self.config.resolution
            )
            self._recording = True
            logger.info("Recording started: %s", filepath)
        except Exception as e:
            logger.error("Failed to start recording: %s", e)

    def write_frame(self, frame):
        """Write a frame to the current recording."""
        if self._recording and self._video_writer is not None:
            self._video_writer.write(frame)

    def stop_recording(self):
        """Stop recording and release the video writer."""
        self._recording = False
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
            logger.info("Recording stopped for '%s'", self.config.camera_id)

    def start_buffering(self):
        """Start circular frame buffer for pre-event recording."""
        self._running = True
        self._frame_buffer = []
        logger.info("Frame buffering started for '%s'", self.config.camera_id)

    def _update_buffer(self, frame):
        """Add frame to circular buffer."""
        if self._running:
            self._frame_buffer.append(frame)
            buffer_size = self.config.fps * self._buffer_duration_seconds
            while len(self._frame_buffer) > buffer_size:
                self._frame_buffer.pop(0)

    def get_buffer_frames(self) -> list:
        """Retrieve all frames from the pre-event buffer."""
        frames = list(self._frame_buffer)
        self._frame_buffer.clear()
        return frames

    def stop(self):
        """Stop camera and release resources."""
        self._running = False
        self.stop_recording()
        if self._camera is not None:
            try:
                if hasattr(self._camera, 'stop'):
                    self._camera.stop()
                elif hasattr(self._camera, 'release'):
                    self._camera.release()
            except Exception as e:
                logger.error("Error stopping camera: %s", e)
        logger.info("Camera '%s' stopped", self.config.camera_id)

    def _simulate_frame(self):
        """Generate a simulated frame for testing."""
        try:
            import numpy as np
            h, w = self.config.resolution
            frame = np.random.randint(0, 50, (h, w, 3), dtype=np.uint8)
            return frame
        except ImportError:
            return None


class CameraManager:
    """Manages multiple camera instances."""

    def __init__(self, cameras_config: list):
        self.cameras: dict = {}
        for cam_config in cameras_config:
            config = CameraConfig(
                camera_id=cam_config["id"],
                name=cam_config["name"],
                camera_type=CameraType(cam_config["type"]),
                resolution=tuple(cam_config.get("resolution", [1920, 1080])),
                fps=cam_config.get("fps", 15),
                night_mode=cam_config.get("night_mode", False),
                recording=cam_config.get("recording", True),
                retention_days=cam_config.get("retention_days", 14),
            )
            camera = CameraCapture(config)
            camera.initialize()
            self.cameras[cam_config["id"]] = camera

    def capture_all(self) -> dict:
        """Capture a frame from each camera."""
        frames = {}
        for cam_id, camera in self.cameras.items():
            frames[cam_id] = camera.capture_frame()
        return frames

    def stop_all(self):
        """Stop all cameras."""
        for camera in self.cameras.values():
            camera.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    import argparse
    parser = argparse.ArgumentParser(description="Factory Guardian Camera Capture")
    parser.add_argument("--test", action="store_true", help="Test camera capture")
    args = parser.parse_args()

    # Default test camera
    config = [
        {"id": "test_cam", "name": "Test Camera",
         "type": "usb_webcam", "resolution": [1280, 720], "fps": 10}
    ]

    manager = CameraManager(config)

    if args.test:
        print("=== Camera Test Mode ===")
        frames = manager.capture_all()
        for cam_id, frame in frames.items():
            if frame is not None:
                print(f"  {cam_id}: {frame.shape[1]}x{frame.shape[0]} captured")
            else:
                print(f"  {cam_id}: No frame (simulation mode)")
    else:
        print("Camera manager ready. Use --test for quick test.")

    manager.stop_all()
