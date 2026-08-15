# ArbScanner Refactor Recovery Plan — Stages 01–12

Every stage is a transaction: verified parent -> isolated change -> QA -> package -> user verification -> next stage.

## Stage 01 — Scope reconstruction and architecture inventory

Current stage. Documentation/static inventory only. No runtime behaviour change.

Exit gate: complete scope, route/data ownership map, staged plan, no runtime source changes, 0.9.57 integrity checks still pass.

## Stage 02 — Refactor safety harness

Add/strengthen automated gates before structural change:

- SIM/LIVE projection ownership and stale-response tests;
- central LIVE order-write lock tests;
- read-projection DB write counter/audit;
- route query-count and output-fingerprint benchmark harness;
- authoritative lifecycle/account drift checks that report without repairing;
- same-DB reference/candidate comparison tooling.

No semantic refactor yet. The purpose is to make later regressions observable immediately.

## Stage 03 — Frontend mode and route ownership

Centralise mode context, route generation/request cancellation and loader ownership while preserving rendered semantics.

Goals:

- one stale-response rule for all page loads;
- synchronous mode-owned shell clearing where needed;
- no historical-mode flash during SIM/LIVE switch;
- reduce stacked function redefinitions/wrappers without altering page results.

## Stage 04 — Backend command/query boundary and read purity

Classify API/DB operations as query, command, lifecycle write, migration/maintenance or diagnostic.

Move forbidden mutation out of UI query paths. Any `ensure_*` or sync/backfill invoked by a query must be justified as a bounded non-authoritative cache operation or relocated to a write/migration boundary.

This stage explicitly prevents recurrence of the old Stage 12 lazy canonical-sync failure.

## Stage 05 — Canonical lifecycle authority

Make Position/Settlement/Result authority explicit at lifecycle write and migration boundaries.

- one authoritative write path for canonical lifecycle state;
- read-only integrity verification;
- no lazy repair from Dashboard/Sports/Racing/Results/Replay/Performance reads;
- settlement/P&L fingerprints identical to parent unless fixing a separately evidenced pre-existing bug.

## Stage 06 — Account and financial projection consolidation

Extract common selected-mode account/portfolio financial projections used by Dashboard, Accounts, Performance and Sports/Racing.

- preserve authoritative field coverage/unavailable semantics;
- eliminate duplicate current-state calculations;
- ensure reads do not create wallets/accounts or otherwise mutate funding state;
- maintain SIM/LIVE isolation and reconciliation semantics.

## Stage 07 — Market evidence and analytics ownership

Separate shared provider market/liquidity evidence from mode-owned lifecycle/economic metrics.

Consolidate Market Analysis/heatmap filtering and stream/scope handling behind explicit contracts. Shared evidence is labelled; LIVE lifecycle values fail closed instead of borrowing SIM values.

## Stage 08 — Sports/Racing/Engine projection consolidation

Reduce redundant page-specific projection logic while preserving domain differences.

- common engine status/activity contract;
- Sports/Racing page parity where semantics are genuinely common;
- no Racing LIVE unlock;
- no cross-domain lifecycle or account attribution.

## Stage 09 — API route consolidation

Use evidence from Stages 02–08 to remove or deprecate genuinely redundant RPC methods/data paths.

A route is removed only if:

- all frontend/worker/test consumers are identified;
- replacement output is contract-compatible or deliberately migrated;
- query/write/output comparison passes;
- no mode ownership is weakened.

## Stage 10 — Performance and data-load optimisation

Measure and improve interactive projections:

- query count;
- DB writes per read (target zero for authoritative reads);
- wall-clock projection time;
- duplicate provider/DB fetches;
- mode-switch time to correct owned shell/data;
- cache invalidation by financial revision/mode/scope where safe.

No optimisation is accepted solely from static intuition.

## Stage 11 — Structural decomposition and closure

Complete safe extraction of oversized API/DB/frontend responsibilities into bounded modules while retaining pywebview/in-process architecture.

Run full static architecture gate, targeted regression suites, JS syntax validation, Python compilation, package integrity and reference/candidate comparison.

## Stage 12 — External connected validation and final sign-off

Final sign-off requires both:

1. Reference vs Candidate benchmark using the same copied deployed database.
2. Connected Betfair/Matchbook live-feed soak with central LIVE order writing locked.

The local environment can prepare the tools and run static/snapshot comparisons, but it cannot substitute for the deployed process, credentials, live feeds, runtime logs and real deployed DB.

No final refactor sign-off until both external gates pass.
