"""
DRISHTI-PAT Automated Benchmark Suite & Comparative Evaluator
Executes paired comparisons across:
- Baseline B0 (Detector + PID)
- Baseline B1 (Kalman + PID)
- DRISHTI-PAT (Adaptive Evidence Core)

Measures exact ISRO official specification targets:
1. Acquisition Time (<= 2.0s)
2. Re-acquisition Time (<= 1.0s)
3. Tracking Error (<= 10.0 px)
4. Target Loss Rate (< 5.0%)
5. Processing FPS (>= 20.0 FPS)
6. Artifact False-Lock Reduction
7. Invalid-READY Safety Duration
"""

from typing import Any

import numpy as np
import pandas as pd

from core.baselines import BaselineB0Tracker, BaselineB1Tracker
from core.contracts import FrameState, ReadinessState
from core.gimbal import GimbalActuator
from core.simulator import TrajectoryType, WorldSimulator
from core.tracker import DRISHTIPATTracker


class BenchmarkSuite:
    def __init__(self, seed: int = 42):
        self.seed = seed

    def run_scenario(
        self,
        tracker_type: str = "DRISHTI_PAT",
        trajectory: str = TrajectoryType.LEO_PASS,
        duration_s: float = 10.0,
        fps: float = 30.0,
        inject_occlusion_at_s: float | None = 4.0,
        occlusion_duration_s: float = 1.0,
        noise_std: float = 6.0,
        hot_pixel_count: int = 4,
    ) -> dict[str, Any]:
        """
        Runs one complete closed-loop tracking episode and returns comprehensive metrics.
        """
        # Instantiate simulator
        sim = WorldSimulator(camera_fps=fps, seed=self.seed)
        sim.set_trajectory(trajectory)
        sim.noise_std = noise_std
        sim._init_hot_pixels(count=hot_pixel_count)

        # Instantiate gimbal
        gimbal = GimbalActuator(max_rate_dps=5.0, dt=1.0 / fps)
        gimbal.reset()

        # Instantiate chosen tracker
        if tracker_type == "DRISHTI_PAT":
            tracker = DRISHTIPATTracker()
        elif tracker_type == "BASELINE_B0":
            tracker = BaselineB0Tracker()
        elif tracker_type == "BASELINE_B1":
            tracker = BaselineB1Tracker()
        else:
            tracker = DRISHTIPATTracker()

        records: list[dict[str, Any]] = []
        total_frames = int(duration_s * fps)
        dt = 1.0 / fps

        acquisition_time = None
        reacquisition_time = None
        occlusion_start_frame = (
            int(inject_occlusion_at_s * fps) if inject_occlusion_at_s else None
        )
        occlusion_end_frame = (
            int((inject_occlusion_at_s + occlusion_duration_s) * fps)
            if inject_occlusion_at_s
            else None
        )
        reacq_measure_start_frame = None

        consecutive_locks = 0

        for frame_idx in range(total_frames):
            sim_time = frame_idx * dt

            # Handle scheduled occlusion injection
            if occlusion_start_frame and frame_idx == occlusion_start_frame:
                sim.trigger_occlusion(occlusion_duration_s)
                reacq_measure_start_frame = occlusion_end_frame

            # Render camera frame from current gimbal pointing
            packet, truth = sim.render_camera_frame(
                gimbal_pan_deg=gimbal.pan_deg,
                gimbal_tilt_deg=gimbal.tilt_deg,
                gimbal_pan_rate=gimbal.pan_rate_dps,
                gimbal_tilt_rate=gimbal.tilt_rate_dps,
            )

            # Process frame through tracker
            audit = tracker.process_frame(packet, truth_reference=truth)

            # Extract command and update gimbal if closed-loop simulation
            if tracker_type == "DRISHTI_PAT":
                if tracker.last_command:
                    gimbal.command_rates(
                        tracker.last_command.commanded_pan_rate_dps,
                        tracker.last_command.commanded_tilt_rate_dps,
                        sim_time,
                    )
            elif tracker_type == "BASELINE_B1":
                # Basic PID command
                err_x = (audit.estimated_x - 320.0) / 160.0
                err_y = -(audit.estimated_y - 240.0) / 160.0
                gimbal.command_rates(1.5 * err_x, 1.5 * err_y, sim_time)
            elif tracker_type == "BASELINE_B0":
                err_x = (audit.estimated_x - 320.0) / 160.0 if audit.measured_x else 0.0
                err_y = (
                    -(audit.estimated_y - 240.0) / 160.0 if audit.measured_y else 0.0
                )
                gimbal.command_rates(1.2 * err_x, 1.2 * err_y, sim_time)

            gimbal.update(dt)
            sim.update_physics(dt)

            # Check Acquisition timing (ISRO <= 2.0s target)
            is_valid_lock = (
                audit.tracking_error_px is not None
                and audit.tracking_error_px <= 15.0
                and audit.frame_state == FrameState.MEASURED
            )
            if is_valid_lock:
                consecutive_locks += 1
            else:
                consecutive_locks = 0

            if acquisition_time is None and consecutive_locks >= 5:
                acquisition_time = sim_time

            # Check Re-acquisition timing (ISRO <= 1.0s target)
            if (
                reacq_measure_start_frame
                and frame_idx >= reacq_measure_start_frame
                and reacquisition_time is None
            ) and consecutive_locks >= 3:
                reacquisition_time = sim_time - (reacq_measure_start_frame * dt)

            # Check false lock on hot pixels
            is_locked_on_artifact = False
            if audit.measured_x is not None and truth["is_in_fov"]:
                for hx, hy, _ in truth["hot_pixels"]:
                    if (
                        np.hypot(audit.measured_x - hx, audit.measured_y - hy) < 3.0
                        and audit.tracking_error_px > 20.0
                    ):
                        is_locked_on_artifact = True
                        break

            # Check Invalid-READY duration (Claimed READY while true error > fine cone)
            is_invalid_ready = False
            if audit.readiness_state == ReadinessState.READY and (
                audit.pointing_error_px is None or audit.pointing_error_px > 6.0
            ):
                is_invalid_ready = True

            records.append(
                {
                    "frame_id": frame_idx,
                    "time_s": sim_time,
                    "frame_state": audit.frame_state.value,
                    "track_state": audit.track_state.value,
                    "action_type": audit.action_type.value,
                    "action_cost_ms": audit.action_cost_ms,
                    "tracking_error_px": audit.tracking_error_px,
                    "pointing_error_px": audit.pointing_error_px,
                    "uncertainty_r95": audit.uncertainty_r95,
                    "is_locked_on_artifact": is_locked_on_artifact,
                    "is_invalid_ready": is_invalid_ready,
                    "readiness_state": audit.readiness_state.value,
                    "is_visible": truth["is_visible"],
                    "gimbal_pan_deg": gimbal.pan_deg,
                    "gimbal_tilt_deg": gimbal.tilt_deg,
                }
            )

        df = pd.DataFrame(records)

        # Compute summary metrics
        valid_errors = df["tracking_error_px"].dropna()
        median_tracking_error = (
            float(valid_errors.median()) if len(valid_errors) > 0 else 999.0
        )
        p95_tracking_error = (
            float(np.percentile(valid_errors, 95)) if len(valid_errors) > 0 else 999.0
        )
        rmse_tracking_error = (
            float(np.sqrt(np.mean(valid_errors**2))) if len(valid_errors) > 0 else 999.0
        )

        target_losses = df[
            (df["is_visible"] == True)
            & ((df["tracking_error_px"].isna()) | (df["tracking_error_px"] > 15.0))
        ]
        visible_frames = df[df["is_visible"] == True]
        loss_rate_pct = float(len(target_losses) / max(1, len(visible_frames)) * 100.0)

        mean_cost_ms = float(df["action_cost_ms"].mean())
        p95_cost_ms = float(np.percentile(df["action_cost_ms"], 95))
        fps_achieved = 1000.0 / mean_cost_ms if mean_cost_ms > 0 else 100.0

        artifact_lock_frames = int(df["is_locked_on_artifact"].sum())
        artifact_lock_duration_s = float(artifact_lock_frames * dt)
        invalid_ready_duration_s = float(df["is_invalid_ready"].sum() * dt)

        # Action distribution
        action_counts = df["action_type"].value_counts().to_dict()

        summary = {
            "tracker_type": tracker_type,
            "trajectory": trajectory,
            "total_frames": total_frames,
            "acquisition_time_s": acquisition_time
            if acquisition_time is not None
            else 9.99,
            "reacquisition_time_s": reacquisition_time
            if reacquisition_time is not None
            else 9.99,
            "tracking_error_median_px": median_tracking_error,
            "tracking_error_p95_px": p95_tracking_error,
            "tracking_error_rmse_px": rmse_tracking_error,
            "target_loss_rate_pct": loss_rate_pct,
            "mean_processing_cost_ms": mean_cost_ms,
            "p95_processing_cost_ms": p95_cost_ms,
            "effective_fps": fps_achieved,
            "artifact_false_lock_time_s": artifact_lock_duration_s,
            "invalid_ready_duration_s": invalid_ready_duration_s,
            "action_distribution": action_counts,
            # Compliance with ISRO Specifications:
            "pass_acquisition_target": (
                acquisition_time is not None and acquisition_time <= 2.0
            ),
            "pass_reacquisition_target": (
                reacquisition_time is not None and reacquisition_time <= 1.0
            ),
            "pass_tracking_error_target": (median_tracking_error <= 10.0),
            "pass_loss_rate_target": (loss_rate_pct < 5.0),
            "pass_fps_target": (fps_achieved >= 20.0),
            "overall_isro_compliant": (
                (acquisition_time is not None and acquisition_time <= 2.0)
                and (reacquisition_time is not None and reacquisition_time <= 1.0)
                and (median_tracking_error <= 10.0)
                and (loss_rate_pct < 5.0)
                and (fps_achieved >= 20.0)
            ),
        }

        return {"summary": summary, "dataframe": df}

    def run_paired_comparison(
        self, trajectory: str = TrajectoryType.LEO_PASS, duration_s: float = 8.0
    ) -> dict[str, Any]:
        """
        Runs identical paired benchmark across Baseline B0, Baseline B1, and DRISHTI-PAT.
        """
        res_b0 = self.run_scenario(
            tracker_type="BASELINE_B0", trajectory=trajectory, duration_s=duration_s
        )
        res_b1 = self.run_scenario(
            tracker_type="BASELINE_B1", trajectory=trajectory, duration_s=duration_s
        )
        res_pat = self.run_scenario(
            tracker_type="DRISHTI_PAT", trajectory=trajectory, duration_s=duration_s
        )

        return {
            "BASELINE_B0": res_b0["summary"],
            "BASELINE_B1": res_b1["summary"],
            "DRISHTI_PAT": res_pat["summary"],
        }
