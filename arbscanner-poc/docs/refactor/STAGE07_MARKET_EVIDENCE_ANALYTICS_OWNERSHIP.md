# Stage 07 — Market Evidence and Analytics Ownership

Parent checkpoint: Stage 06 — Account and Financial Projection Consolidation.

## Objective

Make Market Analysis and heatmap ownership explicit without changing their observable 0.9.57 behaviour:

- provider discovery, market breadth, quote/liquidity depth and executable-capacity observations are shared market evidence;
- SIM qualification/execution/settlement/deployed/P&L remain SIM lifecycle/economic authority;
- LIVE canonical lifecycle/economic values remain actual-LIVE only and fail closed while central LIVE order writing is locked;
- provider-derived LIVE decision qualification remains diagnostic evidence and never becomes canonical Qualified/Executed/Settlement/P&L;
- scope, stream, phase, sport and search semantics are defined once rather than independently reimplemented by each analytics route.

## New pure ownership module

`arbscanner.market_analytics` is a pure projection/contract module. It has no DB, provider runtime or live-provider dependency.

It owns:

1. `MarketFilters` — canonical scope/phase/sport/search/stream parsing.
2. `market_stream()` / `market_row_matches()` — shared row classification/filter rules.
3. Explicit heatmap metric ownership for `shared`, `sim`, `live` and `live_diagnostic` facts.
4. `live_heatmap_cell()` — preserves shared market/liquidity evidence while forcing actual-LIVE lifecycle/economics to zero.
5. `live_market_row()` — overlays isolated LIVE decision diagnostics while forcing canonical LIVE execution/economic fields to zero.

The module receives already-acquired rows/evidence. It cannot fetch SIM or LIVE authority, repair data, refresh providers, create accounts/wallets or mutate the DB.

## Compatibility rules preserved deliberately

Stage 07 does not reinterpret historical analytics semantics during the structural refactor.

- A complete `pre_match,in_play,racing` stream set remains the legacy unfiltered `All` state for Market Analysis/filtering.
- Racing remains its own stream regardless of a row's phase/in-play flag.
- Heatmap Sports stream classification continues to use the stored `in_play` flag rather than an optional phase hint.
- Rejection reasons and hourly activity retain their historical behaviour of following scope/phase/stream/sport while not being narrowed by Market Analysis free-text search.
- Discovery search continues to include `event_name`; normal market/liquidity search continues to use sport/market/section.
- LIVE heatmap retains its historical explicit-stream domain rule, including the distinction between an explicit mixed complete stream set and an omitted/unrestricted set. This is recorded rather than silently changed inside the refactor.

## Cross-mode hardening

`API.market_analysis()` now treats `mode=live` as shared-evidence-only at acquisition time. It cannot execute SIM lifecycle/economic SQL when the selected mode is LIVE, even if that lower-level route is called directly.

`API.live_market_analysis()` then overlays isolated `live_decision_analytics` diagnostics and uses `live_market_row()` to ensure canonical Qualified/Executed/Settlement/P&L remain actual-LIVE only.

`API.live_market_heatmap()` continues to acquire the shared heatmap with financial work disabled, overlays isolated LIVE decision evidence where the stored granularity is precise enough, and sanitises lifecycle/economic cells through the explicit LIVE ownership helper.

Market Analysis now requests a feed-only operational projection. That projection keeps scanner/provider evidence and the selected mode's account readiness needed for the existing feed RAG, but skips heavier storage/pipeline diagnostics and does not read the opposite mode's account snapshots. Full Admin/operational status remains unchanged.

No LIVE order-writing capability is introduced or enabled.

## Read purity

Stage 07 changes no lifecycle/account write boundary. The new ownership module is pure, and the existing Stage 02 comparison harness remains the gate for output equivalence and non-regressing query/write behaviour. Feed-only Market Analysis is allowed to reduce unrelated reads while preserving the exact projection fingerprint.

Market Analysis/heatmap derived rollup maintenance remains classified as the existing bounded derived analytics maintenance from the Stage 04 baseline. Stage 07 does not promote it to authority and does not move lifecycle/account repair into reads.

## Exit gates

Stage 07 may be promoted only when:

- Stage 02–07 targeted safety tests and the 0.9.57 Dashboard integrity test pass;
- Stage 06 Reference vs Stage 07 Candidate same-DB comparison passes all 23 projections with no blockers or warnings;
- market-analysis/heatmap fingerprints, query counts and write counts do not regress;
- candidate read projections perform zero authority-table writes;
- JavaScript syntax, Python compile and Dashboard browser integrity remain clean;
- the packaged Candidate is rebuilt from a fresh extraction of the user-verified Stage 06 ZIP and contains no QA cache artifacts.
