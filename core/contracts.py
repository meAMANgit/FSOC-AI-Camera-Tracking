"""
DRISHTI-PAT Data Contracts & Interfaces
Defines immutable/dataclass contracts for all stages of the tracking pipeline.
Follows SIH26169 / ISRO Revision 1.2 specification:
FramePacket -> CandidateSet -> ActionDecision -> Measurement -> TrackEstimate / Command -> ReadinessStatus -> RunManifest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class TrackState(str, Enum):
    SEARCH = "SEARCH"
    CONFIRM = "CONFIRM"
    TRACK = "TRACK"
    AMBIGUOUS = "AMBIGUOUS"
    COAST = "COAST"
    RECOVER = "RECOVER"
    LOST = "LOST"



class FrameState(str, Enum):
    MEASURED = "MEASURED"  # Fresh valid observation
    PREDICTED = "PREDICTED"  # Coasting on Kalman prediction
    AMBIGUOUS = "AMBIGUOUS"  # Multiple conflicting candidates
    LOST = "LOST"  # Out of FOV or recovery expired


class ActionType(str, Enum):
    FAST = "FAST"  # Strong isolated candidate -> direct centroid
    SUPPORT = "SUPPORT"  # Weak signal + consistent motion -> temporal patch stacking
    FIT = "FIT"  # High uncertainty / clutter -> template / model fit
    WIDEN = "WIDEN"  # Candidate missing / inconsistent -> expand local ROI
    GLOBAL = "GLOBAL"  # Stale/no track -> belief-guided full available search
    DEFER = "DEFER"  # Low evidence / occlusion -> skip measurement, coast


class ArtifactVerdict(str, Enum):
    SCENE = "SCENE"  # Moves with camera rotation -> true beacon
    SENSOR_DEFECT = "SENSOR_DEFECT"  # Fixed in sensor coordinates -> hot/dead pixel (REJECT)
    INCONCLUSIVE = "INCONCLUSIVE"  # Gimbal stationary or insufficient motion baseline


class ReadinessState(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    NOT_READY = "NOT_READY"
    READY = "READY"
    WITHDRAWN = "WITHDRAWN"


@dataclass
class FramePacket:
    frame_id: int
    timestamp_capture: float  # Simulated or system capture time (s)
    timestamp_arrival: float  # Pipeline arrival time (s)
    pixels: np.ndarray  # Grayscale 2D array (H, W), uint8 or float32
    source: str  # "SIMULATOR" or "MP4"
    camera_pan_deg: float = 0.0  # Gimbal pan angle (deg)
    camera_tilt_deg: float = 0.0  # Gimbal tilt angle (deg)
    pan_rate_dps: float = 0.0  # Gimbal pan angular velocity (deg/s)
    tilt_rate_dps: float = 0.0  # Gimbal tilt angular velocity (deg/s)
    fov_deg: tuple[float, float] = (4.0, 3.0)  # (horizontal, vertical) FOV


@dataclass
class Candidate:
    candidate_id: int
    x: float  # Sensor pixel X (0..W-1)
    y: float  # Sensor pixel Y (0..H-1)
    peak_intensity: float
    snr_sigma: float  # Signal-to-noise ratio in standard deviations
    area_px: float
    bbox: tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max)
    motion_residual_px: float = 0.0
    artifact_score: float = 0.0  # Distance to sensor-fixed defect hypothesis


@dataclass
class CandidateSet:
    frame_id: int
    timestamp: float
    candidates: list[Candidate] = field(default_factory=list)
    roi_bbox: tuple[int, int, int, int] | None = None  # Active ROI (x1, y1, x2, y2)
    overflow_flag: bool = False
    background_mean: float = 0.0
    background_std: float = 1.0


@dataclass
class ActionDecision:
    frame_id: int
    action_type: ActionType
    roi_radius_px: int
    history_frames_used: int
    predicted_cost_ms: float
    actual_cost_ms: float = 0.0
    reason: str = ""
    eligible_actions: list[ActionType] = field(default_factory=list)


@dataclass
class Measurement:
    frame_id: int
    is_valid: bool
    centroid_x: float | None = None
    centroid_y: float | None = None
    intensity: float = 0.0
    snr_sigma: float = 0.0
    covariance_diag: tuple[float, float] = (1.0, 1.0)
    artifact_verdict: ArtifactVerdict = ArtifactVerdict.INCONCLUSIVE
    action_used: ActionType = ActionType.DEFER
    rejection_reason: str | None = None
    support_frames_count: int = 1


@dataclass
class TrackEstimate:
    frame_id: int
    timestamp: float
    state: TrackState
    frame_state: FrameState
    estimated_x: float  # Filtered pixel X
    estimated_y: float  # Filtered pixel Y
    estimated_vx: float  # Pixel velocity X (px/s)
    estimated_vy: float  # Pixel velocity Y (px/s)
    uncertainty_r95: float  # 95% confidence radius (px)
    measurement_age_s: float  # Time elapsed since last fresh MEASURED update (s)
    consecutive_measured: int = 0
    consecutive_coasted: int = 0


@dataclass
class GimbalCommand:
    frame_id: int
    timestamp: float
    commanded_pan_rate_dps: float
    commanded_tilt_rate_dps: float
    pan_error_px: float
    tilt_error_px: float
    is_saturated: bool = False


@dataclass
class ReadinessStatus:
    frame_id: int
    state: ReadinessState
    is_ready: bool
    fresh_observations_met: bool
    offset_within_fine_cone: bool
    uncertainty_within_margin: bool
    association_resolved: bool
    motion_within_fine_stage: bool
    no_artifact_alarm: bool
    rejection_reasons: list[str] = field(default_factory=list)
    pointing_offset_px: float = 0.0
    fine_cone_threshold_px: float = 6.0


@dataclass
class FrameAuditRecord:
    frame_id: int
    timestamp: float
    frame_state: FrameState
    track_state: TrackState
    action_type: ActionType
    action_reason: str
    action_cost_ms: float
    measured_x: float | None
    measured_y: float | None
    estimated_x: float
    estimated_y: float
    uncertainty_r95: float
    measurement_age_ms: float
    artifact_verdict: ArtifactVerdict
    readiness_state: ReadinessState
    gimbal_pan_deg: float
    gimbal_tilt_deg: float
    true_x: float | None = None
    true_y: float | None = None
    tracking_error_px: float | None = None
    pointing_error_px: float | None = None
