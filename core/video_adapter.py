"""
DRISHTI-PAT Native .MP4 Video Tracking Adapter
Bypasses virtual camera gimbal loop to process native video frames directly in image space.
Extracts per-frame audit trails, runtime FPS metrics, and CSV logs.
"""

from __future__ import annotations

import time
from typing import Any

import cv2

from core.contracts import FrameAuditRecord, FramePacket
from core.tracker import DRISHTIPATTracker


class VideoBenchmarkAdapter:
    def __init__(self, video_path: str):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise ValueError(f"Unable to open video source: {video_path}")

        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def get_info(self) -> dict[str, Any]:
        return {
            "path": self.video_path,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "duration_s": self.total_frames / self.fps if self.fps > 0 else 0.0,
        }

    def process_video(
        self,
        tracker: DRISHTIPATTracker | None = None,
        max_frames: int | None = None,
    ) -> tuple[list[FrameAuditRecord], dict[str, Any]]:
        """
        Processes entire video sequentially through DRISHTI-PAT core.
        """
        if tracker is None:
            tracker = DRISHTIPATTracker()
        tracker.reset()

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        audit_records: list[FrameAuditRecord] = []

        frame_idx = 0
        t0 = time.perf_counter()

        while True:
            if max_frames and frame_idx >= max_frames:
                break

            ret, frame = self.cap.read()
            if not ret:
                break

            # Convert to grayscale if color
            if len(frame.shape) == 3 and frame.shape[2] == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = frame

            timestamp = frame_idx / self.fps

            # Assemble FramePacket with zero PTZ motion (image-space bypass)
            packet = FramePacket(
                frame_id=frame_idx,
                timestamp_capture=timestamp,
                timestamp_arrival=timestamp + 0.001,
                pixels=gray,
                source="MP4",
                camera_pan_deg=0.0,
                camera_tilt_deg=0.0,
                pan_rate_dps=0.0,
                tilt_rate_dps=0.0,
                fov_deg=(4.0, 3.0),
            )

            audit = tracker.process_frame(packet)
            audit_records.append(audit)
            frame_idx += 1

        total_time_s = time.perf_counter() - t0
        effective_fps = (
            frame_idx / total_time_s if total_time_s > 0 else 0.0
        )

        stats = {
            "frames_processed": frame_idx,
            "total_runtime_s": total_time_s,
            "effective_fps": effective_fps,
            "average_frame_latency_ms": (
                total_time_s / max(1, frame_idx)
            )
            * 1000.0,
            "isro_fps_target_met": effective_fps >= 20.0,
        }

        return audit_records, stats

    def release(self):
        if self.cap and self.cap.isOpened():
            self.cap.release()
