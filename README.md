# TradePilot AI Multi-Agent Backend

A backend prototype for shipment decision support. It uses a LangGraph `StateGraph` to coordinate five deterministic specialist agents plus the Logistics Orchestrator. It works offline and does not require Gemini.

## Current capabilities and limits

- Route estimates use a small built-in connected network plus city-coordinate fallback. Feasible options are ranked deterministically by configurable cost, time, risk and reliability weights. User-provided disruption signals adjust delay/risk and can block a route. Results remain estimates—not navigable routes, live ETAs or carrier quotes.
- Carrier records, contacts, certifications, capacities, coverage and reliability scores are fictional synthetic fixtures. The carrier agent filters capacity/coverage, calculates a service score and returns an approval-gated candidate; none of the demo contacts are bookable.
- Risk checks are basic keyword and completeness rules. There is no live customs, sanctions, tariff, or jurisdiction screening.
- Document intelligence validates structured fields and can extract explicitly labelled values from supplied plain text before cross-document checks. It does not read PDFs, scans or images and does not perform OCR.
- Procurement ranks submitted carrier quotations and supplier offers, enforces quantity/certification gates, refuses cross-currency winners without FX data, and always requires human approval. It never books, purchases or contacts a provider.
- Every agent result carries provenance labels where applicable: `USER_PROVIDED`, `ESTIMATED`, or `SYNTHETIC`. The health endpoint confirms that no live logistics feeds are configured.
- Offline mode is the default. Gemini is an optional narrative pass over tool output only; it cannot change calculations, call tools, or establish facts. Its model and API key are supplied via environment variables. It is disabled by default, and quota failures degrade to deterministic results.
- A Google API key alone does not guarantee zero cost: account/project billing, quotas, and free eligibility are controlled by Google. Keep Gemini disabled unless the owner has confirmed the project is constrained to the no-cost tier and has billing disabled. Never commit a key.

## Run locally

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Replace `TRADEPILOT_API_KEY` with a locally generated random secret in `.env`. Do not share it. Start the server:

```bash
uvicorn tradepilot.main:app --reload
```

API docs: <http://127.0.0.1:8000/docs>  
Health: <http://127.0.0.1:8000/api/v1/health>

Protected API calls use `Authorization: Bearer <TRADEPILOT_API_KEY>`. Health is public. Requests with a missing/invalid key return 401; protected routes return 503 if no API key is configured. A per-process request limit (default 60 requests/minute per source IP) returns 429 with `Retry-After`; it is not a distributed limiter.

## API

- `POST /api/v1/shipments/analyze` — deterministic offline analysis, SQLite save, optional Gemini narrative.
- `GET /api/v1/shipments/{shipment_id}` — retrieves an analysis saved in this SQLite database.
- `POST /api/v1/documents/validate` — validates user-supplied document fields.
- `GET /api/v1/health`
- `/openapi.json`, `/docs`

See [`API_INTEGRATION_GUIDE.md`](API_INTEGRATION_GUIDE.md) and [`examples/`](examples/).

## Persistence and hosting

SQLite is configured by `TRADEPILOT_DB_PATH` (default `./data/tradepilot.sqlite3`). On a machine with durable local storage, retrieval survives process restarts. A free Render instance does not provide durable storage for this file by default; records may disappear on restart or deploy. The included `render.yaml` is a deployment configuration, not proof that an account is eligible or that service is currently free. Check current terms and estimate cost before provisioning; never enable billing to proceed. Deployment was not performed as part of this source build.

## Agents and workflow

Each specialist lives in a separate module under `tradepilot/agents/`, declares its authorized deterministic tools, and returns typed `AgentResult` data through the shared LangGraph workflow state.

1. Logistics Orchestrator — coordinates nodes, tracks bounded retries, combines results and reports partial failures.
2. Route Optimization — applies constraints/disruptions and multi-objective scoring to rank feasible alternatives.
3. Carrier Research — checks synthetic coverage/capacity/history and scores an approval candidate.
4. Risk & Compliance — runs local screening rules and flags required human review.
5. Document Intelligence — extracts labelled plain text and validates invoice, bill of lading and packing-list consistency.
6. Market & Procurement — ranks comparable carrier quotes and qualified supplier offers with human approval.

The graph conditionally selects document validation or an empty-input checklist. The workflow makes no LLM calls in offline mode.

## Verify

```bash
pytest
```

Tests cover agent behavior, deterministic tools, workflow and retry behavior, invalid data, authentication, carrier coverage and capacity, mixed-currency quote handling, unrealistic deadlines, missing documents, offline model operation, and API documentation. Test results should be reported only after running this command in the target environment.

## Security

Use TLS at the hosting edge. Store API keys as host secrets. Do not log request bodies or credentials. Restrict API access, rotate exposed secrets, and review data retention before production use. Sample records are synthetic; do not send sensitive customer or trade data to a model without approval.

### Deployment checks

Configure a fresh private `TRADEPILOT_API_KEY` before rollout. Both hosting
configurations probe `GET /api/v1/ready`. This returns 503 when the secret is
missing or still set to the example value, or when SQLite cannot open a write
transaction against the expected schema. The public liveness endpoint remains
`GET /api/v1/health`. Both probes are exempt from client rate limits.
A successful readiness probe describes the current instance only. Retrieving
shipments after instance replacement requires the same persistent database.

From the repository root on Linux, run `python -m scripts.check_restart`
to launch two successive Uvicorn processes on loopback. The check submits
the synthetic split-shipment example, restarts the server against the same
temporary database, and verifies the saved response and revision history.
