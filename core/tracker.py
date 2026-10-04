"""
DRISHTI-PAT Master Tracking Pipeline
Implements the 5-lane architecture:
Input -> Perception -> Decision -> Estimation & Control -> Output & Audit
One shared core for both live closed-loop simulation and raw .mp4 video tracking.
"""

import time
from typing import Any

import numpy as np

from core.actions import ActionExecutor
from core.artifact_challenge import ArtifactChallengeEvaluator
from core.contracts import (
    ActionType,
    ArtifactVerdict,
    FrameAuditRecord,
    FramePacket,
    GimbalCommand,
    ReadinessStatus,
    TrackEstimate,
    TrackState,
)
from core.controller import PredictivePIDController
from core.estimator import KalmanTrackEstimator
from core.perception import ProposalGenerator
from core.policy import PrecisionOnDemandPolicy
from core.readiness import RevocableReadinessMonitor


class DRISHTIPATTracker:
    def __init__(self, target_frame_budget_ms: float = 50.0):
        self.proposals = ProposalGenerator()
        self.actions = ActionExecutor()
        self.artifact_challenge = ArtifactChallengeEvaluator()
        self.estimator = KalmanTrackEstimator()
        self.controller = PredictivePIDController()
        self.readiness = RevocableReadinessMonitor()
        self.policy = PrecisionOnDemandPolicy(target_frame_budget_ms)

        self.prev_packet: FramePacket | None = None
        self.prev_pan_deg = 0.0
        self.prev_tilt_deg = 0.0
        self.active_roi_radius = 96

        self.last_estimate: TrackEstimate | None = None
        self.last_readiness: ReadinessStatus | None = None
        self.last_command: GimbalCommand | None = None

    def reset(self):
        self.proposals = ProposalGenerator()
        self.actions.reset_history()
        self.artifact_challenge.reset()
        self.estimator.reset()
        self.controller.reset()
        self.readiness.reset()
        self.prev_packet = None
        self.prev_pan_deg = 0.0
        self.prev_tilt_deg = 0.0
        self.active_roi_radius = 96
        self.last_estimate = None
        self.last_readiness = None
        self.last_command = None

    def process_frame(
        self, packet: FramePacket, truth_reference: dict[str, Any] | None = None
    ) -> FrameAuditRecord:
        """
        Executes one full frame tracking cycle adhering strictly to the SIH26169 data flow.
        """
        t_pipeline_start = time.perf_counter()

        # Step 1: Kalman State Prediction with Gimbal Motion Compensation
        if self.estimator.track_state != TrackState.SEARCH:
            self.estimator.predict(
                current_time=packet.timestamp_capture,
                camera_pan_deg=packet.camera_pan_deg,
                camera_tilt_deg=packet.camera_tilt_deg,
                prev_pan_deg=self.prev_pan_deg,
                prev_tilt_deg=self.prev_tilt_deg,
                fov_deg=packet.fov_deg,
                fpa_size=(packet.pixels.shape[1], packet.pixels.shape[0]),
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

        # Step 2: Perception & Candidate Generation in Active ROI or Full Frame
        # If in SEARCH or RECOVER, search full frame; otherwise search active ROI
        search_radius = (
            None
            if self.estimator.track_state in (TrackState.SEARCH, TrackState.RECOVER)
            else self.active_roi_radius
        )
        candidate_set = self.proposals.extract_candidates(
            packet=packet,
            prior_x=prior_x,
            prior_y=prior_y,
            roi_radius=search_radius,
            defect_blacklist=self.artifact_challenge.confirmed_defects,
        )

        # Step 3: Artifact Challenge on Candidates (Reject sensor-fixed defects!)
        valid_candidates = []
        best_verdict = ArtifactVerdict.INCONCLUSIVE

        est_vx = (
            self.estimator.state[2]
            if self.estimator.track_state != TrackState.SEARCH
            else 0.0
        )
        est_vy = (
            self.estimator.state[3]
            if self.estimator.track_state != TrackState.SEARCH
            else 0.0
        )

        for cand in candidate_set.candidates:
            verdict, _ = self.artifact_challenge.evaluate_candidate(
                candidate=cand,
                packet=packet,
                prev_packet=self.prev_packet,
                est_target_vx=est_vx,
                est_target_vy=est_vy,
            )
            if verdict == ArtifactVerdict.SENSOR_DEFECT:
                # Challenge triggered! Candidate is fixed to sensor array while camera moved -> REJECT
                # If current track was locked onto this defect, reset track immediately!
                if self.estimator.track_state != TrackState.SEARCH:
                    dist_to_track = np.hypot(
                        cand.x - self.estimator.state[0],
                        cand.y - self.estimator.state[1],
                    )
                    if dist_to_track < 20.0:
                        self.estimator.reset()
                continue

            valid_candidates.append(cand)
            if verdict == ArtifactVerdict.SCENE:
                best_verdict = ArtifactVerdict.SCENE

        # Update candidate set with non-artifact candidates
        candidate_set.candidates = valid_candidates

        # If track was reset due to artifact, re-extract candidates over full frame
        if (
            self.estimator.track_state == TrackState.SEARCH
            and len(valid_candidates) == 0
        ):
            candidate_set = self.proposals.extract_candidates(
                packet,
                prior_x=None,
                prior_y=None,
                roi_radius=None,
                defect_blacklist=self.artifact_challenge.confirmed_defects,
            )
            cands_clean = []
            for c in candidate_set.candidates:
                v, _ = self.artifact_challenge.evaluate_candidate(
                    c, packet, self.prev_packet, 0.0, 0.0
                )
                if v != ArtifactVerdict.SENSOR_DEFECT:
                    cands_clean.append(c)
            candidate_set.candidates = cands_clean

        # Step 4: Precision-on-Demand Action Policy Selection
        t_action_select_start = time.perf_counter()
        elapsed_so_far = (t_action_select_start - t_pipeline_start) * 1000.0

        decision, target_cand = self.policy.select_action(
            candidate_set=candidate_set,
            track_estimate=self.last_estimate,
            elapsed_decode_ms=elapsed_so_far,
        )
        self.active_roi_radius = decision.roi_radius_px

        # If WIDEN or GLOBAL selected and no target cand was found, expand search immediately
        if (decision.action_type in (ActionType.WIDEN, ActionType.GLOBAL)) and (
            target_cand is None
        ):
            expanded_radius = 192 if decision.action_type == ActionType.WIDEN else None
            candidate_set = self.proposals.extract_candidates(
                packet=packet,
                prior_x=prior_x,
                prior_y=prior_y,
                roi_radius=expanded_radius,
                defect_blacklist=self.artifact_challenge.confirmed_defects,
            )
            # Re-filter sensor defects
            cands_clean = []
            for c in candidate_set.candidates:
                v, _ = self.artifact_challenge.evaluate_candidate(
                    c, packet, self.prev_packet, est_vx, est_vy
                )
                if v != ArtifactVerdict.SENSOR_DEFECT:
                    cands_clean.append(c)
            if len(cands_clean) > 0:
                target_cand = cands_clean[0]

        # Step 5: Execute Selected Action
        t_action_exec_start = time.perf_counter()
        if decision.action_type == ActionType.FAST and target_cand:
            measurement = self.actions.execute_fast(packet, target_cand)
        elif decision.action_type == ActionType.SUPPORT and target_cand:
            measurement = self.actions.execute_support(
                packet, target_cand, est_vx, est_vy
            )
        elif decision.action_type == ActionType.FIT and target_cand:
            measurement = self.actions.execute_fit(packet, target_cand)
        elif (
            decision.action_type == ActionType.WIDEN
            and target_cand
            or decision.action_type == ActionType.GLOBAL
            and target_cand
        ):
            measurement = self.actions.execute_fast(packet, target_cand)
        else:
            measurement = self.actions.execute_defer(packet, reason=decision.reason)

        measurement.artifact_verdict = best_verdict
        action_cost_ms = (time.perf_counter() - t_action_exec_start) * 1000.0
        decision.actual_cost_ms = action_cost_ms

        # Step 6: Estimator Update & Track State
        if self.estimator.track_state == TrackState.SEARCH and measurement.is_valid:
            self.estimator.initialize_track(
                measurement.centroid_x, measurement.centroid_y, packet.timestamp_capture
            )
            estimate = self.estimator.update(measurement, packet.timestamp_capture)
        else:
            estimate = self.estimator.update(measurement, packet.timestamp_capture)
        self.last_estimate = estimate

        # Step 7: Predictive PID Gimbal Command Computation (if closed-loop simulation)
        is_tracking_active = estimate.state in (
            TrackState.TRACK,
            TrackState.CONFIRM,
            TrackState.COAST,
        )
        gimbal_cmd = self.controller.compute_command(
            frame_id=packet.frame_id,
            timestamp=packet.timestamp_capture,
            target_sensor_x=estimate.estimated_x,
            target_sensor_y=estimate.estimated_y,
            target_sensor_vx=estimate.estimated_vx,
            target_sensor_vy=estimate.estimated_vy,
            is_tracking=is_tracking_active,
        )
        self.last_command = gimbal_cmd

        # Step 8: Revocable Readiness Handover Check
        readiness_status = self.readiness.evaluate(
            estimate=estimate,
            candidate_set=candidate_set,
            artifact_verdict=best_verdict,
        )
        self.last_readiness = readiness_status

        # Step 9: Frame History and Encoder Memory Update
        self.actions.push_frame_history(packet)
        self.prev_packet = packet
        self.prev_pan_deg = packet.camera_pan_deg
        self.prev_tilt_deg = packet.camera_tilt_deg

        # Calculate tracking and pointing errors if private ground truth provided
        true_x = truth_reference.get("true_u") if truth_reference else None
        true_y = truth_reference.get("true_v") if truth_reference else None
        tracking_err = None
        pointing_err = None
        if (
            true_x is not None
            and true_y is not None
            and truth_reference.get("is_in_fov", False)
        ):
            tracking_err = float(
                np.hypot(estimate.estimated_x - true_x, estimate.estimated_y - true_y)
            )
            pointing_err = float(np.hypot(true_x - 320.0, true_y - 240.0))

        # Total frame processing cost
        total_cost_ms = (time.perf_counter() - t_pipeline_start) * 1000.0

        # Step 10: Assemble Full Frame Audit Record
        audit_record = FrameAuditRecord(
            frame_id=packet.frame_id,
            timestamp=packet.timestamp_capture,
            frame_state=estimate.frame_state,
            track_state=estimate.state,
            action_type=decision.action_type,
            action_reason=decision.reason,
            action_cost_ms=total_cost_ms,
            measured_x=measurement.centroid_x,
            measured_y=measurement.centroid_y,
            estimated_x=estimate.estimated_x,
            estimated_y=estimate.estimated_y,
            uncertainty_r95=estimate.uncertainty_r95,
            measurement_age_ms=estimate.measurement_age_s * 1000.0,
            artifact_verdict=best_verdict,
            readiness_state=readiness_status.state,
            gimbal_pan_deg=packet.camera_pan_deg,
            gimbal_tilt_deg=packet.camera_tilt_deg,
            true_x=true_x,
            true_y=true_y,
            tracking_error_px=tracking_err,
            pointing_error_px=pointing_err,
        )

        return audit_record
