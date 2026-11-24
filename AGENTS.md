# Repository agent instructions

- Keep logistics calculations deterministic and isolated in `tradepilot/tools.py`.
- Keep each specialist in its own module under `tradepilot/agents/` with explicit input types and authorized tool lists.
- The Logistics Orchestrator coordinates via LangGraph `StateGraph`; do not replace the graph with a hand-written LLM loop.
- Never let a model produce or modify route, cost, deadline, carrier, capacity, customs, or document facts. Model output is an optional narrative over deterministic tool output.
- Preserve provenance (`LIVE`, `HISTORICAL`, `USER_PROVIDED`, `ESTIMATED`, `SYNTHETIC`) and clearly label all fixtures.
- Any procurement recommendation requires human approval. Never implement booking, RFQ sending, ledger posting, or ERP writes without a separate authorized workflow.
- Never commit `.env`, API keys, or user-provided credentials. Keep Gemini disabled by default.
- SQLite persistence is local/ephemeral unless durable storage is configured. Do not claim durable production retrieval without verifying the storage layer.
- Run `pytest` after changes that affect behavior. Do not claim a test or deployment result that was not observed.
