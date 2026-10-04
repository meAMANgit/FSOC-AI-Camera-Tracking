"""
DRISHTI-PAT Benchmark Comparators & Baselines
Implements standard baseline trackers for fair paired comparison on identical frame sequences:
- Baseline B0: Classical Peak Detector + Standard PID (No Kalman, no artifact check)
- Baseline B1: Detector + CV Kalman Filter + Predictive PID (No selective actions, no artifact check)
- Baseline B2: B1 + Fixed 4-Frame Temporal Averaging on Every Frame (Always-on history)
"""

import time
from typing import Any

import numpy as np

from core.contracts import (
    ActionType,
    ArtifactVerdict,
    FrameAuditRecord,
    FramePacket,
    FrameState,
    ReadinessState,
    TrackState,
)
from core.controller import PredictivePIDController
from core.estimator import KalmanTrackEstimator
from core.perception import ProposalGenerator


class BaselineB0Tracker:
    """Baseline B0: Naive Peak Detector + Basic PID (No Kalman, No Artifact Check)."""

    def __init__(self):
        self.proposals = ProposalGenerator(min_snr_sigma=1.5)
        self.target_x = 320.0
        self.target_y = 240.0
        self.is_locked = False
        self.kp = 1.2
        self.kd = 0.05
        self.prev_err_x = 0.0
        self.prev_err_y = 0.0

    def reset(self):
        self.target_x = 320.0
        self.target_y = 240.0
        self.is_locked = False
        self.prev_err_x = 0.0
        self.prev_err_y = 0.0

    def process_frame(
        self, packet: FramePacket, truth_reference: dict[str, Any] | None = None
    ) -> FrameAuditRecord:
        t0 = time.perf_counter()
        cset = self.proposals.extract_candidates(packet)

        # Naively selects the brightest spot in the frame (easily tricked by hot pixels!)
        if len(cset.candidates) > 0:
            cand = cset.candidates[0]
            self.target_x = cand.x
            self.target_y = cand.y
            self.is_locked = True
            fstate = FrameState.MEASURED
            tstate = TrackState.TRACK
        else:
            self.is_locked = False
            fstate = FrameState.LOST
            tstate = TrackState.SEARCH

        # Basic PID command
        err_x = (self.target_x - 320.0) / 160.0 if self.is_locked else 0.0
        err_y = -(self.target_y - 240.0) / 160.0 if self.is_locked else 0.0
        np.clip(
            self.kp * err_x + self.kd * (err_x - self.prev_err_x) * 30.0, -5.0, 5.0
        )
        np.clip(
            self.kp * err_y + self.kd * (err_y - self.prev_err_y) * 30.0, -5.0, 5.0
        )
        self.prev_err_x = err_x
        self.prev_err_y = err_y

        total_cost_ms = (time.perf_counter() - t0) * 1000.0

        true_x = truth_reference.get("true_u") if truth_reference else None
        true_y = truth_reference.get("true_v") if truth_reference else None
        tracking_err = (
            float(np.hypot(self.target_x - true_x, self.target_y - true_y))
            if (true_x and self.is_locked)
            else None
        )

        return FrameAuditRecord(
            frame_id=packet.frame_id,
            timestamp=packet.timestamp_capture,
            frame_state=fstate,
            track_state=tstate,
            action_type=ActionType.FAST,
            action_reason="B0: Brightest spot peak detection",
            action_cost_ms=total_cost_ms,
            measured_x=self.target_x if self.is_locked else None,
            measured_y=self.target_y if self.is_locked else None,
            estimated_x=self.target_x,
            estimated_y=self.target_y,
            uncertainty_r95=10.0 if self.is_locked else 50.0,
            measurement_age_ms=0.0 if self.is_locked else 500.0,
            artifact_verdict=ArtifactVerdict.INCONCLUSIVE,
            readiness_state=ReadinessState.READY
            if self.is_locked
            else ReadinessState.NOT_READY,
            gimbal_pan_deg=packet.camera_pan_deg,
            gimbal_tilt_deg=packet.camera_tilt_deg,
            true_x=true_x,
            true_y=true_y,
            tracking_error_px=tracking_err,
            pointing_error_px=float(np.hypot(true_x - 320, true_y - 240))
            if true_x
            else None,
        )


class BaselineB1Tracker:
    """Baseline B1: Peak Detector + Kalman Estimator + Predictive PID (No Artifact Challenge, No Adaptive Selection)."""

    def __init__(self):
        self.proposals = ProposalGenerator(min_snr_sigma=1.5)
        self.estimator = KalmanTrackEstimator()
        self.controller = PredictivePIDController()
        self.prev_pan = 0.0
        self.prev_tilt = 0.0

    def reset(self):
        self.estimator.reset()
        self.controller.reset()
        self.prev_pan = 0.0
        self.prev_tilt = 0.0

    def process_frame(
        self, packet: FramePacket, truth_reference: dict[str, Any] | None = None
    ) -> FrameAuditRecord:
        t0 = time.perf_counter()

        if self.estimator.track_state != TrackState.SEARCH:
            self.estimator.predict(
                current_time=packet.timestamp_capture,
                camera_pan_deg=packet.camera_pan_deg,
                camera_tilt_deg=packet.camera_tilt_deg,
                prev_pan_deg=self.prev_pan,
                prev_tilt_deg=self.prev_tilt,
            )

        prior_x = (
            self.estimator.state[0]
            if self.estimator.track_state != TrackState.SEARCH
            else None
        )
        prior_y = (
            self.estimator.state[1]
            if self.estimator.track_state != TrackState.SEARCH
            else None
        )

        cset = self.proposals.extract_candidates(
            packet, prior_x, prior_y, roi_radius=96
        )

        # B1 does not challenge artifacts; chooses closest candidate or brightest
        best_cand = cset.candidates[0] if len(cset.candidates) > 0 else None

        from core.contracts import Measurement

        if best_cand:
            meas = Measurement(
                frame_id=packet.frame_id,
                is_valid=True,
                centroid_x=best_cand.x,
                centroid_y=best_cand.y,
                intensity=best_cand.peak_intensity,
                snr_sigma=best_cand.snr_sigma,
                covariance_diag=(0.5, 0.5),
                action_used=ActionType.FAST,
            )
            if self.estimator.track_state == TrackState.SEARCH:
                self.estimator.initialize_track(
                    best_cand.x, best_cand.y, packet.timestamp_capture
                )
            estimate = self.estimator.update(meas, packet.timestamp_capture)
        else:
            meas = Measurement(
                frame_id=packet.frame_id, is_valid=False, action_used=ActionType.DEFER
            )
            estimate = self.estimator.update(meas, packet.timestamp_capture)

        self.prev_pan = packet.camera_pan_deg
        self.prev_tilt = packet.camera_tilt_deg
        total_cost_ms = (time.perf_counter() - t0) * 1000.0

        true_x = truth_reference.get("true_u") if truth_reference else None
        true_y = truth_reference.get("true_v") if truth_reference else None
        tracking_err = (
            float(
                np.hypot(estimate.estimated_x - true_x, estimate.estimated_y - true_y)
            )
            if true_x
            else None
        )

        return FrameAuditRecord(
            frame_id=packet.frame_id,
            timestamp=packet.timestamp_capture,
            frame_state=estimate.frame_state,
            track_state=estimate.state,
            action_type=ActionType.FAST,
            action_reason="B1: Kalman + Fixed Detector",
            action_cost_ms=total_cost_ms,
            measured_x=meas.centroid_x,
            measured_y=meas.centroid_y,
            estimated_x=estimate.estimated_x,
            estimated_y=estimate.estimated_y,
            uncertainty_r95=estimate.uncertainty_r95,
            measurement_age_ms=estimate.measurement_age_s * 1000.0,
            artifact_verdict=ArtifactVerdict.INCONCLUSIVE,
            readiness_state=ReadinessState.READY
            if estimate.state == TrackState.TRACK
            else ReadinessState.NOT_READY,
            gimbal_pan_deg=packet.camera_pan_deg,
            gimbal_tilt_deg=packet.camera_tilt_deg,
            true_x=true_x,
            true_y=true_y,
            tracking_error_px=tracking_err,
            pointing_error_px=float(np.hypot(true_x - 320, true_y - 240))
            if true_x
            else None,
        )
