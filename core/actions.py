"""
DRISHTI-PAT Precision-on-Demand Measurement Actions
Implements the 6 core action operators:
1. FAST: Direct current-frame sub-pixel centroiding (low cost ~0.2ms)
2. SUPPORT: Multi-frame temporal patch alignment & stacking for weak beacons (~1.5ms)
3. FIT: Gauss-Newton 2D Gaussian spot model fit for ambiguous / high-clutter targets (~2.0ms)
4. WIDEN: Enlarge search window from 96px to 192px on motion disagreement
5. GLOBAL: Full-frame candidate acquisition
6. DEFER: Kalman coasting without measurement corruption on total loss/occlusion
"""

from __future__ import annotations

from collections import deque

import numpy as np

from core.contracts import (
    ActionType,
    Candidate,
    FramePacket,
    Measurement,
)


class ActionExecutor:
    def __init__(self, history_buffer_size: int = 5):
        self.history_buffer_size = history_buffer_size
        # Buffer of past frames: list of (timestamp, image, camera_pan, camera_tilt)
        self.frame_history = deque(maxlen=history_buffer_size)

    def push_frame_history(self, packet: FramePacket):
        self.frame_history.append(
            {
                "frame_id": packet.frame_id,
                "timestamp": packet.timestamp_capture,
                "pixels": packet.pixels.copy(),
                "pan_deg": packet.camera_pan_deg,
                "tilt_deg": packet.camera_tilt_deg,
            }
        )

    def reset_history(self):
        self.frame_history.clear()

    def execute_fast(self, packet: FramePacket, cand: Candidate) -> Measurement:
        """FAST Action: Direct sub-pixel centroid on current frame."""
        img = packet.pixels
        h, w = img.shape
        u_int, v_int = round(cand.x), round(cand.y)
        radius = 7

        y1, y2 = max(0, v_int - radius), min(h, v_int + radius + 1)
        x1, x2 = max(0, u_int - radius), min(w, u_int + radius + 1)

        patch = img[y1:y2, x1:x2].astype(np.float32)
        bg = float(np.percentile(patch, 20))
        weights = np.maximum(0.0, patch - bg)
        w_sum = np.sum(weights)

        if w_sum > 1e-3:
            yy, xx = np.mgrid[0 : (y2 - y1), 0 : (x2 - x1)]
            sub_x = x1 + np.sum(xx * weights) / w_sum
            sub_y = y1 + np.sum(yy * weights) / w_sum
            var_x = float(np.sum((xx - (sub_x - x1)) ** 2 * weights) / w_sum)
            var_y = float(np.sum((yy - (sub_y - y1)) ** 2 * weights) / w_sum)
            cov_diag = (
                max(0.05, var_x / max(1.0, cand.snr_sigma)),
                max(0.05, var_y / max(1.0, cand.snr_sigma)),
            )
        else:
            sub_x, sub_y = cand.x, cand.y
            cov_diag = (0.5, 0.5)

        return Measurement(
            frame_id=packet.frame_id,
            is_valid=True,
            centroid_x=float(sub_x),
            centroid_y=float(sub_y),
            intensity=cand.peak_intensity,
            snr_sigma=cand.snr_sigma,
            covariance_diag=cov_diag,
            action_used=ActionType.FAST,
            support_frames_count=1,
        )

    def execute_support(
        self,
        packet: FramePacket,
        cand: Candidate,
        est_vx_px_s: float = 0.0,
        est_vy_px_s: float = 0.0,
    ) -> Measurement:
        """
        SUPPORT Action: Aligns 2 to 4 trailing historical patches to boost SNR on weak signals.
        Refines centroid on the temporally enhanced stack.
        """
        img = packet.pixels
        h, w = img.shape
        u_int, v_int = round(cand.x), round(cand.y)
        radius = 12

        y1, y2 = max(0, v_int - radius), min(h, v_int + radius + 1)
        x1, x2 = max(0, u_int - radius), min(w, u_int + radius + 1)
        curr_patch = img[y1:y2, x1:x2].astype(np.float32)

        stacked_patch = curr_patch.copy()
        count = 1

        # Motion compensate past frames from history buffer
        for hist in reversed(list(self.frame_history)[:-1]):
            dt = packet.timestamp_capture - hist["timestamp"]
            if dt <= 0 or dt > 0.2:
                continue

            # Target displacement in sensor pixels
            dx_target = est_vx_px_s * dt
            dy_target = est_vy_px_s * dt

            # Gimbal displacement compensation
            d_pan_deg = packet.camera_pan_deg - hist["pan_deg"]
            d_tilt_deg = packet.camera_tilt_deg - hist["tilt_deg"]
            dx_gimbal = d_pan_deg * (w / packet.fov_deg[0])
            dy_gimbal = -d_tilt_deg * (h / packet.fov_deg[1])

            # Past target position on historical frame
            past_x = u_int - (dx_target + dx_gimbal)
            past_y = v_int - (dy_target + dy_gimbal)

            hy1, hy2 = max(0, round(past_y - radius)), min(
                h, round(past_y + radius + 1)
            )
            hx1, hx2 = max(0, round(past_x - radius)), min(
                w, round(past_x + radius + 1)
            )

            if (hy2 - hy1) == (y2 - y1) and (hx2 - hx1) == (x2 - x1):
                hist_patch = hist["pixels"][hy1:hy2, hx1:hx2].astype(np.float32)
                stacked_patch += hist_patch
                count += 1
            if count >= 4:
                break

        # Average patch
        stacked_patch /= count
        bg = float(np.percentile(stacked_patch, 25))
        weights = np.maximum(0.0, stacked_patch - bg)
        w_sum = np.sum(weights)

        if w_sum > 1e-3:
            yy, xx = np.mgrid[0 : (y2 - y1), 0 : (x2 - x1)]
            sub_x = x1 + np.sum(xx * weights) / w_sum
            sub_y = y1 + np.sum(yy * weights) / w_sum
            boosted_snr = cand.snr_sigma * np.sqrt(count)
            cov_diag = (0.3 / count, 0.3 / count)
        else:
            sub_x, sub_y = cand.x, cand.y
            boosted_snr = cand.snr_sigma
            cov_diag = (0.6, 0.6)

        return Measurement(
            frame_id=packet.frame_id,
            is_valid=True,
            centroid_x=float(sub_x),
            centroid_y=float(sub_y),
            intensity=cand.peak_intensity,
            snr_sigma=float(boosted_snr),
            covariance_diag=cov_diag,
            action_used=ActionType.SUPPORT,
            support_frames_count=count,
        )

    def execute_fit(self, packet: FramePacket, cand: Candidate) -> Measurement:
        """
        FIT Action: Gauss-Newton 2D Gaussian spot model fitting.
        Model: I(x, y) = I_bg + A * exp(-((x-x0)^2 + (y-y0)^2) / (2 * sigma^2))
        """
        img = packet.pixels
        h, w = img.shape
        u_int, v_int = round(cand.x), round(cand.y)
        radius = 8

        y1, y2 = max(0, v_int - radius), min(h, v_int + radius + 1)
        x1, x2 = max(0, u_int - radius), min(w, u_int + radius + 1)
        patch = img[y1:y2, x1:x2].astype(np.float32)
        ph, pw = patch.shape

        if ph < 5 or pw < 5:
            return self.execute_fast(packet, cand)

        # Initial parameter estimates
        x0_init = cand.x - x1
        y0_init = cand.y - y1
        bg_init = float(np.min(patch))
        amp_init = max(10.0, float(np.max(patch) - bg_init))
        sigma_init = 2.0

        # Gauss-Newton Iterations (max 5 iterations for speed < 1.5ms)
        x0, y0, amp, bg, sigma = x0_init, y0_init, amp_init, bg_init, sigma_init
        yy, xx = np.mgrid[0:ph, 0:pw]

        for _ in range(5):
            dist_sq = (xx - x0) ** 2 + (yy - y0) ** 2
            exp_term = np.exp(-dist_sq / (2.0 * sigma**2 + 1e-4))
            model = bg + amp * exp_term
            residual = (patch - model).ravel()

            # Partial derivatives
            d_x0 = (amp * exp_term * (xx - x0) / (sigma**2 + 1e-4)).ravel()
            d_y0 = (amp * exp_term * (yy - y0) / (sigma**2 + 1e-4)).ravel()
            d_amp = exp_term.ravel()
            d_bg = np.ones_like(d_amp)

            J = np.column_stack((d_x0, d_y0, d_amp, d_bg))
            try:
                delta, _, _, _ = np.linalg.lstsq(J, residual, rcond=1e-3)
                x0 += np.clip(delta[0], -1.5, 1.5)
                y0 += np.clip(delta[1], -1.5, 1.5)
                amp += np.clip(delta[2], -20.0, 20.0)
                bg += np.clip(delta[3], -10.0, 10.0)
            except np.linalg.LinAlgError:
                break

        full_x = float(np.clip(x1 + x0, 0, w - 1))
        full_y = float(np.clip(y1 + y0, 0, h - 1))

        return Measurement(
            frame_id=packet.frame_id,
            is_valid=True,
            centroid_x=full_x,
            centroid_y=full_y,
            intensity=float(amp + bg),
            snr_sigma=cand.snr_sigma,
            covariance_diag=(0.1, 0.1),
            action_used=ActionType.FIT,
            support_frames_count=1,
        )

    def execute_defer(
        self, packet: FramePacket, reason: str = "No supported measurement"
    ) -> Measurement:
        """DEFER Action: Skips measurement update on missing/occluded signal."""
        return Measurement(
            frame_id=packet.frame_id,
            is_valid=False,
            centroid_x=None,
            centroid_y=None,
            intensity=0.0,
            snr_sigma=0.0,
            action_used=ActionType.DEFER,
            rejection_reason=reason,
        )
