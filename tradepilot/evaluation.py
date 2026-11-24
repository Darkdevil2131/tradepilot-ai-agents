from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import Field

from .models import StrictModel


class HistoricalDecision(StrictModel):
    shipment_id: str = Field(min_length=1, max_length=80)
    recommended_mode: Literal["air", "sea", "road", "rail"]
    actual_mode: Literal["air", "sea", "road", "rail"]
    predicted_cost: float = Field(ge=0)
    actual_cost: float = Field(ge=0)
    baseline_cost: float = Field(ge=0)
    predicted_hours: float = Field(ge=0)
    actual_hours: float = Field(ge=0)
    predicted_risk: float = Field(ge=0, le=1)
    disruption_occurred: bool
    provenance: Literal["HISTORICAL", "SYNTHETIC"]


class EvaluationRequest(StrictModel):
    records: list[HistoricalDecision] = Field(min_length=5, max_length=10000)


def evaluate_decisions(records: list[HistoricalDecision]) -> dict:
    count = len(records)
    cost_savings = [row.baseline_cost - row.actual_cost for row in records]
    eta_errors = [abs(row.predicted_hours - row.actual_hours) for row in records]
    cost_errors = [abs(row.predicted_cost - row.actual_cost) for row in records]
    agreements = [row.recommended_mode == row.actual_mode for row in records]
    brier = [(row.predicted_risk - float(row.disruption_occurred)) ** 2 for row in records]

    bins: dict[int, list[HistoricalDecision]] = defaultdict(list)
    for row in records:
        bins[min(9, int(row.predicted_risk * 10))].append(row)
    calibration = []
    ece = 0.0
    for index in sorted(bins):
        group = bins[index]
        mean_prediction = sum(row.predicted_risk for row in group) / len(group)
        observed = sum(row.disruption_occurred for row in group) / len(group)
        ece += len(group) / count * abs(mean_prediction - observed)
        calibration.append({
            "range": [index / 10, (index + 1) / 10],
            "count": len(group),
            "mean_predicted_risk": round(mean_prediction, 6),
            "observed_disruption_rate": round(observed, 6),
        })

    provenances = sorted({row.provenance for row in records})
    return {
        "records": count,
        "provenance": provenances,
        "cost": {
            "total_savings": round(sum(cost_savings), 2),
            "mean_savings": round(sum(cost_savings) / count, 2),
            "mean_absolute_error": round(sum(cost_errors) / count, 2),
        },
        "eta": {"mean_absolute_error_hours": round(sum(eta_errors) / count, 4)},
        "expert_or_actual_mode_agreement": round(sum(agreements) / count, 6),
        "risk_calibration": {
            "brier_score": round(sum(brier) / count, 6),
            "expected_calibration_error": round(ece, 6),
            "bins": calibration,
        },
        "interpretation": "Metrics describe only the submitted historical or synthetic records; they do not establish future accuracy.",
    }
