# Stage 09 — API Route Consolidation

Stage 09 reduces only the genuinely dead pywebview RPC surface. It does **not** merge SIM and LIVE routes whose authority differs, and it does not rename active frontend routes.

## Removed public routes

The Stage 08 repository census found no frontend, worker, application/runtime or test consumer for these public API methods:

- `cancel_job_schedule`
- `settle_now`
- `engine_types`
- `detailed_market_history`
- `archive_reset_history`
- `daily_summary`

They are removed from `API` rather than retained indefinitely as invisible surface area. None is present in the benchmark projection manifest.

## Compatibility routes deliberately retained

Some thin wrappers look redundant but still have known consumers, so Stage 09 keeps them:

- `activate_monitor_timing` and `stop_monitor_timing` — historical continuous-mode contract tests.
- `set_feed_enabled` — historical compatibility route with explicit SIM/LIVE mode; delegates to venue controls.
- `engine_import_package` — current frontend plus package tests still consume the quarantine/validation compatibility route.
- `live_view_data` — LIVE isolation tests still consume this diagnostic-only route.

Removing these would create churn without proving a real ownership or performance benefit.

## Route safety rules

1. A route can be removed only after a repository consumer census across frontend, worker/app/runtime code and tests.
2. Every literal frontend `pywebview.api`/`call()` target must resolve to a public `API` method.
3. Every remaining public `API` method must be classified exactly once in `validation/refactor_operation_boundaries.json`.
4. SIM/LIVE routes remain separate where lifecycle, economic or account authority differs.
5. Route removal must not change benchmark output fingerprints, query counts, write counts or authority-write classification.

The removal census is captured in `validation/refactor_api_routes.json` and enforced by `tests/test_stage09_api_route_consolidation.py`.
