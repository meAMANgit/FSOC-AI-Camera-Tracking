"""
DRISHTI-PAT 2-Axis PTZ Gimbal Mechanics
Implements physical actuator dynamics with rate/acceleration limits and delay.
"""

from __future__ import annotations

from collections import deque

import numpy as np


class GimbalActuator:
    def __init__(
        self,
        max_rate_dps: float = 5.0,  # Max angular rate: 5-10 deg/s (ISRO spec default: 5)
        max_accel_dps2: float = 25.0,  # Max angular acceleration: deg/s^2
        pan_limit_deg: float = 45.0,
        tilt_limit_deg: float = 30.0,
        actuator_delay_s: float = 0.025,  # 25ms physical actuator response lag
        dt: float = 1.0 / 30.0,
    ):
        self.max_rate = max_rate_dps
        self.max_accel = max_accel_dps2
        self.pan_limit = pan_limit_deg
        self.tilt_limit = tilt_limit_deg
        self.delay_s = actuator_delay_s
        self.dt = dt

        # Current actual state
        self.pan_deg = 0.0
        self.tilt_deg = 0.0
        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0

        # Delay queue for commanded rates: stores (timestamp, pan_rate_cmd, tilt_rate_cmd)
        self.cmd_queue = deque()
        self.current_time = 0.0

    def reset(self, pan: float = 0.0, tilt: float = 0.0):
        self.pan_deg = pan
        self.tilt_deg = tilt
        self.pan_rate_dps = 0.0
        self.tilt_rate_dps = 0.0
        self.cmd_queue.clear()
        self.current_time = 0.0

    def command_rates(
        self, pan_rate_cmd: float, tilt_rate_cmd: float, current_time: float
    ):
        """Buffers the commanded rate for the actuator delay."""
        self.current_time = current_time
        self.cmd_queue.append(
            (current_time + self.delay_s, pan_rate_cmd, tilt_rate_cmd)
        )

    def update(self, dt: float) -> tuple[float, float, float, float]:
        """
        Steps physical gimbal state forward by dt.
        Returns: (pan_deg, tilt_deg, pan_rate_dps, tilt_rate_dps)
        """
        # Retrieve active command after delay
        active_cmd_pan = self.pan_rate_dps
        active_cmd_tilt = self.tilt_rate_dps

        while self.cmd_queue and self.cmd_queue[0][0] <= self.current_time:
            _, p_cmd, t_cmd = self.cmd_queue.popleft()
            active_cmd_pan = p_cmd
            active_cmd_tilt = t_cmd

        # Apply rate limits
        target_pan_rate = float(np.clip(active_cmd_pan, -self.max_rate, self.max_rate))
        target_tilt_rate = float(np.clip(active_cmd_tilt, -self.max_rate, self.max_rate))

        # Apply acceleration limits to rate transitions
        max_delta_rate = self.max_accel * dt
        delta_pan = float(np.clip(
            target_pan_rate - self.pan_rate_dps, -max_delta_rate, max_delta_rate
        ))
        delta_tilt = float(np.clip(
            target_tilt_rate - self.tilt_rate_dps, -max_delta_rate, max_delta_rate
        ))

        self.pan_rate_dps += delta_pan
        self.tilt_rate_dps += delta_tilt

        # Update angles
        self.pan_deg += self.pan_rate_dps * dt
        self.tilt_deg += self.tilt_rate_dps * dt

        # Apply physical travel limits
        self.pan_deg = float(np.clip(self.pan_deg, -self.pan_limit, self.pan_limit))
        self.tilt_deg = float(np.clip(self.tilt_deg, -self.tilt_limit, self.tilt_limit))

        return self.pan_deg, self.tilt_deg, self.pan_rate_dps, self.tilt_rate_dps
