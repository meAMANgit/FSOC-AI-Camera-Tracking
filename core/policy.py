"""
DRISHTI-PAT Precision-on-Demand Action Policy
Implements adaptive resource allocation:
Chooses the cheapest eligible action predicted to meet the quality target.
"""

from __future__ import annotations

import numpy as np

from core.contracts import (
    ActionDecision,
    ActionType,
    Candidate,
    CandidateSet,
    TrackEstimate,
    TrackState,
)


class PrecisionOnDemandPolicy:
    def __init__(self, target_frame_budget_ms: float = 50.0):
        self.frame_budget_ms = target_frame_budget_ms

        # Profiled average execution costs (ms)
        self.profiled_costs = {
            ActionType.FAST: 0.25,
            ActionType.SUPPORT: 1.20,
            ActionType.FIT: 1.80,
            ActionType.WIDEN: 0.85,
            ActionType.GLOBAL: 4.20,
            ActionType.DEFER: 0.05,
        }

    def select_action(
        self,
        candidate_set: CandidateSet,
        track_estimate: TrackEstimate | None,
        elapsed_decode_ms: float = 0.0,
    ) -> tuple[ActionDecision, Candidate | None]:
        """
        Decides the optimal action and selects primary target candidate.
        """
        frame_id = candidate_set.frame_id
        candidates = candidate_set.candidates

        # 1. If tracker is in SEARCH or RECOVER state (or no previous track)
        if track_estimate is None or track_estimate.state in (
            TrackState.SEARCH,
            TrackState.RECOVER,
        ):
            if len(candidates) > 0:
                best_cand = candidates[0]  # Highest SNR
                if best_cand.snr_sigma >= 1.4:
                    return ActionDecision(
                        frame_id=frame_id,
                        action_type=ActionType.FAST,
                        roi_radius_px=96,
                        history_frames_used=1,
                        predicted_cost_ms=self.profiled_costs[ActionType.FAST],
                        reason=f"Initial acquisition candidate detected (SNR {best_cand.snr_sigma:.1f}σ)",
                        eligible_actions=[ActionType.FAST, ActionType.FIT],
                    ), best_cand
            return ActionDecision(
                frame_id=frame_id,
                action_type=ActionType.GLOBAL,
                roi_radius_px=320,
                history_frames_used=0,
                predicted_cost_ms=self.profiled_costs[ActionType.GLOBAL],
                reason="Searching full FOV for initial beacon lock",
                eligible_actions=[ActionType.GLOBAL],
            ), None

        # 2. If no candidates found in current ROI
        if len(candidates) == 0:
            if track_estimate.consecutive_coasted < 3:
                return ActionDecision(
                    frame_id=frame_id,
                    action_type=ActionType.WIDEN,
                    roi_radius_px=192,
                    history_frames_used=0,
                    predicted_cost_ms=self.profiled_costs[ActionType.WIDEN],
                    reason="ROI empty: expanding search window",
                    eligible_actions=[ActionType.WIDEN, ActionType.DEFER],
                ), None
            else:
                return ActionDecision(
                    frame_id=frame_id,
                    action_type=ActionType.DEFER,
                    roi_radius_px=96,
                    history_frames_used=0,
                    predicted_cost_ms=self.profiled_costs[ActionType.DEFER],
                    reason="Missing target: coasting on Kalman prediction",
                    eligible_actions=[ActionType.DEFER],
                ), None

        # 3. We have candidates: evaluate candidate with maximum likelihood score
        best_cand = None
        best_score = -1.0
        gate_sigma = max(
            15.0,
            2.5 * (track_estimate.uncertainty_r95 if track_estimate else 10.0),
        )

        for cand in candidates:
            # Spatial gating distance
            dist = cand.motion_residual_px
            # Spatial likelihood Gaussian penalty
            spatial_weight = np.exp(-0.5 * (dist / gate_sigma) ** 2)
            # Optical spot quality weight (rewards true Gaussian optical spot area >= 15px)
            spot_weight = min(2.0, max(0.5, np.sqrt(cand.area_px / 10.0)))
            # Joint candidate score
            score = cand.snr_sigma * spatial_weight * spot_weight

            if score > best_score:
                best_score = score
                best_cand = cand

        if best_cand is None:
            best_cand = candidates[0]

        # 4. Action decision based on candidate quality & motion consistency
        snr = best_cand.snr_sigma
        residual = best_cand.motion_residual_px

        # Case A: High SNR and low motion residual -> FAST Action
        if snr >= 3.0 and residual <= 12.0:
            return ActionDecision(
                frame_id=frame_id,
                action_type=ActionType.FAST,
                roi_radius_px=96,
                history_frames_used=1,
                predicted_cost_ms=self.profiled_costs[ActionType.FAST],
                reason=f"Strong isolated candidate (SNR {snr:.1f}σ, res {residual:.1f}px)",
                eligible_actions=[ActionType.FAST, ActionType.FIT],
            ), best_cand

        # Case B: Weak SNR but consistent motion -> SUPPORT Action (temporal patch stacking)
        if 1.4 <= snr < 3.0 and residual <= 15.0:
            return ActionDecision(
                frame_id=frame_id,
                action_type=ActionType.SUPPORT,
                roi_radius_px=96,
                history_frames_used=4,
                predicted_cost_ms=self.profiled_costs[ActionType.SUPPORT],
                reason=f"Weak beacon (SNR {snr:.1f}σ) with consistent track -> temporal stacking",
                eligible_actions=[
                    ActionType.SUPPORT,
                    ActionType.FIT,
                    ActionType.FAST,
                ],
            ), best_cand

        # Case C: High localization uncertainty or background clutter -> FIT Action
        if snr >= 1.8 and residual <= 25.0:
            return ActionDecision(
                frame_id=frame_id,
                action_type=ActionType.FIT,
                roi_radius_px=96,
                history_frames_used=1,
                predicted_cost_ms=self.profiled_costs[ActionType.FIT],
                reason=f"High localization uncertainty (res {residual:.1f}px) -> Gauss-Newton fit",
                eligible_actions=[ActionType.FIT, ActionType.FAST],
            ), best_cand

        # Case D: Motion residual too large (target jumped / possible false glint)
        if residual > 25.0:
            return ActionDecision(
                frame_id=frame_id,
                action_type=ActionType.WIDEN,
                roi_radius_px=192,
                history_frames_used=0,
                predicted_cost_ms=self.profiled_costs[ActionType.WIDEN],
                reason=f"Motion disagreement ({residual:.1f}px) -> widening ROI",
                eligible_actions=[ActionType.WIDEN, ActionType.DEFER],
            ), best_cand

        # Case E: Extremely low signal -> DEFER
        return ActionDecision(
            frame_id=frame_id,
            action_type=ActionType.DEFER,
            roi_radius_px=96,
            history_frames_used=0,
            predicted_cost_ms=self.profiled_costs[ActionType.DEFER],
            reason=f"Low signal ({snr:.1f}σ) -> deferring measurement",
            eligible_actions=[ActionType.DEFER],
        ), None
