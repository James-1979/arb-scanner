# ArbScanner 0.9.57 Architecture Inventory — Recovery Stage 01

This is a static inventory of the verified 0.9.57 reference. No runtime behaviour is changed by Stage 01.

## Runtime shape

ArbScanner is a local pywebview application:

`frontend/index.html` -> `window.pywebview.api.<method>` -> `arbscanner.api.API` -> DB / scanner / provider runtime / engine runtime

A separate `worker.py` process runs discovery, fast price scans, settlement polling and storage/archive maintenance against the same SQLite source of truth.

### Concentration metrics

- `frontend/index.html`: 6,308 lines; HTML, CSS and JavaScript are co-located.
- `arbscanner/api.py`: 8,071 lines; `API` exposes 156 methods including public projections/commands and private helpers.
- `arbscanner/db.py`: 8,622 lines; schema migration, persistence, accounting, lifecycle, analytics and engine storage are concentrated in one DB class.
- `arbscanner/scanner.py`: 2,079 lines; discovery, price refresh/evaluation, SIM lifecycle production and settlement are concentrated in the scanner.
- Static analysis finds 100 API methods that directly call the DB, 4 that directly call the Scanner, and 9 that directly call the LiveProviderRegistry.
- 82 API method names are referenced somewhere in the frontend; dynamic method selection means exact call-site counts are a lower bound.

The current implementation is workable but makes route/data ownership difficult to inspect because presentation, orchestration and read-model composition are broad and highly centralised.

## Core ownership boundaries already present

### Economic modes

`arbscanner.modes` defines canonical `sim` and `live` execution modes. Historical monitor/watch/paper labels map to SIM for compatibility.

### Provider service boundary

`arbscanner.contracts` defines JSON-serialisable provider contracts and explicitly prevents live provider SDK/session objects or DB handles from crossing the provider boundary. Transport remains in-process, but the contract is RPC-ready.

### LIVE provider boundary

`arbscanner.live_providers.LiveProviderRegistry` owns LIVE account/preflight/provider views. The API repeatedly returns `live_execution_allowed: false`; read-only provider state does not imply trading capability.

### Worker/scanner boundary

`worker.py` owns continuous discovery/price/settlement scheduling. Slow discovery runs independently from fast price refresh so provider catalogue walking cannot starve quote freshness.

### Engine boundary

`strategy_engines.py` has registry/router/runtime abstractions and engine instance/config/evaluation persistence in the DB. This is more modular than the page/API layer and should be preserved rather than flattened during refactor.

## UI route orchestration

The current frontend uses `showTab()` plus `orchestrateRouteLoad()` to choose loaders. SIM and LIVE frequently have separate loaders, with request tokens and mode/route checks layered over historical loader redefinitions.

Principal route ownership:

| UI route | SIM principal loader/projection | LIVE principal loader/projection | Ownership note |
|---|---|---|---|
| Dashboard | `loadDashboardOverview` -> `dashboard_overview` | `loadLiveDashboard` -> LIVE account/activity/latest-result projections | Economic state is mode-owned; provider operational evidence selected by mode. |
| Accounts | `loadAccountsPage` -> `accounts_page` | same page loader with LIVE mode -> `accounts_page` / provider account reads | Read-only operational/account surface; Admin owns mutation/config. |
| Active Positions | SIM Dashboard/open-position projection | `loadLiveActivePositions` -> `live_execution_activity` | LIVE must remain empty/actual-only while order writing is locked. |
| Sports Overview | `loadSports` -> `sports_overview` | LIVE Sports loader / LIVE evidence + actual account state | Shared market evidence may exist; lifecycle/economics remain mode-owned. |
| Sports Engines | `loadEngines0914('sports')` -> engine routes | same engine catalogue with mode enablement | Engine configuration/runtime status is shared metadata with explicit mode enablement. |
| Sports Monitor | `loadMonitor` -> monitor/opportunity/lifecycle projections | LIVE monitor loader -> actual/diagnostic LIVE projections | SIM Monitor history is not LIVE lifecycle authority. |
| Racing Overview | `loadRacing` -> `racing_overview` | `loadLiveRacing` -> LIVE-safe racing projection/evidence | Racing LIVE remains hard-locked. |
| Racing Monitor | `loadRacingMonitor` -> `racing_monitor` | `loadLiveRacingMonitor` -> LIVE-safe/empty actual lifecycle | No SIM lifecycle fallback. |
| Racing Engines | `loadEngines0914('racing')` | same engine catalogue with mode enablement | Should stay semantically aligned with Sports Engines. |
| Performance | `performance_analytics` + `portfolio_financial_state` | `live_performance` + `portfolio_financial_state` | Current financial state and period performance must use the same selected scope/mode. |
| Market Analysis | `market_analysis` + `market_heatmap` | `live_market_analysis` + `live_market_heatmap` | Market/liquidity evidence may be shared; lifecycle/economic cells must be explicitly owned. |
| Replay | SIM replay/evidence projections | `live_replay` actual-only/empty until lifecycle exists | No SIM replay fallback in LIVE. |
| Scenarios | `analytics_replay` + `scenario_capital_sources` | hidden/empty in LIVE | Scenario economics are SIM-only. |
| Results | settled SIM ledger projections | `live_results` | Results are settlement-authority projections, not decision evidence. |
| Admin | settings/provider/account/engine commands | same Admin with explicit mode-aware provider controls | Mutation belongs here, not operational observation pages. |

The evidence bundle contains a generated method/dependency inventory for more detailed inspection.

## Backend data domains

### Configuration/runtime settings

- `DB.get_setting` / `DB.set_setting`
- operating/data-context mode
- provider controls, scanner/runtime settings and feature flags

Refactor risk: reads that call `set_setting` or `ensure_*` functions can become hidden writes. Such behaviour must be classified explicitly rather than inferred from method names.

### Provider market evidence

- market cache / latest snapshots / matched markets
- discovery rollups
- liquidity/market heatmap inputs
- provider runtime health

This domain is the main candidate for explicitly shared SIM/LIVE evidence where the facts are provider observations rather than economic lifecycle state.

### SIM lifecycle/economics

- opportunities and qualification
- execution runs
- monitor wallets and positions
- settlements and settlement audits
- account snapshots/reconciliation
- realised/period performance

This is authoritative SIM state and must never be used as a LIVE fallback.

### LIVE account/evidence/lifecycle

- provider-derived LIVE account snapshots/activity
- LIVE decision evidence (diagnostic)
- LIVE order-attempt persistence structures exist, but central execution remains locked
- actual LIVE execution/results projections are intentionally empty unless real authoritative rows exist

Decision evidence and actual LIVE lifecycle are distinct domains and must remain so.

### Engine state

- installed instances/config history
- route/lifecycle/mode enablement
- evaluations, decisions, experiments and performance

Engine metadata can be shared; economic results still inherit the mode/lifecycle authority of the evidence being evaluated.

## Primary coupling/duplication risks to address

1. **Frontend redefinition layering** — loaders/functions are repeatedly wrapped/reassigned by later release scripts. This makes final ownership non-local and makes stale-response bugs easier to introduce.
2. **Large RPC facade** — `API` composes operational, financial, analytics, engine, admin and lifecycle concerns in one class.
3. **Large DB facade** — schema, commands, projections and rollup maintenance live in one class, making read purity hard to prove by inspection.
4. **Read-time `ensure_*` risk** — some DB helpers intentionally materialise/maintain rollups or compatibility state. The refactor must distinguish approved derived-cache maintenance from forbidden canonical lifecycle/account writes.
5. **Parallel SIM/LIVE routes** — some duplication is necessary for safety, but duplicated orchestration can drift. Consolidation must happen at stable contracts/ownership boundaries, not by simply merging data sources.
6. **Mode-switch rendering races** — 0.9.57 already contains request-token/final-owner protections, proving this is a real failure mode. Refactor must centralise rather than weaken those protections.
7. **Projection breadth** — Dashboard, Performance, Accounts, Sports/Racing and Market Analysis aggregate many DB/provider domains. Query count and write count need measured route gates.
8. **Worker/UI shared SQLite** — refactor must avoid increasing write contention or moving expensive maintenance onto interactive reads.

## Target architecture direction

The refactor should move incrementally toward:

- explicit **query services** for read-only page projections;
- explicit **command services** for mutation/config/lifecycle actions;
- a small **mode context / route generation** object passed consistently through frontend and backend boundaries;
- explicit **market evidence**, **SIM lifecycle**, **LIVE actual lifecycle**, **account/financial**, **engine**, and **admin** service boundaries;
- one frontend route orchestrator with cancellable/stale-safe loaders rather than release-layer wrapper chains;
- thin pywebview API methods that delegate to bounded services;
- DB repository/query helpers whose side-effect class is obvious and testable;
- benchmarkable projection contracts with query/write counts and output fingerprints.

The refactor does **not** require an external service or RPC migration. Existing in-process serialisable contracts are a sufficient boundary for this phase.
