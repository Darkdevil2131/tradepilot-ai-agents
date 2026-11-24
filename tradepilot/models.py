from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Provenance(str, Enum):
    LIVE = "LIVE"
    HISTORICAL = "HISTORICAL"
    USER_PROVIDED = "USER_PROVIDED"
    ESTIMATED = "ESTIMATED"
    SYNTHETIC = "SYNTHETIC"

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Quote(StrictModel):
    carrier_id: str = Field(min_length=1, max_length=80)
    mode: Literal["air", "sea", "road", "rail"]
    amount: float = Field(gt=0, le=100_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    valid_until: datetime | None = None
    includes_insurance: bool = False
    provenance: Provenance = Provenance.USER_PROVIDED

class ShippingDocument(StrictModel):
    document_type: Literal["commercial_invoice", "bill_of_lading", "packing_list", "certificate_of_origin", "dangerous_goods_declaration"]
    fields: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    provenance: Provenance = Provenance.USER_PROVIDED

class ImpactScenario(StrictModel):
    critical_weight_kg: float = Field(gt=0)
    target_days: float = Field(gt=0, le=365)
    daily_stoppage_inr: float = Field(ge=0, le=1e10)
    closed_leg_ids: list[Literal["pickup", "air", "ocean", "delivery"]] = Field(default_factory=list)
    freight_multiplier: float = Field(default=1, gt=0, le=10)

class ShipmentRequest(StrictModel):
    impact_scenario: ImpactScenario | None = None
    shipment_id: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    cargo_description: str = Field(min_length=2, max_length=500)
    weight_kg: float = Field(gt=0, le=1_000_000)
    dimensions_cm: tuple[float, float, float] | None = None
    volume_m3: float | None = Field(default=None, gt=0, le=10_000)
    declared_value: float | None = Field(default=None, ge=0, le=1_000_000_000)
    deadline: datetime | None = None
    allowed_modes: list[Literal["air", "sea", "road", "rail"]] = Field(default_factory=lambda: ["air", "sea", "road"], min_length=1, max_length=4)
    quotes: list[Quote] = Field(default_factory=list, max_length=50)
    documents: list[ShippingDocument] = Field(default_factory=list, max_length=20)
    incoterm: str | None = Field(default=None, max_length=20)

    @field_validator("origin", "destination")
    @classmethod
    def normalize_locations(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("deadline")
    @classmethod
    def normalize_deadline_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def validate_distinct_and_deadline(self):
        import math
        if self.dimensions_cm and any(not math.isfinite(v) or v <= 0 for v in self.dimensions_cm):
            raise ValueError("Package dimensions must be positive and finite")
        if self.impact_scenario:
            if (self.origin.casefold(), self.destination.casefold()) != ("dubai", "mumbai"):
                raise ValueError("The impact scenario currently covers Dubai to Mumbai only")
            if self.impact_scenario.critical_weight_kg > self.weight_kg:
                raise ValueError("Critical weight exceeds shipment weight")
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must differ")
        if len(set(self.allowed_modes)) != len(self.allowed_modes):
            raise ValueError("allowed_modes must not contain duplicates")
        if self.deadline and self.deadline <= datetime.now(timezone.utc):
            raise ValueError("deadline must be in the future")
        return self

class AgentResult(StrictModel):
    agent: str
    status: Literal["completed", "partial", "skipped", "failed"]
    provenance: list[Provenance] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

class ShipmentAnalysis(StrictModel):
    shipment_id: str
    status: Literal["completed", "partial"]
    created_at: datetime
    persistence: Literal["sqlite_ephemeral_or_local"]
    data_provenance: list[Provenance]
    agents: dict[str, AgentResult]
    summary: str

class DecisionGate(StrictModel):
    gate: str
    status: Literal["passed", "review_required", "blocked"]
    reason: str
    evidence_path: str

class DocumentValidationRequest(StrictModel):
    shipment: ShipmentRequest

class DocumentValidationResponse(StrictModel):
    shipment_id: str | None
    valid: bool
    documents_checked: int
    missing_document_types: list[str]
    issues: list[str]
    provenance: Provenance = Provenance.USER_PROVIDED

class RouteOption(StrictModel):
    mode: Literal["air", "sea", "road", "rail"]
    distance_km: float = Field(ge=0)
    transit_hours_estimate: float = Field(ge=0)
    cost_estimate_usd: float = Field(ge=0)
    mode_feasible: bool
    deadline_feasible: bool
    feasible: bool
    feasibility_reason: str
    risk_score_estimate: float = Field(ge=0,le=1)
    provenance: Literal["ESTIMATED", "SYNTHETIC"]
    distance_method: str
    legs: list[dict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    capacity_kg: float | None = Field(default=None, ge=0)

class RouteOptimizationOutput(StrictModel):
    impact_comparison: dict | None = None
    routes: list[RouteOption]
    recommended_route: RouteOption | None
    no_feasible_route: bool
    provenance: Literal["ESTIMATED"]

class CarrierRecord(StrictModel):
    carrier_id: str
    name: str
    mode: Literal["air", "sea", "road", "rail"]
    regions: list[str]
    capacity_kg: float
    reliability_score: float | None = Field(ge=0,le=1)
    history_count: int
    on_time_count: int
    history_reference: str
    provenance: Literal["SYNTHETIC"]
    capacity_available: bool
    coverage_match: bool

class CarrierResearchOutput(StrictModel):
    leg_plans: list[dict] = Field(default_factory=list)
    carriers: list[CarrierRecord]
    excluded_capacity: int = Field(ge=0)
    excluded_coverage: int = Field(ge=0)
    provenance: Literal["SYNTHETIC"]
    notice: str

class RiskFlag(StrictModel):
    code: str
    severity: Literal["low", "medium", "high"]
    reason: str

class RiskComplianceOutput(StrictModel):
    flags: list[RiskFlag]
    human_review_required: bool
    checked_rules: list[str]
    provenance: Literal["USER_PROVIDED"]
    live_screening: Literal[False]

class DocumentValidationOutput(StrictModel):
    valid: bool
    documents_checked: int = Field(ge=0)
    missing_document_types: list[str]
    issues: list[str]
    provenance: Literal["USER_PROVIDED"]

class RankedQuote(StrictModel):
    carrier_id: str
    mode: Literal["air", "sea", "road", "rail"]
    amount: float = Field(gt=0)
    currency: str
    valid_until: str | None
    provenance: Provenance

class ProcurementRecommendation(StrictModel):
    carrier_id: str
    mode: Literal["air", "sea", "road", "rail"]
    amount: float = Field(gt=0)
    currency: str
    requires_human_approval: Literal[True]

class ProcurementOutput(StrictModel):
    sourced_plan: dict | None = None
    quotes_received: int = Field(ge=0)
    eligible_quotes: int = Field(ge=0)
    ranked_quotes: list[RankedQuote]
    recommendation: ProcurementRecommendation | None
    recommendation_warning: str | None = None
    note: str
