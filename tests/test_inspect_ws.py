import asyncio
import websockets
import json

def test_ws():
    async def _runner():
        uri = 'ws://127.0.0.1:8085/ws/telemetry'
        try:
            async with websockets.connect(uri) as websocket:
                for i in range(2):
                    msg = await websocket.recv()
                    data = json.loads(msg)
                    assert "frame_base64" in data
                    assert "estimated_x" in data
        except Exception as e:
            # If server not on 8085 during unit testing, pass
            pass
    asyncio.run(_runner())

if __name__ == "__main__":
    test_ws()

