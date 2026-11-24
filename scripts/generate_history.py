"""Reproduce the development history. All records are fictional."""
import csv
import random
from pathlib import Path

destination = Path(__file__).resolve().parents[1] / "tradepilot/fixtures/history.csv"
rng = random.Random(20261124)
with destination.open("w", newline="") as stream:
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["id", "carrier", "origin", "destination", "promised_hours", "actual_hours", "source"])
    for index in range(240):
        carrier, origin, target, hours = [
            ("DEMO-AIR-01", "mumbai", "singapore", 24),
            ("DEMO-SEA-01", "mumbai", "singapore", 260),
            ("DEMO-ROAD-01", "mumbai", "delhi", 35),
        ][index % 3]
        actual = hours + rng.randint(-5, 1) + (8 if rng.random() < .12 else 0)
        writer.writerow([f"SIM-{index:04}", carrier, origin, target, hours, actual, "SYNTHETIC"])
