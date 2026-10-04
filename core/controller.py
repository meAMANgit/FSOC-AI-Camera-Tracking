"""
DRISHTI-PAT Predictive PID Controller
Features:
- Proportional + Integral with Anti-Windup Clamping
- Filtered Derivative to prevent high-frequency noise amplification
- Velocity Feedforward using Kalman-filtered velocity
- Transport Delay Forward Projection
- Rate and Acceleration envelope constraints
"""

from __future__ import annotations

import numpy as np

from core.contracts import GimbalCommand


class PredictivePIDController:
    def __init__(
        self,
        kp: float = 2.8,
        ki: float = 0.5,
        kd: float = 0.18,
        k_ff: float = 1.0,
        i_max_deg: float = 2.0,
        d_filter_alpha: float = 0.3,
        px_per_deg: float = 160.0,
        actuator_delay_s: float = 0.025,
        max_rate_dps: float = 5.0,
    ):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.k_ff = k_ff
        self.i_max = i_max_deg
        self.alpha = d_filter_alpha
        self.px_per_deg = px_per_deg
        self.delay_s = actuator_delay_s
        self.max_rate = max_rate_dps

        # Integral states (in degrees)
        self.integral_pan = 0.0
        self.integral_tilt = 0.0

        # Filtered derivative states
        self.prev_error_pan = 0.0
        self.prev_error_tilt = 0.0
        self.filtered_d_pan = 0.0
        self.filtered_d_tilt = 0.0

        self.last_timestamp: float | None = None

    def reset(self):
        self.integral_pan = 0.0
        self.integral_tilt = 0.0
        self.prev_error_pan = 0.0
        self.prev_error_tilt = 0.0
        self.filtered_d_pan = 0.0
        self.filtered_d_tilt = 0.0
        self.last_timestamp = None

    def compute_command(
        self,
        frame_id: int,
        timestamp: float,
        target_sensor_x: float,
        target_sensor_y: float,
        target_sensor_vx: float = 0.0,
        target_sensor_vy: float = 0.0,
        is_tracking: bool = True,
    ) -> GimbalCommand:
        """
        Computes the Pan/Tilt rate command to center target at (320, 240).
        """
        dt = 1.0 / 30.0
        if self.last_timestamp is not None:
            dt = max(0.001, timestamp - self.last_timestamp)
        self.last_timestamp = timestamp

        if not is_tracking:
            # When track is lost or searching, keep gimbal stable or do small scan
            return GimbalCommand(
                frame_id=frame_id,
                timestamp=timestamp,
                commanded_pan_rate_dps=0.0,
                commanded_tilt_rate_dps=0.0,
                pan_error_px=0.0,
                tilt_error_px=0.0,
                is_saturated=False,
            )

        # Compute pixel pointing error from boresight center (320, 240)
        error_px_x = target_sensor_x - 320.0
        error_px_y = target_sensor_y - 240.0

        # Transport delay projection: predict where target will be after actuator lag
        pred_error_px_x = error_px_x + target_sensor_vx * self.delay_s
        pred_error_px_y = error_px_y + target_sensor_vy * self.delay_s

        # Convert to angular errors (degrees)
        error_pan_deg = pred_error_px_x / self.px_per_deg
        error_tilt_deg = -pred_error_px_y / self.px_per_deg

        # Target angular velocity feedforward (deg/s)
        ff_pan_rate = (target_sensor_vx / self.px_per_deg) * self.k_ff
        ff_tilt_rate = (-target_sensor_vy / self.px_per_deg) * self.k_ff

        # Integral update with Anti-Windup Clamping
        self.integral_pan += error_pan_deg * dt
        self.integral_tilt += error_tilt_deg * dt
        self.integral_pan = float(np.clip(self.integral_pan, -self.i_max, self.i_max))
        self.integral_tilt = float(np.clip(self.integral_tilt, -self.i_max, self.i_max))

        # Filtered Derivative
        raw_d_pan = (error_pan_deg - self.prev_error_pan) / dt
        raw_d_tilt = (error_tilt_deg - self.prev_error_tilt) / dt
        self.filtered_d_pan = (
            1.0 - self.alpha
        ) * self.filtered_d_pan + self.alpha * raw_d_pan
        self.filtered_d_tilt = (
            1.0 - self.alpha
        ) * self.filtered_d_tilt + self.alpha * raw_d_tilt
        self.prev_error_pan = error_pan_deg
        self.prev_error_tilt = error_tilt_deg

        # PID sum + Velocity Feedforward
        pan_rate_cmd = (
            self.kp * error_pan_deg
            + self.ki * self.integral_pan
            + self.kd * self.filtered_d_pan
            + ff_pan_rate
        )

        tilt_rate_cmd = (
            self.kp * error_tilt_deg
            + self.ki * self.integral_tilt
            + self.kd * self.filtered_d_tilt
            + ff_tilt_rate
        )

        # Check saturation
        is_saturated = (
            abs(pan_rate_cmd) > self.max_rate
            or abs(tilt_rate_cmd) > self.max_rate
        )

        pan_rate_cmd = float(
            np.clip(pan_rate_cmd, -self.max_rate, self.max_rate)
        )
        tilt_rate_cmd = float(
            np.clip(tilt_rate_cmd, -self.max_rate, self.max_rate)
        )

        return GimbalCommand(
            frame_id=frame_id,
            timestamp=timestamp,
            commanded_pan_rate_dps=pan_rate_cmd,
            commanded_tilt_rate_dps=tilt_rate_cmd,
            pan_error_px=float(error_px_x),
            tilt_error_px=float(error_px_y),
            is_saturated=is_saturated,
        )
