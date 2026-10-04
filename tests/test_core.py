"""
DRISHTI-PAT Automated Test Suite
Verifies core algorithm components, ISRO performance targets, and safety mechanics.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.artifact_challenge import ArtifactChallengeEvaluator
from core.benchmark_suite import BenchmarkSuite
from core.contracts import (
    ArtifactVerdict,
    Candidate,
    FramePacket,
    FrameState,
    ReadinessState,
    TrackState,
)
from core.estimator import KalmanTrackEstimator
from core.perception import ProposalGenerator
from core.readiness import RevocableReadinessMonitor
from core.simulator import TrajectoryType, WorldSimulator
from core.tracker import DRISHTIPATTracker
from core.video_adapter import VideoBenchmarkAdapter


def test_simulator_and_frame_packet():
    sim = WorldSimulator(camera_fps=30.0, seed=123)
    packet, truth = sim.render_camera_frame(gimbal_pan_deg=0.0, gimbal_tilt_deg=0.0)

    assert packet.pixels.shape == (480, 640)
    assert packet.source == "SIMULATOR"
    assert "true_u" in truth
    assert "true_v" in truth
    assert len(truth["hot_pixels"]) >= 2


def test_proposal_generator():
    sim = WorldSimulator(seed=123)
    packet, _truth = sim.render_camera_frame(0.0, 0.0)
    proposals = ProposalGenerator()
    cset = proposals.extract_candidates(packet)

    assert len(cset.candidates) > 0
    assert cset.background_std > 0.0


def test_artifact_challenge_rejection():
    """Verifies that sensor-fixed hot pixels are identified as SENSOR_DEFECT when camera moves."""
    evaluator = ArtifactChallengeEvaluator()

    # Frame 1: Camera at pan=0.0
    img = np.zeros((480, 640), dtype=np.uint8)
    p1 = FramePacket(
        frame_id=1,
        timestamp_capture=0.0,
        timestamp_arrival=0.001,
        pixels=img,
        source="SIM",
        camera_pan_deg=0.0,
        camera_tilt_deg=0.0,
    )
    cand1 = Candidate(
        candidate_id=101,
        x=340.0,
        y=240.0,
        peak_intensity=250.0,
        snr_sigma=5.0,
        area_px=10.0,
        bbox=(335, 235, 345, 245),
    )
    v1, _ = evaluator.evaluate_candidate(cand1, p1, None)
    assert v1 == ArtifactVerdict.INCONCLUSIVE

    # Frame 2: Gimbal moved to pan=0.1 deg (16 px scene shift!), but hot pixel remained at (340.0, 240.0)
    p2 = FramePacket(
        frame_id=2,
        timestamp_capture=0.033,
        timestamp_arrival=0.034,
        pixels=img,
        source="SIM",
        camera_pan_deg=0.1,
        camera_tilt_deg=0.0,
    )
    cand2 = Candidate(
        candidate_id=101,
        x=340.0,
        y=240.0,
        peak_intensity=250.0,
        snr_sigma=5.0,
        area_px=10.0,
        bbox=(335, 235, 345, 245),
    )
    v2, metrics = evaluator.evaluate_candidate(cand2, p2, p1)

    assert v2 == ArtifactVerdict.SENSOR_DEFECT
    assert metrics["g_mag"] >= 15.0  # Noticeable camera motion
    assert metrics["d_mag"] < 1.0  # Defect did not move on sensor!


def test_kalman_estimator():
    estimator = KalmanTrackEstimator()
    estimator.initialize_track(320.0, 240.0, 0.0)
    assert estimator.track_state == TrackState.CONFIRM

    # Predict & Update with moving target
    from core.contracts import Measurement

    estimator.predict(0.033, 0.0, 0.0, 0.0, 0.0)
    meas = Measurement(
        frame_id=1, is_valid=True, centroid_x=325.0, centroid_y=242.0, snr_sigma=4.0
    )
    est = estimator.update(meas, 0.033)

    assert est.frame_state == FrameState.MEASURED
    assert est.uncertainty_r95 > 0.0


def test_revocable_readiness_conditions():
    readiness = RevocableReadinessMonitor()
    from core.contracts import CandidateSet, TrackEstimate

    # Initially not ready
    est = TrackEstimate(
        frame_id=1,
        timestamp=0.0,
        state=TrackState.TRACK,
        frame_state=FrameState.MEASURED,
        estimated_x=320.5,
        estimated_y=240.2,
        estimated_vx=2.0,
        estimated_vy=1.0,
        uncertainty_r95=1.5,
        measurement_age_s=0.01,
        consecutive_measured=6,
    )
    cset = CandidateSet(frame_id=1, timestamp=0.0, candidates=[])

    # 1. Meets all conditions -> READY
    status = readiness.evaluate(est, cset, ArtifactVerdict.SCENE)
    assert status.is_ready is True
    assert status.state == ReadinessState.READY

    # 2. Sudden occlusion occurs (measurement age > 0.1s) -> WITHDRAWN
    est_occluded = TrackEstimate(
        frame_id=2,
        timestamp=0.15,
        state=TrackState.COAST,
        frame_state=FrameState.PREDICTED,
        estimated_x=320.5,
        estimated_y=240.2,
        estimated_vx=2.0,
        estimated_vy=1.0,
        uncertainty_r95=5.0,
        measurement_age_s=0.15,
        consecutive_measured=0,
    )
    status_withdrawn = readiness.evaluate(
        est_occluded, cset, ArtifactVerdict.INCONCLUSIVE
    )
    assert status_withdrawn.is_ready is False
    assert status_withdrawn.state == ReadinessState.WITHDRAWN


def test_full_tracker_pipeline():
    tracker = DRISHTIPATTracker()
    sim = WorldSimulator(seed=42)
    packet, truth = sim.render_camera_frame(0.0, 0.0)

    audit = tracker.process_frame(packet, truth)
    assert audit.frame_id == packet.frame_id
    assert audit.action_cost_ms < 50.0  # Real-time processing


def test_benchmark_suite():
    bench = BenchmarkSuite(seed=77)
    res = bench.run_scenario("DRISHTI_PAT", TrajectoryType.LEO_PASS, duration_s=2.0)
    summary = res["summary"]

    assert "effective_fps" in summary
    assert summary["effective_fps"] >= 20.0
    assert summary["pass_fps_target"] is True


def test_mp4_video_adapter():
    sample_mp4 = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "sample_data", "sample_leo_pass.mp4"
        )
    )
    adapter = VideoBenchmarkAdapter(sample_mp4)
    records, stats = adapter.process_video(max_frames=30)
    adapter.release()

    assert len(records) == 30
    assert stats["effective_fps"] >= 20.0
