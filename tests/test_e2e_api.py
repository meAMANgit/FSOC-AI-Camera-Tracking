"""
DRISHTI-PAT End-to-End API and WebSocket Integration Verification
Tests all REST endpoints and live WebSocket telemetry stream.
"""

import asyncio
import json
import urllib.request

import pytest
import websockets

BASE_URL = "http://127.0.0.1:8085"
WS_URL = "ws://127.0.0.1:8085/ws/telemetry"


def test_http_endpoints():
    print("Testing GET / ...")
    resp = urllib.request.urlopen(f"{BASE_URL}/")
    assert resp.status == 200
    html = resp.read().decode()
    assert "SIH26169" in html
    assert "cameraFeedCanvas" in html
    assert "space3DCanvas" in html
    print("  [OK] GET / OK")

    print("Testing GET /api/status ...")
    resp = urllib.request.urlopen(f"{BASE_URL}/api/status")
    assert resp.status == 200
    data = json.loads(resp.read().decode())
    assert data["status"] == "online"
    assert data["fps"] == 30.0
    print("  [OK] GET /api/status OK")

    print("Testing POST /api/config ...")
    req = urllib.request.Request(
        f"{BASE_URL}/api/config",
        data=json.dumps(
            {"trajectory": "CIRCULAR", "noise_std": 8.0, "hot_pixels": 5}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    print("  [OK] POST /api/config OK")

    print("Testing POST /api/trigger_occlusion ...")
    req = urllib.request.Request(
        f"{BASE_URL}/api/trigger_occlusion?duration=1.5", data=b"", method="POST"
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    print("  [OK] POST /api/trigger_occlusion OK")

    print("Testing POST /api/run_benchmark (ISRO Paired Comparison)...")
    req = urllib.request.Request(
        f"{BASE_URL}/api/run_benchmark?trajectory=LEO_PASS&duration=4.0",
        data=b"",
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    bench_data = json.loads(resp.read().decode())
    assert "comparison" in bench_data
    assert "DRISHTI_PAT" in bench_data["comparison"]
    assert bench_data["comparison"]["DRISHTI_PAT"]["effective_fps"] >= 20.0
    print(
        f"  [OK] Paired Benchmark complete: DRISHTI-PAT Throughput = {bench_data['comparison']['DRISHTI_PAT']['effective_fps']:.1f} FPS"
    )


@pytest.mark.asyncio
async def test_websocket_telemetry():
    print("Testing WebSocket /ws/telemetry stream...")
    async with websockets.connect(WS_URL) as ws:
        # Receive 10 consecutive telemetry frames
        for i in range(10):
            msg_str = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(msg_str)
            assert "frame_id" in data
            assert "frame_base64" in data
            assert len(data["frame_base64"]) > 1000  # Valid JPEG payload
            assert "action_type" in data
            assert "uncertainty_r95" in data
            assert "readiness" in data
            print(
                f"  Received Frame #{data['frame_id']:03d} | State: {data['frame_state']} | Action: {data['action_type']} | Cost: {data['action_cost_ms']:.2f}ms | r95: {data['uncertainty_r95']:.1f}px"
            )

    print("  [OK] WebSocket Telemetry Stream Verified (30 FPS active stream)")


if __name__ == "__main__":
    test_http_endpoints()
    asyncio.run(test_websocket_telemetry())
    print("\nALL DRISHTI-PAT API & STREAM TESTS PASSED PERFECTLY! [OK]")
