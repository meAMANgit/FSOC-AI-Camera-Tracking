"""
DRISHTI-PAT Web Server & Real-Time Telemetry Engine
FastAPI + WebSockets application for live coarse optical tracking, ISRO benchmarks, and MP4 video analysis.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from typing import Any

import cv2
import pandas as pd
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.baselines import BaselineB0Tracker, BaselineB1Tracker
from core.benchmark_suite import BenchmarkSuite
from core.contracts import FramePacket
from core.gimbal import GimbalActuator
from core.simulator import WorldSimulator
from core.tracker import DRISHTIPATTracker
from core.video_adapter import VideoBenchmarkAdapter

app = FastAPI(title="DRISHTI-PAT Coarse Optical Tracking System", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), "reports"), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), "uploads"), exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Global Simulation & Tracking State Manager
class SimSession:
    def __init__(self):
        self.fps = 30.0
        self.dt = 1.0 / self.fps
        self.sim = WorldSimulator(camera_fps=self.fps, seed=42)
        self.gimbal = GimbalActuator(max_rate_dps=5.0, dt=self.dt)
        self.tracker = DRISHTIPATTracker()
        self.baseline_b0 = BaselineB0Tracker()
        self.baseline_b1 = BaselineB1Tracker()
        self.active_tracker_name = "DRISHTI_PAT"
        self.is_running = True
        self.mode = "SIMULATION"  # or "MP4"
        self.video_adapter: VideoBenchmarkAdapter | None = None
        self.video_frame_idx = 0
        self.audit_history: list[dict[str, Any]] = []

    def reset_simulation(self, trajectory: str = "LEO_PASS", seed: int = 42):
        self.sim = WorldSimulator(camera_fps=self.fps, seed=seed)
        self.sim.set_trajectory(trajectory)
        self.gimbal.reset()
        self.tracker.reset()
        self.baseline_b0.reset()
        self.baseline_b1.reset()
        self.audit_history.clear()

    def get_active_tracker(self):
        if self.active_tracker_name == "BASELINE_B0":
            return self.baseline_b0
        elif self.active_tracker_name == "BASELINE_B1":
            return self.baseline_b1
        return self.tracker


session = SimSession()


@app.get("/")
async def get_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>DRISHTI-PAT Telemetry Server</h1><p>Static index.html initializing...</p>"
    )


@app.get("/api/status")
async def get_status():
    return {
        "status": "online",
        "mode": session.mode,
        "tracker": session.active_tracker_name,
        "is_running": session.is_running,
        "trajectory": session.sim.trajectory_type,
        "fps": session.fps,
    }


class ConfigModel(BaseModel):
    tracker: str | None = None
    trajectory: str | None = None
    noise_std: float | None = None
    turbulence: float | None = None
    jitter: float | None = None
    hot_pixels: int | None = None
    target_size: float | None = None
    target_speed: float | None = None
    target_intensity: float | None = None
    is_running: bool | None = None


@app.post("/api/config")
async def update_config(config: ConfigModel):
    if config.tracker is not None:
        session.active_tracker_name = config.tracker
    if config.trajectory is not None:
        session.sim.set_trajectory(config.trajectory)
    if config.noise_std is not None:
        session.sim.noise_std = config.noise_std
    if config.turbulence is not None:
        session.sim.turbulence_strength = config.turbulence
    if config.jitter is not None:
        session.sim.jitter_amplitude_deg = config.jitter
    if config.hot_pixels is not None:
        session.sim._init_hot_pixels(config.hot_pixels)
    if config.target_size is not None:
        session.sim.target_size_px = config.target_size
    if config.target_speed is not None:
        session.sim.traj_speed_dps = config.target_speed
    if config.target_intensity is not None:
        session.sim.target_peak_intensity = config.target_intensity
    if config.is_running is not None:
        session.is_running = config.is_running
    return {"status": "success", "config": config.model_dump()}


@app.post("/api/trigger_occlusion")
async def trigger_occlusion(duration: float = 1.5):
    session.sim.trigger_occlusion(duration)
    return {"status": "occlusion_triggered", "duration_s": duration}


@app.post("/api/reset")
async def reset_sim(trajectory: str = "LEO_PASS"):
    session.reset_simulation(trajectory=trajectory)
    return {"status": "reset_complete"}


@app.post("/api/gimbal_slew")
async def gimbal_slew(
    pan_rate: float = 0.0, tilt_rate: float = 0.0, duration: float = 0.3
):
    """Applies a manual slew pulse to the gimbal."""
    session.gimbal.command_rates(pan_rate, tilt_rate, session.sim.sim_time)
    return {"status": "slew_commanded", "pan_rate": pan_rate, "tilt_rate": tilt_rate}


@app.post("/api/upload_video")
async def upload_video(file: UploadFile = File(...)):  # noqa: B008
    """Uploads a user MP4 video for native image-space tracking."""
    upload_path = os.path.join(os.path.dirname(__file__), "uploads", file.filename)
    content = await file.read()

    def _write_file():
        with open(upload_path, "wb") as f:
            f.write(content)

    await asyncio.to_thread(_write_file)

    session.mode = "MP4"
    session.video_adapter = VideoBenchmarkAdapter(upload_path)
    session.video_frame_idx = 0
    session.tracker.reset()
    info = session.video_adapter.get_info()
    return {"status": "video_loaded", "info": info}


@app.post("/api/load_sample_video")
async def load_sample_video(name: str = "sample_leo_pass.mp4"):
    sample_path = os.path.join(os.path.dirname(__file__), "sample_data", name)
    if not os.path.exists(sample_path):
        raise HTTPException(status_code=404, detail=f"Sample video {name} not found")

    session.mode = "MP4"
    session.video_adapter = VideoBenchmarkAdapter(sample_path)
    session.video_frame_idx = 0
    session.tracker.reset()
    return {"status": "sample_loaded", "info": session.video_adapter.get_info()}


@app.post("/api/switch_to_sim")
async def switch_to_sim():
    session.mode = "SIMULATION"
    if session.video_adapter:
        session.video_adapter.release()
        session.video_adapter = None
    session.reset_simulation()
    return {"status": "simulation_mode_active"}


@app.post("/api/run_benchmark")
async def run_benchmark(
    trajectory: str = "LEO_PASS",
    duration: float = 8.0,
    noise_std: float = 6.0,
    hot_pixels: int = 4,
    seed: int = 42,
):
    """Executes a full paired benchmark suite and returns comparative ISRO target card."""
    bench = BenchmarkSuite(seed=seed)
    comp = bench.run_paired_comparison(trajectory=trajectory, duration_s=duration)

    # Save CSV report
    report_csv_path = os.path.join(
        os.path.dirname(__file__),
        "reports",
        f"benchmark_{trajectory}_{int(time.time())}.csv",
    )
    pat_run = bench.run_scenario(
        "DRISHTI_PAT",
        trajectory=trajectory,
        duration_s=duration,
        noise_std=noise_std,
        hot_pixel_count=hot_pixels,
    )
    pat_run["dataframe"].to_csv(report_csv_path, index=False)

    return {
        "status": "success",
        "comparison": comp,
        "csv_download_url": f"/api/download_report?filename={os.path.basename(report_csv_path)}",
    }


@app.get("/api/download_report")
async def download_report(filename: str):
    path = os.path.join(os.path.dirname(__file__), "reports", filename)
    if os.path.exists(path):
        return FileResponse(path, media_type="text/csv", filename=filename)
    raise HTTPException(status_code=404, detail="Report file not found")


@app.get("/api/export_history_csv")
async def export_history_csv():
    """Exports current session audit history to CSV."""
    if not session.audit_history:
        raise HTTPException(
            status_code=400, detail="No audit records available in current session"
        )

    df = pd.DataFrame(session.audit_history)
    export_path = os.path.join(
        os.path.dirname(__file__), "reports", f"session_audit_{int(time.time())}.csv"
    )
    df.to_csv(export_path, index=False)
    return FileResponse(
        export_path, media_type="text/csv", filename="drishti_pat_audit_ledger.csv"
    )


@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """
    High-speed real-time WebSocket stream:
    Transmits 30 FPS camera frames (JPEG/base64) + complete HUD telemetry + 5-point readiness status + audit ledger.
    """
    await websocket.accept()
    try:
        while True:
            t_loop_start = time.perf_counter()

            if session.mode == "SIMULATION":
                # 1. Render camera frame from current gimbal angles
                packet, truth = session.sim.render_camera_frame(
                    gimbal_pan_deg=session.gimbal.pan_deg,
                    gimbal_tilt_deg=session.gimbal.tilt_deg,
                    gimbal_pan_rate=session.gimbal.pan_rate_dps,
                    gimbal_tilt_rate=session.gimbal.tilt_rate_dps,
                )

                # 2. Process frame through active tracker
                tracker = session.get_active_tracker()
                audit = tracker.process_frame(packet, truth_reference=truth)

                # 3. Update gimbal if closed-loop and simulation is running
                if session.is_running:
                    if session.active_tracker_name == "DRISHTI_PAT":
                        if session.tracker.last_command:
                            session.gimbal.command_rates(
                                session.tracker.last_command.commanded_pan_rate_dps,
                                session.tracker.last_command.commanded_tilt_rate_dps,
                                session.sim.sim_time,
                            )
                    elif session.active_tracker_name == "BASELINE_B1":
                        err_x = (audit.estimated_x - 320.0) / 160.0
                        err_y = -(audit.estimated_y - 240.0) / 160.0
                        session.gimbal.command_rates(
                            2.0 * err_x, 2.0 * err_y, session.sim.sim_time
                        )
                    elif session.active_tracker_name == "BASELINE_B0":
                        err_x = (
                            (audit.estimated_x - 320.0) / 160.0
                            if audit.measured_x
                            else 0.0
                        )
                        err_y = (
                            -(audit.estimated_y - 240.0) / 160.0
                            if audit.measured_y
                            else 0.0
                        )
                        session.gimbal.command_rates(
                            1.5 * err_x, 1.5 * err_y, session.sim.sim_time
                        )

                    # 4. Advance physics & gimbal
                    session.gimbal.update(session.dt)
                    session.sim.update_physics(session.dt)

                readiness_data = None
                if session.tracker.last_readiness:
                    r = session.tracker.last_readiness
                    readiness_data = {
                        "state": r.state.value,
                        "is_ready": r.is_ready,
                        "fresh_met": r.fresh_observations_met,
                        "cone_met": r.offset_within_fine_cone,
                        "assoc_met": r.association_resolved,
                        "motion_met": r.motion_within_fine_stage,
                        "artifact_clean": r.no_artifact_alarm,
                        "pointing_offset_px": r.pointing_offset_px,
                        "reasons": r.rejection_reasons,
                    }

            else:
                # MP4 Mode
                if session.video_adapter is None:
                    await asyncio.sleep(0.1)
                    continue

                ret, frame = session.video_adapter.cap.read()
                if not ret:
                    # Loop video
                    session.video_adapter.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = session.video_adapter.cap.read()

                gray = (
                    cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if len(frame.shape) == 3
                    else frame
                )
                timestamp = session.video_frame_idx / session.video_adapter.fps
                packet = FramePacket(
                    frame_id=session.video_frame_idx,
                    timestamp_capture=timestamp,
                    timestamp_arrival=timestamp + 0.001,
                    pixels=gray,
                    source="MP4",
                    fov_deg=(4.0, 3.0),
                )
                session.video_frame_idx += 1
                audit = session.tracker.process_frame(packet)
                truth = {
                    "true_u": None,
                    "true_v": None,
                    "is_visible": True,
                    "hot_pixels": [],
                }
                readiness_data = None

            # Encode image to JPEG base64 for fast browser rendering
            _, buffer = cv2.imencode(
                ".jpg", packet.pixels, [int(cv2.IMWRITE_JPEG_QUALITY), 80]
            )
            jpeg_base64 = base64.b64encode(buffer).decode("utf-8")

            # Maintain running history for export
            record_dict = {
                "frame_id": audit.frame_id,
                "timestamp": audit.timestamp,
                "frame_state": audit.frame_state.value,
                "track_state": audit.track_state.value,
                "action_type": audit.action_type.value,
                "action_cost_ms": audit.action_cost_ms,
                "measured_x": audit.measured_x,
                "measured_y": audit.measured_y,
                "estimated_x": audit.estimated_x,
                "estimated_y": audit.estimated_y,
                "uncertainty_r95": audit.uncertainty_r95,
                "artifact_verdict": audit.artifact_verdict.value,
                "readiness_state": audit.readiness_state.value,
                "gimbal_pan_deg": session.gimbal.pan_deg,
                "gimbal_tilt_deg": session.gimbal.tilt_deg,
                "tracking_error_px": audit.tracking_error_px,
                "pointing_error_px": audit.pointing_error_px,
            }
            session.audit_history.append(record_dict)
            if len(session.audit_history) > 1000:
                session.audit_history.pop(0)

            # Assemble telemetry payload
            telemetry_msg = {
                "frame_id": audit.frame_id,
                "timestamp": audit.timestamp,
                "frame_base64": jpeg_base64,
                "frame_state": audit.frame_state.value,
                "track_state": audit.track_state.value,
                "action_type": audit.action_type.value,
                "action_reason": audit.action_reason,
                "action_cost_ms": audit.action_cost_ms,
                "measured_x": audit.measured_x,
                "measured_y": audit.measured_y,
                "estimated_x": audit.estimated_x,
                "estimated_y": audit.estimated_y,
                "estimated_vx": session.tracker.estimator.state[2]
                if session.tracker.estimator
                else 0.0,
                "estimated_vy": session.tracker.estimator.state[3]
                if session.tracker.estimator
                else 0.0,
                "uncertainty_r95": audit.uncertainty_r95,
                "measurement_age_ms": audit.measurement_age_ms,
                "artifact_verdict": audit.artifact_verdict.value,
                "confirmed_defects": list(
                    session.tracker.artifact_challenge.confirmed_defects
                ),
                "readiness": readiness_data,
                "gimbal_pan_deg": session.gimbal.pan_deg,
                "gimbal_tilt_deg": session.gimbal.tilt_deg,
                "gimbal_pan_rate": session.gimbal.pan_rate_dps,
                "gimbal_tilt_rate": session.gimbal.tilt_rate_dps,
                "true_x": truth.get("true_u"),
                "true_y": truth.get("true_v"),
                "tracking_error_px": audit.tracking_error_px,
                "pointing_error_px": audit.pointing_error_px,
                "is_occluded": session.sim.occlusion_active
                if session.mode == "SIMULATION"
                else False,
                "mode": session.mode,
                "tracker": session.active_tracker_name,
            }

            def _json_default(obj):
                if hasattr(obj, "item"):
                    return obj.item()
                if hasattr(obj, "__dict__"):
                    return obj.__dict__
                return str(obj)

            await websocket.send_text(json.dumps(telemetry_msg, default=_json_default))

            # Maintain ~30 FPS stream rate
            elapsed = time.perf_counter() - t_loop_start
            sleep_time = max(0.005, (1.0 / session.fps) - elapsed)
            await asyncio.sleep(sleep_time)

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"WebSocket telemetry notice: {e}")


if __name__ == "__main__":
    import uvicorn

    print("Starting DRISHTI-PAT Telemetry Server on http://localhost:8000 ...")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False, ws_ping_interval=None, ws_ping_timeout=None)

