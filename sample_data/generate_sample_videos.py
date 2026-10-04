"""
DRISHTI-PAT Sample Video Generator
Generates realistic optical benchmark MP4 videos for standalone offline testing.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import cv2

from core.simulator import TrajectoryType, WorldSimulator


def generate_benchmark_mp4(
    output_path: str,
    trajectory: str = TrajectoryType.LEO_PASS,
    duration_s: float = 6.0,
    fps: float = 30.0,
    noise_std: float = 6.0,
    hot_pixel_count: int = 3,
    occlusion_start_s: float = 2.5,
    occlusion_duration_s: float = 0.8,
):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    sim = WorldSimulator(camera_fps=fps, seed=101)
    sim.set_trajectory(trajectory)
    sim.noise_std = noise_std
    sim._init_hot_pixels(count=hot_pixel_count)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (640, 480), isColor=False)

    total_frames = int(duration_s * fps)
    dt = 1.0 / fps

    for i in range(total_frames):
        t = i * dt
        if occlusion_start_s and (
            occlusion_start_s <= t < occlusion_start_s + occlusion_duration_s
        ) and not sim.occlusion_active:
            sim.trigger_occlusion(occlusion_duration_s)

        packet, _ = sim.render_camera_frame(gimbal_pan_deg=0.0, gimbal_tilt_deg=0.0)
        out.write(packet.pixels)
        sim.update_physics(dt)

    out.release()
    print(
        f"Generated benchmark video: {output_path} ({total_frames} frames, {duration_s}s)"
    )


if __name__ == "__main__":
    sample_dir = os.path.join(os.path.dirname(__file__))
    generate_benchmark_mp4(
        os.path.join(sample_dir, "sample_leo_pass.mp4"), TrajectoryType.LEO_PASS, 6.0
    )
    generate_benchmark_mp4(
        os.path.join(sample_dir, "sample_figure8.mp4"), TrajectoryType.FIGURE_EIGHT, 6.0
    )
    generate_benchmark_mp4(
        os.path.join(sample_dir, "sample_turbulent.mp4"),
        TrajectoryType.RANDOM_WALK,
        6.0,
        noise_std=12.0,
    )
