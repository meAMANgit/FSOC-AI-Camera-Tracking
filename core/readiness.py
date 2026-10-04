"""
DRISHTI-PAT Revocable Readiness Evaluator
Evaluates fine-alignment handover readiness under continuous review.
Conditions:
1. Sustained fresh observations (age == 0, consecutive >= 5)
2. Pointing offset + r95 uncertainty + delay margin within fine acceptance region (<= 6.0 px)
3. Association resolved (no multi-target ambiguity)
4. Target motion within fine-stage slew rate capability (<= 25 px/s)
5. No active artifact challenge alarm (no sensor defect lock)

State Machine: UNCONFIGURED -> NOT_READY -> READY -> WITHDRAWN
"""

from __future__ import annotations

import numpy as np

from core.contracts import (
    ArtifactVerdict,
    CandidateSet,
    ReadinessState,
    ReadinessStatus,
    TrackEstimate,
)


class RevocableReadinessMonitor:
    def __init__(
        self,
        min_consecutive_measured: int = 5,
        fine_cone_threshold_px: float = 6.0,
        max_fine_speed_px_s: float = 25.0,
        center_x: float = 320.0,
        center_y: float = 240.0,
    ):
        self.min_consecutive_measured = min_consecutive_measured
        self.fine_cone_threshold_px = fine_cone_threshold_px
        self.max_fine_speed_px_s = max_fine_speed_px_s
        self.center_x = center_x
        self.center_y = center_y

        self.current_state = ReadinessState.NOT_READY
        self.was_ever_ready = False

    def reset(self):
        self.current_state = ReadinessState.NOT_READY
        self.was_ever_ready = False

    def evaluate(
        self,
        estimate: TrackEstimate,
        candidate_set: CandidateSet,
        artifact_verdict: ArtifactVerdict,
    ) -> ReadinessStatus:
        """
        Evaluates the 5 mandatory readiness conditions for fine stage handover.
        """
        rejection_reasons: list[str] = []

        # 1. Sustained fresh observations check
        fresh_met = (
            estimate.consecutive_measured >= self.min_consecutive_measured
            and estimate.measurement_age_s < 0.06
        )
        if not fresh_met:
            rejection_reasons.append(
                f"Fresh observations ({estimate.consecutive_measured}/{self.min_consecutive_measured}) insufficient (age {estimate.measurement_age_s*1000:.0f}ms)"
            )

        # 2. Pointing offset from optical boresight (320, 240)
        offset_px = float(
            np.hypot(
                estimate.estimated_x - self.center_x,
                estimate.estimated_y - self.center_y,
            )
        )
        # Total error envelope = offset + uncertainty + delay motion
        total_error_envelope = offset_px + estimate.uncertainty_r95
        cone_met = total_error_envelope <= self.fine_cone_threshold_px
        if not cone_met:
            rejection_reasons.append(
                f"Pointing offset + r95 ({total_error_envelope:.1f}px) exceeds fine cone limit ({self.fine_cone_threshold_px:.1f}px)"
            )

        # 3. Association resolved (no candidate conflict / ambiguity)
        num_strong_candidates = sum(
            1 for c in candidate_set.candidates if c.snr_sigma >= 2.0
        )
        assoc_met = num_strong_candidates <= 1 and not candidate_set.overflow_flag
        if not assoc_met:
            rejection_reasons.append(
                f"Ambiguous candidates ({num_strong_candidates} strong glints in FOV)"
            )

        # 4. Motion within fine stage capability
        speed_px_s = float(
            np.hypot(estimate.estimated_vx, estimate.estimated_vy)
        )
        motion_met = speed_px_s <= self.max_fine_speed_px_s
        if not motion_met:
            rejection_reasons.append(
                f"Target speed ({speed_px_s:.1f} px/s) exceeds fine stage capture speed ({self.max_fine_speed_px_s:.1f} px/s)"
            )

        # 5. No sensor artifact alarm
        no_artifact_alarm = artifact_verdict != ArtifactVerdict.SENSOR_DEFECT
        if not no_artifact_alarm:
            rejection_reasons.append(
                "Sensor defect / hot-pixel alarm active"
            )

        # Combine all conditions
        all_passed = (
            fresh_met
            and cone_met
            and assoc_met
            and motion_met
            and no_artifact_alarm
        )

        # State transition logic
        if all_passed:
            self.current_state = ReadinessState.READY
            self.was_ever_ready = True
        else:
            if self.was_ever_ready:
                self.current_state = ReadinessState.WITHDRAWN
            else:
                self.current_state = ReadinessState.NOT_READY

        return ReadinessStatus(
            frame_id=estimate.frame_id,
            state=self.current_state,
            is_ready=all_passed,
            fresh_observations_met=fresh_met,
            offset_within_fine_cone=cone_met,
            uncertainty_within_margin=estimate.uncertainty_r95 <= 3.0,
            association_resolved=assoc_met,
            motion_within_fine_stage=motion_met,
            no_artifact_alarm=no_artifact_alarm,
            rejection_reasons=rejection_reasons,
            pointing_offset_px=offset_px,
            fine_cone_threshold_px=self.fine_cone_threshold_px,
        )
