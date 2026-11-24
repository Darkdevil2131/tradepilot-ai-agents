# TradePilot AI Multi-Agent Backend

A backend prototype for shipment decision support. It uses a LangGraph `StateGraph` to coordinate five deterministic specialist agents plus the Logistics Orchestrator. It works offline and does not require Gemini.

## Current capabilities and limits

- Route estimates use a small built-in city-coordinate table, straight-line distance, and fixed demo factors. Mode eligibility uses a small country/port sample table; it is not a verified transport connection. They are not navigable routes, live ETAs, or carrier quotes.
- Carrier records, capacities, coverage, and reliability scores are fictional synthetic fixtures. Demo coverage checks distinguish sample domestic India city pairs from cross-border routes; they are not verified carrier service areas.
- Risk checks are basic keyword and completeness rules. There is no live customs, sanctions, tariff, or jurisdiction screening.
- Document validation checks user-entered fields and returns the required-document checklist even when no document data was supplied. It does not read PDFs, scans, images, or OCR.
- Procurement ranks submitted quotations only, excludes expired quotes and routes disallowed by the analysis, avoids ranking raw amounts across currencies when FX data is absent, and always requires human approval. It never books or contacts a carrier.
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
2. Route Optimization — estimates mode options and ranks deadline-feasible routes.
3. Carrier Research — searches synthetic carrier fixtures and checks capacity.
4. Risk & Compliance — runs local screening rules and flags required human review.
5. Document Intelligence — validates declared invoice, bill of lading and packing-list fields.
6. Market & Procurement — ranks user-provided quotations and marks the recommendation for human approval.

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
