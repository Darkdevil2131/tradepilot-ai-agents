"""Zero-dependency concurrent API smoke/load test; run only against an authorized target."""
from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import time

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    if args.requests < 1 or args.requests > 10000 or args.concurrency < 1 or args.concurrency > 100:
        parser.error("requests must be 1..10000 and concurrency 1..100")

    client = httpx.Client(timeout=30, trust_env=False)

    def call(index: int):
        payload = {"shipment_id": f"LOAD-{index}", "origin": "Mumbai", "destination": "Singapore",
                   "cargo_description": "synthetic load test parts", "weight_kg": 1000,
                   "allowed_modes": ["air", "sea"]}
        started = time.perf_counter()
        response = client.post(args.base_url + "/api/v1/shipments/analyze", json=payload,
                               headers={"Authorization": "Bearer " + args.api_key})
        return response.status_code, time.perf_counter() - started

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(call, range(args.requests)))
    client.close()
    durations = sorted(duration for _, duration in results)
    statuses = {}
    for status, _ in results:
        statuses[status] = statuses.get(status, 0) + 1
    p95 = durations[min(len(durations) - 1, int(len(durations) * .95))]
    print({"requests": len(results), "statuses": statuses, "mean_seconds": round(statistics.mean(durations), 4),
           "p95_seconds": round(p95, 4), "max_seconds": round(max(durations), 4)})
    return 0 if all(status == 200 for status, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
