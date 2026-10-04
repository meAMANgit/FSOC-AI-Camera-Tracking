"""
DRISHTI-PAT Kalman Estimator & Track State Manager
- Constant-Velocity Kalman Filter with camera motion compensation
- Dynamic covariance scaling based on measurement SNR and action type
- 95% Confidence Uncertainty Radius (r_95)
- Track State Machine: SEARCH -> CONFIRM -> TRACK -> AMBIGUOUS -> COAST -> RECOVER
- Distinguishes MEASURED from PREDICTED observations
"""

from __future__ import annotations

import numpy as np

from core.contracts import (
    FrameState,
    Measurement,
    TrackEstimate,
    TrackState,
)


class KalmanTrackEstimator:
    def __init__(
        self,
        process_noise_accel: float = 30.0,  # px/s^2 target acceleration uncertainty
        max_coast_duration_s: float = 1.0,  # Max coast time before dropping to RECOVER
        confirm_hits_required: int = 3,
        lost_age_threshold_s: float = 2.0,
    ):
        self.q_accel = process_noise_accel
        self.max_coast_s = max_coast_duration_s
        self.confirm_hits = confirm_hits_required
        self.lost_age_s = lost_age_threshold_s

        # State vector [x, y, vx, vy]^T
        self.state = np.zeros(4, dtype=np.float64)
        # Covariance matrix P (4x4)
        self.P = np.eye(4, dtype=np.float64) * 100.0

        # Operational state
        self.track_state = TrackState.SEARCH
        self.frame_state = FrameState.LOST

        self.last_timestamp: float | None = None
        self.last_measured_timestamp: float | None = None
        self.consecutive_measured = 0
        self.consecutive_coasted = 0
        self.confirm_counter = 0

    def reset(self):
        self.state = np.zeros(4, dtype=np.float64)
        self.P = np.eye(4, dtype=np.float64) * 100.0
        self.track_state = TrackState.SEARCH
        self.frame_state = FrameState.LOST
        self.last_timestamp = None
        self.last_measured_timestamp = None
        self.consecutive_measured = 0
        self.consecutive_coasted = 0
        self.confirm_counter = 0

    def initialize_track(self, x: float, y: float, timestamp: float):
        self.state = np.array([x, y, 0.0, 0.0], dtype=np.float64)
        self.P = np.diag([4.0, 4.0, 50.0, 50.0])
        self.track_state = TrackState.CONFIRM
        self.frame_state = FrameState.MEASURED
        self.last_timestamp = timestamp
        self.last_measured_timestamp = timestamp
        self.consecutive_measured = 1
        self.consecutive_coasted = 0
        self.confirm_counter = 1

    def predict(
        self,
        current_time: float,
        camera_pan_deg: float,
        camera_tilt_deg: float,
        prev_pan_deg: float,
        prev_tilt_deg: float,
        fov_deg: tuple[float, float] = (4.0, 3.0),
        fpa_size: tuple[int, int] = (640, 480),
    ):
        """
        Kalman State Prediction with Camera Motion Compensation.
        """
        if self.last_timestamp is None:
            dt = 1.0 / 30.0
        else:
            dt = max(0.001, current_time - self.last_timestamp)
        self.last_timestamp = current_time

        # 1. State Transition Matrix F
        F = np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

        # 2. Continuous White Noise Acceleration Q Matrix
        q = self.q_accel
        dt2 = dt * dt
        dt3 = dt2 * dt / 2.0
        dt4 = dt2 * dt2 / 4.0
        Q = np.array(
            [
                [dt4 * q, 0.0, dt3 * q, 0.0],
                [0.0, dt4 * q, 0.0, dt3 * q],
                [dt3 * q, 0.0, dt2 * q, 0.0],
                [0.0, dt3 * q, 0.0, dt2 * q],
            ],
            dtype=np.float64,
        )

        # 3. Camera Induced Shift (gx, gy)
        px_per_deg_x = fpa_size[0] / fov_deg[0]
        px_per_deg_y = fpa_size[1] / fov_deg[1]
        delta_pan = camera_pan_deg - prev_pan_deg
        delta_tilt = camera_tilt_deg - prev_tilt_deg
        gx = -delta_pan * px_per_deg_x
        gy = delta_tilt * px_per_deg_y

        # Predict State: x_pred = F * x + [gx, gy, 0, 0]^T
        self.state = F @ self.state + np.array([gx, gy, 0.0, 0.0])
        # Predict Covariance: P_pred = F * P * F^T + Q
        self.P = F @ self.P @ F.T + Q

    def update(
        self, measurement: Measurement, timestamp: float
    ) -> TrackEstimate:
        """
        Kalman Measurement Update & Track FSM Transition.
        """
        H = np.array(
            [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float64
        )

        if (
            measurement.is_valid
            and measurement.centroid_x is not None
            and measurement.centroid_y is not None
        ):
            # Valid observation available -> MEASURED
            z = np.array(
                [measurement.centroid_x, measurement.centroid_y],
                dtype=np.float64,
            )

            # Measurement noise covariance R scaled by action accuracy
            var_x, var_y = measurement.covariance_diag
            R = np.diag([var_x, var_y])

            # Innovation y_tilde = z - H * x
            y_tilde = z - H @ self.state
            S = H @ self.P @ H.T + R
            K = self.P @ H.T @ np.linalg.inv(S)

            # Updated state & covariance (Joseph form for numerical stability)
            self.state = self.state + K @ y_tilde
            I_KH = np.eye(4) - K @ H
            self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T

            self.last_measured_timestamp = timestamp
            self.consecutive_measured += 1
            self.consecutive_coasted = 0
            self.frame_state = FrameState.MEASURED

            # FSM Transitions
            if self.track_state == TrackState.SEARCH:
                self.track_state = TrackState.CONFIRM
                self.confirm_counter = 1
            elif self.track_state == TrackState.CONFIRM:
                self.confirm_counter += 1
                if self.confirm_counter >= self.confirm_hits:
                    self.track_state = TrackState.TRACK
            elif self.track_state in (TrackState.COAST, TrackState.RECOVER):
                self.track_state = TrackState.TRACK

        else:
            # No measurement -> PREDICTED / COAST
            self.consecutive_measured = 0
            self.consecutive_coasted += 1
            self.frame_state = FrameState.PREDICTED

            # Check age since last fresh measurement
            meas_age = (
                (timestamp - self.last_measured_timestamp)
                if self.last_measured_timestamp
                else 999.0
            )

            if self.track_state in (TrackState.TRACK, TrackState.CONFIRM):
                self.track_state = TrackState.COAST

            if (
                self.track_state == TrackState.COAST
                and meas_age > self.max_coast_s
            ):
                self.track_state = TrackState.RECOVER

            if meas_age > self.lost_age_s:
                self.track_state = TrackState.LOST
                self.frame_state = FrameState.LOST

        # Calculate 95% Confidence Uncertainty Radius: r95 = 2.4477 * sqrt(lambda_max(P_pos))
        P_pos = self.P[0:2, 0:2]
        eigvals = np.linalg.eigvalsh(P_pos)
        max_eig = max(1e-4, float(np.max(eigvals)))
        r95 = float(2.4477 * np.sqrt(max_eig))

        meas_age_s = float(
            (timestamp - self.last_measured_timestamp)
            if self.last_measured_timestamp
            else 999.0
        )

        return TrackEstimate(
            frame_id=measurement.frame_id,
            timestamp=timestamp,
            state=self.track_state,
            frame_state=self.frame_state,
            estimated_x=float(self.state[0]),
            estimated_y=float(self.state[1]),
            estimated_vx=float(self.state[2]),
            estimated_vy=float(self.state[3]),
            uncertainty_r95=r95,
            measurement_age_s=meas_age_s,
            consecutive_measured=self.consecutive_measured,
            consecutive_coasted=self.consecutive_coasted,
        )
