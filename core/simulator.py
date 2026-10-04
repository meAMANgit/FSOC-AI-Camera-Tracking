"""
DRISHTI-PAT Optical Scene & Virtual Camera Simulator
High-fidelity simulation of:
- >=2000x2000 world scene with realistic celestial/atmospheric background
- 640x480 FPA Camera with 4x3 deg FOV (160 px/deg)
- Beacon kinematics: Straight LEO pass, Circular orbit, Figure-8, Bounded random walk
- Atmospheric scintillation, shot/readout noise, micro-jitter vibrations
- Sensor-fixed defects (Hot/Dead pixels) injected POST-rendering (at FPA level)
- Optical scene distractors (moving with scene)
- Dynamic occlusion events
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from core.contracts import FramePacket


class TrajectoryType:
    LEO_PASS = "LEO_PASS"
    CIRCULAR = "CIRCULAR"
    FIGURE_EIGHT = "FIGURE_EIGHT"
    RANDOM_WALK = "RANDOM_WALK"


class WorldSimulator:
    def __init__(
        self,
        scene_width_deg: float = 16.0,
        scene_height_deg: float = 12.0,
        fpa_width: int = 640,
        fpa_height: int = 480,
        fov_x_deg: float = 4.0,
        fov_y_deg: float = 3.0,
        camera_fps: float = 30.0,
        seed: int = 42,
    ):
        self.scene_w_deg = scene_width_deg
        self.scene_h_deg = scene_height_deg
        self.fpa_w = fpa_width
        self.fpa_h = fpa_height
        self.fov_x_deg = fov_x_deg
        self.fov_y_deg = fov_y_deg
        self.fps = camera_fps
        self.dt = 1.0 / camera_fps
        self.seed = seed
        self.rng = np.random.RandomState(seed)

        # Angular resolution: pixels per degree
        self.px_per_deg_x = self.fpa_w / self.fov_x_deg  # 160 px/deg
        self.px_per_deg_y = self.fpa_h / self.fov_y_deg  # 160 px/deg

        # World background dimensions in pixels (e.g. 2560x1920)
        self.world_w_px = int(self.scene_w_deg * self.px_per_deg_x)
        self.world_h_px = int(self.scene_h_deg * self.px_per_deg_y)

        # Generate textured background (stars, background gradient, nebula/atmospheric glow)
        self._generate_world_background()

        # Beacon State (in World Angular coordinates in degrees, origin at center)
        self.beacon_pos_deg = np.array([0.0, 0.0], dtype=np.float64)
        self.beacon_vel_deg = np.array([0.5, 0.2], dtype=np.float64)
        self.beacon_size_px = 8.0  # Gaussian sigma ~ 2.0 px
        self.beacon_peak_intensity = 220.0
        self.trajectory_type = TrajectoryType.LEO_PASS

        # Disturbance settings
        self.noise_std = 6.0  # Sensor noise sigma (0-255 scale)
        self.turbulence_strength = 0.25  # Scintillation variance
        self.jitter_amplitude_deg = (
            0.015  # Platform micro-jitter in degrees (~2.4 px)
        )
        self.jitter_phase = 0.0

        # Sensor artifacts (Hot pixels fixed on FPA coordinates)
        self.hot_pixels: list[tuple[int, int, float]] = []  # (u, v, intensity)
        self._init_hot_pixels(count=4)

        # Scene distractors (Glints fixed in World Scene)
        self.scene_distractors: list[tuple[float, float, float, float]] = (
            []
        )  # (world_x_deg, world_y_deg, size, intensity)
        self._init_scene_distractors(count=5)

        # Occlusion state
        self.occlusion_active = False
        self.occlusion_timer = 0.0

        # Frame counter & time
        self.sim_time = 0.0
        self.frame_id = 0

        # Trajectory parameters
        self._init_trajectory_params()

    def _generate_world_background(self):
        """Creates a realistic deep space / high altitude sky with faint stars and atmospheric glow."""
        self.world_bg = np.zeros(
            (self.world_h_px, self.world_w_px), dtype=np.float32
        )
        # Add smooth background gradient / illumination
        y_coords = np.linspace(15, 30, self.world_h_px, dtype=np.float32)[
            :, None
        ]
        self.world_bg += y_coords

        # Add faint celestial stars
        num_stars = 300
        star_x = self.rng.randint(0, self.world_w_px, size=num_stars)
        star_y = self.rng.randint(0, self.world_h_px, size=num_stars)
        star_brightness = self.rng.uniform(30.0, 80.0, size=num_stars)
        for x, y, b in zip(star_x, star_y, star_brightness):
            cv2.circle(self.world_bg, (int(x), int(y)), 1, float(b), -1)

    def _init_hot_pixels(self, count: int = 4):
        """Initializes sensor-fixed defect pixels (defects in the detector array)."""
        self.hot_pixels = []
        # Place a prominent hot pixel near center tracking region to challenge the tracker
        self.hot_pixels.append((345, 230, 240.0))
        self.hot_pixels.append((280, 260, 210.0))
        for _ in range(count - 2):
            u = self.rng.randint(50, self.fpa_w - 50)
            v = self.rng.randint(50, self.fpa_h - 50)
            intensity = self.rng.uniform(190.0, 250.0)
            self.hot_pixels.append((u, v, intensity))

    def _init_scene_distractors(self, count: int = 5):
        """Places optical distractors (e.g. debris, stars, glints) that move with scene."""
        self.scene_distractors = []
        for _ in range(count):
            wx = self.rng.uniform(
                -self.scene_w_deg * 0.4, self.scene_w_deg * 0.4
            )
            wy = self.rng.uniform(
                -self.scene_h_deg * 0.4, self.scene_h_deg * 0.4
            )
            size = self.rng.uniform(4.0, 10.0)
            intensity = self.rng.uniform(120.0, 180.0)
            self.scene_distractors.append((wx, wy, size, intensity))

    def _init_trajectory_params(self):
        """Initializes kinematic parameters for each trajectory type."""
        self.traj_speed_dps = 0.8
        self.orbit_radius_deg = 1.5
        self.orbit_period_s = 8.0
        self.rw_state = np.array([0.0, 0.0])
        self.rw_vel = np.array([0.3, 0.2])

    def set_trajectory(self, trajectory_type: str):
        self.trajectory_type = trajectory_type
        self.sim_time = 0.0
        self.frame_id = 0
        self._update_trajectory_position(0.0)

    def _update_trajectory_position(self, t: float):
        if self.trajectory_type == TrajectoryType.LEO_PASS:
            x = -1.5 + self.traj_speed_dps * t
            y = 0.6 * np.sin(0.4 * t) - 0.2
            self.beacon_pos_deg = np.array([x, y], dtype=np.float64)
            self.beacon_vel_deg = np.array(
                [self.traj_speed_dps, 0.6 * 0.4 * np.cos(0.4 * t)],
                dtype=np.float64,
            )
        elif self.trajectory_type == TrajectoryType.CIRCULAR:
            omega = 2.0 * np.pi / self.orbit_period_s
            x = self.orbit_radius_deg * np.cos(omega * t)
            y = self.orbit_radius_deg * np.sin(omega * t)
            self.beacon_pos_deg = np.array([x, y], dtype=np.float64)
            self.beacon_vel_deg = np.array(
                [
                    -self.orbit_radius_deg * omega * np.sin(omega * t),
                    self.orbit_radius_deg * omega * np.cos(omega * t),
                ],
                dtype=np.float64,
            )
        elif self.trajectory_type == TrajectoryType.FIGURE_EIGHT:
            omega = 2.0 * np.pi / 6.0
            x = 1.8 * np.sin(omega * t)
            y = 1.0 * np.sin(2.0 * omega * t)
            self.beacon_pos_deg = np.array([x, y], dtype=np.float64)
            self.beacon_vel_deg = np.array(
                [
                    1.8 * omega * np.cos(omega * t),
                    2.0 * omega * np.cos(2.0 * omega * t),
                ],
                dtype=np.float64,
            )
        elif self.trajectory_type == TrajectoryType.RANDOM_WALK:
            self.beacon_pos_deg = self.rw_state.copy()
            self.beacon_vel_deg = self.rw_vel.copy()

    def trigger_occlusion(self, duration_s: float = 1.5):
        """Triggers a cloud or structural occlusion event for a specified duration."""
        self.occlusion_active = True
        self.occlusion_timer = duration_s

    def update_physics(self, dt: float | None = None):
        """Steps target kinematics and disturbances forward in time."""
        if dt is None:
            dt = self.dt
        self.sim_time += dt
        self.frame_id += 1
        t = self.sim_time

        # Update Occlusion
        if self.occlusion_active:
            self.occlusion_timer -= dt
            if self.occlusion_timer <= 0:
                self.occlusion_active = False

        # Update Beacon Trajectory in World Angular Coordinates (deg)
        self._update_trajectory_position(t)

    def render_camera_frame(
        self,
        gimbal_pan_deg: float,
        gimbal_tilt_deg: float,
        gimbal_pan_rate: float = 0.0,
        gimbal_tilt_rate: float = 0.0,
    ) -> tuple[FramePacket, dict[str, Any]]:
        """
        Renders the 640x480 FPA camera frame for the current gimbal pointing state.
        Returns:
            - FramePacket (Public tracker input: pixels + declared encoder angles)
            - Ground Truth Dict (Private reference for independent evaluator)
        """
        # Calculate platform micro-jitter
        self.jitter_phase += (
            2.0 * np.pi * 12.0 * self.dt
        )  # 12 Hz platform resonance
        jitter_x = self.jitter_amplitude_deg * np.sin(self.jitter_phase)
        jitter_y = self.jitter_amplitude_deg * np.cos(self.jitter_phase * 1.3)

        # Net optical pointing angle of camera
        eff_cam_pan = gimbal_pan_deg + jitter_x
        eff_cam_tilt = gimbal_tilt_deg + jitter_y

        # Camera boresight center in world pixels
        world_center_x = (
            self.scene_w_deg / 2.0 + eff_cam_pan
        ) * self.px_per_deg_x
        world_center_y = (
            self.scene_h_deg / 2.0 - eff_cam_tilt
        ) * self.px_per_deg_y

        # Compute crop bounding box from world background
        crop_x1 = int(world_center_x - self.fpa_w / 2)
        crop_y1 = int(world_center_y - self.fpa_h / 2)
        crop_x2 = crop_x1 + self.fpa_w
        crop_y2 = crop_y1 + self.fpa_h

        # Extract background patch with boundary padding if necessary
        frame = np.zeros((self.fpa_h, self.fpa_w), dtype=np.float32)
        src_x1 = max(0, crop_x1)
        src_y1 = max(0, crop_y1)
        src_x2 = min(self.world_w_px, crop_x2)
        src_y2 = min(self.world_h_px, crop_y2)

        dst_x1 = src_x1 - crop_x1
        dst_y1 = src_y1 - crop_y1
        dst_x2 = dst_x1 + (src_x2 - src_x1)
        dst_y2 = dst_y1 + (src_y2 - src_y1)

        if src_x2 > src_x1 and src_y2 > src_y1:
            frame[dst_y1:dst_y2, dst_x1:dst_x2] = self.world_bg[
                src_y1:src_y2, src_x1:src_x2
            ]

        # Render Scene Distractors (Glints/stars moving with the scene)
        for wx, wy, dsize, dintens in self.scene_distractors:
            # Project world angular coordinate to FPA sensor pixel coordinate
            rel_x_deg = wx - eff_cam_pan
            rel_y_deg = wy - eff_cam_tilt
            u = self.fpa_w / 2.0 + rel_x_deg * self.px_per_deg_x
            v = self.fpa_h / 2.0 - rel_y_deg * self.px_per_deg_y
            if 0 <= u < self.fpa_w and 0 <= v < self.fpa_h:
                self._draw_gaussian_spot(frame, u, v, dsize, dintens)

        # Calculate True Beacon Projection on FPA sensor coordinates (u, v)
        rel_beacon_x_deg = self.beacon_pos_deg[0] - eff_cam_pan
        rel_beacon_y_deg = self.beacon_pos_deg[1] - eff_cam_tilt
        true_u = self.fpa_w / 2.0 + rel_beacon_x_deg * self.px_per_deg_x
        true_v = self.fpa_h / 2.0 - rel_beacon_y_deg * self.px_per_deg_y

        is_in_fov = (0 <= true_u < self.fpa_w) and (0 <= true_v < self.fpa_h)
        is_visible = is_in_fov and (not self.occlusion_active)

        # Scintillation / Atmospheric turbulence effect on beacon intensity
        scintillation_factor = 1.0
        if self.turbulence_strength > 0:
            # Log-normal intensity fluctuation
            log_i = self.rng.normal(
                -0.5 * self.turbulence_strength,
                np.sqrt(self.turbulence_strength),
            )
            scintillation_factor = float(np.clip(np.exp(log_i), 0.2, 2.5))

        current_beacon_intensity = (
            self.beacon_peak_intensity * scintillation_factor
        )
        if self.occlusion_active:
            current_beacon_intensity *= 0.05  # heavily occluded / dim

        # Render True Beacon on frame if visible
        if is_in_fov and current_beacon_intensity > 5.0:
            self._draw_gaussian_spot(
                frame,
                true_u,
                true_v,
                self.beacon_size_px,
                current_beacon_intensity,
            )

        # -------------------------------------------------------------
        # SENSOR DEFECTS (HOT PIXELS): INJECTED POST-RENDER AT SENSOR FPA
        # -------------------------------------------------------------
        # Notice: Hot pixels are injected at constant (u, v) regardless of camera pan/tilt!
        for u, v, intensity in self.hot_pixels:
            if 0 <= u < self.fpa_w and 0 <= v < self.fpa_h:
                self._draw_gaussian_spot(
                    frame, float(u), float(v), size_px=3.5, peak_val=intensity
                )

        # Add Sensor Noise (Poisson shot noise approximation + Gaussian readout noise)
        if self.noise_std > 0:
            noise = self.rng.normal(0, self.noise_std, size=frame.shape).astype(
                np.float32
            )
            frame += noise

        # Quantize and clip to uint8
        frame_uint8 = np.clip(frame, 0, 255).astype(np.uint8)

        # Assemble Public FramePacket for Tracker
        packet = FramePacket(
            frame_id=self.frame_id,
            timestamp_capture=self.sim_time,
            timestamp_arrival=self.sim_time + 0.002,  # 2ms transport delay
            pixels=frame_uint8,
            source="SIMULATOR",
            camera_pan_deg=gimbal_pan_deg,
            camera_tilt_deg=gimbal_tilt_deg,
            pan_rate_dps=gimbal_pan_rate,
            tilt_rate_dps=gimbal_tilt_rate,
            fov_deg=(self.fov_x_deg, self.fov_y_deg),
        )

        # Assemble Private Ground Truth for Independent Evaluator
        truth = {
            "frame_id": self.frame_id,
            "timestamp": self.sim_time,
            "true_u": true_u,
            "true_v": true_v,
            "is_in_fov": is_in_fov,
            "is_visible": is_visible,
            "beacon_pos_deg": self.beacon_pos_deg.copy(),
            "beacon_vel_deg": self.beacon_vel_deg.copy(),
            "eff_cam_pan": eff_cam_pan,
            "eff_cam_tilt": eff_cam_tilt,
            "scintillation": scintillation_factor,
            "occluded": self.occlusion_active,
            "hot_pixels": list(self.hot_pixels),
        }

        return packet, truth

    def _draw_gaussian_spot(
        self,
        img: np.ndarray,
        u: float,
        v: float,
        size_px: float,
        peak_val: float,
    ):
        """Renders sub-pixel centered 2D Gaussian optical spot."""
        radius = int(np.ceil(size_px * 2.5))
        u_int = round(u)
        v_int = round(v)

        y1 = max(0, v_int - radius)
        y2 = min(img.shape[0], v_int + radius + 1)
        x1 = max(0, u_int - radius)
        x2 = min(img.shape[1], u_int + radius + 1)

        if y2 <= y1 or x2 <= x1:
            return

        sigma = size_px / 2.355  # FWHM to sigma
        yy, xx = np.ogrid[y1:y2, x1:x2]
        dist_sq = (xx - u) ** 2 + (yy - v) ** 2
        gaussian = peak_val * np.exp(-dist_sq / (2.0 * sigma**2))
        img[y1:y2, x1:x2] = np.maximum(img[y1:y2, x1:x2], gaussian)
