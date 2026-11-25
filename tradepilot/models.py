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
    source_reference: str | None = Field(default=None, min_length=1, max_length=300)
    route_leg_ids: list[str] = Field(default_factory=list, max_length=64)
    transit_hours: float | None = Field(default=None, gt=0, le=10_000)
    quoted_capacity_kg: float | None = Field(default=None, gt=0, le=1_000_000)
    door_to_door_total: bool = False

class ShippingDocument(StrictModel):
    document_type: Literal["commercial_invoice", "bill_of_lading", "packing_list", "certificate_of_origin", "dangerous_goods_declaration", "consignment_note"]
    fields: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    raw_text: str | None = Field(default=None, max_length=50_000)
    provenance: Provenance = Provenance.USER_PROVIDED

class OptimizationPreferences(StrictModel):
    cost_weight: float = Field(default=0.40, ge=0, le=1)
    time_weight: float = Field(default=0.30, ge=0, le=1)
    risk_weight: float = Field(default=0.20, ge=0, le=1)
    reliability_weight: float = Field(default=0.10, ge=0, le=1)

    @model_validator(mode="after")
    def at_least_one_objective(self):
        if self.cost_weight + self.time_weight + self.risk_weight + self.reliability_weight <= 0:
            raise ValueError("At least one optimization weight must be positive")
        return self

class DisruptionSignal(StrictModel):
    signal_id: str = Field(min_length=1, max_length=80)
    affected_modes: list[Literal["air", "sea", "road", "rail"]] = Field(default_factory=list, max_length=4)
    affected_locations: list[str] = Field(default_factory=list, max_length=20)
    affected_leg_ids: list[str] = Field(default_factory=list, max_length=30)
    severity: float = Field(ge=0, le=1)
    delay_hours: float = Field(default=0, ge=0, le=10_000)
    active: bool = True
    provenance: Provenance = Provenance.USER_PROVIDED

class SupplierOffer(StrictModel):
    supplier_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    unit_price: float = Field(gt=0, le=100_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    lead_time_days: float = Field(ge=0, le=3650)
    available_quantity: float = Field(gt=0, le=1_000_000_000)
    quality_score: float = Field(ge=0, le=1)
    origin: str = Field(min_length=2, max_length=100)
    contact_email: str | None = Field(default=None, max_length=254)
    certifications: list[str] = Field(default_factory=list, max_length=30)
    provenance: Provenance = Provenance.USER_PROVIDED

class SubmittedCarrierService(StrictModel):
    carrier_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    mode: Literal["air", "sea", "road", "rail"]
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    capacity_kg: float = Field(gt=0, le=1_000_000)
    contact_email: str | None = Field(default=None, max_length=254)
    source_reference: str = Field(min_length=1, max_length=300)

class ImpactScenario(StrictModel):
    critical_weight_kg: float = Field(gt=0)
    target_days: float = Field(gt=0, le=365)
    daily_stoppage_inr: float = Field(ge=0, le=1e10)
    closed_leg_ids: list[str] = Field(default_factory=list, max_length=50)
    freight_multiplier: float = Field(default=1, gt=0, le=10)

class RoadCoordinate(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class TruckProfile(StrictModel):
    height_cm: int = Field(gt=0, le=1000)
    width_cm: int = Field(gt=0, le=1000)
    length_cm: int = Field(gt=0, le=10000)
    tare_weight_kg: int = Field(gt=0, le=200000)
    loaded_weight_kg: int = Field(gt=0, le=200000)
    gross_weight_rating_kg: int = Field(gt=0, le=200000)
    max_axle_weight_kg: int = Field(gt=0, le=200000)
    axle_count: int = Field(ge=2, le=20)

    @model_validator(mode="after")
    def check_weights(self):
        if not self.tare_weight_kg < self.loaded_weight_kg <= self.gross_weight_rating_kg:
            raise ValueError("Truck weights must be tare < loaded <= gross weight rating")
        if not self.loaded_weight_kg <= self.max_axle_weight_kg * self.axle_count or self.max_axle_weight_kg > self.loaded_weight_kg:
            raise ValueError("Maximum axle weight is inconsistent with loaded weight and axle count")
        return self


class RoadGuidanceRequest(StrictModel):
    origin: RoadCoordinate
    destination: RoadCoordinate
    departure_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    truck: TruckProfile | None = None
    max_toll_inr: float | None = Field(default=None, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def toll_requires_truck(self):
        if self.max_toll_inr is not None and self.truck is None:
            raise ValueError("A toll budget requires a truck profile")
        return self

    @field_validator("departure_at")
    @classmethod
    def utc_departure(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("departure_at must include a timezone")
        return value.astimezone(timezone.utc)


class ShipmentRequest(StrictModel):
    impact_scenario: ImpactScenario | None = None
    road_guidance: RoadGuidanceRequest | None = None
    shipment_id: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    origin: str = Field(min_length=2, max_length=100)
    destination: str = Field(min_length=2, max_length=100)
    cargo_description: str = Field(min_length=2, max_length=500)
    weight_kg: float = Field(gt=0, le=1_000_000)
    dimensions_cm: tuple[float, float, float] | None = None
    volume_m3: float | None = Field(default=None, gt=0, le=10_000)
    declared_value: float | None = Field(default=None, ge=0, le=1_000_000_000)
    declared_value_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    deadline: datetime | None = None
    allowed_modes: list[Literal["air", "sea", "road", "rail"]] = Field(default_factory=lambda: ["air", "sea", "road"], min_length=1, max_length=4)
    quotes: list[Quote] = Field(default_factory=list, max_length=50)
    carrier_services: list[SubmittedCarrierService] = Field(default_factory=list, max_length=50)
    commercial_mode: Literal["demo", "submitted"] = "demo"
    documents: list[ShippingDocument] = Field(default_factory=list, max_length=20)
    incoterm: str | None = Field(default=None, max_length=20)
    optimization: OptimizationPreferences = Field(default_factory=OptimizationPreferences)
    disruptions: list[DisruptionSignal] = Field(default_factory=list, max_length=50)
    supplier_offers: list[SupplierOffer] = Field(default_factory=list, max_length=50)
    required_certifications: list[str] = Field(default_factory=list, max_length=30)
    required_quantity: float | None = Field(default=None, gt=0, le=1_000_000_000)

    @property
    def requires_submitted_offers(self) -> bool:
        return (self.road_guidance is not None or self.commercial_mode == "submitted" or bool(self.carrier_services)
                or any(quote.source_reference for quote in self.quotes))

    @field_validator("origin", "destination")
    @classmethod
    def normalize_locations(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        return {"cochin": "Kochi", "ernakulam": "Kochi", "madras": "Chennai",
                "bangalore": "Bengaluru", "bombay": "Mumbai", "calcutta": "Kolkata",
                "vizag": "Visakhapatnam", "new delhi": "Delhi", "calicut": "Kozhikode",
                "trichur": "Thrissur", "belgaum": "Belagavi", "hubli": "Hubballi",
                "tumkur": "Tumakuru", "allahabad": "Prayagraj"}.get(value.casefold(), value)

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
            if self.impact_scenario.critical_weight_kg > self.weight_kg:
                raise ValueError("Critical weight exceeds shipment weight")
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("origin and destination must differ")
        if len(set(self.allowed_modes)) != len(self.allowed_modes):
            raise ValueError("allowed_modes must not contain duplicates")
        if self.road_guidance:
            if self.impact_scenario or self.allowed_modes != ["road"]:
                raise ValueError("road_guidance requires road-only service without a split impact_scenario")
            if self.road_guidance.origin == self.road_guidance.destination:
                raise ValueError("road_guidance endpoints must have distinct coordinates")
            truck = self.road_guidance.truck
            if truck and self.weight_kg > truck.loaded_weight_kg - truck.tare_weight_kg:
                raise ValueError("Shipment exceeds the declared loaded truck payload")
        service_keys = {(row.carrier_id, row.mode) for row in self.carrier_services}
        if len(service_keys) != len(self.carrier_services):
            raise ValueError("carrier_services must not duplicate a carrier and mode")
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
    persistence: Literal["sqlite_ephemeral_or_local", "postgresql_durable"]
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

class RouteWeatherSample(StrictModel):
    latitude: float
    longitude: float
    route_km: float
    estimated_arrival_utc: datetime
    forecast_hour_utc: datetime | None = None
    precipitation_mm: float | None = None
    wind_gust_kmh: float | None = None
    visibility_m: float | None = None
    weather_code: int | None = None
    flag: Literal["low", "elevated", "high", "unavailable"]


class RouteWeather(StrictModel):
    source: Literal["Open-Meteo"] = "Open-Meteo"
    status: Literal["complete", "partial", "unavailable"]
    severity: Literal["low", "elevated", "high", "unknown"]
    retrieved_at_utc: datetime | None = None
    sample_eta_method: Literal["segment_duration", "distance_interpolation"]
    samples: list[RouteWeatherSample]


class TruckRouteEvidence(StrictModel):
    source: Literal["HERE Routing API v8"] = "HERE Routing API v8"
    retrieved_at_utc: datetime
    traffic_status: Literal["time_aware", "baseline_unavailable"]
    provider_duration_hours: float
    base_duration_hours: float | None = None
    time_aware_delta_hours: float | None = None
    toll_status: Literal["priced", "unavailable"]
    toll_inr: float | None = None
    access_status: Literal["no_reported_violation", "restriction_reported", "review_required"]
    notices: list[str] = Field(default_factory=list)


class RouteOption(StrictModel):
    mode: Literal["air", "sea", "road", "rail"]
    distance_km: float = Field(ge=0)
    transit_hours_estimate: float = Field(ge=0)
    cost_estimate_usd: float | None = Field(default=None, ge=0)
    cost_estimate_inr: float | None = Field(default=None, ge=0)
    mode_feasible: bool
    deadline_feasible: bool
    feasible: bool
    feasibility_reason: str
    risk_score_estimate: float = Field(ge=0,le=1)
    provenance: Literal["ESTIMATED", "SYNTHETIC"]
    distance_method: str
    legs: list[dict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
    capacity_kg: float | None = Field(default=None, ge=0)
    decision_score: float | None = Field(default=None, ge=0, le=1)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    disruption_ids: list[str] = Field(default_factory=list)
    geometry_lon_lat: list[tuple[float, float]] | None = None
    weather: RouteWeather | None = None
    truck_evidence: TruckRouteEvidence | None = None

class RouteOptimizationOutput(StrictModel):
    impact_comparison: dict | None = None
    routes: list[RouteOption]
    recommended_route: RouteOption | None
    no_feasible_route: bool
    provenance: Literal["ESTIMATED"]
    ranking_method: str = "weighted multi-objective deterministic ranking"
    alternatives: list[dict] = Field(default_factory=list)
    provider_status: Literal["not_configured", "routing_unavailable", "no_route", "weather_unavailable", "weather_partial", "truck_evidence_incomplete", "available"] | None = None

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
    provenance: Literal["SYNTHETIC", "USER_PROVIDED"]
    capacity_available: bool
    coverage_match: bool
    contact: dict[str, str] = Field(default_factory=dict)
    certifications: list[str] = Field(default_factory=list)
    service_score: float | None = Field(default=None, ge=0, le=1)
    history_confidence: Literal["unknown", "limited_synthetic", "synthetic_sample"] = "unknown"
    coverage_reference: str | None = None

class CarrierResearchOutput(StrictModel):
    leg_plans: list[dict] = Field(default_factory=list)
    carriers: list[CarrierRecord]
    excluded_capacity: int = Field(ge=0)
    excluded_coverage: int = Field(ge=0)
    provenance: Literal["SYNTHETIC", "USER_PROVIDED"]
    notice: str
    recommended_carrier: dict | None = None

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
    disruption_summary: list[dict] = Field(default_factory=list)

class DocumentValidationOutput(StrictModel):
    valid: bool
    documents_checked: int = Field(ge=0)
    missing_document_types: list[str]
    issues: list[str]
    provenance: Literal["USER_PROVIDED"]
    extracted_fields: dict[str, dict] = Field(default_factory=dict)

class RankedQuote(StrictModel):
    includes_insurance: bool = False
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
    evidence: dict | None = None

class QuoteAssessment(StrictModel):
    quote_index: int
    carrier_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)

class ProcurementOutput(StrictModel):
    sourced_plan: dict | None = None
    quotes_received: int = Field(ge=0)
    eligible_quotes: int = Field(ge=0)
    ranked_quotes: list[RankedQuote]
    recommendation: ProcurementRecommendation | None
    recommendation_warning: str | None = None
    quote_assessments: list[QuoteAssessment] = Field(default_factory=list)
    ranked_supplier_offers: list[dict] = Field(default_factory=list)
    excluded_supplier_offers: list[dict] = Field(default_factory=list)
    supplier_recommendation: dict | None = None
    supplier_warning: str | None = None
    note: str
