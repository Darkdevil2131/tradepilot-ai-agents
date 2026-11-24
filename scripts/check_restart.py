"""Exercise two Uvicorn processes against one temporary SQLite database."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

import httpx


@contextmanager
def running_api(database, key):
    # Pass a bound socket to Uvicorn to avoid a race for the ephemeral port.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        env = dict(os.environ, TRADEPILOT_DB_PATH=str(database),
                   TRADEPILOT_API_KEY=key, GEMINI_ENABLED="false")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "tradepilot.main:app",
             "--fd", str(listener.fileno()), "--log-level", "warning"],
            pass_fds=(listener.fileno(),), env=env,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False,
                              headers={"Authorization": f"Bearer {key}"}, timeout=5) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Server exited during startup")
                    try:
                        if client.get("/api/v1/ready").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise TimeoutError("Readiness probe timed out")
                yield client
        finally:
            process.terminate()
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
            if code not in (0, -signal.SIGTERM):
                raise RuntimeError(f"Server exited with status {code}")


def main():
    payload = json.loads(Path("examples/impact_comparison.json").read_text())
    key = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "shipments.db"
        with running_api(database, key) as api:
            response = api.post("/api/v1/shipments/analyze", json=payload)
            response.raise_for_status()
            result = response.json()
            assert len(result["agents"]) == 6
            assert all(agent["status"] == "completed" for agent in result["agents"].values())
            plan = result["agents"]["market_procurement"]["data"]["sourced_plan"]
            assert (plan["option"], plan["total_impact_inr"]) == ("air/ocean", 78000)
        with running_api(database, key) as api:
            path = "/api/v1/shipments/" + result["shipment_id"]
            assert api.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert api.get(path).json() == result
            revisions = api.get(path + "/history").json()["revisions"]
            assert len(revisions) == 1 and revisions[0]["analysis"] == result
    print("Passed: six agents, synthetic split plan, restart, authentication, retrieval and history.")


if __name__ == "__main__":
    main()
