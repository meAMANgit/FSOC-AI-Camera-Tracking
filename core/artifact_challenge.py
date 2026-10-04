"""
DRISHTI-PAT Artifact Challenge Module
Challenges sensor-fixed defects (hot pixels, dead pixels) using camera encoder motion.
Distinguishes real world beacons (which shift with camera motion) from FPA defects (which remain stationary).
"""

from __future__ import annotations

import numpy as np

from core.contracts import ArtifactVerdict, Candidate, FramePacket


class ArtifactChallengeEvaluator:
    def __init__(
        self,
        min_gimbal_motion_px: float = 0.8,
        sensor_defect_tolerance_px: float = 1.0,
        scene_motion_tolerance_px: float = 3.5,
    ):
        self.min_gimbal_motion = min_gimbal_motion_px
        self.sensor_defect_tol = sensor_defect_tolerance_px
        self.scene_motion_tol = scene_motion_tolerance_px

        # Historical positions per candidate track: candidate_id -> list of (timestamp, x, y, pan, tilt)
        self.candidate_history: dict[
            int, list[tuple[float, float, float, float, float]]
        ] = {}
        # Persistent memory of confirmed sensor-fixed defects (detector hot/dead pixels)
        self.confirmed_defects: list[tuple[float, float]] = []

    def reset(self):
        self.candidate_history.clear()
        self.confirmed_defects.clear()

    def is_known_defect(self, x: float, y: float, radius_px: float = 6.0) -> bool:
        for dx, dy in self.confirmed_defects:
            if np.hypot(x - dx, y - dy) <= radius_px:
                return True
        return False

    def evaluate_candidate(
        self,
        candidate: Candidate,
        packet: FramePacket,
        prev_packet: FramePacket | None,
        est_target_vx: float = 0.0,
        est_target_vy: float = 0.0,
    ) -> tuple[ArtifactVerdict, dict[str, float]]:
        """
        Evaluates candidate displacement vs expected camera motion.
        Returns (Verdict, Metrics_Dict)
        """
        # Instant check against persistent defect memory
        if self.is_known_defect(candidate.x, candidate.y):
            return ArtifactVerdict.SENSOR_DEFECT, {
                "g_mag": 0.0,
                "d_mag": 0.0,
                "residual_scene": 0.0,
            }

        cand_id = candidate.candidate_id
        entry = (
            packet.timestamp_capture,
            candidate.x,
            candidate.y,
            packet.camera_pan_deg,
            packet.camera_tilt_deg,
        )

        if prev_packet is None:
            self.candidate_history[cand_id] = [entry]
            return ArtifactVerdict.INCONCLUSIVE, {
                "g_mag": 0.0,
                "d_mag": 0.0,
                "residual_scene": 0.0,
            }

        max(
            0.001, packet.timestamp_capture - prev_packet.timestamp_capture
        )

        # 1. Compute camera-induced image displacement (g) in sensor pixels
        px_per_deg_x = packet.pixels.shape[1] / packet.fov_deg[0]
        px_per_deg_y = packet.pixels.shape[0] / packet.fov_deg[1]

        delta_pan_deg = packet.camera_pan_deg - prev_packet.camera_pan_deg
        delta_tilt_deg = packet.camera_tilt_deg - prev_packet.camera_tilt_deg

        # Pan right -> scene moves LEFT in camera image (-gx)
        # Tilt up -> scene moves DOWN in camera image (+gy)
        gx = -delta_pan_deg * px_per_deg_x
        gy = delta_tilt_deg * px_per_deg_y
        g_mag = float(np.hypot(gx, gy))

        # Check candidate displacement relative to initial / previous matched position
        if (
            cand_id not in self.candidate_history
            or len(self.candidate_history[cand_id]) == 0
        ):
            self.candidate_history[cand_id] = [entry]
            return ArtifactVerdict.INCONCLUSIVE, {
                "g_mag": g_mag,
                "d_mag": 0.0,
                "residual_scene": 0.0,
            }

        hist = self.candidate_history[cand_id]
        _, prev_x, prev_y, _, _ = hist[-1]
        _, init_x, init_y, init_pan, init_tilt = hist[0]

        dx = candidate.x - prev_x
        dy = candidate.y - prev_y
        d_mag = float(np.hypot(dx, dy))

        # Multi-frame accumulated motion baseline
        total_dx = candidate.x - init_x
        total_dy = candidate.y - init_y
        total_d_mag = float(np.hypot(total_dx, total_dy))

        tot_delta_pan = packet.camera_pan_deg - init_pan
        tot_delta_tilt = packet.camera_tilt_deg - init_tilt
        tot_gx = -tot_delta_pan * px_per_deg_x
        tot_gy = tot_delta_tilt * px_per_deg_y
        tot_g_mag = float(np.hypot(tot_gx, tot_gy))

        # Update candidate history
        self.candidate_history[cand_id].append(entry)
        if len(self.candidate_history[cand_id]) > 15:
            self.candidate_history[cand_id].pop(0)

        metrics = {
            "g_mag": g_mag,
            "d_mag": d_mag,
            "tot_g_mag": tot_g_mag,
            "tot_d_mag": total_d_mag,
            "residual_scene": float(np.hypot(dx - gx, dy - gy)),
        }

        # Sensor Defect Check:
        # Candidate position remained pinned (total_d_mag < 1.0 px) while cumulative camera moved >= 0.8 px
        if (
            total_d_mag < self.sensor_defect_tol
            and tot_g_mag >= 0.8
            and len(hist) >= 2
        ) or (d_mag < self.sensor_defect_tol and g_mag >= 1.0):
            # Register in persistent defect memory!
            if not self.is_known_defect(candidate.x, candidate.y):
                self.confirmed_defects.append((candidate.x, candidate.y))
            return ArtifactVerdict.SENSOR_DEFECT, metrics

        # Scene Consistency Check: Candidate moved coherently with gimbal motion
        if (
            g_mag >= self.min_gimbal_motion
            and metrics["residual_scene"] < self.scene_motion_tol
        ):
            return ArtifactVerdict.SCENE, metrics

        return ArtifactVerdict.INCONCLUSIVE, metrics
